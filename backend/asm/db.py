"""SQLite storage: projects, library files, catalog (FTS5), jobs, API-call and retrieval logs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import data_dir

_LOCK = threading.RLock()
_DB_PATH: Path | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, number TEXT DEFAULT '', ae_firm TEXT DEFAULT '',
  contractor TEXT DEFAULT '', owner TEXT DEFAULT '', address TEXT DEFAULT '',
  settings TEXT DEFAULT '{}', created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS library_files (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, filename TEXT NOT NULL,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, pages INTEGER DEFAULT 0, size INTEGER DEFAULT 0,
  status TEXT DEFAULT 'queued', progress REAL DEFAULT 0, error TEXT DEFAULT '', notes TEXT DEFAULT '',
  uploaded_at REAL, version INTEGER DEFAULT 1, superseded INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS catalog (
  id TEXT NOT NULL, project_id TEXT NOT NULL, kind TEXT NOT NULL, number TEXT DEFAULT '',
  title TEXT DEFAULT '', parent_id TEXT DEFAULT '', file_id TEXT DEFAULT '',
  page_start INTEGER DEFAULT 0, page_end INTEGER DEFAULT 0, summary TEXT DEFAULT '',
  keywords TEXT DEFAULT '[]', xrefs TEXT DEFAULT '[]', amended_by TEXT DEFAULT '[]',
  discipline TEXT DEFAULT '', text TEXT DEFAULT '', token_est INTEGER DEFAULT 0, sort_key TEXT DEFAULT '',
  meta TEXT DEFAULT '{}',
  PRIMARY KEY (project_id, id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
  project_id UNINDEXED, item_id UNINDEXED, number, title, summary, keywords, text,
  tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS scope_presets (
  project_id TEXT NOT NULL, spec_section TEXT NOT NULL, scope TEXT NOT NULL, updated_at REAL,
  PRIMARY KEY (project_id, spec_section)
);
CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, filename TEXT, path TEXT, sha256 TEXT, pages INTEGER, size INTEGER,
  flags TEXT DEFAULT '[]', created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT DEFAULT '',
  params TEXT DEFAULT '{}', result TEXT DEFAULT '{}', cost REAL DEFAULT 0, error TEXT DEFAULT '',
  created_at REAL, updated_at REAL, parent_job TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS api_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, project_id TEXT, stage TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cache_write INTEGER, cache_read INTEGER,
  web_searches INTEGER DEFAULT 0, cost REAL, ms INTEGER, ts REAL
);
CREATE TABLE IF NOT EXISTS retrievals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, stage TEXT, tool TEXT, item_id TEXT,
  pages INTEGER, tokens INTEGER, in_scope INTEGER, allowed INTEGER, reason TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT DEFAULT '', pw_hash TEXT NOT NULL,
  api_key TEXT DEFAULT '', prefs TEXT DEFAULT '{}', is_admin INTEGER DEFAULT 0, created_at REAL, last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL, expires_at REAL
);
CREATE TABLE IF NOT EXISTS subcontractors (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, contact TEXT DEFAULT '',
  email TEXT DEFAULT '', phone TEXT DEFAULT '', divisions TEXT DEFAULT '[]', spec_sections TEXT DEFAULT '[]',
  notes TEXT DEFAULT '', created_at REAL
);
CREATE TABLE IF NOT EXISTS submittals (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, spec_section TEXT NOT NULL, seq INTEGER DEFAULT 1,
  revision TEXT DEFAULT '0', title TEXT DEFAULT '', type TEXT DEFAULT 'Product Data', spec_title TEXT DEFAULT '',
  subcontractor_id TEXT DEFAULT '', status TEXT DEFAULT 'draft', required_date TEXT DEFAULT '',
  latest_job_id TEXT DEFAULT '', options TEXT DEFAULT '{}', created_by TEXT DEFAULT '', created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_subs_project ON subcontractors(project_id);
CREATE INDEX IF NOT EXISTS ix_submittals_project ON submittals(project_id);
CREATE INDEX IF NOT EXISTS ix_catalog_kind ON catalog(project_id, kind);
CREATE INDEX IF NOT EXISTS ix_files_project ON library_files(project_id);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON jobs(project_id);
"""


def db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = data_dir() / "asm.sqlite3"
    return _DB_PATH


def set_db_path(path: Path) -> None:
    """Used by tests."""
    global _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _DB_PATH = path
    init()


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    with _LOCK:
        c = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


MIGRATIONS = [
    "ALTER TABLE projects ADD COLUMN owner_id TEXT DEFAULT ''",
    "ALTER TABLE library_files ADD COLUMN meta TEXT DEFAULT '{}'",
    "ALTER TABLE library_files ADD COLUMN division TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN submittal_id TEXT DEFAULT ''",
    "ALTER TABLE uploads ADD COLUMN user_id TEXT DEFAULT ''",
]


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                c.execute(m)
            except sqlite3.OperationalError:
                pass  # column already exists


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def now() -> float:
    return time.time()


def row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("settings", "keywords", "xrefs", "amended_by", "params", "result", "flags", "scope", "meta",
              "divisions", "spec_sections", "prefs", "options"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row(r) for r in rs]  # type: ignore[misc]


# ----------------------------------------------------------------------------- projects

def create_project(**fields: Any) -> dict[str, Any]:
    pid = new_id("p_")
    settings = json.dumps(fields.pop("settings", {}) or {})
    with conn() as c:
        c.execute(
            "INSERT INTO projects(id,name,number,ae_firm,contractor,owner,address,settings,created_at,updated_at,owner_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (pid, fields.get("name", "Untitled"), fields.get("number", ""), fields.get("ae_firm", ""),
             fields.get("contractor", ""), fields.get("owner", ""), fields.get("address", ""),
             settings, now(), now(), fields.get("owner_id", "")),
        )
    return get_project(pid)  # type: ignore[return-value]


def update_project(pid: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "number", "ae_firm", "contractor", "owner", "address", "settings"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k == "settings" else v)
    if sets:
        with conn() as c:
            c.execute(f"UPDATE projects SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, now(), pid))
    return get_project(pid)


def get_project(pid: str) -> dict[str, Any] | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def list_projects(owner_id: str | None = None) -> list[dict[str, Any]]:
    with conn() as c:
        if owner_id is None:
            return rows(c.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall())
        return rows(c.execute("SELECT * FROM projects WHERE owner_id=? ORDER BY updated_at DESC", (owner_id,)).fetchall())


def delete_project(pid: str) -> None:
    with conn() as c:
        for t in ("library_files", "catalog", "scope_presets", "jobs", "subcontractors", "submittals"):
            c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
        c.execute("DELETE FROM catalog_fts WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))


# ----------------------------------------------------------------------------- library files

def add_library_file(project_id: str, kind: str, filename: str, path: str, sha: str, pages: int, size: int,
                     status: str = "queued", meta: dict | None = None) -> dict:
    fid = new_id("f_")
    with conn() as c:
        prev = c.execute("SELECT MAX(version) v FROM library_files WHERE project_id=? AND filename=?",
                         (project_id, filename)).fetchone()
        version = (prev["v"] or 0) + 1
        c.execute("UPDATE library_files SET superseded=1 WHERE project_id=? AND filename=?", (project_id, filename))
        c.execute(
            "INSERT INTO library_files(id,project_id,kind,filename,path,sha256,pages,size,status,uploaded_at,version,meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, project_id, kind, filename, path, sha, pages, size, status, now(), version, json.dumps(meta or {})),
        )
    return get_library_file(fid)  # type: ignore[return-value]


def get_library_file(fid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM library_files WHERE id=?", (fid,)).fetchone())


def find_library_file_by_hash(project_id: str, sha: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM library_files WHERE project_id=? AND sha256=? AND superseded=0",
                             (project_id, sha)).fetchone())


def list_library_files(project_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT * FROM library_files WHERE project_id=? AND superseded=0 ORDER BY uploaded_at",
                              (project_id,)).fetchall())


def update_library_file(fid: str, **fields: Any) -> None:
    if "meta" in fields and not isinstance(fields["meta"], str):
        fields["meta"] = json.dumps(fields["meta"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE library_files SET {sets} WHERE id=?", (*fields.values(), fid))


def delete_library_file(fid: str) -> None:
    f = get_library_file(fid)
    if not f:
        return
    with conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM catalog WHERE project_id=? AND file_id=?",
                                          (f["project_id"], fid)).fetchall()]
        c.execute("DELETE FROM catalog WHERE project_id=? AND file_id=?", (f["project_id"], fid))
        for i in ids:
            c.execute("DELETE FROM catalog_fts WHERE project_id=? AND item_id=?", (f["project_id"], i))
        c.execute("DELETE FROM library_files WHERE id=?", (fid,))


# ----------------------------------------------------------------------------- catalog

CATALOG_COLS = ["id", "project_id", "kind", "number", "title", "parent_id", "file_id", "page_start", "page_end",
                "summary", "keywords", "xrefs", "amended_by", "discipline", "text", "token_est", "sort_key", "meta"]


def upsert_catalog(items: list[dict[str, Any]]) -> None:
    with conn() as c:
        for it in items:
            vals = []
            for col in CATALOG_COLS:
                v = it.get(col, "" if col not in ("page_start", "page_end", "token_est") else 0)
                if col in ("keywords", "xrefs", "amended_by"):
                    v = json.dumps(v or [])
                if col == "meta":
                    v = json.dumps(v or {})
                vals.append(v)
            c.execute(f"INSERT OR REPLACE INTO catalog({','.join(CATALOG_COLS)}) VALUES({','.join('?' * len(CATALOG_COLS))})", vals)
            c.execute("DELETE FROM catalog_fts WHERE project_id=? AND item_id=?", (it["project_id"], it["id"]))
            c.execute(
                "INSERT INTO catalog_fts(project_id,item_id,number,title,summary,keywords,text) VALUES(?,?,?,?,?,?,?)",
                (it["project_id"], it["id"], it.get("number", ""), it.get("title", ""), it.get("summary", ""),
                 " ".join(it.get("keywords") or []), it.get("text", "")),
            )


def get_catalog_item(project_id: str, item_id: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM catalog WHERE project_id=? AND id=?", (project_id, item_id)).fetchone())


def list_catalog(project_id: str, kinds: list[str] | None = None, with_text: bool = False) -> list[dict]:
    cols = "*" if with_text else ",".join(c for c in CATALOG_COLS if c != "text")
    q = f"SELECT {cols} FROM catalog WHERE project_id=?"
    args: list[Any] = [project_id]
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    q += " ORDER BY sort_key"
    with conn() as c:
        return rows(c.execute(q, args).fetchall())


def search_catalog(project_id: str, query: str, kinds: list[str] | None = None, limit: int = 15) -> list[dict]:
    import re
    terms = [t for t in re.findall(r"[A-Za-z0-9.]+", query) if len(t) > 1]
    if not terms:
        return []
    fts_q = " OR ".join(f'"{t}"' for t in terms)
    sql = ("SELECT c.id,c.kind,c.number,c.title,c.summary,c.page_start,c.page_end,c.file_id,c.token_est,"
           " bm25(catalog_fts) AS score FROM catalog_fts JOIN catalog c"
           " ON c.project_id=catalog_fts.project_id AND c.id=catalog_fts.item_id"
           " WHERE catalog_fts MATCH ? AND catalog_fts.project_id=?")
    args: list[Any] = [fts_q, project_id]
    if kinds:
        sql += f" AND c.kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    sql += " ORDER BY score LIMIT ?"
    args.append(limit)
    with conn() as c:
        try:
            return rows(c.execute(sql, args).fetchall())
        except sqlite3.OperationalError:
            return []


def delete_catalog_for_file(project_id: str, file_id: str) -> None:
    with conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM catalog WHERE project_id=? AND file_id=?",
                                          (project_id, file_id)).fetchall()]
        c.execute("DELETE FROM catalog WHERE project_id=? AND file_id=?", (project_id, file_id))
        for i in ids:
            c.execute("DELETE FROM catalog_fts WHERE project_id=? AND item_id=?", (project_id, i))


def set_amended_by(project_id: str, item_id: str, amended_by: list[str]) -> None:
    with conn() as c:
        c.execute("UPDATE catalog SET amended_by=? WHERE project_id=? AND id=?",
                  (json.dumps(sorted(set(amended_by))), project_id, item_id))


# ----------------------------------------------------------------------------- scope presets

def save_scope_preset(project_id: str, spec_section: str, scope: dict) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO scope_presets(project_id,spec_section,scope,updated_at) VALUES(?,?,?,?)",
                  (project_id, spec_section, json.dumps(scope), now()))


def get_scope_preset(project_id: str, spec_section: str) -> dict | None:
    with conn() as c:
        r = row(c.execute("SELECT * FROM scope_presets WHERE project_id=? AND spec_section=?",
                          (project_id, spec_section)).fetchone())
    return r["scope"] if r else None


# ----------------------------------------------------------------------------- uploads (zones 1, 3, 4)

def add_upload(filename: str, path: str, sha: str, pages: int, size: int, flags: list[str], user_id: str = "") -> dict:
    uid = new_id("u_")
    with conn() as c:
        c.execute("INSERT INTO uploads(id,filename,path,sha256,pages,size,flags,created_at,user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (uid, filename, path, sha, pages, size, json.dumps(flags), now(), user_id))
    return get_upload(uid)  # type: ignore[return-value]


def get_upload(uid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone())


# ----------------------------------------------------------------------------- jobs

def create_job(project_id: str, params: dict, parent_job: str = "", submittal_id: str = "") -> dict:
    jid = new_id("j_")
    with conn() as c:
        c.execute("INSERT INTO jobs(id,project_id,status,params,created_at,updated_at,parent_job,submittal_id) VALUES(?,?,?,?,?,?,?,?)",
                  (jid, project_id, "queued", json.dumps(params), now(), now(), parent_job, submittal_id))
    return get_job(jid)  # type: ignore[return-value]


def get_job(jid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())


def list_jobs(project_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT id,project_id,status,stage,params,cost,error,created_at,updated_at,parent_job,submittal_id"
                              " FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())


def update_job(jid: str, **fields: Any) -> None:
    for k in ("params", "result"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?", (*fields.values(), now(), jid))


# ----------------------------------------------------------------------------- logs

def log_api_call(job_id: str | None, project_id: str | None, stage: str, model: str, usage: dict, cost: float, ms: int) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO api_calls(job_id,project_id,stage,model,input_tokens,output_tokens,cache_write,cache_read,web_searches,cost,ms,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, project_id, stage, model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
             usage.get("cache_creation_input_tokens", 0), usage.get("cache_read_input_tokens", 0),
             usage.get("web_searches", 0), cost, ms, now()),
        )


def api_calls_for_job(job_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT * FROM api_calls WHERE job_id=? ORDER BY id", (job_id,)).fetchall())


def log_retrieval(job_id: str, stage: str, tool: str, item_id: str, pages: int, tokens: int,
                  in_scope: bool, allowed: bool, reason: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO retrievals(job_id,stage,tool,item_id,pages,tokens,in_scope,allowed,reason,ts)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (job_id, stage, tool, item_id, pages, tokens, int(in_scope), int(allowed), reason, now()))


def retrievals_for_job(job_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT * FROM retrievals WHERE job_id=? ORDER BY id", (job_id,)).fetchall())


# ----------------------------------------------------------------------------- users & sessions

def create_user(username: str, display_name: str, pw_hash: str, is_admin: bool = False) -> dict:
    uid = new_id("usr_")
    with conn() as c:
        c.execute("INSERT INTO users(id,username,display_name,pw_hash,is_admin,created_at) VALUES(?,?,?,?,?,?)",
                  (uid, username, display_name, pw_hash, int(is_admin), now()))
    return get_user(uid)  # type: ignore[return-value]


def get_user(uid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def get_user_by_name(username: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username,)).fetchone())


def count_users() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def update_user(uid: str, **fields: Any) -> None:
    if "prefs" in fields and not isinstance(fields["prefs"], str):
        fields["prefs"] = json.dumps(fields["prefs"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), uid))


def create_session(token: str, user_id: str, ttl_s: float) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                  (token, user_id, now(), now() + ttl_s))


def session_user(token: str) -> dict | None:
    with conn() as c:
        s = c.execute("SELECT * FROM sessions WHERE token=? AND expires_at > ?", (token, now())).fetchone()
        if not s:
            return None
        return row(c.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone())


def delete_session(token: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def claim_ownerless_projects(user_id: str) -> int:
    with conn() as c:
        cur = c.execute("UPDATE projects SET owner_id=? WHERE owner_id IS NULL OR owner_id=''", (user_id,))
        return cur.rowcount


# ----------------------------------------------------------------------------- subcontractors

def list_subs(project_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT * FROM subcontractors WHERE project_id=? ORDER BY name COLLATE NOCASE",
                              (project_id,)).fetchall())


def get_sub(sid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM subcontractors WHERE id=?", (sid,)).fetchone())


def upsert_sub(project_id: str, data: dict, sid: str | None = None) -> dict:
    cols = ["name", "contact", "email", "phone", "divisions", "spec_sections", "notes"]
    vals = [json.dumps(data.get(k) or []) if k in ("divisions", "spec_sections") else (data.get(k) or "") for k in cols]
    with conn() as c:
        if sid:
            c.execute(f"UPDATE subcontractors SET {', '.join(f'{k}=?' for k in cols)} WHERE id=? AND project_id=?",
                      (*vals, sid, project_id))
        else:
            sid = new_id("sub_")
            c.execute(f"INSERT INTO subcontractors(id,project_id,{','.join(cols)},created_at) VALUES(?,?,{','.join('?' * len(cols))},?)",
                      (sid, project_id, *vals, now()))
    return get_sub(sid)  # type: ignore[return-value,arg-type]


def delete_sub(sid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM subcontractors WHERE id=?", (sid,))


# ----------------------------------------------------------------------------- submittal register

SUBMITTAL_COLS = ["spec_section", "seq", "revision", "title", "type", "spec_title", "subcontractor_id", "status",
                  "required_date", "latest_job_id", "options", "created_by"]


def create_submittal(project_id: str, data: dict) -> dict:
    sid = new_id("sm_")
    vals = [json.dumps(data.get(k) or {}) if k == "options" else data.get(k, "" if k != "seq" else 1) for k in SUBMITTAL_COLS]
    with conn() as c:
        c.execute(f"INSERT INTO submittals(id,project_id,{','.join(SUBMITTAL_COLS)},created_at,updated_at)"
                  f" VALUES(?,?,{','.join('?' * len(SUBMITTAL_COLS))},?,?)", (sid, project_id, *vals, now(), now()))
    return get_submittal(sid)  # type: ignore[return-value]


def update_submittal(sid: str, **fields: Any) -> None:
    if "options" in fields and not isinstance(fields["options"], str):
        fields["options"] = json.dumps(fields["options"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE submittals SET {sets}, updated_at=? WHERE id=?", (*fields.values(), now(), sid))


def get_submittal(sid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM submittals WHERE id=?", (sid,)).fetchone())


def find_submittal(project_id: str, spec_section: str, seq: int, revision: str) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM submittals WHERE project_id=? AND spec_section=? AND seq=? AND revision=?",
                             (project_id, spec_section, seq, revision)).fetchone())


def list_submittals(project_id: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT * FROM submittals WHERE project_id=? ORDER BY spec_section, seq, revision",
                              (project_id,)).fetchall())


def delete_submittal(sid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM submittals WHERE id=?", (sid,))


def next_seq(project_id: str, spec_section: str) -> int:
    with conn() as c:
        r = c.execute("SELECT MAX(seq) m FROM submittals WHERE project_id=? AND spec_section=?",
                      (project_id, spec_section)).fetchone()
    return int(r["m"] or 0) + 1


def jobs_for_submittal(sid: str) -> list[dict]:
    with conn() as c:
        return rows(c.execute("SELECT id,status,stage,cost,error,created_at,updated_at,parent_job FROM jobs"
                              " WHERE submittal_id=? ORDER BY created_at DESC", (sid,)).fetchall())
