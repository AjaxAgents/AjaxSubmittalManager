"""Pre-run cost model. Rough by design: live metering during the run is the source of truth.

Assumptions (tuned as real runs come in; see the Sources & cost tab of each run):
* a submittal page sent as PDF costs ~2,200 input tokens (text + page image)
* the Advocate writes the shared cached prefix once; the Examiner reads it (cache reads are 5% of input price)
* each reviewer takes ~6 tool-loop turns and writes ~300 output tokens per requirement plus ~6k of reasoning
* the Adjudicator sees only disputed items (~25% of requirements) and their cited pages
"""
from __future__ import annotations

from typing import Any

from .agents.llm import price_for
from .config import models_cfg, settings_cfg, stage_cfg

PAGE_TOK = 2200


def _c(stage: str, inp: float = 0, out: float = 0, read: float = 0, write: float = 0) -> float:
    p = price_for(stage_cfg(stage)["model"])
    return (inp * p["input"] + out * p["output"] + read * p["cache_read"] + write * p["cache_write"]) / 1e6


def estimate(sub_pages: int, lib_tokens: int, n_req: int | None = None) -> dict[str, Any]:
    inline_max = int(models_cfg().get("limits", {}).get("submittal_inline_max_pages", 75))
    S = sub_pages * PAGE_TOK if sub_pages <= inline_max else sub_pages * 700 + 20 * PAGE_TOK  # text + on-demand pages
    L = max(lib_tokens, 3000)
    n = n_req or int(min(60, max(15, L / 450)))
    R = n * 150
    prefix = S + 6000 + 5000 + R
    turns = 6
    out_rev = n * 300 + 6000
    pulled = int(L * 0.5)
    disputes = max(2, n // 4)
    exc_pages = min(20, int(disputes * 1.5))

    stages = {
        "intake": _c("intake", inp=S + 3000, out=sub_pages * 60 + 1500),
        "planner": _c("planner", inp=2 * (L + 5000) + 10000, out=n * 120 + 3000),
        "advocate": _c("advocate", write=prefix + pulled, read=(turns - 1) * (prefix + pulled), out=out_rev),
        "examiner": _c("examiner", write=pulled, read=turns * (prefix + pulled), out=out_rev),
        "adjudicator": _c("adjudicator", write=exc_pages * PAGE_TOK + 5000, out=disputes * 250 + 1500),
    }
    if (settings_cfg().get("qa") or {}).get("llm_check"):
        stages["qa"] = _c("qa", inp=12000, out=1500)
    total = sum(stages.values())
    return {
        "by_stage": {k: round(v, 3) for k, v in stages.items()},
        "low": round(total * 0.75, 2), "mid": round(total, 2), "high": round(total * 1.5, 2),
        "requirements_assumed": n, "input_tokens": int(S * 2 + L * 2 + turns * 2 * prefix),
    }
