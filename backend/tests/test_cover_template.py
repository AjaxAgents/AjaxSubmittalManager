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


def test_subcontractor_and_date_tokens():
    from datetime import date, timedelta

    info = {"supplier": "Northside Door Supply", "date_format": "%m/%d/%Y", "date_prepared": date(2026, 11, 13),
            "sub": {"address": "1250 Industrial Blvd", "city": "Hickory", "state": "NC", "zip": "28601"}}
    vals = ct.values_from(info)
    assert vals["SubcontractorAddress"] == "1250 Industrial Blvd"
    assert vals["SubcontractorCity"] == "Hickory, NC 28601"
    assert vals["DatePrepared"] == "11/13/2026"
    assert vals["RequiredDate"] == (date.today() + timedelta(days=14)).strftime("%m/%d/%Y")
    # per-field "today + N days", unless the review form gave a date
    assert ct._resolve("RequiredDate+7", vals, info) == (date.today() + timedelta(days=7)).strftime("%m/%d/%Y")
    assert ct._resolve("RequiredDate+7", vals, {**info, "required_date": "2026-12-01"}) == "12/01/2026"
    assert ct.guess_token("Subcontractor Address") == "SubcontractorAddress"
    assert ct.guess_token("City/State/Zip") == "SubcontractorCity"
    assert ct.guess_token("Date Needed By") == "RequiredDate"
    assert ct.city_line("Hickory", "", "") == "Hickory"


def test_submittal_type_normalizing():
    from asm.schemas import SUBMITTAL_TYPES, normalize_type

    assert normalize_type("product data and shop drawings") == "Product Data & Shop Drawings"
    assert normalize_type("samples") == "Samples" and "Calculations" in SUBMITTAL_TYPES
