"""Catalog views: Tier-0 index text for agents, the GUI scope tree, and heuristic scope suggestions."""
from __future__ import annotations

import re
from typing import Any

from .. import db
from .ingest import DIVISIONS, norm_section


def section_lookup(project_id: str, number: str) -> dict | None:
    return db.get_catalog_item(project_id, f"S:{norm_section(number)}")


def item_pages(item: dict[str, Any]) -> int:
    return max(1, int(item.get("page_end") or 0) - int(item.get("page_start") or 0) + 1)


def catalog_index_text(project_id: str, scope_ids: set[str] | None = None, max_chars: int = 60000) -> str:
    """Tier 0: compact map of the library. Full content only through retrieval tools."""
    scope_ids = scope_ids or set()
    cat = db.list_catalog(project_id)
    in_scope_secs = {i.split("/")[0] for i in scope_ids}
    lines = ["CATALOG INDEX (ids in brackets; [IN SCOPE] = selected for this review; amended items list their amendments)"]
    lines.append("\n## SPECIFICATION SECTIONS")
    arts_by_sec: dict[str, list[dict]] = {}
    for c in cat:
        if c["kind"] == "spec_article":
            arts_by_sec.setdefault(c["id"].split("/")[0], []).append(c)
    for c in cat:
        if c["kind"] != "spec_section":
            continue
        tag = " [IN SCOPE]" if c["id"] in in_scope_secs else ""
        am = f" amended_by={','.join(c['amended_by'])}" if c.get("amended_by") else ""
        summ = (c.get("summary") or "").replace("\n", " ")[:140]
        lines.append(f"[{c['id']}] {c['number']} {c['title']} | {item_pages(c)}pp{tag}{am} | {summ}")
        if c["id"] in in_scope_secs:
            for a in arts_by_sec.get(c["id"], []):
                lines.append(f"    [{a['id']}] {a['number'].split(' ')[-1]} {a['title']} (p.{a['page_start']})")
    sheets = [c for c in cat if c["kind"] == "drawing_sheet"]
    if sheets:
        lines.append("\n## DRAWING SHEETS")
        for c in sheets:
            tag = " [IN SCOPE]" if c["id"] in scope_ids else ""
            am = f" amended_by={','.join(c['amended_by'])}" if c.get("amended_by") else ""
            summ = (c.get("summary") or "").replace("\n", " ")[:110]
            lines.append(f"[{c['id']}] {c['number']} {c['title']} ({c.get('discipline', '')}){tag}{am} | {summ}")
    ams = [c for c in cat if c["kind"] == "amendment"]
    if ams:
        lines.append("\n## AMENDMENTS (addenda / ASIs / RFIs - outrank base documents)")
        for c in ams:
            tag = " [IN SCOPE]" if c["id"] in scope_ids else ""
            refs = ", ".join((c.get("xrefs") or [])[:12])
            lines.append(f"[{c['id']}] {c['title']}{tag} | modifies: {refs or 'see text'}")
    docs = [c for c in cat if c["kind"] == "document"]
    if docs:
        lines.append("\n## OTHER PROJECT DOCUMENTS")
        for c in docs:
            tag = " [IN SCOPE]" if c["id"] in scope_ids else ""
            lines.append(f"[{c['id']}] {c['title']} | {item_pages(c)}pp{tag} | {(c.get('summary') or '')[:110]}")
    out = "\n".join(lines)
    return out if len(out) <= max_chars else out[:max_chars] + "\n...(index truncated; use catalog_search)"


def scope_tree(project_id: str) -> list[dict[str, Any]]:
    """Checkbox tree for the Reference scope dropdown."""
    cat = db.list_catalog(project_id)
    by_parent: dict[str, list[dict]] = {}
    for c in cat:
        by_parent.setdefault(c.get("parent_id") or "", []).append(c)

    def node(c: dict) -> dict[str, Any]:
        n = {"id": c["id"], "kind": c["kind"], "label": f"{c['number']} {c['title']}".strip(),
             "number": c["number"], "title": c["title"], "pages": item_pages(c),
             "page_start": c["page_start"], "page_end": c["page_end"], "amended_by": c.get("amended_by") or [],
             "tokens": c.get("token_est", 0), "file_id": c.get("file_id", ""), "children": []}
        if c["kind"] == "spec_article":
            n["label"] = f"{c['number'].split(' ')[-1]} {c['title']}"
        if c["kind"] == "spec_part":
            n["label"] = f"Part {c['id'][-1]} {c['title']}"
        if c["kind"] in ("amendment", "document"):
            n["label"] = c["title"] or c["number"]
        for ch in by_parent.get(c["id"], []):
            n["children"].append(node(ch))
        return n

    tree: list[dict[str, Any]] = []
    secs = [c for c in cat if c["kind"] == "spec_section"]
    if secs:
        divs: dict[str, list] = {}
        for s in secs:
            divs.setdefault(s["number"][:2], []).append(node(s))
        tree.append({"id": "GROUP:SPECS", "kind": "group", "label": "Specifications", "children": [
            {"id": f"DIV:{d}", "kind": "division", "label": f"Division {d} - {DIVISIONS.get(d, '')}".strip(" -"),
             "children": kids} for d, kids in sorted(divs.items())]})
    sheets = [c for c in cat if c["kind"] == "drawing_sheet"]
    if sheets:
        disc: dict[str, list] = {}
        for s in sheets:
            disc.setdefault(s.get("discipline") or "Other", []).append(node(s))
        tree.append({"id": "GROUP:DWGS", "kind": "group", "label": "Drawings", "children": [
            {"id": f"DISC:{d}", "kind": "discipline", "label": d, "children": kids} for d, kids in sorted(disc.items())]})
    ams = [node(c) for c in cat if c["kind"] == "amendment"]
    if ams:
        tree.append({"id": "GROUP:AMEND", "kind": "group", "label": "Amendments (Addenda / ASIs / RFIs)", "children": ams})
    docs = [node(c) for c in cat if c["kind"] == "document"]
    if docs:
        tree.append({"id": "GROUP:DOCS", "kind": "group", "label": "Other Documents", "children": docs})
    return tree


def heuristic_scope(project_id: str, spec_section: str, keywords: list[str] | None = None) -> list[dict[str, Any]]:
    """Cheap, deterministic starting scope used when no API key is set (and as a floor for the AI pass)."""
    sec_num = norm_section(spec_section)
    sid = f"S:{sec_num}"
    sec = db.get_catalog_item(project_id, sid)
    picks: dict[str, str] = {}
    if sec:
        picks[sid] = "Designated specification section (locked)"
        for x in (sec.get("xrefs") or [])[:8]:
            if db.get_catalog_item(project_id, f"S:{x}") and not x.startswith("01"):
                picks.setdefault(f"S:{x}", f"Referenced in {sec_num}")
        for am in sec.get("amended_by") or []:
            picks.setdefault(am, f"Amends {sec_num}")
    div01 = [c for c in db.list_catalog(project_id, ["spec_section"]) if c["number"].startswith("01 33")]
    for d in div01[:2]:
        picks.setdefault(d["id"], "Division 01 submittal procedures")
    for sh in db.list_catalog(project_id, ["drawing_sheet"], with_text=True):
        txt = (sh.get("text") or "") + " " + sh.get("title", "")
        if sec_num in txt or sec_num.replace(" ", "") in txt:
            picks.setdefault(sh["id"], f"Sheet references {sec_num}")
        elif keywords and any(re.search(rf"\b{re.escape(k)}\b", txt, re.I) for k in keywords if len(k) > 3):
            picks.setdefault(sh["id"], "Sheet mentions submitted product")
        for am in sh.get("amended_by") or []:
            if sh["id"] in picks:
                picks.setdefault(am, f"Amends {sh['number']}")
    return [{"id": k, "reason": v, "locked": k == sid} for k, v in picks.items()]
