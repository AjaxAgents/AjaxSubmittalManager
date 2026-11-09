import pymupdf

from asm.pdf import cover_template as ct


def test_guess_token_rules():
    assert ct.guess_token("Project Name") == "ProjectName"
    assert ct.guess_token("submittal_no") == "SubmittalNo"
    assert ct.guess_token("SpecSectionTitle") == "SpecTitle"
    assert ct.guess_token("Subcontractor") == "Subcontractor"
    assert ct.guess_token("Date Required") == "RequiredDate"
    assert ct.guess_token("zzz") == ""


def test_page_text_tokens_are_replaced_in_place():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Sub: {Subcontractor}", fontsize=11, fontname="helv")
    page.insert_text((72, 130), "Keep this line", fontsize=11, fontname="helv")
    vals = ct.values_from({"supplier": "Northside Door Supply", "project_name": "CLT40"})
    notes = ct.fill(doc, 1, {}, vals)
    text = doc[0].get_text()
    assert not notes
    assert "Northside Door Supply" in text and "{Subcontractor}" not in text
    assert "Sub:" in text and "Keep this line" in text


def test_render_text_leaves_unknown_tokens():
    assert ct.render_text("{SpecSection} {Nope}", {"SpecSection": "08 71 00"}) == "08 71 00 {Nope}"
