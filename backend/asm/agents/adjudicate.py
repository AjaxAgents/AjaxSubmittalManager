"""Deterministic verification and rule-based adjudication (spec V2 section 7 adjudication table).

* verify_findings: confirms cited pages exist and quotes appear on them; unsupported findings are
  downgraded to low confidence (which routes them to Human Review).
* rule_verdicts: settles clear-cut agreements by rule and returns the disputes for the Adjudicator.
"""
from __future__ import annotations

import re
from typing import Any

from ..schemas import Finding, PlannerResult, ReviewResult, Verdict

_W = re.compile(r"[A-Za-z0-9.]+")


def _words(s: str) -> list[str]:
    return [w.lower().strip(".") for w in _W.findall(s or "") if w.strip(".")]


def quote_on_page(quote: str, page_text: str) -> bool:
    if not quote.strip():
        return False
    q = " ".join(quote.split()).lower()
    t = " ".join(page_text.split()).lower()
    if q in t:
        return True
    qw = _words(quote)
    if not qw:
        return False
    tw = set(_words(page_text))
    hit = sum(1 for w in qw if w in tw)
    return hit / len(qw) >= 0.7


def verify_findings(review: ReviewResult, page_texts: list[str]) -> list[str]:
    """Mutates findings in place. Returns verifier notes."""
    notes: list[str] = []
    n = len(page_texts)
    for f in review.findings:
        if f.status in ("not_applicable", "no_issue", "no_evidence", "unclear"):
            # nothing affirmative to verify (no_issue is checked via its evidence if given)
            pass
        valid = []
        for ev in f.evidence:
            if not (1 <= ev.page <= n):
                notes.append(f"{f.agent} {f.requirement_id}: cited page {ev.page} does not exist")
                continue
            txt = page_texts[ev.page - 1]
            if ev.quote and len(txt.strip()) > 30 and not quote_on_page(ev.quote, txt):
                notes.append(f"{f.agent} {f.requirement_id}: quote not found on p.{ev.page}: \"{ev.quote[:50]}\"")
                continue
            valid.append(ev)
        affirmative = f.status in ("met", "deviation", "not_met")
        if affirmative and not valid:
            if f.confidence != "low":
                notes.append(f"{f.agent} {f.requirement_id}: no verifiable citation -> confidence lowered")
            f.confidence = "low"
        f.evidence = valid
        for m in f.proposed_markups:
            if not (1 <= m.page <= n):
                m.page = valid[0].page if valid else 1
    return notes


def _pages(*fs: Finding | None) -> list[int]:
    out: list[int] = []
    for f in fs:
        if f:
            for e in f.evidence:
                if e.page not in out:
                    out.append(e.page)
    return out


def _pos(f: Finding | None) -> str:
    if not f:
        return "not evaluated"
    return f"{f.status} ({f.confidence}): {f.reason}"


def rule_verdicts(plan: PlannerResult, adv: ReviewResult, exa: ReviewResult) -> tuple[list[Verdict], list[dict[str, Any]]]:
    A = {f.requirement_id: f for f in adv.findings}
    B = {f.requirement_id: f for f in exa.findings}
    verdicts: list[Verdict] = []
    disputes: list[dict[str, Any]] = []
    for r in plan.requirements:
        a, b = A.get(r.id), B.get(r.id)
        cite = f"{r.source.section} {r.source.article}".strip()
        pages = _pages(a, b)

        def V(result: str, kind: str = "", summary: str = "") -> Verdict:
            return Verdict(requirement_id=r.id, result=result, human_review_kind=kind,  # type: ignore[arg-type]
                           summary=summary, pages=pages, decided_by="rules",
                           advocate_position=_pos(a), examiner_position=_pos(b))

        if a is None and b is None:
            verdicts.append(V("human_review", "questionable", f"Not evaluated by either reviewer; check manually ({cite})."))
            continue
        a_st = a.status if a else "no_evidence"
        b_st = b.status if b else "no_evidence"
        a_cf = a.confidence if a else "low"
        b_cf = b.confidence if b else "low"
        b_kind = b.issue_kind if b else "none"

        if a_st == "not_applicable" and b_st in ("not_applicable", "no_issue"):
            verdicts.append(V("not_applicable", summary=(a.reason if a else "Not applicable") + f" ({cite})"))
            continue
        if b_kind == "rfi":
            verdicts.append(V("human_review", "rfi", f"{b.reason}"))  # type: ignore[union-attr]
            continue
        if b_kind == "faulty_data":
            verdicts.append(V("human_review", "faulty_data", f"{b.reason}"))  # type: ignore[union-attr]
            continue
        if b_kind == "unapproved_substitution":
            verdicts.append(V("human_review", "questionable", f"Possible substitution: {b.reason}"))  # type: ignore[union-attr]
            continue
        if a_st == "met" and b_st == "no_issue" and "low" not in (a_cf, b_cf) and "high" in (a_cf, b_cf):
            verdicts.append(V("compliant", summary=a.reason))  # type: ignore[union-attr]
            continue
        if (a_st in ("not_met", "no_evidence") and b_st in ("deviation", "not_met") and b and b.evidence
                and b_cf != "low" and b_kind in ("deviation", "none")):
            verdicts.append(V("non_compliant", summary=b.reason))
            continue
        if a_st == "no_evidence" and b_st in ("no_evidence", "not_met") and b_kind in ("missing_info", "none", "questionable"):
            verdicts.append(V("human_review", "missing_info",
                              (b.reason if b else a.reason if a else "Not shown in submittal") + f" Confirm with supplier ({cite})."))
            continue
        if "low" in (a_cf, b_cf):
            reason = (b.reason if b and b_cf == "low" else a.reason if a else "") or "Low-confidence evidence"
            verdicts.append(V("human_review", "questionable", reason))
            continue
        disputes.append({
            "requirement_id": r.id, "requirement": r.text, "source": cite, "amended_by": r.amended_by,
            "advocate": a.model_dump(include={"status", "confidence", "reason", "evidence"}) if a else None,
            "examiner": b.model_dump(include={"status", "issue_kind", "confidence", "reason", "evidence"}) if b else None,
            "pages": pages,
        })
    return verdicts, disputes
