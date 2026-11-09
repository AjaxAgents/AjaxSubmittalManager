"""Deterministic markup engine.

Agents never touch PDF bytes. They emit MarkupInstruction objects; this engine turns each into
standard PDF annotation objects (with /T author, /Subj subject, /Contents, unique /NM and an /AP
appearance stream) so Bluebeam Revu lists them in the Markups List and a person can select, move,
restyle, edit or delete every one of them.

Coordinates: instructions carry either `anchor_text` (preferred; snapped to real word boxes) or
`rect` as fractions of the *visible* page with a top-left origin. PyMuPDF annotation methods and
text search both work in unrotated page space, so fractional rects are de-rotated here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import pymupdf

from ..config import markup_cfg
from ..schemas import MarkupInstruction

Rect = pymupdf.Rect


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def pdf_date(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("D:%Y%m%d%H%M%S")


@dataclass
class Applied:
    id: str
    page: int
    type: str
    subject: str
    rect: list[float]
    anchored: bool
    layer: str
    text: str = ""
    annot_xrefs: list[int] = field(default_factory=list)
    warning: str = ""


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


class MarkupEngine:
    """Apply markup instructions to an open PyMuPDF document."""

    def __init__(self, doc: pymupdf.Document, styles: dict[str, Any] | None = None, author: str | None = None):
        self.doc = doc
        self.styles = styles or markup_cfg()
        self.author = author or self.styles.get("author", "Ajax ASM")
        self.font = self.styles.get("font", "Helv")
        self.font_size = float(self.styles.get("font_size", 10))
        self._internal_ocg: int | None = None
        self._note_slots: dict[int, list[Rect]] = {}
        self._counter = 0
        self.applied: list[Applied] = []

    # ------------------------------------------------------------------ helpers

    def style(self, subject: str) -> dict[str, Any]:
        subs = self.styles.get("subjects", {})
        return subs.get(subject) or subs.get("note") or {"subject": "Note", "color": "#000000", "width": 1.0}

    def internal_ocg(self) -> int:
        if self._internal_ocg is None:
            name = self.styles.get("internal_layer_name", "ASM Internal Review")
            visible = bool(self.styles.get("internal_layer_visible", False))
            self._internal_ocg = self.doc.add_ocg(name, on=visible)
        return self._internal_ocg

    def next_id(self, prefix: str = "M") -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:03d}"

    def _finish(self, annot: pymupdf.Annot, nm: str, subject: str, contents: str, layer: str,
                color: tuple[float, float, float] | None = None, fill: tuple | None = None,
                opacity: float | None = None, author: str | None = None) -> int:
        """Common annotation metadata + appearance stream."""
        if color is not None or fill is not None:
            try:
                annot.set_colors(stroke=color, fill=fill)
            except Exception:
                pass
        if opacity is not None and opacity < 1:
            annot.set_opacity(opacity)
        annot.set_info(title=author or (self.styles.get("internal_author", self.author) if layer == "internal" else self.author),
                       subject=subject, content=contents,
                       creationDate=pdf_date(), modDate=pdf_date())
        if layer == "internal":
            annot.set_oc(self.internal_ocg())
        annot.update()
        # unique, stable name -> links markup to finding id; re-runs replace instead of duplicating
        self.doc.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(nm))
        return annot.xref

    def page_rect_from_fraction(self, page: pymupdf.Page, frac: Iterable[float]) -> Rect:
        x0, y0, x1, y1 = list(frac)[:4]
        if max(x0, y0, x1, y1) > 1.5:  # caller passed points (visible space)
            r = Rect(x0, y0, x1, y1)
        else:
            vr = page.rect  # visible (rotated) page size
            r = Rect(x0 * vr.width, y0 * vr.height, x1 * vr.width, y1 * vr.height)
        r.normalize()
        if page.rotation:
            r = r * page.derotation_matrix
            r.normalize()
        return r

    def find_text(self, page: pymupdf.Page, text: str, near: Rect | None = None) -> list[pymupdf.Quad]:
        """Find anchor text; tolerant to whitespace/case and partial matches (model numbers etc.)."""
        text = _norm(text)
        if not text:
            return []
        candidates = [text]
        words = text.split(" ")
        if len(words) > 6:
            candidates.append(" ".join(words[:6]))
        # longest "identifier-like" token (model numbers, finish codes) as last resort
        tokens = sorted((w.strip(",.;:()") for w in words), key=len, reverse=True)
        candidates += [t for t in tokens[:3] if len(t) >= 4]
        for cand in candidates:
            hits = page.search_for(cand, quads=True)
            if hits:
                if near is not None and len(hits) > 1:
                    c = pymupdf.Point((near.x0 + near.x1) / 2, (near.y0 + near.y1) / 2)
                    hits.sort(key=lambda q: abs(q.rect.x0 - c.x) + abs(q.rect.y0 - c.y))
                    return [hits[0]]
                # a multi-word phrase that wraps across lines comes back as consecutive quads on adjacent
                # lines, the second starting left of the first; anything else = separate occurrences
                if (cand == text and " " in cand and len(hits) >= 2
                        and 0 <= hits[1].rect.y0 - hits[0].rect.y0 < hits[0].rect.height * 1.8
                        and hits[1].rect.x0 < hits[0].rect.x0):
                    return hits[:2]
                return [hits[0]]
        return []

    def _occupied(self, page: pymupdf.Page) -> list[Rect]:
        """Printed content boxes (text lines, images, vector graphics) in visible space, cached per page."""
        cache = getattr(self, "_occ_cache", None)
        if cache is None:
            cache = self._occ_cache = {}
        if page.number in cache:
            return cache[page.number]
        boxes: list[Rect] = []
        try:
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") == 1:
                    boxes.append(Rect(b["bbox"]))
                for ln in b.get("lines", []):
                    boxes.append(Rect(ln["bbox"]))
        except Exception:
            pass
        try:
            for dr in page.get_drawings()[:400]:
                r = Rect(dr["rect"])
                if r.width * r.height < page.rect.width * page.rect.height * 0.5:  # ignore page frames
                    boxes.append(r)
        except Exception:
            pass
        if page.rotation:
            out = []
            for r in boxes:
                r = r * page.rotation_matrix
                r.normalize()
                out.append(r)
            boxes = out
        cache[page.number] = boxes
        return boxes

    def _free_slot(self, page: pymupdf.Page, target: Rect, w: float, h: float) -> Rect:
        """Find the nearest whitespace for a note box (visible space): avoid printed content, the target
        and previously placed boxes. Falls back to the least-crowded spot."""
        vr = page.rect
        slots = self._note_slots.setdefault(page.number, [])
        t = target
        if page.rotation:
            t = target * page.rotation_matrix
            t.normalize()
        pad = 10
        occ = self._occupied(page)
        cx, cy = (t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2
        best, best_score = None, None
        step = 12
        y = pad
        while y + h <= vr.height - pad:
            x = pad
            while x + w <= vr.width - pad:
                r = Rect(x, y, x + w, y + h)
                if r.intersects(t) or any(r.intersects(s) for s in slots):
                    x += step
                    continue
                overlap = 0.0
                for o in occ:
                    if r.intersects(o):
                        i = Rect(r) & o
                        overlap += i.width * i.height
                dist = abs((x + w / 2) - cx) + abs((y + h / 2) - cy)
                score = overlap * 4 + dist
                if best_score is None or score < best_score:
                    best, best_score = r, score
                x += step
            y += step
        if best is None:
            best = Rect(pad, pad, pad + w, pad + h)
        slots.append(best)
        return best

    def _to_unrotated(self, page: pymupdf.Page, r: Rect) -> Rect:
        if page.rotation:
            r = r * page.derotation_matrix
            r.normalize()
        return r

    @staticmethod
    def _box_size(text: str, fontsize: float, max_w: float = 190) -> tuple[float, float]:
        avg = fontsize * 0.5
        chars_per_line = max(10, int((max_w - 8) / avg))
        lines = 0
        for para in (text or " ").split("\n"):
            lines += max(1, -(-len(para) // chars_per_line))
        longest = min(max(len(p) for p in (text or " ").split("\n")) * avg + 10, max_w)
        return max(longest, 60), lines * fontsize * 1.25 + 8

    # ------------------------------------------------------------------ main entry

    def apply(self, ins: MarkupInstruction, page_offset: int = 0) -> Applied:
        """Apply one instruction. `ins.page` is 1-based within the source; page_offset shifts into the output doc."""
        pno = ins.page - 1 + page_offset
        if pno < 0 or pno >= self.doc.page_count:
            raise ValueError(f"markup page {ins.page} out of range")
        page = self.doc[pno]
        nm = ins.id or self.next_id()
        st = self.style(ins.subject)
        color = hex_rgb(st.get("color", "#000000"))
        width = float(st.get("width", 1.5))
        subject = st.get("subject", ins.subject.title())
        layer = ins.layer
        contents = ins.text or subject

        if ins.type == "stamp":
            hint = self.page_rect_from_fraction(page, ins.rect) if ins.rect else None
            xs = self._review_stamp(page, hint, nm, ins.text)
            res = Applied(nm, pno, "stamp", st.get("subject", "Review Stamp"), list(hint or []), True, "ae",
                          ins.text, xs)
            self.applied.append(res)
            return res

        hint = self.page_rect_from_fraction(page, ins.rect) if ins.rect else None
        quads = self.find_text(page, ins.anchor_text, near=hint) if ins.anchor_text else []
        anchored = bool(quads)
        if quads:
            target = quads[0].rect
            for q in quads[1:]:
                target |= q.rect
        elif hint is not None:
            target = hint
        else:
            target = None
        warning = ""
        if target is None:
            warning = "anchor not found; placed as page note"
            if ins.anchor_text:
                contents = f"{contents} [ref: \"{ins.anchor_text[:60]}\"]"

        xrefs: list[int] = []
        t = ins.type

        if target is None or t == "note":
            # Sticky note: top-right corner (visible), or at the target
            if target is None:
                vr = page.rect
                n = len([a for a in self.applied if a.page == pno and a.type == "note"])
                pt_rect = self._to_unrotated(page, Rect(vr.width - 40, 20 + n * 26, vr.width - 20, 40 + n * 26))
                pt = pt_rect.tl
            else:
                pt = pymupdf.Point(target.x1 + 2, target.y0)
            a = page.add_text_annot(pt, contents, icon="Comment" if ins.subject != "deviation" else "Note")
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color))
            t_out = "note"
            rect_out = list(a.rect)
            # A deviation that cannot be anchored is still worth a visible text box
            if ins.subject in ("deviation", "not_submitted") and ins.text and target is None:
                w, h = self._box_size(ins.text, self.font_size)
                vbox = self._free_slot(page, Rect(page.rect.width - 60, 20, page.rect.width - 20, 60), w, h)
                box = self._to_unrotated(page, vbox)
                fa = page.add_freetext_annot(box, ins.text, fontsize=self.font_size, fontname=self.font,
                                             text_color=color, fill_color=(1, 1, 1),
                                             border_width=1, rotate=page.rotation)
                xrefs.append(self._finish(fa, nm + "-T", subject, ins.text, layer))
            res = Applied(nm, pno, t_out, subject, rect_out, anchored, layer, contents, xrefs, warning)
            self.applied.append(res)
            return res

        if t in ("highlight", "underline", "strikeout"):
            if quads:
                fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                      "strikeout": page.add_strikeout_annot}[t]
                a = fn(quads)
                c = hex_rgb(self.styles.get("highlight_color", "#FFE14D")) if (t == "highlight" and ins.subject == "selection") else color
                xrefs.append(self._finish(a, nm, subject, contents, layer, color=c))
            else:  # image-only content -> transparent rectangle
                a = page.add_rect_annot(target)
                a.set_border(width=width)
                xrefs.append(self._finish(a, nm, subject, contents, layer, color=color,
                                          fill=color if t == "highlight" else None,
                                          opacity=0.3 if t == "highlight" else None))
        elif t in ("rectangle", "ellipse"):
            r = Rect(target.x0 - 3, target.y0 - 3, target.x1 + 3, target.y1 + 3)
            a = page.add_rect_annot(r) if t == "rectangle" else page.add_circle_annot(r)
            a.set_border(width=width)
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color))
        elif t == "cloud":
            r = Rect(target.x0 - 8, target.y0 - 8, target.x1 + 8, target.y1 + 8)
            pts = [r.tl, r.tr, r.br, r.bl, r.tl]
            a = page.add_polygon_annot(pts)
            a.set_border(width=width, clouds=int(round(float(self.styles.get("cloud_intensity", 1)))))
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color))
            # Bluebeam's own cloud intent so Revu shows it as a Cloud markup
            self.doc.xref_set_key(a.xref, "IT", "/PolygonCloud")
            target = r
        elif t in ("arrow", "line"):
            # arrow from free margin point to target
            w, h = 1, 1
            vbox = self._free_slot(page, target, 60, 30)
            start = self._to_unrotated(page, vbox).tl
            end = pymupdf.Point(target.x0, (target.y0 + target.y1) / 2)
            a = page.add_line_annot(start, end)
            a.set_border(width=width)
            if t == "arrow":
                a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color, fill=color))
        elif t == "xout":
            r = target
            ink = [[(r.x0, r.y0), (r.x1, r.y1)], [(r.x0, r.y1), (r.x1, r.y0)]]
            a = page.add_ink_annot(ink)
            a.set_border(width=max(width, 2.0))
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color))
        elif t == "check":
            r = target
            s = min(14.0, max(8.0, r.height))
            x = r.x0 - s - 4
            y = r.y0 + r.height / 2
            ink = [[(x, y), (x + s * 0.35, y + s * 0.4), (x + s, y - s * 0.6)]]
            a = page.add_ink_annot(ink)
            a.set_border(width=2.0)
            xrefs.append(self._finish(a, nm, subject, contents, layer, color=color))
        elif t == "stamp":
            xrefs += self._review_stamp(page, target, nm, ins.text)
        elif t in ("textbox", "callout"):
            pass  # handled below
        else:
            raise ValueError(f"unknown markup type {t}")

        # Text boxes, callouts and the text that accompanies clouds/arrows/highlights on A/E-facing deviations
        wants_text = t in ("textbox", "callout") or (ins.text and t in ("cloud",) )
        if t in ("highlight", "rectangle", "ellipse", "xout") and ins.text and ins.subject in ("deviation", "not_submitted", "note"):
            wants_text = True
        if wants_text and ins.text:
            w, h = self._box_size(ins.text, self.font_size)
            if t == "textbox" and not anchored and ins.rect:
                box = Rect(target.x0, target.y0, max(target.x1, target.x0 + 60), max(target.y1, target.y0 + h))
            else:
                vbox = self._free_slot(page, target, w, h)
                box = self._to_unrotated(page, vbox)
            text_color = color if ins.subject in ("deviation", "not_submitted", "human_review") else (0, 0, 0)
            if t == "textbox":
                fa = page.add_freetext_annot(box, ins.text, fontsize=self.font_size, fontname=self.font,
                                             text_color=text_color, fill_color=(1, 1, 1),
                                             border_width=1, rotate=page.rotation)
            else:
                # callout leader from box edge to target
                bx = box
                tgt = pymupdf.Point(target.x0 if bx.x0 > target.x1 else (target.x1 if bx.x1 < target.x0 else (target.x0 + target.x1) / 2),
                                    (target.y0 + target.y1) / 2)
                knee = pymupdf.Point(bx.x0 if bx.x0 > tgt.x else bx.x1, (bx.y0 + bx.y1) / 2)
                fa = page.add_freetext_annot(box, ins.text, fontsize=self.font_size, fontname=self.font,
                                             text_color=text_color, fill_color=(1, 1, 1),
                                             border_width=1, callout=[tgt, knee, knee],
                                             line_end=pymupdf.PDF_ANNOT_LE_OPEN_ARROW, rotate=page.rotation)
            sfx = "" if t in ("textbox", "callout") else "-T"
            xrefs.append(self._finish(fa, nm + sfx, subject, ins.text, layer))

        res = Applied(nm, pno, t, subject, [round(v, 2) for v in target], anchored, layer, contents, xrefs, warning)
        self.applied.append(res)
        return res

    # ------------------------------------------------------------------ review stamp

    def _review_stamp(self, page: pymupdf.Page, target: Rect | None, nm: str, text: str = "") -> list[int]:
        """Unsigned contractor review stamp: an editable FreeText block (Reviewed by / Date / Signature blank)."""
        st = self.style("review_stamp")
        color = hex_rgb(st.get("color", "#232729"))
        body = text or self.styles.get("stamp_text") or (
            "CONTRACTOR'S REVIEW\n"
            "Reviewed for general conformance with the\n"
            "Contract Documents. Review does not relieve\n"
            "the subcontractor of contract requirements.\n"
            "[ ] No exceptions taken  [ ] Exceptions noted\n"
            "Reviewed by: ______________________\n"
            "Date: ______________  Signature: __________"
        )
        w, h = 250, 11.5 * len(body.split("\n")) + 8
        if target is None or target.is_empty:
            vr = page.rect
            vbox = Rect(vr.width - w - 36, vr.height - h - 36, vr.width - 36, vr.height - 36)
            box = self._to_unrotated(page, vbox)
        else:
            box = Rect(target.x0, target.y0, target.x0 + w, target.y0 + h)
        a = page.add_freetext_annot(box, body, fontsize=9, fontname=self.font, text_color=color,
                                    fill_color=(1, 1, 1), border_width=1.5,
                                    rotate=page.rotation)
        return [self._finish(a, nm, st.get("subject", "Review Stamp"), body, "ae")]


def markup_inventory(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """List every annotation with the fields Bluebeam shows in its Markups List (used by tests and QA)."""
    out = []
    for page in doc:
        for a in page.annots() or []:
            info = a.info
            nm = doc.xref_get_key(a.xref, "NM")[1]
            out.append({
                "page": page.number + 1, "type": a.type[1], "author": info.get("title", ""),
                "subject": info.get("subject", ""), "contents": info.get("content", ""),
                "nm": nm.strip("()"), "has_ap": doc.xref_get_key(a.xref, "AP")[0] != "null",
                "it": doc.xref_get_key(a.xref, "IT")[1], "oc": doc.xref_get_key(a.xref, "OC")[0] != "null",
            })
    return out
