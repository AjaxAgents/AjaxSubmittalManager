"""Review-notes TXT (spec V2 section 10): plain ASCII, <= 100-char lines, decisions first, decided
non-compliances next, Human Review last and kept separate from non-compliance."""
from __future__ import annotations

import textwrap
import unicodedata
from datetime import datetime
from typing import Any

WIDTH = 100


def ascii(s: str) -> str:
    s = (s or "").replace("–", "-").replace("—", "-").replace("‘", "'").replace("’", "'") \
        .replace("“", '"').replace("”", '"').replace("•", "-").replace("×", "x") \
        .replace("≤", "<=").replace("≥", ">=").replace("°", " deg")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def wrap(line: str, indent: str = "") -> list[str]:
    line = ascii(line)
    if len(line) <= WIDTH:
        return [line]
    first, *rest = textwrap.wrap(line, WIDTH, subsequent_indent=indent or "      ", break_long_words=True)
    return [first, *rest]


def models_label(models: list[str]) -> str:
    """['claude-haiku-5-5','claude-opus-5-5'] -> 'Haiku/Opus 5.5'."""
    if not models:
        return "offline (mock)"
    order = {"haiku": 0, "sonnet": 1, "opus": 2}
    names, vers = [], []
    for m in models:
        parts = m.replace("claude-", "").split("-")
        names.append(parts[0])
        if len(parts) > 1:
            vers.append(".".join(p for p in parts[1:] if p.isdigit()))
    names = sorted(set(names), key=lambda n: order.get(n, 9))
    v = sorted(set(x for x in vers if x))
    return "/".join(n.title() for n in names) + (f" {', '.join(v)}" if v else "")


def recommendation(n_nc: int, n_hr: int) -> str:
    if n_nc == 0 and n_hr == 0:
        return "SUBMIT - NO EXCEPTIONS FOUND"
    if n_nc == 0:
        return f"RESOLVE {n_hr} HUMAN REVIEW ITEM(S), THEN SUBMIT"
    return (f"SUBMIT WITH DEVIATIONS NOTED, OR RETURN TO SUBCONTRACTOR "
            f"({n_nc} non-compliant, {n_hr} for human review)")


def build_txt(*, project: dict[str, Any], params: dict[str, Any], file_base: str, run_id: str,
              models: list[str], cost: float, verdicts: list[dict[str, Any]], format_decisions: list[str],
              excluded: list[str], extra_hr: list[dict[str, Any]], scope_checked: list[str],
              scope_not_checked: list[str], qa_lines: list[str], verifier_notes: list[str],
              human_review_note: str = "") -> str:
    L: list[str] = []
    add = L.extend
    nc = [v for v in verdicts if v["result"] == "non_compliant"]
    hr = [v for v in verdicts if v["result"] == "human_review"]
    comp = [v for v in verdicts if v["result"] == "compliant"]
    na = [v for v in verdicts if v["result"] == "not_applicable"]
    n_hr = len(hr) + len(extra_hr)

    add(wrap("AJAX SUBMITTAL MANAGER - REVIEW NOTES"))
    add(wrap(f"Project: {project.get('number') or ''} {project.get('name') or ''}".replace("  ", " ").strip()
             + f" | Submittal {file_base}"))
    add(wrap(f"Prepared: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Run ID: {run_id}"
             f" | Cost: ${cost:.2f}"))
    add(wrap(f"RECOMMENDATION: {recommendation(len(nc), n_hr)}"))
    add([""])

    add(["SCOPE CHECKED"])
    for s in scope_checked:
        add(wrap(f"- {s}", "  "))
    for s in scope_not_checked:
        add(wrap(f"- Not checked: {s}", "  "))
    add([""])

    add(["1. DECISIONS AND JUSTIFICATIONS"])
    if not (comp or format_decisions or excluded or na):
        add(["(none)"])
    for v in comp:
        add(wrap(f"[{v['txt_id']}] {v['summary']}"))
    for v in na:
        add(wrap(f"[{v['txt_id']}] N/A: {v['summary']}"))
    for i, x in enumerate(excluded, 1):
        add(wrap(f"[X-{i:02d}] {x}"))
    for i, f in enumerate(format_decisions, 1):
        add(wrap(f"[F-{i:02d}] {f}"))
    add([""])

    add(["2. NON-COMPLIANT (DECIDED)"])
    if not nc:
        add(["(none)"])
    for v in nc:
        add(wrap(f"[{v['txt_id']}] {v['summary']}"))
    add([""])

    add(["3. HUMAN REVIEW REQUIRED"])
    groups = [
        ("3a. Questionable compliance / missing information", ("questionable", "missing_info", "budget_hit", "scope", "")),
        ("3b. RFI candidates", ("rfi",)),
        ("3c. Faulty or contradictory data", ("faulty_data",)),
    ]
    any_hr = False
    for title, kinds in groups:
        items = [v for v in hr if v.get("human_review_kind", "") in kinds]
        xs = [x for x in extra_hr if x.get("kind", "") in kinds or (kinds[0] == "questionable" and x.get("kind") in ("injection", "deviation"))]
        if not items and not xs:
            continue
        any_hr = True
        add([title])
        for v in items:
            note = " (both reviewers' positions differ)" if v.get("decided_by") == "adjudicator" and v.get("human_review_kind") == "questionable" else ""
            add(wrap(f"[{v['txt_id']}] {v['summary']}{note}"))
        for x in xs:
            add(wrap(f"[{x['txt_id']}] {x['summary']}"))
    if not any_hr:
        add(["(none)"])
    if human_review_note:
        add(wrap(human_review_note))

    if qa_lines or verifier_notes:
        add(["", "QA / VERIFIER NOTES"])
        for q in qa_lines:
            add(wrap(f"- {q}", "  "))
        for q in verifier_notes[:15]:
            add(wrap(f"- {q}", "  "))
    return "\n".join(L).rstrip() + "\n"
