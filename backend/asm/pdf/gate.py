"""Bluebeam compatibility gate (spec V2 section 8).

Builds a test PDF containing every markup type the engine can write, on a plain page and on a
90-degree rotated page. Open it in Bluebeam Revu and confirm that each markup appears in the
Markups List with correct Author / Subject / Comment, can be selected, moved, resized, restyled,
keeps editable text, and survives save + reopen. Record the Revu version in docs/BLUEBEAM_GATE.md.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from ..schemas import MarkupInstruction
from .markup import MarkupEngine, markup_inventory

SAMPLE_LINES = [
    ("DOOR CLOSER - SERIES 4040XP", 16),
    ("Model 4040XP-REG  Grade 1 per ANSI/BHMA A156.4", 11),
    ("Finish: 689 Aluminum Painted    Optional finish: US26D", 11),
    ("Arm options: REG  |  CUSH  |  EDA  |  HCUSH", 11),
    ("Cylinder: Cast iron body, full rack and pinion", 11),
    ("UL 10C positive pressure fire tested, 3 hour", 11),
    ("Optional delayed action valve (DA) not included", 11),
    ("Spring power adjustable sizes 1-6", 11),
]


def _sample_page(doc: pymupdf.Document, rotate: int = 0) -> None:
    page = doc.new_page(width=612, height=792)
    y = 90
    for text, size in SAMPLE_LINES:
        page.insert_text((72, y), text, fontsize=size, fontname="helv")
        y += size * 2.6
    page.draw_rect(pymupdf.Rect(72, 420, 300, 560), color=(0.4, 0.4, 0.4), width=0.8)
    page.insert_text((80, 440), "Figure: closer body (image area)", fontsize=9)
    if rotate:
        page.set_rotation(rotate)


def build_gate_pdf(out: Path) -> list[dict]:
    doc = pymupdf.open()
    _sample_page(doc)
    _sample_page(doc, rotate=90)
    eng = MarkupEngine(doc)
    plan = [
        ("highlight", "selection", "4040XP-REG", None, "Selected model 4040XP-REG", "ae"),
        ("underline", "note", "Cast iron body", None, "Cast-iron body per 2.3.A", "ae"),
        ("strikeout", "not_submitted", "Optional finish: US26D", None, "Not submitted", "ae"),
        ("xout", "not_submitted", "EDA", None, "Not submitted", "ae"),
        ("rectangle", "selection", "Arm options", None, "Arm option box", "ae"),
        ("ellipse", "selection", "REG", None, "Selected arm", "ae"),
        ("cloud", "deviation", "689 Aluminum Painted", None, "Finish 689 shown; spec 08 71 00 2.3.C requires US26D. DEVIATION.", "ae"),
        ("arrow", "note", "UL 10C", None, "UL 10C listing", "ae"),
        ("callout", "note", "Spring power", None, "Size per manufacturer's chart for door width.", "ae"),
        ("textbox", "note", "", [0.55, 0.55, 0.9, 0.6], "Provide closer template with shop drawings.", "ae"),
        ("note", "note", "delayed action", None, "Sticky note: DA valve not included - confirm with hardware schedule.", "ae"),
        ("check", "compliant", "Grade 1", None, "Compliant: Grade 1 (internal layer)", "internal"),
        ("cloud", "human_review", "", [0.12, 0.53, 0.49, 0.71], "Image-only area: confirm body material (internal layer)", "internal"),
        ("stamp", "review_stamp", "", None, "", "ae"),
    ]
    for pno in (1, 2):
        for i, (t, subj, anchor, rect, text, layer) in enumerate(plan):
            ins = MarkupInstruction(id=f"G{pno}-{i + 1:02d}", page=pno, type=t, subject=subj, anchor_text=anchor,
                                    rect=rect, text=text, layer=layer)
            eng.apply(ins)
    doc.set_metadata({"title": "ASM Bluebeam Compatibility Gate", "author": "Ajax ASM",
                      "subject": "Markup engine test - every annotation type"})
    doc.set_toc([[1, "Gate - upright page", 1], [1, "Gate - rotated 90 page", 2]])
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    with pymupdf.open(out) as d:
        return markup_inventory(d)
