"""Shared agent tool loop.

Each stage = system prompt (prompts/<stage>.md) + tools + a Pydantic result model. The model must
finish by calling `submit_result`, whose input schema IS the result model. Invalid output is
returned to the model as a tool error with the validation message and retried.

Note: forced tool_choice is not available on the 5.5-series Opus/Sonnet models, so the loop uses
tool_choice auto and nudges the model to call submit_result if it ends its turn without it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import models_cfg
from .llm import CostTracker, create_message

log = logging.getLogger("asm.agent")

T = TypeVar("T", bound=BaseModel)

CACHE = {"type": "ephemeral"}

ToolHandler = Callable[[dict[str, Any]], Any]  # returns str | list[content blocks]


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $refs inlined (simplest form for tool input_schema)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = copy.deepcopy(defs[name])
                extra = {k: v for k, v in node.items() if k != "$ref"}
                merged.update(extra)
                return resolve(merged)
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    out = resolve(schema)
    out["type"] = "object"
    return out


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class AgentRun(Generic[T]):
    result: T
    turns: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _strip_cache(messages: list[dict]) -> None:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("_keep_cache"):
                    continue
                if isinstance(b, dict):
                    b.pop("cache_control", None)


def _mark_last_user_block(messages: list[dict]) -> None:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], list) and m["content"]:
            last = m["content"][-1]
            if isinstance(last, dict):
                last["cache_control"] = CACHE
            return


def _clean_for_api(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            nc = []
            for b in c:
                if isinstance(b, dict) and "_keep_cache" in b:
                    b = {k: v for k, v in b.items() if k != "_keep_cache"}
                nc.append(b)
            out.append({"role": m["role"], "content": nc})
        else:
            out.append(m)
    return out


async def run_agent(
    *,
    stage: str,
    tracker: CostTracker,
    system: str,
    user_content: list[dict[str, Any]],
    result_model: type[T],
    tools: list[Tool] | None = None,
    submit_description: str = "Submit your final result. Call exactly once when done.",
    max_turns: int | None = None,
    model: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    web_search: bool = False,
    first_call_done: asyncio.Event | None = None,
) -> AgentRun[T]:
    """Run one stage to completion and return its validated result."""
    tools = tools or []
    lim = models_cfg().get("limits", {})
    max_turns = max_turns or int(lim.get("max_turns_per_stage", 30))
    submit = {"name": "submit_result", "description": submit_description, "input_schema": inline_schema(result_model)}
    # cache breakpoint on the last client tool (tools are first in the cached prefix)
    tool_specs = [t.spec() for t in tools] + [{**submit, "cache_control": CACHE}]
    ws = models_cfg().get("web_search") or {}
    if web_search and ws.get("enabled") and stage in (ws.get("stages") or []):
        tool_specs.append({"type": ws.get("tool_type", "web_search_20250305"), "name": "web_search",
                           "max_uses": int(ws.get("max_uses", 3))})
    handlers = {t.name: t.handler for t in tools}
    system_blocks = [{"type": "text", "text": system, "cache_control": CACHE}]

    messages: list[dict[str, Any]] = [{"role": "user", "content": list(user_content)}]
    tool_log: list[dict[str, Any]] = []
    notes: list[str] = []
    nudges = 0
    invalid = 0

    for turn in range(1, max_turns + 1):
        _strip_cache(messages)
        _mark_last_user_block(messages)
        msg = await create_message(tracker, stage, system=system_blocks, messages=_clean_for_api(messages),
                                   tools=tool_specs, model=model)
        if first_call_done is not None and not first_call_done.is_set():
            first_call_done.set()
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})

        if msg.stop_reason == "pause_turn":  # server tool (web search) still working
            continue
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"{stage}: the model declined to continue")

        tool_uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            if msg.stop_reason == "max_tokens":
                messages.append({"role": "user", "content": [{"type": "text", "text":
                    "Your output was cut off by the token limit. Call submit_result now with a complete but more concise result."}]})
                continue
            nudges += 1
            if nudges > 3:
                raise RuntimeError(f"{stage}: model never called submit_result")
            messages.append({"role": "user", "content": [{"type": "text", "text":
                "Call the submit_result tool now with your complete result. Do not answer in plain text."}]})
            continue

        results: list[dict[str, Any]] = []
        final: T | None = None
        for tu in tool_uses:
            if tu.name == "submit_result":
                try:
                    final = result_model.model_validate(tu.input)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "Received."})
                except ValidationError as exc:
                    invalid += 1
                    err = exc.errors()[:12]
                    detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                    "content": f"Result failed schema validation: {detail}. Fix these fields and call submit_result again."})
                    if invalid > 2:
                        raise RuntimeError(f"{stage}: invalid structured output after retries: {detail}")
                continue
            handler = handlers.get(tu.name)
            if handler is None:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                "content": f"Unknown tool {tu.name}"})
                continue
            try:
                out = await asyncio.to_thread(handler, dict(tu.input))
                content = out if isinstance(out, list) else [{"type": "text", "text": str(out)}]
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
                tool_log.append({"tool": tu.name, "input": tu.input})
                if on_event:
                    on_event("tool", {"stage": stage, "tool": tu.name, "input": _short(tu.input)})
            except Exception as exc:  # report tool errors to the model instructively
                log.exception("tool %s failed", tu.name)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                "content": f"{type(exc).__name__}: {exc}"})
        if final is not None:
            return AgentRun(result=final, turns=turn, tool_calls=tool_log, notes=notes)
        messages.append({"role": "user", "content": results})
        if turn == max_turns - 2:
            messages[-1]["content"].append({"type": "text", "text":
                "Turn limit nearly reached. Stop retrieving and call submit_result with what you have; "
                "mark anything unverified as low confidence."})

    raise RuntimeError(f"{stage}: exceeded {max_turns} turns without a result")


def _short(d: Any) -> str:
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) < 160 else s[:157] + "..."


def text_block(text: str, cache: bool = False) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        b["cache_control"] = CACHE
        b["_keep_cache"] = True
    return b


def pdf_block(data_b64: str, title: str = "", cache: bool = False) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data_b64}}
    if title:
        b["title"] = title
    if cache:
        b["cache_control"] = CACHE
        b["_keep_cache"] = True
    return b


def image_block(png_b64: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}
