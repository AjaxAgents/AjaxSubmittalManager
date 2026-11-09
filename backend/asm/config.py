"""Configuration loading: .env + config/*.yaml. Nothing model- or price-specific is hard-coded."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Two layouts:
#  * source checkout: everything relative to the repo root
#  * Windows .exe (PyInstaller, sys.frozen): read-only resources (UI, default config, prompts) are bundled
#    inside the exe; the exe's folder (APP_DIR) holds the Outputs folder and may hold config/ or prompts/
#    overrides; the database, project libraries and the API key live in %LOCALAPPDATA%, outside OneDrive
#    sync (SQLite and synced folders do not mix, and the key should not sync).
FROZEN = bool(getattr(sys, "frozen", False))
REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT)) if FROZEN else REPO_ROOT
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else REPO_ROOT
CONFIG_DIR = BUNDLE_DIR / "config"
PROMPTS_DIR = BUNDLE_DIR / "prompts"
FRONTEND_DIST = BUNDLE_DIR / "frontend" / "dist"


def user_state_dir() -> Path:
    if FROZEN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        p = base / "AjaxSubmittalManager"
    else:
        p = REPO_ROOT
    p.mkdir(parents=True, exist_ok=True)
    return p


ENV_PATH = user_state_dir() / ".env"

load_dotenv(ENV_PATH)


def _resource(subdir: str, name: str) -> Path:
    """User override next to the exe/repo wins over the bundled default."""
    local = APP_DIR / subdir / name
    if FROZEN and local.exists():
        return local
    return BUNDLE_DIR / subdir / name


def _load_yaml(name: str) -> dict[str, Any]:
    path = _resource("config", name)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def overrides_path() -> Path:
    return data_dir() / "settings_overrides.yaml"


def _overrides() -> dict[str, Any]:
    p = overrides_path()
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_overrides(section: str, values: dict[str, Any]) -> None:
    """Persist GUI settings changes (models / settings) without rewriting the commented config files."""
    cur = _overrides()
    cur[section] = _deep_merge(cur.get(section, {}), values)
    with overrides_path().open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cur, fh, sort_keys=False)
    reload_all()


@lru_cache(maxsize=None)
def models_cfg() -> dict[str, Any]:
    return _deep_merge(_load_yaml("models.yaml"), _overrides().get("models", {}))


@lru_cache(maxsize=None)
def pricing_cfg() -> dict[str, Any]:
    return _load_yaml("pricing.yaml")


@lru_cache(maxsize=None)
def naming_cfg() -> dict[str, Any]:
    return _load_yaml("naming.yaml")


@lru_cache(maxsize=None)
def markup_cfg() -> dict[str, Any]:
    return _load_yaml("markup_styles.yaml")


@lru_cache(maxsize=None)
def settings_cfg() -> dict[str, Any]:
    cfg = _deep_merge(_load_yaml("settings.yaml"), _overrides().get("settings", {}))
    if os.getenv("ASM_JOB_USD_CAP"):
        cfg.setdefault("budget", {})["job_usd_cap"] = float(os.environ["ASM_JOB_USD_CAP"])
    if os.getenv("ASM_PORT"):
        cfg.setdefault("server", {})["port"] = int(os.environ["ASM_PORT"])
    return cfg


def reload_all() -> None:
    for fn in (models_cfg, pricing_cfg, naming_cfg, markup_cfg, settings_cfg):
        fn.cache_clear()
    load_dotenv(ENV_PATH, override=True)


def data_dir() -> Path:
    raw = os.getenv("ASM_DATA_DIR")
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = (user_state_dir() if FROZEN else REPO_ROOT) / p
    else:
        p = user_state_dir() / "data" if FROZEN else REPO_ROOT / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def outputs_dir() -> Path:
    """Where finished PDFs/TXTs are copied for the user: <exe folder>/Outputs, or data/outputs in a checkout."""
    raw = os.getenv("ASM_OUTPUT_DIR") or (settings_cfg().get("output", {}) or {}).get("folder") or ""
    if raw:
        p = Path(raw)
    elif FROZEN:
        p = APP_DIR / "Outputs"
    else:
        p = data_dir() / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return key or None


def mock_mode() -> bool:
    return os.getenv("ASM_MOCK_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}


def stage_cfg(stage: str) -> dict[str, Any]:
    stages = models_cfg().get("stages", {})
    cfg = dict(stages.get(stage) or {})
    cfg.setdefault("model", "claude-sonnet-5-5")
    cfg.setdefault("max_tokens", 8000)
    return cfg


def prompt(name: str) -> str:
    path = _resource("prompts", f"{name}.md")
    text = path.read_text(encoding="utf-8")
    rule_path = _resource("prompts", "_data_rule.md")
    rule = rule_path.read_text(encoding="utf-8").strip() if rule_path.exists() else ""
    return text.replace("{data_rule}", rule).replace("{{", "{").replace("}}", "}")


def save_api_key(key: str) -> None:
    """Persist the API key to the backend .env (server side only)."""
    save_env("ANTHROPIC_API_KEY", key)


def save_env(name: str, value: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out, found = [], False
    for line in lines:
        if line.startswith(f"{name}="):
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[name] = value
