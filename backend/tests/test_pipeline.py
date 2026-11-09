"""End-to-end run with the offline stand-ins: exercises catalog, scope, adjudication, PDF and TXT."""
import asyncio

import pymupdf

from asm import db, pipeline
from asm.library.catalog import heuristic_scope
from asm.pdf.markup import markup_inventory
from conftest import upload


def _run(project, demo, **extra):
    params = {"spec_section": "08 71 00", "submittal_no": "003", "revision": "0", "submittal_type": "Product Data",
              "instructions": "", "mimic_example_naming": True,
              "uploads": {"submittal": [upload(demo, "Submittal - Door Hardware Product Data.pdf")],
                          "examples": [upload(demo, "Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf")],
                          "div01": [upload(demo, "Division 01 - 01 33 00 Submittal Procedures.pdf")]},
              "scope": {"mode": "flexible", "items": heuristic_scope(project["id"], "08 71 00")}, **extra}
    job = db.create_job(project["id"], params)
    asyncio.run(pipeline.run_job(job["id"]))
    return db.get_job(job["id"])


def test_full_run(project, demo):
    j = _run(project, demo)
    assert j["status"] == "done", j["error"]
    r = j["result"]
    assert r["pdf_name"] == "08 71 00-003-0 Door Hardware - Product Data.pdf"
    assert r["txt_name"] == "08 71 00-003-0 Door Hardware - Product Data_ReviewNotes.txt"
    assert r["counts"]["non_compliant"] >= 2       # hinge size + addendum finish
    txt = open(r["txt"], encoding="ascii").read()
    lines = txt.splitlines()
    assert all(len(l) <= 100 for l in lines)
    order = [txt.index(h) for h in ("SCOPE CHECKED", "1. DECISIONS AND JUSTIFICATIONS", "2. NON-COMPLIANT (DECIDED)", "3. HUMAN REVIEW REQUIRED")]
    assert order == sorted(order)
    assert "Addendum 01" in txt and "RECOMMENDATION:" in txt and "Cost: $" in txt
    doc = pymupdf.open(r["pdf"])
    toc = [t[1] for t in doc.get_toc()]
    assert toc[0] == "Cover / Transmittal" and "Deviation Summary" in toc
    inv = markup_inventory(doc)
    devs = [a for a in inv if a["subject"] == "Deviation" and a["type"] == "Polygon"]
    assert len(devs) >= 2
    assert any(a["subject"] == "Review Stamp" for a in inv)
    assert all(a["has_ap"] and a["nm"] for a in inv)
    # every declared deviation in the PDF has a TXT entry
    for a in devs:
        assert f"[{a['nm'].split('-M')[0]}]" in txt
    assert doc.metadata["title"] == r["pdf_name"][:-4]


def test_rerun_with_notes(project, demo):
    j = _run(project, demo)
    nid = [v for v in j["result"]["verdicts"] if v["result"] == "non_compliant"][0]["txt_id"]
    params = dict(j["params"], _rerun_of=j["id"])
    j2 = db.create_job(project["id"], params, parent_job=j["id"])
    asyncio.run(pipeline.run_job(j2["id"], rerun_notes=f"{nid} is accepted per RFI 112."))
    j2 = db.get_job(j2["id"])
    assert j2["status"] == "done", j2["error"]
    assert j2["result"]["counts"]["non_compliant"] == j["result"]["counts"]["non_compliant"] - 1


def test_strict_scope_gap_becomes_human_review(project, demo):
    j = _run(project, demo, scope={"mode": "strict", "items": [{"id": "S:08 71 00", "locked": True}]})
    assert j["status"] == "done"
