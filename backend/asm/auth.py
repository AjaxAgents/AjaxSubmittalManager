"""Local accounts: scrypt password hashing, session tokens, per-user API keys protected at rest.

Accounts live in this computer's ASM database. Each user sees only their own projects and submittals.
API keys are encrypted with Windows DPAPI (bound to the Windows user) when available.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import os
import secrets
import sys
from typing import Any

from . import db
from .config import api_key as global_api_key

SESSION_COOKIE = "asm_session"
SESSION_TTL_S = 14 * 24 * 3600
current_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("asm_user", default=None)


# ----------------------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ----------------------------------------------------------------------------- secret storage

def _dpapi(data: bytes, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = BLOB()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
        if not ok:
            return None
        res = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return res
    except Exception:
        return None


def protect(secret: str) -> str:
    if not secret:
        return ""
    enc = _dpapi(secret.encode(), True)
    if enc is not None:
        return "dpapi:" + base64.b64encode(enc).decode()
    return "plain:" + base64.b64encode(secret.encode()).decode()


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    kind, _, payload = stored.partition(":")
    raw = base64.b64decode(payload or "")
    if kind == "dpapi":
        dec = _dpapi(raw, False)
        return dec.decode() if dec else ""
    return raw.decode()


# ----------------------------------------------------------------------------- helpers

def public_user(u: dict) -> dict[str, Any]:
    return {"id": u["id"], "username": u["username"], "display_name": u.get("display_name") or u["username"],
            "is_admin": bool(u.get("is_admin")), "prefs": u.get("prefs") or {},
            "api_key_set": bool(user_api_key(u)), "api_key_hint": (user_api_key(u) or "")[-4:],
            "own_key": bool(u.get("api_key"))}


def user_api_key(u: dict | None) -> str | None:
    if u and u.get("api_key"):
        k = unprotect(u["api_key"])
        if k:
            return k
    return global_api_key()


def api_key_for_project(project_id: str | None) -> str | None:
    """The project owner's key, else the install-wide key from .env."""
    if project_id:
        p = db.get_project(project_id)
        if p and p.get("owner_id"):
            return user_api_key(db.get_user(p["owner_id"]))
    u = current_user.get()
    return user_api_key(u)
