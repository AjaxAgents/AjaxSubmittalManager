"""Scoped retrieval tools exposed to agents (spec V2 sections 5-6).

Agents see the catalog index only; full content flows through these tools, which enforce the run's
Reference scope:
  * flexible: selected items + up to N extra pages, every extra retrieval logged
  * strict:   selected items only; refusals become Human Review "scope" items
A hard page budget applies to everything sent. Every retrieval is logged (GUI + TXT "Scope checked").
"""
from __future__ import annotations

import base64
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from .. import db
from ..agents.base import Tool, image_block, pdf_block
from ..config import settings_cfg
from ..pdf.util import render_png, slice_pdf_bytes
from .ingest import norm_section


@dataclass
class ScopeItem:
    id: str
    pages: list[int] | None = None   # explicit page subset (source-file page numbers), None = whole item
    reason: str = ""
    locked: bool = False


@dataclass
class ScopeGuard:
    project_id: str
    job_id: str
    items: dict[str, ScopeItem]
    mode: str = "flexible"
    extra_cap: int = 20
    page_budget: int = 150
    pages_served: int = 0
    extra_used: int = 0
    served: dict[str, int] = field(default_factory=dict)
    gaps: list[dict[str, str]] = field(default_factory=list)   # strict-mode refusals / budget hits
    log: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_scope(cls, project_id: str, job_id: str, scope: dict[str, Any]) -> "ScopeGuard":
        s = settings_cfg().get("scope", {})
        items = {}
        for it in scope.get("items", []):
            items[it["id"]] = ScopeItem(it["id"], it.get("pages"), it.get("reason", ""), bool(it.get("locked")))
        return cls(project_id=project_id, job_id=job_id, items=items,
                   mode=scope.get("mode", s.get("default_mode", "flexible")),
                   extra_cap=int(scope.get("extra_pages_cap", s.get("flexible_extra_pages", 20))),
                   page_budget=int(scope.get("page_budget", s.get("page_budget", 150))))

    def scope_ids(self) -> set[str]:
        return set(self.items)

    def covering(self, item_id: str) -> ScopeItem | None:
        """Scope entry covering item_id (itself or an ancestor like the section of an article)."""
        if item_id in self.items:
            return self.items[item_id]
        base = item_id.split("/")[0]
        if base in self.items:
            return self.items[base]
        if "/" in item_id:  # article under an in-scope part
            sec, rest = item_id.split("/", 1)
            part = f"{sec}/P{rest[0]}"
            if part in self.items:
                return self.items[part]
        return None

    def admit(self, stage: str, tool: str, item_id: str, pages: int, reason: str = "") -> tuple[bool, str]:
        """Decide whether content may be sent; account pages; log either way."""
        with self._lock:
            in_scope = self.covering(item_id) is not None
            already = item_id in self.served
            cost = 0 if already else pages
            allowed, why = True, ""
            if self.pages_served + cost > self.page_budget:
                allowed, why = False, f"page budget of {self.page_budget} reached"
            elif not in_scope:
                if self.mode == "strict":
                    allowed, why = False, "outside selected Reference scope (strict mode)"
                elif self.extra_used + cost > self.extra_cap:
                    allowed, why = False, f"flexible extra-page cap of {self.extra_cap} reached"
            if allowed:
                self.pages_served += cost
                if not in_scope:
                    self.extra_used += cost
                self.served[item_id] = self.served.get(item_id, 0) + 1
            else:
                self.gaps.append({"item": item_id, "why": why, "reason": reason, "stage": stage})
            entry = {"stage": stage, "tool": tool, "item": item_id, "pages": pages, "in_scope": in_scope,
                     "allowed": allowed, "reason": reason or why}
            self.log.append(entry)
        try:
            db.log_retrieval(self.job_id, stage, tool, item_id, pages, pages * 800, in_scope, allowed, reason or why)
        except Exception:
            pass
        return allowed, why


def _filter_pages(text: str, pages: list[int] | None) -> str:
    """Keep only [p.N] blocks whose page is in `pages`."""
    if not pages:
        return text
    keep = set(pages)
    out, cur_ok = [], True
    for line in text.splitlines():
        m = re.match(r"^\[p\.(\d+)\]$", line.strip())
        if m:
            cur_ok = int(m.group(1)) in keep
        if cur_ok:
            out.append(line)
    return "\n".join(out)


class LibraryTools:
    """Factory for the retrieval tools of one stage."""

    def __init__(self, guard: ScopeGuard, stage: str, submittal_path: Path | None = None,
                 submittal_pages: int = 0):
        self.g = guard
        self.stage = stage
        self.project_id = guard.project_id
        self.submittal_path = submittal_path
        self.submittal_pages = submittal_pages
        s = settings_cfg().get("render", {})
        self.sheet_dpi = int(s.get("sheet_dpi", 100))
        self.crop_dpi = int(s.get("crop_dpi", 250))
        self.max_px = int(s.get("max_image_px", 1568))

    # ------------------------------------------------------------------ tools

    def catalog_search(self, args: dict[str, Any]) -> str:
        kinds = args.get("kinds") or None
        res = db.search_catalog(self.project_id, args.get("query", ""), kinds, limit=int(args.get("limit", 12)))
        if not res:
            return "No catalog matches."
        lines = []
        for r in res:
            tag = "IN SCOPE" if self.g.covering(r["id"]) else "out of scope"
            pages = max(1, (r.get("page_end") or 0) - (r.get("page_start") or 0) + 1)
            lines.append(f"[{r['id']}] {r['number']} {r['title']} | {pages}pp | {tag} | {(r.get('summary') or '')[:120]}")
        return "\n".join(lines)

    def _resolve_spec(self, section: str, part: str = "", article: str = "") -> dict | None:
        sec = norm_section(section)
        if article:
            art = article.strip().upper()
            m = re.match(r"(\d{1,2}\.\d{1,2})", art)
            if m:
                it = db.get_catalog_item(self.project_id, f"S:{sec}/{m.group(1)}")
                if it:
                    return it
        if part:
            pm = re.search(r"([1-3])", str(part))
            if pm:
                it = db.get_catalog_item(self.project_id, f"S:{sec}/P{pm.group(1)}")
                if it:
                    return it
        return db.get_catalog_item(self.project_id, f"S:{sec}")

    def get_spec(self, args: dict[str, Any]) -> Any:
        item = self._resolve_spec(args.get("section", ""), args.get("part", ""), args.get("article", ""))
        if not item:
            return f"Section {args.get('section')} is not in the project library. Say so in your findings if it matters."
        cov = self.g.covering(item["id"])
        pages = max(1, item["page_end"] - item["page_start"] + 1)
        trimmed = cov.pages if cov and cov.pages else None
        if trimmed:
            pages = len([p for p in trimmed if item["page_start"] <= p <= item["page_end"]]) or 1
        ok, why = self.g.admit(self.stage, "get_spec", item["id"], pages, args.get("reason", ""))
        if not ok:
            return (f"REFUSED: {item['number']} {item['title']} is {why}. If this content is needed to decide a "
                    f"requirement, mark it human_review and state: 'Needs {item['number']} to confirm; {why}'.")
        full = db.get_catalog_item(self.project_id, item["id"])
        text = _filter_pages(full.get("text", ""), trimmed)
        header = f"{item['number']} {item['title']} (spec book pages {item['page_start']}-{item['page_end']})"
        if full.get("amended_by"):
            header += f"\nNOTE: amended by {', '.join(full['amended_by'])} - call get_amendments before relying on this text."
        if args.get("as_pdf"):
            f = db.get_library_file(item["file_id"])
            pg = [p - 1 for p in range(item["page_start"], item["page_end"] + 1) if not trimmed or p in trimmed]
            data = slice_pdf_bytes(f["path"], pg[:20])
            return [{"type": "text", "text": header}, pdf_block(base64.b64encode(data).decode(), title=header[:200])]
        return f"{header}\n<document_content>\n{text}\n</document_content>"

    def get_sheet(self, args: dict[str, Any]) -> Any:
        num = str(args.get("sheet_number", "")).strip().upper().replace(" ", "")
        item = db.get_catalog_item(self.project_id, f"D:{num}")
        if not item:
            hits = [c for c in db.list_catalog(self.project_id, ["drawing_sheet"])
                    if c["number"].replace("-", "").replace(".", "") == num.replace("-", "").replace(".", "")]
            item = hits[0] if hits else None
        if not item:
            return f"Sheet {num} is not in the project library."
        region = args.get("region")
        ok, why = self.g.admit(self.stage, "get_sheet", item["id"], 1, args.get("reason", ""))
        if not ok:
            return f"REFUSED: sheet {item['number']} is {why}. Mark dependent requirements human_review if needed."
        f = db.get_library_file(item["file_id"])
        with pymupdf.open(f["path"]) as doc:
            page = doc[item["page_start"] - 1]
            clip = None
            dpi = self.sheet_dpi
            if region and isinstance(region, list) and len(region) == 4:
                vr = page.rect
                clip = pymupdf.Rect(region[0] * vr.width, region[1] * vr.height, region[2] * vr.width, region[3] * vr.height)
                if page.rotation:
                    clip = clip * page.derotation_matrix
                    clip.normalize()
                dpi = self.crop_dpi
            png = render_png(doc, page.number, dpi=dpi, clip=clip, max_px=self.max_px, annots=False)
            text = page.get_text("text", clip=clip)[:6000]
        cap = f"Sheet {item['number']} {item['title']}" + (f" region {region}" if region else " (full sheet)")
        if item.get("amended_by"):
            cap += f" | amended by {', '.join(item['amended_by'])}"
        return [{"type": "text", "text": cap + "\nExtracted text:\n" + text}, image_block(base64.b64encode(png).decode())]

    def get_amendments(self, args: dict[str, Any]) -> str:
        target = str(args.get("target", "")).strip()
        tid = target if ":" in target else (f"S:{norm_section(target)}" if re.match(r"\d{2}\s?\d{2}\s?\d{2}", target) else f"D:{target.upper()}")
        item = db.get_catalog_item(self.project_id, tid)
        ams = item.get("amended_by") if item else []
        if not ams:
            return f"No registered amendments affect {target}."
        out = []
        for am_id in ams:
            am = db.get_catalog_item(self.project_id, am_id)
            if not am:
                continue
            ok, why = self.g.admit(self.stage, "get_amendments", am_id, max(1, am["page_end"] - am["page_start"] + 1),
                                   args.get("reason", ""))
            if not ok:
                out.append(f"[{am_id}] REFUSED ({why}). Amendment exists - treat affected requirements as human_review.")
                continue
            out.append(f"[{am_id}] {am['title']}\n<document_content>\n{am.get('text', '')[:20000]}\n</document_content>")
        return "\n\n".join(out)

    def view_submittal_pages(self, args: dict[str, Any]) -> Any:
        if not self.submittal_path:
            return "Submittal not available."
        pages = [int(p) for p in (args.get("pages") or [])][:15]
        pages = [p for p in pages if 1 <= p <= self.submittal_pages]
        if not pages:
            return f"Give 1-15 page numbers between 1 and {self.submittal_pages}."
        if args.get("region") and len(pages) == 1:
            with pymupdf.open(self.submittal_path) as doc:
                page = doc[pages[0] - 1]
                r = args["region"]
                vr = page.rect
                clip = pymupdf.Rect(r[0] * vr.width, r[1] * vr.height, r[2] * vr.width, r[3] * vr.height)
                if page.rotation:
                    clip = clip * page.derotation_matrix
                    clip.normalize()
                png = render_png(doc, page.number, dpi=self.crop_dpi, clip=clip, max_px=self.max_px, annots=False)
            return [{"type": "text", "text": f"Submittal p.{pages[0]} region {r} (zoomed)"},
                    image_block(base64.b64encode(png).decode())]
        data = slice_pdf_bytes(self.submittal_path, [p - 1 for p in pages])
        return [{"type": "text", "text": f"Submittal pages {pages} (in order)"},
                pdf_block(base64.b64encode(data).decode(), title=f"Submittal pages {pages}")]

    # ------------------------------------------------------------------ tool specs

    def tools(self, include_submittal_viewer: bool = True) -> list[Tool]:
        reason = {"type": "string", "description": "why you need it (logged for the reviewer)"}
        t = [
            Tool("catalog_search", "Search the project library catalog. Returns ids, titles and one-line summaries only.",
                 {"type": "object", "properties": {
                     "query": {"type": "string"},
                     "kinds": {"type": "array", "items": {"type": "string", "enum": ["spec_section", "spec_article", "drawing_sheet", "amendment", "document"]}},
                     "limit": {"type": "integer"}}, "required": ["query"]}, self.catalog_search),
            Tool("get_spec", "Full text of a spec section, part or article from the project library. Prefer article-level requests. Out-of-scope requests may be refused.",
                 {"type": "object", "properties": {
                     "section": {"type": "string", "description": "CSI number, e.g. '08 71 00'"},
                     "part": {"type": "string", "description": "1, 2 or 3 (optional)"},
                     "article": {"type": "string", "description": "e.g. '2.3' (optional)"},
                     "as_pdf": {"type": "boolean", "description": "true only if you must see tables/graphics as rendered"},
                     "reason": reason}, "required": ["section", "reason"]}, self.get_spec),
            Tool("get_sheet", "Image + extracted text of a drawing sheet. Use `region` [x0,y0,x1,y1] (fractions, top-left origin) to zoom into a schedule or detail at high resolution.",
                 {"type": "object", "properties": {
                     "sheet_number": {"type": "string", "description": "e.g. 'A-501'"},
                     "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                     "reason": reason}, "required": ["sheet_number", "reason"]}, self.get_sheet),
            Tool("get_amendments", "Text of addenda / ASIs / RFIs that modify a section or sheet. Amendments outrank base documents.",
                 {"type": "object", "properties": {"target": {"type": "string", "description": "'08 71 00' or 'A-501'"},
                                                   "reason": reason}, "required": ["target"]}, self.get_amendments),
        ]
        if include_submittal_viewer and self.submittal_path:
            t.append(Tool("view_submittal_pages",
                          "View submittal pages as PDF (up to 15), or one page `region` zoomed for small print.",
                          {"type": "object", "properties": {
                              "pages": {"type": "array", "items": {"type": "integer"}},
                              "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
                           "required": ["pages"]}, self.view_submittal_pages))
        return t
