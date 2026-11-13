"""API: accounts, isolation, library upload-then-process, bulk ops, cover templates, register, full run."""
import time

import pymupdf
from fastapi.testclient import TestClient


def make_cover(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "AJAX TRANSMITTAL", fontsize=16)
    page.insert_text((72, 100), "Project:", fontsize=10)
    for i, name in enumerate(["Project Name", "Submittal No", "Date"]):
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = pymupdf.Rect(150, 88 + i * 24, 450, 106 + i * 24)
        page.add_widget(w)
    a = page.add_freetext_annot(pymupdf.Rect(72, 200, 400, 230), "Section {SpecSection} {SpecTitle}", fontsize=10)
    a.update()
    page.insert_text((72, 260), "Sub: {Subcontractor}", fontsize=10)
    doc.save(path)


def wait_ready(c, pid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        lib = c.get(f"/api/projects/{pid}/library").json()
        if lib["summary"]["processing"] == 0:
            return lib
        time.sleep(0.3)
    raise AssertionError("library never finished")


def test_accounts_and_isolation(data, demo):
    from asm.api.app import app
    with TestClient(app) as c:
        assert c.get("/api/projects").status_code == 401
        st = c.get("/api/auth/status").json()
        assert st["has_users"] is False
        r = c.post("/api/auth/register", json={"username": "jack", "password": "short"})
        assert r.status_code == 400
        assert c.post("/api/auth/register", json={"username": "jack", "password": "correct horse", "display_name": "Jack"}).status_code == 200
        p = c.post("/api/projects", json={"name": "CLT40", "number": "50000394"}).json()
        c.post("/api/auth/logout")
        assert c.get("/api/projects").status_code == 401
        assert c.post("/api/auth/login", json={"username": "jack", "password": "nope"}).status_code == 401
        # a second user sees nothing of the first user's
        c.post("/api/auth/register", json={"username": "pat", "password": "another pass"})
        assert c.get("/api/projects").json() == []
        assert c.get(f"/api/projects/{p['id']}/library").status_code == 404
        c.post("/api/auth/logout")
        assert c.post("/api/auth/login", json={"username": "JACK", "password": "correct horse"}).status_code == 200
        assert [x["id"] for x in c.get("/api/projects").json()] == [p["id"]]
        s = c.put("/api/settings", json={"theme": "dark"}).json()
        assert s["theme"] == "dark"
        assert c.put("/api/settings", json={"api_key": "not-a-key"}).status_code == 400


def test_library_workflow_and_review(data, demo, tmp_path):
    from asm.api.app import app
    make_cover(tmp_path / "cover.pdf")
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"username": "jack", "password": "correct horse"})
        pid = c.post("/api/projects", json={"name": "Riverside", "number": "2417", "ae_firm": "Hale"}).json()["id"]

        # upload with a pre-selected type -> stored but NOT processed until asked
        files = [("files", (n, open(demo / n, "rb"), "application/pdf"))
                 for n in ["Project Manual - Riverside MOB.pdf", "Drawings - Riverside MOB.pdf"]]
        up = c.post(f"/api/projects/{pid}/library", files=files, data={"kind": "auto"}).json()
        assert all(f["status"] == "uploaded" for f in up["files"])
        am = c.post(f"/api/projects/{pid}/library", data={"kind": "amendment"},
                    files=[("files", ("Addendum 01.pdf", open(demo / "Addendum 01.pdf", "rb"), "application/pdf"))]).json()
        assert am["files"][0]["kind"] == "amendment"
        time.sleep(0.5)
        lib = c.get(f"/api/projects/{pid}/library").json()
        assert lib["summary"]["sections"] == 0 and lib["pending"]["files"] == 3

        # bulk: set type, then delete one, then process the rest
        extra = c.post(f"/api/projects/{pid}/library", data={"kind": "document"},
                       files=[("files", ("junk.pdf", open(demo / "Addendum 01.pdf", "rb"), "application/pdf"))]).json()
        assert extra["files"][0].get("duplicate")  # same bytes as Addendum 01 -> deduplicated
        ids = [f["id"] for f in up["files"]]
        assert c.post(f"/api/projects/{pid}/library/bulk", json={"file_ids": ids[:1], "action": "set_kind", "value": "spec"}).json()["updated"] == 1
        assert c.post(f"/api/projects/{pid}/library/process", json={"use_ai": False}).json()["queued"] == 3
        lib = wait_ready(c, pid)
        assert lib["summary"]["sections"] == 4 and lib["summary"]["sheets"] == 4 and lib["summary"]["amendments"] == 1

        # quality example, division 01 and a cover template live in the library too
        ex = c.post(f"/api/projects/{pid}/library", data={"kind": "example"}, files=[("files", (
            "Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf",
            open(demo / "Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        d1 = c.post(f"/api/projects/{pid}/library", data={"kind": "div01"}, files=[("files", (
            "Div01.pdf", open(demo / "Division 01 - 01 33 00 Submittal Procedures.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        cv = c.post(f"/api/projects/{pid}/library", data={"kind": "cover"},
                    files=[("files", ("cover.pdf", open(tmp_path / "cover.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        assert ex["status"] == "stored" and cv["kind"] == "cover"
        # the same PDF already in Project information can still be added as Division 01 (dedupe is per library section)
        d1b = c.post(f"/api/projects/{pid}/library", data={"kind": "div01"}, files=[("files", (
            "Addendum as div01.pdf", open(demo / "Addendum 01.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        assert not d1b.get("duplicate") and d1b["kind"] == "div01"
        again = c.post(f"/api/projects/{pid}/library", data={"kind": "div01"}, files=[("files", (
            "Addendum as div01.pdf", open(demo / "Addendum 01.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        assert again.get("duplicate")
        c.delete(f"/api/library/{d1b['id']}")
        cov = c.get(f"/api/library/{cv['id']}/cover").json()
        mapping = cov["mapping"]
        assert mapping["w:Project Name"] == "ProjectName" and mapping["w:Submittal No"] == "SubmittalNo"
        assert any(v == "@template" for v in mapping.values())
        assert c.post(f"/api/library/{cv['id']}/cover/preview", json={"params": {}}).status_code == 200

        # catalog item as its own PDF (just that section's pages)
        tree = c.get(f"/api/projects/{pid}/catalog/tree").json()
        def leaves(ns):
            for n in ns:
                if n["kind"] == "spec_section":
                    yield n
                yield from leaves(n.get("children") or [])
        sec = next(leaves(tree))
        r = c.get(f"/api/projects/{pid}/catalog/item.pdf", params={"id": sec["id"]})
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
        import pymupdf
        assert pymupdf.open(stream=r.content, filetype="pdf").page_count == sec["page_end"] - sec["page_start"] + 1

        # subcontractors + divisions
        sub = c.post(f"/api/projects/{pid}/subcontractors", json={"name": "Northside Door Supply", "divisions": ["08"]}).json()
        divs = {d["code"]: d for d in c.get(f"/api/projects/{pid}/divisions").json()["divisions"]}
        assert divs["08"]["enabled"] and divs["08"]["subcontractors"][0]["name"] == "Northside Door Supply"

        # submittal upload + auto-fill
        u = c.post("/api/uploads", files=[("files", ("Door Hardware PD.pdf", open(demo / "Submittal - Door Hardware Product Data.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        af = c.post(f"/api/projects/{pid}/autofill", json={"upload_ids": [u["id"]]}).json()
        assert af["spec_section"] == "08 71 00" and af["subcontractor"] == "Northside Door Supply" and af["next_seq"] == 1

        scope = c.post(f"/api/projects/{pid}/scope/suggest", json={"spec_section": "08 71 00", "upload_ids": [u["id"]]}).json()
        assert "suggestions" in scope and all(s.get("optional") for s in scope["suggestions"])
        assert not any(i.get("optional") for i in scope["items"])
        job = c.post(f"/api/projects/{pid}/jobs", json={
            "spec_section": "08 71 00", "submittal_no": "1", "subcontractor_id": sub["id"], "uploads": {"submittal": [u["id"]]},
            "library_refs": {"examples": [ex["id"]], "div01": [d1["id"]], "cover": cv["id"]},
            "scope": {"mode": "flexible", "items": scope["items"]}}).json()
        t0 = time.time()
        while time.time() - t0 < 60:
            j = c.get(f"/api/jobs/{job['id']}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.3)
        assert j["status"] == "done", j["error"]
        reg = c.get(f"/api/projects/{pid}/submittals").json()
        assert len(reg) == 1 and reg[0]["number"] == "08 71 00 - 01.0" and reg[0]["subcontractor"] == "Northside Door Supply"
        assert reg[0]["status"] == "exceptions" and reg[0]["division"] == "08"
        assert c.get(f"/api/projects/{pid}/submittals/next", params={"spec_section": "08 71 00"}).json()["seq"] == 2
        home = c.get(f"/api/projects/{pid}/home").json()
        assert home["submittals"] == 1 and home["by_division"][0]["code"] == "08"

        # the company cover template is page 1, filled by rule, fields still editable
        doc = pymupdf.open(j["result"]["pdf"])
        p1 = doc[0]
        vals = {w.field_name: w.field_value for w in p1.widgets()}
        assert vals["Project Name"] == "Riverside" and vals["Submittal No"] == "08 71 00 - 01.0"
        texts = [a.info["content"] for a in p1.annots(types=[pymupdf.PDF_ANNOT_FREE_TEXT])]
        assert any("08 71 00 Door Hardware" in t for t in texts)
        assert "Northside Door Supply" in p1.get_text()
        assert "{Subcontractor}" not in p1.get_text()


def test_cancel_processing(data, demo):
    from asm.api.app import app
    from asm.library import ingest
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"username": "jack", "password": "correct horse"})
        pid = c.post("/api/projects", json={"name": "P"}).json()["id"]
        f = c.post(f"/api/projects/{pid}/library", data={"kind": "spec"}, files=[("files", (
            "PM.pdf", open(demo / "Project Manual - Riverside MOB.pdf", "rb"), "application/pdf"))]).json()["files"][0]
        # queued -> back to uploaded
        from asm import db
        db.update_library_file(f["id"], status="queued")
        assert c.post(f"/api/projects/{pid}/library/cancel", json={"file_ids": [f["id"]]}).json()["cancelled"] == 1
        assert db.get_library_file(f["id"])["status"] == "uploaded"
        # processing -> the ingest stops at its next step and leaves no catalog rows
        ingest.request_cancel(f["id"])
        assert ingest.ingest_file(pid, f["id"], use_ai=False) == {"cancelled": True}
        row = db.get_library_file(f["id"])
        assert row["status"] == "uploaded" and c.get(f"/api/projects/{pid}/library").json()["summary"]["sections"] == 0
        # a normal run afterwards still works
        assert ingest.ingest_file(pid, f["id"], use_ai=False)["kind"] == "spec"


def test_scope_keywords_ignore_boilerplate():
    from asm.library.catalog import _distinct_keywords
    sheets = [{"title": f"Power plan {i}", "text": "general notes project building electrical"} for i in range(40)]
    sheets.append({"title": "Storefront details", "text": "aluminum storefront glazing sealant"})
    kws = _distinct_keywords(["Project", "Building", "Electrical", "Storefront", "Glazing", "Submittal"], sheets)
    assert kws == ["storefront", "glazing"]  # boilerplate and words on most sheets are dropped
