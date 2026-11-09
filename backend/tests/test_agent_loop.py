"""The real Claude tool loop, driven by a scripted fake client (no network)."""
import asyncio
import json

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from asm.agents import base, llm
from asm.agents.base import Tool, text_block
from asm.schemas import CatalogSummary


class FakeStream:
    def __init__(self, msg): self.msg = msg
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_final_message(self): return self.msg


class FakeClient:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.messages = self

    def stream(self, **params):
        self.requests.append(json.loads(json.dumps(params, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))))
        return FakeStream(self.script.pop(0))


def msg(*blocks, stop="tool_use"):
    return Message(id="m", type="message", role="assistant", model="claude-haiku-5-5", content=list(blocks),
                   stop_reason=stop, stop_sequence=None, usage=Usage(input_tokens=100, output_tokens=20))


def test_tool_loop_retries_invalid_then_succeeds(data, monkeypatch):
    script = [
        msg(TextBlock(type="text", text="Looking"), ToolUseBlock(type="tool_use", id="t1", name="lookup", input={"q": "x"})),
        msg(ToolUseBlock(type="tool_use", id="t2", name="submit_result", input={"keywords": "not-a-list"})),
        msg(TextBlock(type="text", text="done?"), stop="end_turn"),
        msg(ToolUseBlock(type="tool_use", id="t3", name="submit_result", input={"summary": "ok", "keywords": ["UL 10C"]})),
    ]
    fake = FakeClient(script)
    monkeypatch.setattr(llm, "client", lambda *a, **k: fake)
    tracker = llm.CostTracker.for_job(None, None, usd_cap=5)
    calls = []
    tool = Tool("lookup", "d", {"type": "object", "properties": {"q": {"type": "string"}}}, lambda a: calls.append(a) or "result text")
    run = asyncio.run(base.run_agent(stage="catalog", tracker=tracker, system="sys", user_content=[text_block("prefix", cache=True), text_block("go")],
                                     result_model=CatalogSummary, tools=[tool]))
    assert run.result.keywords == ["UL 10C"] and calls == [{"q": "x"}]
    assert tracker.calls == 4 and tracker.spent_usd > 0
    for req in fake.requests:
        s = json.dumps(req)
        assert "_keep_cache" not in s
        n_cache = s.count('"cache_control"')
        assert n_cache <= 4, n_cache
        assert "tool_choice" not in req  # forced tool_choice unsupported on 5.5 Opus/Sonnet
    # validation error went back as an is_error tool_result
    third = fake.requests[2]["messages"][-1]["content"][0]
    assert third["type"] == "tool_result" and third.get("is_error")


def test_budget_downgrade_and_stop(data):
    t = llm.CostTracker.for_job(None, None, usd_cap=1.0)
    t.spent_usd = 0.85
    assert t.choose_model("claude-opus-5-5") == "claude-sonnet-5-5"
    t.spent_usd = 1.0
    try:
        t.check()
        assert False
    except llm.BudgetExceeded:
        pass
