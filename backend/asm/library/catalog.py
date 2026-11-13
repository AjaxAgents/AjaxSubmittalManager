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


STOPWORDS = set("""about above after again against architect architectural building buildings chapter contractor
contractors company copyright details detail document documents drawing drawings engineer engineering general
information install installation installed issued level manufacturer manufacturers material materials notes
number other owner owners page pages per plans please product products project projects provide provided
required requirements revision revisions schedule section sections sheet sheets shall should specification
specifications submit submittal submittals subcontractor supplier system systems their there these those under
where which while with within without""".split())
MAX_OPTIONAL = 30


def _distinct_keywords(keywords: list[str], sheets: list[dict[str, Any]]) -> list[str]:
    """Keep only words that say something about the product: not boilerplate, and not on most sheets."""
    kws = []
    for k in dict.fromkeys(w.lower() for w in keywords or []):
        if len(k) < 5 or k in STOPWORDS or k.isdigit():
            continue
        kws.append(k)
    if not sheets:
        return kws
    texts = [((sh.get("text") or "") + " " + sh.get("title", "")).lower() for sh in sheets]
    limit = max(3, int(len(texts) * 0.08))
    return [k for k in kws if sum(1 for t in texts if re.search(rf"\b{re.escape(k)}\b", t)) <= limit]


def heuristic_scope(project_id: str, spec_section: str, keywords: list[str] | None = None) -> list[dict[str, Any]]:
    """Cheap, deterministic starting scope used when no API key is set (and as a floor for the AI pass).

    Items the review clearly needs are returned selected. Weaker matches (drawing sheets that only share
    product words with the submittal) come back with optional=True: shown to the reviewer, not ticked.
    """
    sec_num = norm_section(spec_section)
    sid = f"S:{sec_num}"
    sec = db.get_catalog_item(project_id, sid)
    picks: dict[str, str] = {}
    optional: dict[str, tuple[int, str]] = {}
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
    sheets = db.list_catalog(project_id, ["drawing_sheet"], with_text=True)
    kws = _distinct_keywords(keywords or [], sheets)
    for sh in sheets:
        txt = (sh.get("text") or "") + " " + sh.get("title", "")
        if sec_num in txt or sec_num.replace(" ", "") in txt:
            picks.setdefault(sh["id"], f"Sheet references {sec_num}")
        elif kws:
            hits = [k for k in kws if re.search(rf"\b{re.escape(k)}\b", txt, re.I)]
            if hits:
                optional[sh["id"]] = (len(hits), "Mentions " + ", ".join(hits[:3]))
        for am in sh.get("amended_by") or []:
            if sh["id"] in picks:
                picks.setdefault(am, f"Amends {sh['number']}")
    out = [{"id": k, "reason": v, "locked": k == sid} for k, v in picks.items()]
    ranked = sorted(((k, v) for k, v in optional.items() if k not in picks), key=lambda kv: -kv[1][0])[:MAX_OPTIONAL]
    out += [{"id": k, "reason": r, "locked": False, "optional": True} for k, (_, r) in ranked]
    return out
