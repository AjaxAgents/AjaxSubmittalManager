from asm import db
from asm.library.catalog import catalog_index_text, heuristic_scope, scope_tree
from asm.library.tools import LibraryTools, ScopeGuard


def test_spec_split(project):
    secs = {c["number"]: c for c in db.list_catalog(project["id"], ["spec_section"])}
    assert set(secs) == {"01 33 00", "08 11 13", "08 71 00", "09 91 23"}
    assert secs["08 11 13"]["title"] == "Hollow Metal Doors and Frames"
    art = db.get_catalog_item(project["id"], "S:08 71 00/2.3")
    assert art and "A156.4" in art["text"]
    assert "08 11 13" in secs["08 71 00"]["xrefs"]


def test_sheets_and_amendments(project):
    sheets = {c["number"] for c in db.list_catalog(project["id"], ["drawing_sheet"])}
    assert sheets == {"A-001", "A-501", "A-502", "E-101"}
    assert "AM:Addendum 01" in db.get_catalog_item(project["id"], "S:08 71 00")["amended_by"]
    assert "AM:Addendum 01" in db.get_catalog_item(project["id"], "D:A-501")["amended_by"]


def test_index_and_tree(project):
    idx = catalog_index_text(project["id"], {"S:08 71 00"})
    assert "[S:08 71 00]" in idx and "[IN SCOPE]" in idx and "2.3 Door Closers" in idx
    assert "Specifications" in [n["label"] for n in scope_tree(project["id"])]


def test_heuristic_scope(project):
    ids = {i["id"] for i in heuristic_scope(project["id"], "08 71 00")}
    assert {"S:08 71 00", "S:01 33 00", "AM:Addendum 01", "D:A-501"} <= ids


def test_scope_guard_strict_and_flexible(project):
    pid = project["id"]
    strict = ScopeGuard.from_scope(pid, "j1", {"mode": "strict", "items": [{"id": "S:08 71 00"}]})
    t = LibraryTools(strict, "examiner")
    assert "DOOR CLOSERS" in t.get_spec({"section": "08 71 00", "article": "2.3", "reason": "x"})
    assert t.get_spec({"section": "09 91 23", "reason": "finish"}).startswith("REFUSED")
    assert strict.gaps and strict.gaps[0]["item"] == "S:09 91 23"
    flex = ScopeGuard.from_scope(pid, "j2", {"mode": "flexible", "items": [{"id": "S:08 71 00"}], "extra_pages_cap": 1})
    t2 = LibraryTools(flex, "examiner")
    assert not t2.get_spec({"section": "09 91 23", "reason": "finish"}).startswith("REFUSED")
    assert t2.get_spec({"section": "08 11 13", "reason": "frames"}).startswith("REFUSED")  # cap of 1 extra page used


def test_page_trim(project):
    g = ScopeGuard.from_scope(project["id"], "j3", {"items": [{"id": "S:08 71 00", "pages": [5]}]})
    txt = LibraryTools(g, "planner").get_spec({"section": "08 71 00", "reason": "x"})
    assert "[p.5]" in txt and "[p.4]" not in txt
