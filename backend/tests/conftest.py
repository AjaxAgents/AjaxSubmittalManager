import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture()
def data(tmp_path, monkeypatch):
    """Isolated data dir + DB per test."""
    monkeypatch.setenv("ASM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ASM_MOCK_LLM", "1")
    from asm import config, db
    config.reload_all()
    monkeypatch.setenv("ASM_MOCK_LLM", "1")
    db.set_db_path(tmp_path / "data" / "asm.sqlite3")
    return tmp_path


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    import make_demo_project
    out = tmp_path_factory.mktemp("demo")
    make_demo_project.main(str(out))
    return out


@pytest.fixture()
def project(data, demo):
    """Project with the demo library ingested."""
    from asm import db
    from asm.library.ingest import ingest_file
    from asm.pdf.util import inspect_pdf, sha256_file
    p = db.create_project(name="Riverside MOB", number="2417", ae_firm="Hale + Moreno", contractor="Ajax Building Company")
    for fn in ["Project Manual - Riverside MOB.pdf", "Drawings - Riverside MOB.pdf", "Addendum 01.pdf"]:
        path = demo / fn
        info = inspect_pdf(path)
        f = db.add_library_file(p["id"], "auto", fn, str(path), sha256_file(path), info["pages"], path.stat().st_size)
        ingest_file(p["id"], f["id"])
    return p


def upload(demo, fn):
    from asm import db
    from asm.pdf.util import inspect_pdf, sha256_file
    p = demo / fn
    i = inspect_pdf(p)
    return db.add_upload(fn, str(p), sha256_file(p), i["pages"], p.stat().st_size, i["flags"])["id"]
