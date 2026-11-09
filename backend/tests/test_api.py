from fastapi.testclient import TestClient


def test_api_smoke(data, demo):
    from asm.api.app import app
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        p = c.post("/api/projects", json={"name": "Test", "number": "1"}).json()
        with open(demo / "Submittal - Door Hardware Product Data.pdf", "rb") as fh:
            r = c.post("/api/uploads", files=[("files", ("sub.pdf", fh, "application/pdf"))]).json()
        assert r["files"][0]["pages"] == 6
        bad = c.post("/api/uploads", files=[("files", ("x.pdf", b"not a pdf", "application/pdf"))]).json()
        assert bad["errors"]
        prev = c.post(f"/api/projects/{p['id']}/naming/preview", json={"params": {"spec_section": "087100", "submittal_no": "2"}}).json()
        assert prev["name"] == "08 71 00 - 02.0 - PD.pdf" or prev["name"].startswith("08 71 00 - 02.0")
        assert c.post(f"/api/projects/{p['id']}/jobs", json={"spec_section": "08 71 00", "uploads": {}, "scope": {}}).status_code == 400
