"""Markup engine: every annotation must be a real, editable PDF annotation Bluebeam can list."""
import pymupdf

from asm.pdf.gate import build_gate_pdf
from asm.pdf.markup import MarkupEngine, markup_inventory
from asm.schemas import MarkupInstruction


def test_gate_pdf_every_type(tmp_path):
    inv = build_gate_pdf(tmp_path / "gate.pdf")
    types = {i["type"] for i in inv}
    assert {"Highlight", "Underline", "StrikeOut", "Ink", "Square", "Circle", "Polygon", "Line", "FreeText", "Text"} <= types
    for a in inv:
        assert a["author"].startswith("Ajax ASM"), a
        assert a["subject"], a
        assert a["nm"], a
        assert a["has_ap"], f"missing appearance stream: {a}"
    nms = [a["nm"] for a in inv]
    assert len(nms) == len(set(nms)), "annotation /NM must be unique"
    clouds = [a for a in inv if a["type"] == "Polygon"]
    assert clouds and all(c["it"] == "/PolygonCloud" for c in clouds)
    assert any(a["oc"] for a in inv), "internal markups go on the hidden layer"
    assert not any(a["oc"] for a in inv if a["subject"] in ("Deviation", "Selection")), "A/E markups stay visible"


def test_cloud_has_border_effect(tmp_path):
    build_gate_pdf(tmp_path / "gate.pdf")
    doc = pymupdf.open(tmp_path / "gate.pdf")
    found = False
    for a in doc[0].annots():
        if a.type[1] == "Polygon":
            assert "/S /C" in doc.xref_object(a.xref).replace("\n", " ").replace("  ", " ")
            found = True
    assert found


def test_rotated_page_anchor(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Model 4040XP Grade 1", fontsize=12)
    p.set_rotation(90)
    eng = MarkupEngine(doc)
    a = eng.apply(MarkupInstruction(page=1, type="cloud", subject="deviation", anchor_text="4040XP", text="x"))
    assert a.anchored
    hit = doc[0].search_for("4040XP")[0]
    r = pymupdf.Rect(a.rect)
    assert r.contains(hit) or r.intersects(hit)


def test_unanchored_becomes_note(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Nothing to see", fontsize=12)
    eng = MarkupEngine(doc)
    a = eng.apply(MarkupInstruction(page=1, type="cloud", subject="deviation", anchor_text="NOT ON PAGE", text="Provide X"))
    assert not a.anchored and a.warning
    inv = markup_inventory(doc)
    assert any(i["type"] == "Text" for i in inv) and any(i["type"] == "FreeText" for i in inv)
