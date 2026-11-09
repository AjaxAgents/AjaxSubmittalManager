"""Claude API wrapper: model selection, streaming calls, usage/cost logging, budgets, cancellation.

Every call's model, tokens in/out/cached and cost goes to SQLite (api_calls) and to the live job
progress stream. Budgets: at `budget_downgrade.at_fraction` of the job's dollar cap the expensive
model is swapped for the fallback; at 100% the job raises BudgetExceeded.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import api_key, models_cfg, pricing_cfg, settings_cfg, stage_cfg

log = logging.getLogger("asm.llm")


class BudgetExceeded(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


def price_for(model: str) -> dict[str, float]:
    p = pricing_cfg().get("models", {})
    return p.get(model) or p.get("default") or {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.5}


def cost_of(model: str, usage: dict[str, int]) -> float:
    pr = price_for(model)
    c = (usage.get("input_tokens", 0) * pr["input"]
         + usage.get("output_tokens", 0) * pr["output"]
         + usage.get("cache_creation_input_tokens", 0) * pr["cache_write"]
         + usage.get("cache_read_input_tokens", 0) * pr["cache_read"]) / 1_000_000
    c += usage.get("web_searches", 0) * float(pricing_cfg().get("web_search_per_1000", 10)) / 1000
    return round(c, 6)


@dataclass
class CostTracker:
    job_id: str | None = None
    project_id: str | None = None
    usd_cap: float = 15.0
    token_cap: int = 3_000_000
    spent_usd: float = 0.0
    tokens: int = 0
    calls: int = 0
    by_stage: dict[str, dict[str, float]] = field(default_factory=dict)
    models_used: set[str] = field(default_factory=set)
    cancelled: bool = False
    on_usage: Callable[[dict[str, Any]], None] | None = None

    @classmethod
    def for_job(cls, job_id: str | None, project_id: str | None, usd_cap: float | None = None) -> "CostTracker":
        b = settings_cfg().get("budget", {})
        return cls(job_id=job_id, project_id=project_id,
                   usd_cap=float(usd_cap if usd_cap is not None else b.get("job_usd_cap", 15)),
                   token_cap=int(b.get("job_token_cap", 3_000_000)))

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled("Job cancelled by user")
        if self.spent_usd >= self.usd_cap or self.tokens >= self.token_cap:
            raise BudgetExceeded(f"Job budget reached (${self.spent_usd:.2f} of ${self.usd_cap:.2f})")

    def choose_model(self, model: str) -> str:
        dg = models_cfg().get("budget_downgrade") or {}
        frac = float(dg.get("at_fraction", 0.8))
        if self.usd_cap and self.spent_usd >= frac * self.usd_cap and model == dg.get("from"):
            return dg.get("to", model)
        return model

    def record(self, stage: str, model: str, usage: dict[str, int], ms: int) -> float:
        c = cost_of(model, usage)
        self.spent_usd += c
        self.tokens += sum(usage.get(k, 0) for k in ("input_tokens", "output_tokens",
                                                       "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.calls += 1
        self.models_used.add(model)
        s = self.by_stage.setdefault(stage, {"cost": 0.0, "calls": 0, "input": 0, "output": 0, "cache_read": 0})
        s["cost"] += c
        s["calls"] += 1
        s["input"] += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        s["output"] += usage.get("output_tokens", 0)
        s["cache_read"] += usage.get("cache_read_input_tokens", 0)
        try:
            db.log_api_call(self.job_id, self.project_id, stage, model, usage, c, ms)
        except Exception as exc:  # logging must never kill a job
            log.warning("could not log api call: %s", exc)
        if self.on_usage:
            self.on_usage({"stage": stage, "model": model, "cost": c, "spent": round(self.spent_usd, 4),
                           "cap": self.usd_cap, "usage": usage})
        return c


_clients: dict[tuple[str, int], Any] = {}


def client(key: str | None = None):
    """AsyncAnthropic client per (API key, event loop). httpx async clients are bound to the loop that
    created them, and ASM runs ingest summaries on a worker-thread loop and reviews on the server loop."""
    key = key or api_key()
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set. Add it in Settings or in the backend .env file.")
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    ck = (key, loop_id)
    if ck not in _clients:
        import anthropic
        timeout = float(models_cfg().get("limits", {}).get("request_timeout_s", 900))
        if len(_clients) > 8:
            _clients.clear()
        _clients[ck] = anthropic.AsyncAnthropic(api_key=key, timeout=timeout, max_retries=4)
    return _clients[ck]


def _usage_dict(u: Any) -> dict[str, int]:
    d = {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        d["web_searches"] = getattr(stu, "web_search_requests", 0) or 0
    return d


async def create_message(tracker: CostTracker, stage: str, *, system: list[dict] | str, messages: list[dict],
                         tools: list[dict] | None = None, model: str | None = None,
                         max_tokens: int | None = None) -> Any:
    """One streamed Messages API call with budget checks and cost logging."""
    tracker.check()
    cfg = stage_cfg(stage)
    model = tracker.choose_model(model or cfg["model"])
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens or cfg["max_tokens"]),
        "system": system,
        "messages": messages,
    }
    if tools:
        params["tools"] = tools
    extra = (models_cfg().get("extra_params") or {}).get(stage) or {}
    params.update(extra)
    t0 = time.time()
    from ..auth import api_key_for_project
    c = client(api_key_for_project(tracker.project_id))
    attempt = 0
    while True:
        try:
            async with c.messages.stream(**params) as stream:
                msg = await stream.get_final_message()
            break
        except Exception as exc:  # overloaded / transient network errors get one more manual retry
            attempt += 1
            name = type(exc).__name__
            if attempt <= 2 and name in {"APIConnectionError", "APITimeoutError", "InternalServerError",
                                         "OverloadedError", "RateLimitError"}:
                await asyncio.sleep(10 * attempt)
                continue
            raise
    tracker.record(stage, model, _usage_dict(msg.usage), int((time.time() - t0) * 1000))
    return msg
