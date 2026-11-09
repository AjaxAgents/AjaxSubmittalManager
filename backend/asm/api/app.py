"""FastAPI app: REST + Server-Sent Events. Binds to localhost; the API key never reaches the browser."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, db, pipeline
from ..agents.llm import price_for
from ..config import (FRONTEND_DIST, api_key, data_dir, mock_mode, models_cfg, reload_all, save_api_key, save_env,
                      save_overrides, settings_cfg, stage_cfg)
from ..library import catalog as cat
from ..library.ingest import ingest_file, norm_section
from ..outputs import naming
from ..pdf.util import inspect_pdf, ocr_available, render_png, sha256_file

log = logging.getLogger("asm.api")
INGEST_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")

async def _startup() -> None:
    db.init()
    # jobs interrupted by a restart are marked as such
    with db.conn() as c:
        c.execute("UPDATE jobs SET status='error', error='Interrupted (server restarted)' WHERE status IN ('queued','running')")
        c.execute("UPDATE library_files SET status='queued' WHERE status='processing'")
    for p in db.list_projects():
        for f in db.list_library_files(p["id"]):
            if f["status"] == "queued":
                _queue_ingest(p["id"], f["id"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(title="Ajax Submittal Manager", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------------- helpers

def _uploads_dir(sub: str) -> Path:
    p = data_dir() / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _save_upload(up: UploadFile, dest_dir: Path) -> Path:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(up.filename or "file.pdf").name)
    dest = dest_dir / name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{Path(name).stem} ({i}){Path(name).suffix}"
        i += 1
    with dest.open("wb") as fh:
        while chunk := await up.read(1 << 20):
            fh.write(chunk)
    return dest


def _queue_ingest(project_id: str, file_id: str) -> None:
    INGEST_POOL.submit(_ingest_safe, project_id, file_id)


def _ingest_safe(project_id: str, file_id: str) -> None:
    try:
        ingest_file(project_id, file_id)
    except Exception as exc:  # status already recorded on the file row
        log.warning("ingest failed for %s: %s", file_id, exc)


def _need_project(pid: str) -> dict:
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _need_job(jid: str) -> dict:
    j = db.get_job(jid)
    if not j:
        raise HTTPException(404, "Job not found")
    return j


# ----------------------------------------------------------------------------- health & settings

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "api_key_set": bool(api_key()), "mock": mock_mode(),
            "ocr": ocr_available(), "pymupdf": pymupdf.__version__, "data_dir": str(data_dir())}


class SettingsIn(BaseModel):
    api_key: Optional[str] = None
    mock: Optional[bool] = None
    stage_models: Optional[dict[str, str]] = None
    usd_cap: Optional[float] = None
    scope_mode: Optional[str] = None
    page_budget: Optional[int] = None
    web_search: Optional[bool] = None


@app.get("/api/settings")
def get_settings() -> dict:
    m = models_cfg()
    s = settings_cfg()
    return {
        "api_key_set": bool(api_key()), "api_key_hint": (api_key() or "")[-4:], "mock": mock_mode(),
        "stages": {k: v.get("model") for k, v in (m.get("stages") or {}).items()},
        "usd_cap": s.get("budget", {}).get("job_usd_cap"), "scope_mode": s.get("scope", {}).get("default_mode"),
        "page_budget": s.get("scope", {}).get("page_budget"), "web_search": bool((m.get("web_search") or {}).get("enabled")),
        "output_dir": str(data_dir() / "outputs"),
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    if body.api_key is not None and body.api_key.strip():
        key = body.api_key.strip()
        if not key.startswith("sk-"):
            raise HTTPException(400, "That does not look like an Anthropic API key (should start with sk-).")
        save_api_key(key)
    if body.mock is not None:
        save_env("ASM_MOCK_LLM", "1" if body.mock else "0")
    if body.stage_models:
        save_overrides("models", {"stages": {k: {"model": v} for k, v in body.stage_models.items() if v}})
    if body.web_search is not None:
        save_overrides("models", {"web_search": {"enabled": bool(body.web_search)}})
    sets: dict[str, Any] = {}
    if body.usd_cap is not None:
        sets.setdefault("budget", {})["job_usd_cap"] = float(body.usd_cap)
    if body.scope_mode in ("flexible", "strict"):
        sets.setdefault("scope", {})["default_mode"] = body.scope_mode
    if body.page_budget:
        sets.setdefault("scope", {})["page_budget"] = int(body.page_budget)
    if sets:
        save_overrides("settings", sets)
    reload_all()
    return get_settings()


# ----------------------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: Optional[str] = None
    number: Optional[str] = None
    ae_firm: Optional[str] = None
    contractor: Optional[str] = None
    owner: Optional[str] = None
    address: Optional[str] = None
    settings: Optional[dict] = None


def _project_summary(p: dict) -> dict:
    files = db.list_library_files(p["id"])
    secs = db.list_catalog(p["id"], ["spec_section"])
    sheets = db.list_catalog(p["id"], ["drawing_sheet"])
    ams = db.list_catalog(p["id"], ["amendment"])
    return {**p, "library": {"files": len(files), "ready": sum(1 for f in files if f["status"] == "ready"),
                             "processing": sum(1 for f in files if f["status"] in ("queued", "processing")),
                             "errors": sum(1 for f in files if f["status"] == "error"),
                             "sections": len(secs), "sheets": len(sheets), "amendments": len(ams)}}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [_project_summary(p) for p in db.list_projects()]


@app.post("/api/projects")
def create_project(body: ProjectIn) -> dict:
    if not (body.name or "").strip():
        raise HTTPException(400, "Project name is required")
    return _project_summary(db.create_project(**body.model_dump(exclude_none=True)))


@app.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectIn) -> dict:
    _need_project(pid)
    return _project_summary(db.update_project(pid, **body.model_dump(exclude_none=True)))  # type: ignore[arg-type]


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    _need_project(pid)
    db.delete_project(pid)
    return {"ok": True}


# ----------------------------------------------------------------------------- library (zone 2)

@app.get("/api/projects/{pid}/library")
def library(pid: str) -> dict:
    p = _need_project(pid)
    return {"files": db.list_library_files(pid), "summary": _project_summary(p)["library"]}


@app.post("/api/projects/{pid}/library")
async def library_upload(pid: str, files: list[UploadFile] = File(...), kind: str = Form("auto")) -> dict:
    _need_project(pid)
    dest = _uploads_dir(f"library/{pid}")
    out, errors = [], []
    for up in files:
        if not (up.filename or "").lower().endswith(".pdf"):
            errors.append({"filename": up.filename, "error": "Only PDF files are supported in v1."})
            continue
        path = await _save_upload(up, dest)
        info = inspect_pdf(path)
        if not info["ok"]:
            path.unlink(missing_ok=True)
            errors.append({"filename": up.filename, "error": info["error"]})
            continue
        sha = sha256_file(path)
        dup = db.find_library_file_by_hash(pid, sha)
        if dup:
            path.unlink(missing_ok=True)
            out.append({**dup, "duplicate": True})
            continue
        f = db.add_library_file(pid, kind if kind in ("auto", "spec", "drawing", "amendment", "document") else "auto",
                                path.name, str(path), sha, info["pages"], path.stat().st_size)
        _queue_ingest(pid, f["id"])
        out.append(f)
    return {"files": out, "errors": errors}


class FileKindIn(BaseModel):
    kind: str


@app.patch("/api/library/{fid}")
def library_set_kind(fid: str, body: FileKindIn) -> dict:
    f = db.get_library_file(fid)
    if not f:
        raise HTTPException(404, "File not found")
    if body.kind not in ("spec", "drawing", "amendment", "document", "auto"):
        raise HTTPException(400, "Unknown kind")
    db.update_library_file(fid, kind=body.kind, status="queued", progress=0, error="")
    _queue_ingest(f["project_id"], fid)
    return db.get_library_file(fid)  # type: ignore[return-value]


@app.delete("/api/library/{fid}")
def library_delete(fid: str) -> dict:
    f = db.get_library_file(fid)
    if not f:
        raise HTTPException(404, "File not found")
    db.delete_library_file(fid)
    cat_mod_relink(f["project_id"])
    return {"ok": True}


def cat_mod_relink(pid: str) -> None:
    from ..library.ingest import relink_amendments
    relink_amendments(pid)


@app.get("/api/library/{fid}/pages/{n}.png")
def library_page(fid: str, n: int, dpi: int = 50) -> Response:
    f = db.get_library_file(fid)
    if not f:
        raise HTTPException(404)
    with pymupdf.open(f["path"]) as d:
        if not 1 <= n <= d.page_count:
            raise HTTPException(404)
        png = render_png(d, n - 1, dpi=min(dpi, 150), max_px=1600)
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/projects/{pid}/catalog/tree")
def catalog_tree(pid: str) -> list[dict]:
    _need_project(pid)
    return cat.scope_tree(pid)


@app.get("/api/projects/{pid}/catalog/sections")
def catalog_sections(pid: str) -> list[dict]:
    return [{"number": c["number"], "title": c["title"], "pages": cat.item_pages(c)}
            for c in db.list_catalog(pid, ["spec_section"])]


@app.get("/api/projects/{pid}/catalog/search")
def catalog_search(pid: str, q: str) -> list[dict]:
    return db.search_catalog(pid, q, None, limit=25)


@app.get("/api/projects/{pid}/catalog/item")
def catalog_item(pid: str, id: str) -> dict:
    it = db.get_catalog_item(pid, id)
    if not it:
        raise HTTPException(404)
    it["text"] = (it.get("text") or "")[:20000]
    return it


# ----------------------------------------------------------------------------- uploads (zones 1, 3, 4)

@app.post("/api/uploads")
async def uploads(files: list[UploadFile] = File(...)) -> dict:
    dest = _uploads_dir("uploads")
    out, errors = [], []
    for up in files:
        if not (up.filename or "").lower().endswith(".pdf"):
            errors.append({"filename": up.filename, "error": "Only PDF files are supported in v1."})
            continue
        path = await _save_upload(up, dest)
        info = inspect_pdf(path)
        if not info["ok"]:
            path.unlink(missing_ok=True)
            errors.append({"filename": up.filename, "error": info["error"]})
            continue
        u = db.add_upload(Path(up.filename or path.name).name, str(path), sha256_file(path), info["pages"],
                          path.stat().st_size, info["flags"])
        out.append(u)
    return {"files": out, "errors": errors}


# ----------------------------------------------------------------------------- scope + estimate + naming

class ScopeIn(BaseModel):
    spec_section: str
    upload_ids: list[str] = []
    instructions: str = ""
    reset: bool = False


@app.post("/api/projects/{pid}/scope/suggest")
async def scope_suggest(pid: str, body: ScopeIn) -> dict:
    _need_project(pid)
    sec_num = norm_section(body.spec_section)
    sec = db.get_catalog_item(pid, f"S:{sec_num}")
    preset = None if body.reset else db.get_scope_preset(pid, sec_num)
    if preset:
        return {"items": preset.get("items", []), "mode": preset.get("mode"), "source": "preset",
                "section": sec_num, "section_title": (sec or {}).get("title", ""), "found": bool(sec)}
    text = ""
    for uid in body.upload_ids[:3]:
        u = db.get_upload(uid)
        if u:
            with pymupdf.open(u["path"]) as d:
                text += "\n".join(d[i].get_text("text") for i in range(min(3, d.page_count)))
    kw = [w for w in re.findall(r"[A-Za-z]{5,}", text)][:40]
    items = cat.heuristic_scope(pid, sec_num, kw)
    source = "heuristic"
    if api_key() and not mock_mode() and db.list_catalog(pid, ["spec_section"]):
        try:
            from ..agents.stages import suggest_scope_ai
            res = await suggest_scope_ai(pid, sec_num, text, body.instructions)
            have = {i["id"] for i in items}
            for s in res.items:
                if db.get_catalog_item(pid, s.item_id) and s.item_id not in have:
                    items.append({"id": s.item_id, "reason": s.reason, "locked": False})
                    have.add(s.item_id)
                elif s.item_id in have:
                    for i in items:
                        if i["id"] == s.item_id and not i.get("locked"):
                            i["reason"] = s.reason
            source = "ai"
        except Exception as exc:
            log.warning("AI scope suggestion failed: %s", exc)
    return {"items": items, "mode": settings_cfg().get("scope", {}).get("default_mode", "flexible"),
            "source": source, "section": sec_num, "section_title": (sec or {}).get("title", ""), "found": bool(sec)}


class EstimateIn(BaseModel):
    upload_ids: list[str] = []
    scope_items: list[dict] = []


@app.post("/api/projects/{pid}/estimate")
def estimate(pid: str, body: EstimateIn) -> dict:
    """Rough pre-run estimate (tokens and cost range). Real usage is metered live during the run."""
    sub_pages = sum((db.get_upload(u) or {}).get("pages", 0) for u in body.upload_ids)
    lib_pages, lib_tokens = 0, 0
    for it in body.scope_items:
        c = db.get_catalog_item(pid, it["id"])
        if not c:
            continue
        pages = len(it["pages"]) if it.get("pages") else cat.item_pages(c)
        lib_pages += pages
        lib_tokens += int(c.get("token_est") or pages * 700) if c["kind"] != "drawing_sheet" else 1600 * pages
    S = sub_pages * 2200
    L = lib_tokens
    I, R = 4000, 6000

    def cost(stage: str, inp: int, out: int, cached_read: int = 0, cache_write: int = 0) -> float:
        p = price_for(stage_cfg(stage)["model"])
        return (inp * p["input"] + out * p["output"] + cached_read * p["cache_read"] + cache_write * p["cache_write"]) / 1e6

    est = (cost("intake", S + 2000, 4000)
           + cost("planner", L + I + 6000, 9000)
           + cost("advocate", int(L * 0.5), 14000, cached_read=4 * (S + I + R), cache_write=S + I + R)
           + cost("examiner", int(L * 0.5), 14000, cached_read=5 * (S + I + R))
           + cost("adjudicator", 4000, 6000, cached_read=S, cache_write=0)
           + cost("qa", 12000, 1500))
    tokens = S * 10 + L * 2 + 60000
    return {"submittal_pages": sub_pages, "library_pages": lib_pages, "est_input_tokens": tokens,
            "est_cost_low": round(est * 0.7, 2), "est_cost_high": round(est * 1.6, 2),
            "usd_cap": settings_cfg().get("budget", {}).get("job_usd_cap"), "mock": mock_mode()}


class NamingIn(BaseModel):
    params: dict
    example_upload_ids: list[str] = []


@app.post("/api/projects/{pid}/naming/preview")
def naming_preview(pid: str, body: NamingIn) -> dict:
    p = _need_project(pid)
    params = dict(body.params)
    params["spec_section"] = norm_section(params.get("spec_section", "")) if params.get("spec_section") else ""
    if not params.get("spec_title") and params["spec_section"]:
        sec = db.get_catalog_item(pid, f"S:{params['spec_section']}")
        params["spec_title"] = (sec or {}).get("title", "")
    names = [(db.get_upload(u) or {}).get("filename", "") for u in body.example_upload_ids]
    pattern, src = naming.choose_pattern(params, "", [n for n in names if n])
    base = naming.render(pattern, naming.tokens(params, p))
    return {"name": f"{base}.pdf", "pattern": pattern, "source": src,
            "note": "Division 01 naming rules found during the run take precedence over the company default."}


# ----------------------------------------------------------------------------- jobs

class JobIn(BaseModel):
    spec_section: str
    spec_title: str = ""
    submittal_no: str = ""
    revision: str = "0"
    submittal_type: str = "Product Data"
    title: str = ""
    required_date: str = ""
    supplier: str = ""
    instructions: str = ""
    naming_pattern: str = ""
    mimic_example_naming: bool = False
    usd_cap: Optional[float] = None
    uploads: dict[str, list[str]]
    scope: dict


@app.post("/api/projects/{pid}/jobs")
async def create_job(pid: str, body: JobIn) -> dict:
    _need_project(pid)
    if not body.uploads.get("submittal"):
        raise HTTPException(400, "Add at least one submittal PDF (zone 1).")
    if len(body.instructions) > 2000:
        raise HTTPException(400, "Reviewer instructions are limited to 2,000 characters.")
    if not api_key() and not mock_mode():
        raise HTTPException(400, "No Anthropic API key is set. Add one in Settings (or enable offline demo mode).")
    params = body.model_dump()
    params["spec_section"] = norm_section(params["spec_section"])
    job = db.create_job(pid, params)
    pipeline.start(job["id"])
    return job


@app.get("/api/projects/{pid}/jobs")
def list_jobs(pid: str) -> list[dict]:
    return db.list_jobs(pid)


@app.get("/api/jobs/{jid}")
def get_job(jid: str) -> dict:
    j = _need_job(jid)
    j["calls"] = db.api_calls_for_job(jid)
    return j


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, request: Request) -> StreamingResponse:
    _need_job(jid)
    ev = pipeline.events_for(jid)
    ev.loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    ev.queues.append(q)
    history = list(ev.history)

    async def gen():
        try:
            for e in history:
                yield f"data: {json.dumps(e, default=str)}\n\n"
            if ev.done and history:
                yield "event: end\ndata: {}\n\n"
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(e, default=str)}\n\n"
                    if e.get("kind") == "job" and e.get("status") in ("done", "error", "cancelled"):
                        yield "event: end\ndata: {}\n\n"
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            if q in ev.queues:
                ev.queues.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str) -> dict:
    _need_job(jid)
    return {"ok": pipeline.cancel(jid)}


class RerunIn(BaseModel):
    notes: str = ""


@app.post("/api/jobs/{jid}/rerun")
async def rerun_job(jid: str, body: RerunIn) -> dict:
    j = _need_job(jid)
    if j["status"] != "done":
        raise HTTPException(400, "Only finished runs can be re-run.")
    params = dict(j["params"])
    params["_rerun_of"] = jid
    params["rerun_notes"] = body.notes
    if body.notes:
        params["instructions"] = ((params.get("instructions") or "") + "\n" + body.notes).strip()[:4000]
    nj = db.create_job(j["project_id"], params, parent_job=jid)
    pipeline.start(nj["id"], rerun_notes=body.notes)
    return nj


def _result(j: dict) -> dict:
    r = j.get("result") or {}
    if not r.get("pdf"):
        raise HTTPException(404, "This run has no outputs yet.")
    return r


@app.get("/api/jobs/{jid}/download/{kind}")
def download(jid: str, kind: str) -> FileResponse:
    r = _result(_need_job(jid))
    if kind == "pdf":
        return FileResponse(r["pdf"], media_type="application/pdf", filename=r["pdf_name"])
    if kind == "txt":
        return FileResponse(r["txt"], media_type="text/plain", filename=r["txt_name"])
    raise HTTPException(404)


@app.get("/api/jobs/{jid}/view.pdf")
def view_pdf(jid: str) -> FileResponse:
    r = _result(_need_job(jid))
    return FileResponse(r["pdf"], media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.get("/api/jobs/{jid}/txt", response_class=PlainTextResponse)
def txt(jid: str) -> str:
    r = _result(_need_job(jid))
    return Path(r["txt"]).read_text(encoding="ascii", errors="ignore")


@app.get("/api/jobs/{jid}/pages/{n}.png")
def job_page(jid: str, n: int, dpi: int = 110, internal: int = 0) -> Response:
    r = _result(_need_job(jid))
    doc = pymupdf.open(r["pdf"])
    try:
        if not 1 <= n <= doc.page_count:
            raise HTTPException(404)
        if internal:
            try:
                ocgs = doc.get_ocgs()
                doc.set_layer(-1, on=list(ocgs.keys()))
            except Exception:
                pass
        png = render_png(doc, n - 1, dpi=max(30, min(dpi, 200)), max_px=2400, annots=True)
    finally:
        doc.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=600"})


@app.post("/api/jobs/{jid}/open-folder")
def open_folder(jid: str) -> dict:
    r = _result(_need_job(jid))
    folder = r.get("output_folder") or str(Path(r["pdf"]).parent)
    try:
        if platform.system() == "Windows":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return {"ok": True, "folder": folder}
    except Exception as exc:
        return {"ok": False, "folder": folder, "error": str(exc)}


# ----------------------------------------------------------------------------- static frontend

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = FRONTEND_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_ui() -> JSONResponse:
        return JSONResponse({"message": "Frontend not built. Run `make frontend` (or `cd frontend && npm install && npm run build`)."})
