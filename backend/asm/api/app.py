"""FastAPI app: REST + Server-Sent Events. Binds to localhost; API keys never reach the browser.

Every /api route except /api/auth/* and /api/health requires a signed-in user; projects, library files,
uploads, submittals and runs are scoped to their owner.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, auth, db, pipeline
from ..config import (FRONTEND_DIST, data_dir, mock_mode, models_cfg, outputs_dir, reload_all, save_overrides,
                      settings_cfg)
from ..library import catalog as cat
from ..library.ingest import DIVISIONS, ingest_file, norm_section, relink_amendments, request_cancel, CANCELLED
from ..outputs import naming
from ..pdf import cover_template as ctpl
from ..pdf.util import inspect_pdf, ocr_available, render_png, sha256_file

log = logging.getLogger("asm.api")
INGEST_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")

INFO_KINDS = {"auto", "spec", "drawing", "amendment", "document"}
LIB_KINDS = INFO_KINDS | {"example", "div01", "cover"}
CATEGORY = {"spec": "info", "drawing": "info", "amendment": "info", "document": "info", "auto": "info",
            "example": "example", "div01": "div01", "cover": "cover"}


async def _startup() -> None:
    db.init()
    with db.conn() as c:
        c.execute("UPDATE jobs SET status='error', error='Interrupted (app was closed)' WHERE status IN ('queued','running')")
        c.execute("UPDATE library_files SET status='queued' WHERE status='processing'")
        rows = c.execute("SELECT id, project_id FROM library_files WHERE status='queued' AND superseded=0").fetchall()
    for r in rows:  # resume work the user had already asked for
        _queue_ingest(r["project_id"], r["id"], True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(title="Ajax Submittal Manager", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

OPEN_PATHS = ("/api/auth/", "/api/health")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(OPEN_PATHS):
        token = request.cookies.get(auth.SESSION_COOKIE, "")
        user = db.session_user(token) if token else None
        if not user:
            return JSONResponse({"detail": "Please sign in."}, status_code=401)
        auth.current_user.set(user)
        request.state.user = user
    return await call_next(request)


# ----------------------------------------------------------------------------- helpers

def me() -> dict:
    u = auth.current_user.get()
    if not u:
        raise HTTPException(401, "Please sign in.")
    return u


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


def _queue_ingest(project_id: str, file_id: str, use_ai: bool) -> None:
    INGEST_POOL.submit(_ingest_safe, project_id, file_id, use_ai)


def _ingest_safe(project_id: str, file_id: str, use_ai: bool) -> None:
    try:
        f = db.get_library_file(file_id)
        if f and f["status"] == "queued":
            ingest_file(project_id, file_id, use_ai=use_ai)
    except Exception as exc:  # status already recorded on the file row
        log.warning("ingest failed for %s: %s", file_id, exc)


def _need_project(pid: str) -> dict:
    p = db.get_project(pid)
    if not p or (p.get("owner_id") or "") != me()["id"]:
        raise HTTPException(404, "Project not found")
    return p


def _need_file(fid: str) -> dict:
    f = db.get_library_file(fid)
    if not f:
        raise HTTPException(404, "File not found")
    _need_project(f["project_id"])
    return f


def _need_job(jid: str) -> dict:
    j = db.get_job(jid)
    if not j:
        raise HTTPException(404, "Run not found")
    _need_project(j["project_id"])
    return j


def _need_upload(uid: str) -> dict:
    u = db.get_upload(uid)
    if not u or (u.get("user_id") or "") not in ("", me()["id"]):
        raise HTTPException(404, "Upload not found")
    return u


def _file_out(f: dict) -> dict:
    f = dict(f)
    f["category"] = CATEGORY.get(f["kind"], "info")
    f.pop("path", None)
    return f


# ----------------------------------------------------------------------------- auth

class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = True


def _set_session(resp: Response, user: dict, remember: bool) -> None:
    token = auth.new_token()
    ttl = auth.SESSION_TTL_S if remember else 12 * 3600
    db.create_session(token, user["id"], ttl)
    resp.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=int(ttl) if remember else None,
                    path="/")


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    u = db.session_user(token) if token else None
    return {"has_users": db.count_users() > 0, "user": auth.public_user(u) if u else None, "version": __version__}


@app.post("/api/auth/register")
def register(body: RegisterIn) -> Response:
    name = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,64}", name):
        raise HTTPException(400, "Username: 3-64 letters, numbers, dots, dashes, @ or underscores.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if db.get_user_by_name(name):
        raise HTTPException(400, "That username is taken.")
    first = db.count_users() == 0
    u = db.create_user(name, body.display_name.strip() or name, auth.hash_password(body.password), is_admin=first)
    claimed = db.claim_ownerless_projects(u["id"]) if first else 0
    resp = JSONResponse({"user": auth.public_user(u), "claimed_projects": claimed})
    _set_session(resp, u, True)
    return resp


@app.post("/api/auth/login")
def login(body: LoginIn) -> Response:
    u = db.get_user_by_name(body.username.strip())
    if not u or not auth.verify_password(body.password, u["pw_hash"]):
        raise HTTPException(401, "Wrong username or password.")
    db.update_user(u["id"], last_login=db.now())
    resp = JSONResponse({"user": auth.public_user(u)})
    _set_session(resp, u, body.remember)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request) -> Response:
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    if token:
        db.delete_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


class MeIn(BaseModel):
    display_name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None
    prefs: Optional[dict] = None


@app.put("/api/me")
def update_me(body: MeIn) -> dict:
    u = me()
    fields: dict[str, Any] = {}
    if body.display_name is not None:
        fields["display_name"] = body.display_name.strip() or u["username"]
    if body.new_password:
        if not auth.verify_password(body.current_password or "", u["pw_hash"]):
            raise HTTPException(400, "Current password is wrong.")
        if len(body.new_password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters.")
        fields["pw_hash"] = auth.hash_password(body.new_password)
    if body.prefs is not None:
        fields["prefs"] = {**(u.get("prefs") or {}), **body.prefs}
    if fields:
        db.update_user(u["id"], **fields)
    return auth.public_user(db.get_user(u["id"]))  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- health & settings

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "mock": mock_mode(), "ocr": ocr_available(),
            "pymupdf": pymupdf.__version__, "has_users": db.count_users() > 0}


class SettingsIn(BaseModel):
    api_key: Optional[str] = None
    clear_api_key: Optional[bool] = None
    mock: Optional[bool] = None
    stage_models: Optional[dict[str, str]] = None
    usd_cap: Optional[float] = None
    scope_mode: Optional[str] = None
    page_budget: Optional[int] = None
    web_search: Optional[bool] = None
    review_mode: Optional[str] = None
    theme: Optional[str] = None


@app.get("/api/settings")
def get_settings() -> dict:
    u = me()
    m, s = models_cfg(), settings_cfg()
    key = auth.user_api_key(u) or ""
    return {
        "api_key_set": bool(key), "api_key_hint": key[-4:], "own_key": bool(u.get("api_key")), "mock": mock_mode(),
        "stages": {k: v.get("model") for k, v in (m.get("stages") or {}).items()},
        "usd_cap": s.get("budget", {}).get("job_usd_cap"), "scope_mode": s.get("scope", {}).get("default_mode"),
        "page_budget": s.get("scope", {}).get("page_budget"), "web_search": bool((m.get("web_search") or {}).get("enabled")),
        "review_mode": (s.get("review") or {}).get("default_mode", "cross"), "output_dir": str(outputs_dir()),
        "theme": (u.get("prefs") or {}).get("theme", "light"), "user": auth.public_user(u),
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    u = me()
    if body.api_key is not None and body.api_key.strip():
        key = body.api_key.strip()
        if not key.startswith("sk-ant-"):
            raise HTTPException(400, "That does not look like an Anthropic API key (it should start with sk-ant-).")
        db.update_user(u["id"], api_key=auth.protect(key))
    if body.clear_api_key:
        db.update_user(u["id"], api_key="")
    if body.theme in ("light", "dark"):
        db.update_user(u["id"], prefs={**(u.get("prefs") or {}), "theme": body.theme})
    if body.mock is not None:
        from ..config import save_env
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
    if body.review_mode in ("cross", "single"):
        sets.setdefault("review", {})["default_mode"] = body.review_mode
    if sets:
        save_overrides("settings", sets)
    reload_all()
    auth.current_user.set(db.get_user(u["id"]))
    return get_settings()


# ----------------------------------------------------------------------------- projects & home

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
    info = [f for f in files if CATEGORY.get(f["kind"]) == "info"]
    secs = db.list_catalog(p["id"], ["spec_section"])
    sheets = db.list_catalog(p["id"], ["drawing_sheet"])
    ams = db.list_catalog(p["id"], ["amendment"])
    return {**p, "library": {
        "files": len(info), "ready": sum(1 for f in info if f["status"] == "ready"),
        "processing": sum(1 for f in info if f["status"] in ("queued", "processing")),
        "unprocessed": sum(1 for f in info if f["status"] == "uploaded"),
        "errors": sum(1 for f in info if f["status"] == "error"), "sections": len(secs), "sheets": len(sheets),
        "amendments": len(ams), "examples": sum(1 for f in files if f["kind"] == "example"),
        "div01": sum(1 for f in files if f["kind"] == "div01"), "covers": sum(1 for f in files if f["kind"] == "cover")}}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    out = []
    for p in db.list_projects(me()["id"]):
        s = _project_summary(p)
        s["submittals"] = len(db.list_submittals(p["id"]))
        out.append(s)
    return out


@app.post("/api/projects")
def create_project(body: ProjectIn) -> dict:
    if not (body.name or "").strip():
        raise HTTPException(400, "Project name is required")
    return _project_summary(db.create_project(**body.model_dump(exclude_none=True), owner_id=me()["id"]))


@app.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectIn) -> dict:
    p = _need_project(pid)
    data = body.model_dump(exclude_none=True)
    if "settings" in data:
        data["settings"] = {**(p.get("settings") or {}), **data["settings"]}
    return _project_summary(db.update_project(pid, **data))  # type: ignore[arg-type]


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    _need_project(pid)
    db.delete_project(pid)
    return {"ok": True}


def _division_of(section: str) -> str:
    return (section or "")[:2]


@app.get("/api/projects/{pid}/home")
def project_home(pid: str) -> dict:
    p = _need_project(pid)
    subs = db.list_submittals(pid)
    jobs = db.list_jobs(pid)
    by_status: dict[str, int] = {}
    for s in subs:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    by_div: dict[str, int] = {}
    for s in subs:
        d = _division_of(s["spec_section"])
        by_div[d] = by_div.get(d, 0) + 1
    names = _division_names(p)
    return {
        "project": _project_summary(p), "submittals": len(subs), "by_status": by_status,
        "by_division": [{"code": d, "name": names.get(d, DIVISIONS.get(d, "")), "count": n} for d, n in sorted(by_div.items())],
        "recent": [_job_brief(j) for j in jobs[:8]], "subcontractors": len(db.list_subs(pid)),
        "spend": round(sum(float(j.get("cost") or 0) for j in jobs), 2),
    }


def _job_brief(j: dict) -> dict:
    p = j.get("params") or {}
    full = db.get_job(j["id"]) or {}
    r = full.get("result") or {}
    return {"id": j["id"], "status": j["status"], "created_at": j["created_at"], "cost": j.get("cost") or 0,
            "label": naming.submittal_number(p) if p.get("spec_section") else "", "title": p.get("title") or p.get("spec_title") or "",
            "subcontractor": p.get("supplier") or "", "counts": r.get("counts") or {}, "extra_hr": r.get("extra_human_review", 0),
            "mode": p.get("review_mode", "")}


# ----------------------------------------------------------------------------- divisions & subcontractors

def _division_names(p: dict) -> dict[str, str]:
    custom = {d["code"]: d.get("name", "") for d in (p.get("settings") or {}).get("divisions") or []}
    return {**DIVISIONS, **{k: v for k, v in custom.items() if v}}


@app.get("/api/projects/{pid}/divisions")
def divisions(pid: str) -> dict:
    p = _need_project(pid)
    enabled = {d["code"] for d in (p.get("settings") or {}).get("divisions") or []}
    secs = db.list_catalog(pid, ["spec_section"])
    subs = db.list_subs(pid)
    names = _division_names(p)
    codes = sorted(set(DIVISIONS) | enabled | {s["number"][:2] for s in secs})
    out = []
    for code in codes:
        ds = [{"number": s["number"], "title": s["title"]} for s in secs if s["number"].startswith(code)]
        dsubs = [{"id": s["id"], "name": s["name"]} for s in subs if code in (s.get("divisions") or [])]
        out.append({"code": code, "name": names.get(code, ""), "enabled": code in enabled or bool(ds) or bool(dsubs),
                    "custom": code in enabled, "sections": ds, "subcontractors": dsubs})
    return {"divisions": out}


class DivisionsIn(BaseModel):
    divisions: list[dict]  # [{code, name}]


@app.put("/api/projects/{pid}/divisions")
def save_divisions(pid: str, body: DivisionsIn) -> dict:
    p = _need_project(pid)
    clean = [{"code": str(d["code"]).zfill(2)[:2], "name": (d.get("name") or "").strip()} for d in body.divisions if d.get("code")]
    db.update_project(pid, settings={**(p.get("settings") or {}), "divisions": clean})
    return divisions(pid)


class SubIn(BaseModel):
    name: str
    contact: str = ""
    email: str = ""
    phone: str = ""
    divisions: list[str] = []
    spec_sections: list[str] = []
    notes: str = ""


@app.get("/api/projects/{pid}/subcontractors")
def subs_list(pid: str) -> list[dict]:
    _need_project(pid)
    return db.list_subs(pid)


@app.post("/api/projects/{pid}/subcontractors")
def subs_create(pid: str, body: SubIn) -> dict:
    _need_project(pid)
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    return db.upsert_sub(pid, body.model_dump())


@app.put("/api/projects/{pid}/subcontractors/{sid}")
def subs_update(pid: str, sid: str, body: SubIn) -> dict:
    _need_project(pid)
    return db.upsert_sub(pid, body.model_dump(), sid)


@app.delete("/api/projects/{pid}/subcontractors/{sid}")
def subs_delete(pid: str, sid: str) -> dict:
    _need_project(pid)
    db.delete_sub(sid)
    return {"ok": True}


# ----------------------------------------------------------------------------- library

@app.get("/api/projects/{pid}/library")
def library(pid: str) -> dict:
    p = _need_project(pid)
    files = [_file_out(f) for f in db.list_library_files(pid)]
    pend = [f for f in files if f["category"] == "info" and f["status"] in ("uploaded", "error")]
    pages = sum(f["pages"] for f in pend)
    return {"files": files, "summary": _project_summary(p)["library"],
            "pending": {"files": len(pend), "pages": pages, "est_ai_cost": round(pages * 0.00025 + len(pend) * 0.001, 2)},
            "defaults": (p.get("settings") or {}).get("defaults") or {}}


@app.post("/api/projects/{pid}/library")
async def library_upload(pid: str, files: list[UploadFile] = File(...), kind: str = Form("auto"),
                         division: str = Form("")) -> dict:
    _need_project(pid)
    if kind not in LIB_KINDS:
        raise HTTPException(400, "Unknown file type")
    dest = _uploads_dir(f"library/{pid}")
    out, errors = [], []
    for up in files:
        if not (up.filename or "").lower().endswith(".pdf"):
            errors.append({"filename": up.filename, "error": "Only PDF files are supported."})
            continue
        path = await _save_upload(up, dest)
        info = inspect_pdf(path)
        if not info["ok"]:
            path.unlink(missing_ok=True)
            errors.append({"filename": up.filename, "error": info["error"]})
            continue
        sha = sha256_file(path)
        # duplicates are per section of the library: the same 01 33 00 can be both a spec and the Division 01 procedures
        dup = db.find_library_file_by_hash(pid, sha, [k for k, cat in CATEGORY.items() if cat == CATEGORY[kind]])
        if dup:
            path.unlink(missing_ok=True)
            out.append({**_file_out(dup), "duplicate": True})
            continue
        meta: dict[str, Any] = {"flags": info["flags"]}
        status = "uploaded" if kind in INFO_KINDS else "stored"
        if kind == "cover":
            try:
                meta.update(ctpl.analyze(path))
                meta["mapping"] = {f["key"]: f["mapping"] for f in meta["fields"]}
            except Exception as exc:
                meta["error"] = str(exc)
        f = db.add_library_file(pid, kind, path.name, str(path), sha, info["pages"], path.stat().st_size,
                                status=status, meta=meta)
        if division:
            db.update_library_file(f["id"], division=division[:2])
        out.append(_file_out(db.get_library_file(f["id"])))  # type: ignore[arg-type]
    return {"files": out, "errors": errors}


class ProcessIn(BaseModel):
    file_ids: list[str] = []      # empty = every unprocessed project-information file
    use_ai: bool = True


@app.post("/api/projects/{pid}/library/process")
def library_process(pid: str, body: ProcessIn) -> dict:
    _need_project(pid)
    files = db.list_library_files(pid)
    if body.file_ids:
        want = set(body.file_ids)
        files = [f for f in files if f["id"] in want]
    else:
        files = [f for f in files if f["status"] in ("uploaded", "error")]
    n = 0
    for f in files:
        if CATEGORY.get(f["kind"]) != "info" or f["status"] in ("queued", "processing"):
            continue
        CANCELLED.discard(f["id"])
        db.update_library_file(f["id"], status="queued", progress=0, error="", notes="queued")
        _queue_ingest(pid, f["id"], body.use_ai)
        n += 1
    return {"queued": n}


class CancelIn(BaseModel):
    file_ids: list[str] = []      # empty = everything queued or processing


def _cancel_file(f: dict) -> bool:
    if f["status"] == "queued":
        request_cancel(f["id"])  # in case the worker is picking it up right now
        db.update_library_file(f["id"], status="uploaded", progress=0, notes="")
        return True
    if f["status"] == "processing":
        request_cancel(f["id"])
        db.update_library_file(f["id"], notes="Stopping")
        return True
    return False


@app.post("/api/projects/{pid}/library/cancel")
def library_cancel(pid: str, body: CancelIn) -> dict:
    _need_project(pid)
    want = set(body.file_ids)
    n = sum(_cancel_file(f) for f in db.list_library_files(pid) if not want or f["id"] in want)
    return {"cancelled": n}


class BulkIn(BaseModel):
    file_ids: list[str]
    action: str                   # delete | set_kind | set_division | reset
    value: str = ""


@app.post("/api/projects/{pid}/library/bulk")
def library_bulk(pid: str, body: BulkIn) -> dict:
    _need_project(pid)
    n = 0
    for fid in body.file_ids:
        f = db.get_library_file(fid)
        if not f or f["project_id"] != pid:
            continue
        if body.action == "delete":
            _cancel_file(f)
            db.delete_library_file(fid)
        elif body.action == "set_kind":
            if body.value not in LIB_KINDS:
                raise HTTPException(400, "Unknown type")
            _change_kind(f, body.value)
        elif body.action == "set_division":
            db.update_library_file(fid, division=body.value[:2])
        elif body.action == "reset":
            if CATEGORY.get(f["kind"]) == "info":
                db.delete_catalog_for_file(pid, fid)
                db.update_library_file(fid, status="uploaded", progress=0, error="", notes="")
        n += 1
    if body.action in ("delete", "set_kind", "reset"):
        relink_amendments(pid)
    return {"updated": n}


def _change_kind(f: dict, kind: str) -> None:
    if f["kind"] == kind:
        return
    db.delete_catalog_for_file(f["project_id"], f["id"])
    status = "uploaded" if kind in INFO_KINDS else "stored"
    meta = f.get("meta") or {}
    if kind == "cover":
        try:
            meta.update(ctpl.analyze(f["path"]))
            meta["mapping"] = {x["key"]: x["mapping"] for x in meta["fields"]}
        except Exception as exc:
            meta["error"] = str(exc)
    db.update_library_file(f["id"], kind=kind, status=status, progress=0, error="", notes="", meta=meta)


class FilePatch(BaseModel):
    kind: Optional[str] = None
    division: Optional[str] = None


@app.patch("/api/library/{fid}")
def library_patch(fid: str, body: FilePatch) -> dict:
    f = _need_file(fid)
    if body.kind:
        if body.kind not in LIB_KINDS:
            raise HTTPException(400, "Unknown kind")
        _change_kind(f, body.kind)
        relink_amendments(f["project_id"])
    if body.division is not None:
        db.update_library_file(fid, division=body.division[:2])
    return _file_out(db.get_library_file(fid))  # type: ignore[arg-type]


@app.delete("/api/library/{fid}")
def library_delete(fid: str) -> dict:
    f = _need_file(fid)
    _cancel_file(f)
    db.delete_library_file(fid)
    relink_amendments(f["project_id"])
    return {"ok": True}


@app.get("/api/library/{fid}/pages/{n}.png")
def library_page(fid: str, n: int, dpi: int = 50, px: int = 1600) -> Response:
    f = _need_file(fid)
    with pymupdf.open(f["path"]) as d:
        if not 1 <= n <= d.page_count:
            raise HTTPException(404)
        png = render_png(d, n - 1, dpi=min(dpi, 150), max_px=max(400, min(px, 3600)))
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/library/{fid}/file.pdf")
def library_pdf(fid: str) -> FileResponse:
    f = _need_file(fid)
    return FileResponse(f["path"], media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.get("/api/library/{fid}/cover")
def cover_get(fid: str) -> dict:
    f = _need_file(fid)
    if f["kind"] != "cover":
        raise HTTPException(400, "Not a cover page template")
    meta = f.get("meta") or {}
    with pymupdf.open(f["path"]) as d:
        w, h = d[0].rect.width, d[0].rect.height
    return {"fields": meta.get("fields") or [], "mapping": meta.get("mapping") or {}, "tokens": ctpl.TOKENS,
            "include_generated": bool(meta.get("include_generated")), "page_size": [w, h],
            "placeholders_in_page_text": bool(meta.get("placeholders_in_page_text")), "error": meta.get("error", "")}


class CoverIn(BaseModel):
    mapping: dict[str, str]
    include_generated: bool = False


@app.put("/api/library/{fid}/cover")
def cover_put(fid: str, body: CoverIn) -> dict:
    f = _need_file(fid)
    meta = f.get("meta") or {}
    meta["mapping"] = body.mapping
    meta["include_generated"] = body.include_generated
    db.update_library_file(fid, meta=meta)
    return cover_get(fid)


@app.post("/api/library/{fid}/cover/preview")
def cover_preview(fid: str, body: dict) -> Response:
    """Render the template page 1 filled with sample/current form values."""
    f = _need_file(fid)
    meta = f.get("meta") or {}
    params = body.get("params") or {}
    p = _need_project(f["project_id"])
    info = {"project_name": p.get("name"), "project_number": p.get("number"), "ae_firm": p.get("ae_firm"),
            "contractor": p.get("contractor"), "owner": p.get("owner"), "address": p.get("address"),
            "submittal_no": naming.submittal_number(params, p) if params.get("spec_section") else "08 71 00 - 01.0",
            "spec_section": params.get("spec_section") or "08 71 00", "spec_title": params.get("spec_title") or "Door Hardware",
            "revision": params.get("revision") or "0", "submittal_type": params.get("submittal_type") or "Product Data",
            "type_abbr": naming.tokens(params, p).get("TypeAbbr", "PD"), "title": params.get("title") or "",
            "supplier": params.get("supplier") or "Subcontractor", "date_prepared": date.today().strftime(naming.naming_cfg().get("date_format", "%Y-%m-%d")),
            "required_date": params.get("required_date") or "", "deviations_statement": "No deviations.",
            "items": [{"label": "Product data", "page": 3}], "prepared_by": me().get("display_name", ""),
            "seq": naming.parse_seq(params.get("submittal_no", ""), params.get("spec_section", ""))[0]}
    doc = pymupdf.open()
    with pymupdf.open(f["path"]) as t:
        doc.insert_pdf(t, from_page=0, to_page=0, annots=True, widgets=True)
    ctpl.fill(doc, 1, body.get("mapping") or meta.get("mapping") or {}, ctpl.values_from({**info, "page_count": 12}))
    png = render_png(doc, 0, dpi=80, max_px=1400, annots=True)
    doc.close()
    return Response(png, media_type="image/png")


class DefaultsIn(BaseModel):
    cover: Optional[str] = None
    examples: Optional[list[str]] = None
    div01: Optional[list[str]] = None


@app.put("/api/projects/{pid}/library/defaults")
def set_defaults(pid: str, body: DefaultsIn) -> dict:
    p = _need_project(pid)
    st = p.get("settings") or {}
    d = st.get("defaults") or {}
    for k, v in body.model_dump(exclude_none=True).items():
        d[k] = v
    db.update_project(pid, settings={**st, "defaults": d})
    return d


@app.get("/api/projects/{pid}/catalog/tree")
def catalog_tree(pid: str) -> list[dict]:
    _need_project(pid)
    return cat.scope_tree(pid)


@app.get("/api/projects/{pid}/catalog/sections")
def catalog_sections(pid: str) -> list[dict]:
    _need_project(pid)
    return [{"number": c["number"], "title": c["title"], "pages": cat.item_pages(c), "division": c["number"][:2]}
            for c in db.list_catalog(pid, ["spec_section"])]


@app.get("/api/projects/{pid}/catalog/search")
def catalog_search(pid: str, q: str) -> list[dict]:
    _need_project(pid)
    return db.search_catalog(pid, q, None, limit=25)


@app.get("/api/projects/{pid}/catalog/item")
def catalog_item(pid: str, id: str) -> dict:
    _need_project(pid)
    it = db.get_catalog_item(pid, id)
    if not it:
        raise HTTPException(404)
    it["text"] = (it.get("text") or "")[:20000]
    return it


@app.get("/api/projects/{pid}/catalog/item.pdf")
def catalog_item_pdf(pid: str, id: str) -> Response:
    """Just this item's pages (a spec section, a sheet, an addendum) as a PDF, for reading in the browser."""
    _need_project(pid)
    it = db.get_catalog_item(pid, id)
    if not it or not it.get("file_id"):
        raise HTTPException(404)
    f = _need_file(it["file_id"])
    with pymupdf.open(f["path"]) as src:
        a = max(1, int(it.get("page_start") or 1))
        b = min(src.page_count, int(it.get("page_end") or a))
        out = pymupdf.open()
        out.insert_pdf(src, from_page=a - 1, to_page=max(a, b) - 1)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
    name = re.sub(r"[^\w .-]+", "_", f"{it.get('number') or ''} {it.get('title') or ''}".strip() or "item")[:80]
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}.pdf"', "Cache-Control": "max-age=600"})


# ----------------------------------------------------------------------------- submittal uploads

@app.post("/api/uploads")
async def uploads(files: list[UploadFile] = File(...)) -> dict:
    dest = _uploads_dir("uploads")
    out, errors = [], []
    for up in files:
        if not (up.filename or "").lower().endswith(".pdf"):
            errors.append({"filename": up.filename, "error": "Only PDF files are supported."})
            continue
        path = await _save_upload(up, dest)
        info = inspect_pdf(path)
        if not info["ok"]:
            path.unlink(missing_ok=True)
            errors.append({"filename": up.filename, "error": info["error"]})
            continue
        out.append(db.add_upload(Path(up.filename or path.name).name, str(path), sha256_file(path), info["pages"],
                                 path.stat().st_size, info["flags"], user_id=me()["id"]))
    return {"files": out, "errors": errors}


class AutofillIn(BaseModel):
    upload_ids: list[str]


@app.post("/api/projects/{pid}/autofill")
async def autofill_route(pid: str, body: AutofillIn) -> dict:
    _need_project(pid)
    ups = [_need_upload(u) for u in body.upload_ids]
    if not ups:
        raise HTTPException(400, "Add the submittal first.")
    from ..autofill import autofill
    res = await autofill(pid, [Path(u["path"]) for u in ups], [u["filename"] for u in ups])
    if res.get("spec_section"):
        res["next_seq"] = db.next_seq(pid, res["spec_section"])
    if res.get("subcontractor"):
        match = next((s for s in db.list_subs(pid) if s["name"].lower() == res["subcontractor"].lower()), None)
        res["subcontractor_id"] = match["id"] if match else ""
    return res


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
    if auth.api_key_for_project(pid) and not mock_mode() and db.list_catalog(pid, ["spec_section"]):
        try:
            from ..agents.stages import suggest_scope_ai
            res = await suggest_scope_ai(pid, sec_num, text, body.instructions)
            have = {i["id"] for i in items}
            for s in res.items:
                if db.get_catalog_item(pid, s.item_id) and s.item_id not in have:
                    items.append({"id": s.item_id, "reason": s.reason, "locked": False})
                    have.add(s.item_id)
            source = "smart"
        except Exception as exc:
            log.warning("scope suggestion failed: %s", exc)
    return {"items": items, "mode": settings_cfg().get("scope", {}).get("default_mode", "flexible"),
            "source": source, "section": sec_num, "section_title": (sec or {}).get("title", ""), "found": bool(sec)}


class EstimateIn(BaseModel):
    upload_ids: list[str] = []
    scope_items: list[dict] = []
    review_mode: str = ""


@app.post("/api/projects/{pid}/estimate")
def estimate(pid: str, body: EstimateIn) -> dict:
    _need_project(pid)
    sub_pages = sum((db.get_upload(u) or {}).get("pages", 0) for u in body.upload_ids)
    lib_pages, lib_tokens = 0, 0
    for it in body.scope_items:
        c = db.get_catalog_item(pid, it["id"])
        if not c:
            continue
        pages = len(it["pages"]) if it.get("pages") else cat.item_pages(c)
        lib_pages += pages
        lib_tokens += int(c.get("token_est") or pages * 700) if c["kind"] != "drawing_sheet" else 1600 * pages
    from ..costing import estimate as cost_estimate
    mode = body.review_mode or (settings_cfg().get("review") or {}).get("default_mode", "cross")
    e = cost_estimate(sub_pages, lib_tokens, mode=mode)
    alt = cost_estimate(sub_pages, lib_tokens, mode="cross" if mode == "single" else "single")
    return {"submittal_pages": sub_pages, "library_pages": lib_pages, "est_input_tokens": e["input_tokens"],
            "est_cost_low": e["low"], "est_cost_high": e["high"], "est_cost_mid": e["mid"], "by_stage": e["by_stage"],
            "alt_mode_mid": alt["mid"], "usd_cap": settings_cfg().get("budget", {}).get("job_usd_cap"), "mock": mock_mode()}


class NamingIn(BaseModel):
    params: dict
    example_upload_ids: list[str] = []
    example_file_ids: list[str] = []


@app.post("/api/projects/{pid}/naming/preview")
def naming_preview(pid: str, body: NamingIn) -> dict:
    p = _need_project(pid)
    params = dict(body.params)
    params["spec_section"] = norm_section(params.get("spec_section", "")) if params.get("spec_section") else ""
    if not params.get("spec_title") and params["spec_section"]:
        sec = db.get_catalog_item(pid, f"S:{params['spec_section']}")
        params["spec_title"] = (sec or {}).get("title", "")
    names = [(db.get_upload(u) or {}).get("filename", "") for u in body.example_upload_ids]
    names += [(db.get_library_file(f) or {}).get("filename", "") for f in body.example_file_ids]
    pattern, src = naming.choose_pattern(params, "", [n for n in names if n])
    base = naming.render(pattern, naming.tokens(params, p))
    return {"name": f"{base}.pdf", "pattern": pattern, "source": src, "number": naming.submittal_number(params, p)}


# ----------------------------------------------------------------------------- submittal register

def _submittal_out(s: dict, p: dict, subs: dict[str, dict], names: dict[str, str]) -> dict:
    params = {"spec_section": s["spec_section"], "submittal_no": str(s["seq"]), "revision": s["revision"],
              "submittal_type": s["type"], "spec_title": s["spec_title"]}
    j = db.get_job(s["latest_job_id"]) if s.get("latest_job_id") else None
    r = (j or {}).get("result") or {}
    d = s["spec_section"][:2]
    return {**s, "number": naming.submittal_number(params, p), "division": d, "division_name": names.get(d, ""),
            "subcontractor": (subs.get(s["subcontractor_id"]) or {}).get("name", ""),
            "latest": {"id": j["id"], "status": j["status"], "counts": r.get("counts") or {},
                       "extra_hr": r.get("extra_human_review", 0), "cost": j.get("cost") or 0,
                       "pdf_name": r.get("pdf_name", ""), "updated_at": j["updated_at"]} if j else None}


@app.get("/api/projects/{pid}/submittals")
def submittals(pid: str) -> list[dict]:
    p = _need_project(pid)
    subs = {s["id"]: s for s in db.list_subs(pid)}
    names = _division_names(p)
    return [_submittal_out(s, p, subs, names) for s in db.list_submittals(pid)]


@app.get("/api/projects/{pid}/submittals/next")
def submittal_next(pid: str, spec_section: str) -> dict:
    _need_project(pid)
    return {"seq": db.next_seq(pid, norm_section(spec_section))}


@app.get("/api/submittals/{sid}")
def submittal_get(sid: str) -> dict:
    s = db.get_submittal(sid)
    if not s:
        raise HTTPException(404)
    p = _need_project(s["project_id"])
    out = _submittal_out(s, p, {x["id"]: x for x in db.list_subs(p["id"])}, _division_names(p))
    out["runs"] = db.jobs_for_submittal(sid)
    return out


@app.delete("/api/submittals/{sid}")
def submittal_delete(sid: str) -> dict:
    s = db.get_submittal(sid)
    if not s:
        raise HTTPException(404)
    _need_project(s["project_id"])
    db.delete_submittal(sid)
    return {"ok": True}


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
    subcontractor_id: str = ""
    instructions: str = ""
    naming_pattern: str = ""
    mimic_example_naming: bool = False
    review_mode: str = ""
    usd_cap: Optional[float] = None
    uploads: dict[str, list[str]]
    library_refs: dict = {}
    scope: dict


@app.post("/api/projects/{pid}/jobs")
async def create_job(pid: str, body: JobIn) -> dict:
    _need_project(pid)
    if not body.uploads.get("submittal"):
        raise HTTPException(400, "Add the submittal PDF first.")
    for u in body.uploads.get("submittal", []):
        _need_upload(u)
    if len(body.instructions) > 2000:
        raise HTTPException(400, "Reviewer instructions are limited to 2,000 characters.")
    if not auth.api_key_for_project(pid) and not mock_mode():
        raise HTTPException(400, "No Anthropic API key is set. Add yours in Settings.")
    params = body.model_dump()
    params["spec_section"] = norm_section(params["spec_section"])
    params["_prepared_by"] = me().get("display_name") or me()["username"]
    # subcontractor: pick existing, or create it on the fly (tagged with this division)
    sub_id = body.subcontractor_id
    if not sub_id and body.supplier.strip():
        match = next((s for s in db.list_subs(pid) if s["name"].lower() == body.supplier.strip().lower()), None)
        sub_id = match["id"] if match else db.upsert_sub(pid, {"name": body.supplier.strip(),
                                                               "divisions": [params["spec_section"][:2]],
                                                               "spec_sections": [params["spec_section"]]})["id"]
    if sub_id:
        s = db.get_sub(sub_id)
        params["supplier"] = (s or {}).get("name", body.supplier)
    seq = naming.parse_seq(body.submittal_no, params["spec_section"])[0]
    rev = (body.revision or "0").strip() or "0"
    sm = db.find_submittal(pid, params["spec_section"], seq, rev)
    data = {"spec_section": params["spec_section"], "seq": seq, "revision": rev, "title": body.title,
            "type": body.submittal_type, "spec_title": body.spec_title, "subcontractor_id": sub_id,
            "required_date": body.required_date, "created_by": me()["id"],
            "options": {"library_refs": body.library_refs, "review_mode": body.review_mode}}
    if sm:
        db.update_submittal(sm["id"], **{k: v for k, v in data.items() if k != "created_by"})
    else:
        sm = db.create_submittal(pid, data)
    params["submittal_id"] = sm["id"]
    job = db.create_job(pid, params, submittal_id=sm["id"])
    pipeline.start(job["id"])
    return job


@app.get("/api/projects/{pid}/jobs")
def list_jobs(pid: str) -> list[dict]:
    _need_project(pid)
    return [_job_brief(j) for j in db.list_jobs(pid)]


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
    nj = db.create_job(j["project_id"], params, parent_job=jid, submittal_id=j.get("submittal_id") or "")
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
                doc.set_layer(-1, on=list(doc.get_ocgs().keys()))
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
        return JSONResponse({"message": "Frontend not built. Run `make ui`."})
