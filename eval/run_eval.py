"""Evaluation runner (spec V2 section 16).

Each case folder in eval/cases/ holds the PDFs for one past submittal plus case.yaml:

    spec_section: "08 71 00"
    submittal_no: "003"
    revision: "0"
    submittal_type: Product Data
    library: [Project Manual.pdf, Drawings.pdf, Addendum 01.pdf]
    submittal: [Door Hardware PD.pdf]
    examples: []            # optional
    div01: []               # optional
    instructions: ""
    scope_mode: flexible
    expected:               # one entry per A/E comment that marked a deviation
      - id: AE-1
        keywords: ["630", "finish"]   # all must appear (case-insensitive) in one NC / Human Review line
        page: 4                       # optional: submittal page the comment is on
      - id: AE-2
        keywords: ["hinge", "5 x 4"]

Usage: python eval/run_eval.py [case_name ...]  (add --mock to exercise the pipeline offline)
Prints recall (A/E deviations caught as Non-compliant or Human Review), unmatched Non-compliant calls
(candidate false positives for a reviewer to judge), citation coverage, run time and cost per case.
Real runs call the Claude API and cost money. Keep confidential cases out of git (eval/cases is ignored).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--mock" in sys.argv:
        os.environ["ASM_MOCK_LLM"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix="asm-eval-"))
    os.environ["ASM_DATA_DIR"] = str(tmp)
    from asm import db, pipeline
    from asm.library.catalog import heuristic_scope
    from asm.library.ingest import ingest_file
    from asm.pdf.util import inspect_pdf, sha256_file

    db.set_db_path(tmp / "asm.sqlite3")
    cases_dir = ROOT / "eval" / "cases"
    cases = sorted(p for p in cases_dir.iterdir() if (p / "case.yaml").exists()) if cases_dir.exists() else []
    if args:
        cases = [c for c in cases if c.name in args]
    if not cases:
        print("No cases found in eval/cases/*/case.yaml - see the docstring of this file for the format.")
        return

    def add_upload(path: Path) -> str:
        i = inspect_pdf(path)
        return db.add_upload(path.name, str(path), sha256_file(path), i["pages"], path.stat().st_size, i["flags"])["id"]

    rows = []
    for case in cases:
        spec = yaml.safe_load((case / "case.yaml").read_text())
        proj = db.create_project(name=f"eval {case.name}", number="EVAL")
        for fn in spec.get("library", []):
            p = case / fn
            i = inspect_pdf(p)
            f = db.add_library_file(proj["id"], "auto", fn, str(p), sha256_file(p), i["pages"], p.stat().st_size)
            ingest_file(proj["id"], f["id"])
        params = {
            "spec_section": spec["spec_section"], "submittal_no": str(spec.get("submittal_no", "1")),
            "revision": str(spec.get("revision", "0")), "submittal_type": spec.get("submittal_type", "Product Data"),
            "instructions": spec.get("instructions", ""), "mimic_example_naming": True,
            "uploads": {"submittal": [add_upload(case / f) for f in spec["submittal"]],
                        "examples": [add_upload(case / f) for f in spec.get("examples", [])],
                        "div01": [add_upload(case / f) for f in spec.get("div01", [])]},
            "scope": {"mode": spec.get("scope_mode", "flexible"), "items": heuristic_scope(proj["id"], spec["spec_section"])},
        }
        job = db.create_job(proj["id"], params)
        t0 = time.time()
        asyncio.run(pipeline.run_job(job["id"]))
        el = time.time() - t0
        j = db.get_job(job["id"])
        if j["status"] != "done":
            rows.append((case.name, "ERROR", j["error"]))
            continue
        r = j["result"]
        flagged = [v for v in r["verdicts"] if v["result"] in ("non_compliant", "human_review")]
        lines = [(v["result"], v["summary"].lower(), v.get("pages", [])) for v in flagged]
        lines += [("human_review", x["summary"].lower(), []) for x in r.get("extra_hr", [])]
        caught, used = 0, set()
        misses = []
        for exp in spec.get("expected", []):
            kws = [k.lower() for k in exp["keywords"]]
            hit = next((i for i, (_, s, pg) in enumerate(lines) if all(k in s for k in kws)
                        and (not exp.get("page") or not pg or exp["page"] in pg)), None)
            if hit is not None:
                caught += 1
                used.add(hit)
            else:
                misses.append(exp["id"])
        n_exp = len(spec.get("expected", []))
        unmatched_nc = [s for i, (res, s, _) in enumerate(lines) if res == "non_compliant" and i not in used]
        cited = sum(1 for v in r["verdicts"] if v.get("pages") or v["result"] == "not_applicable")
        rows.append((case.name, f"recall {caught}/{n_exp} ({(caught / n_exp * 100 if n_exp else 100):.0f}%)",
                     f"unmatched NC {len(unmatched_nc)} | cited {cited}/{len(r['verdicts'])} | {el / 60:.1f} min | ${r['cost']:.2f}"
                     + (f" | missed {', '.join(misses)}" if misses else "")))
    print("\nEVALUATION")
    for row in rows:
        print("  " + " | ".join(str(x) for x in row))
    print(f"\nWork folder: {tmp}")


if __name__ == "__main__":
    main()
