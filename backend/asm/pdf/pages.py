"""Generated pages: cover sheet / transmittal, table of contents, deviation summary, dividers.

Drawn directly with PyMuPDF (real, searchable text; exact column widths) in a restrained Ajax style:
a thin orange accent bar, asphalt text, light-grey rules.
"""
from __future__ import annotations

from typing import Any

import pymupdf

LETTER = (612, 792)
M = 42  # side margin
ORANGE = (1, 0.322, 0)
TEXT = (0.137, 0.153, 0.161)
MUTED = (0.416, 0.463, 0.486)
RULE = (0.839, 0.855, 0.863)
SHADE = (0.961, 0.965, 0.969)
RED = (0.839, 0.188, 0.173)
GREEN = (0.122, 0.541, 0.298)
F, FB = "helv", "hebo"


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def wrap_text(text: str, width: float, size: float, font: str = F) -> list[str]:
    lines: list[str] = []
    for para in _s(text).split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if pymupdf.get_text_length(trial, fontname=font, fontsize=size) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                while pymupdf.get_text_length(w, fontname=font, fontsize=size) > width and len(w) > 1:
                    cut = len(w)
                    while cut > 1 and pymupdf.get_text_length(w[:cut], fontname=font, fontsize=size) > width:
                        cut -= 1
                    lines.append(w[:cut])
                    w = w[cut:]
                cur = w
        lines.append(cur)
    return lines or [""]


class Canvas:
    """Tiny layout helper with automatic page breaks."""

    def __init__(self, doc: pymupdf.Document, footer: str, header: str = ""):
        self.doc, self.footer, self.header = doc, footer, header
        self.page: pymupdf.Page = None  # type: ignore[assignment]
        self.y = 0.0
        self.first: pymupdf.Page | None = None
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=LETTER[0], height=LETTER[1])
        if self.first is None:
            self.first = self.page
        W, H = LETTER
        self.page.draw_rect(pymupdf.Rect(0, 0, W, 6), color=None, fill=ORANGE)
        self.page.draw_line((M, H - 40), (W - M, H - 40), color=RULE, width=0.6)
        self.page.insert_text((M, H - 27), self.footer, fontsize=7.5, fontname=F, color=MUTED)
        self.page.insert_text((W - M - 70, H - 27), "Ajax Submittal Manager", fontsize=7.5, fontname=F, color=MUTED)
        self.y = 36

    def ensure(self, h: float, bottom: float = 60) -> None:
        if self.y + h > LETTER[1] - bottom:
            self.new_page()

    def text(self, s: str, size: float = 9.5, font: str = F, color=TEXT, gap: float = 3, x: float = M,
             width: float | None = None) -> None:
        width = width or (LETTER[0] - M - x)
        for line in wrap_text(s, width, size, font):
            self.ensure(size * 1.3)
            self.page.insert_text((x, self.y + size), line, fontsize=size, fontname=font, color=color)
            self.y += size * 1.3
        self.y += gap

    def heading(self, s: str) -> None:
        self.ensure(30)
        self.y += 8
        self.page.insert_text((M, self.y + 11), s, fontsize=11, fontname=FB, color=TEXT)
        self.y += 15
        self.page.draw_line((M, self.y), (LETTER[0] - M, self.y), color=RULE, width=0.8)
        self.y += 5

    def table(self, widths: list[float], rows: list[list[str]], header: list[str] | None = None,
              size: float = 9, shade_first_col: bool = False, fonts: list[str] | None = None,
              colors: list[Any] | None = None, align_right: set[int] | None = None) -> None:
        align_right = align_right or set()
        pad = 4
        total = sum(widths)
        scale = (LETTER[0] - 2 * M) / total
        widths = [w * scale for w in widths]

        def draw_row(cells: list[str], bold: bool = False, fill=None, row_fonts=None, row_colors=None) -> None:
            wrapped = []
            for i, c in enumerate(cells):
                fn = FB if bold else (row_fonts[i] if row_fonts else F)
                wrapped.append((wrap_text(c, widths[i] - 2 * pad, size, fn), fn))
            h = max(len(w[0]) for w in wrapped) * size * 1.25 + 2 * pad
            self.ensure(h)
            x = M
            for i, (lines, fn) in enumerate(wrapped):
                r = pymupdf.Rect(x, self.y, x + widths[i], self.y + h)
                f = fill if fill else (SHADE if (shade_first_col and i == 0) else None)
                self.page.draw_rect(r, color=RULE, fill=f, width=0.6)
                ty = self.y + pad + size
                col = (row_colors[i] if row_colors and row_colors[i] else TEXT) if not (shade_first_col and i == 0) else MUTED
                for line in lines:
                    tx = x + pad
                    if i in align_right:
                        tx = x + widths[i] - pad - pymupdf.get_text_length(line, fontname=fn, fontsize=size)
                    self.page.insert_text((tx, ty), line, fontsize=size, fontname=fn, color=col)
                    ty += size * 1.25
                x += widths[i]
            self.y += h

        if header:
            draw_row(header, bold=True, fill=SHADE)
        for row in rows:
            draw_row(row, row_fonts=fonts, row_colors=colors)
        self.y += 4

    def title_block(self, brand: str, title: str, sub: str) -> None:
        self.page.insert_text((M, self.y + 8), brand, fontsize=7.5, fontname=F, color=MUTED)
        self.y += 14
        for line in wrap_text(title, LETTER[0] - 2 * M, 18, FB):
            self.page.insert_text((M, self.y + 18), line, fontsize=18, fontname=FB, color=TEXT)
            self.y += 22
        self.page.insert_text((M, self.y + 10), sub, fontsize=9.5, fontname=F, color=MUTED)
        self.y += 18


STAMP_ZONE_H = 120


def cover_sheet(doc: pymupdf.Document, info: dict[str, Any]) -> dict[str, Any]:
    """Returns {"pages": n, "stamp_page": 0-based index, "stamp_y": y where the review-stamp zone starts}."""
    start = doc.page_count
    c = Canvas(doc, f"{_s(info.get('submittal_no'))}  |  Cover / Transmittal")
    c.title_block("AJAX BUILDING COMPANY  |  SUBMITTAL TRANSMITTAL",
                  f"{_s(info.get('spec_section'))}  {_s(info.get('spec_title'))}",
                  f"Submittal {_s(info.get('submittal_no'))}  ·  Rev {_s(info.get('revision', '0'))}  ·  {_s(info.get('submittal_type'))}".replace("·", "-"))
    c.heading("Submittal Information")
    proj = _s(info.get("project_name")) + (f"   (No. {info.get('project_number')})" if info.get("project_number") else "")
    rows = [
        ["Project", proj],
        ["Architect / Engineer", _s(info.get("ae_firm")) or "-"],
        ["Contractor", _s(info.get("contractor")) or "-"],
        ["Submittal No. / Revision", f"{_s(info.get('submittal_no'))}   /   Rev {_s(info.get('revision', '0'))}"],
        ["Specification Section", f"{_s(info.get('spec_section'))}  {_s(info.get('spec_title'))}"],
        ["Submittal Type", _s(info.get("submittal_type"))],
        ["Description", _s(info.get("title") or info.get("spec_title"))],
        ["Subcontractor / Supplier", _s(info.get("supplier")) or "-"],
        ["Manufacturer(s)", ", ".join(info.get("manufacturers") or []) or "-"],
        ["Date Prepared", _s(info.get("date_prepared"))],
        ["Review Requested By", _s(info.get("required_date")) or "-"],
    ]
    c.table([30, 70], rows, shade_first_col=True)
    c.heading("Items Included")
    items = info.get("items") or []
    c.table([6, 82, 12], [[str(i + 1), _s(it.get("label")), _s(it.get("page"))] for i, it in enumerate(items)] or [["", "-", ""]],
            header=["#", "Item", "Page"], align_right={2})
    c.heading("Deviations")
    dev = _s(info.get("deviations_statement")) or "No deviations."
    c.text(dev, size=10, font=FB, color=GREEN if dev.lower().startswith("no deviation") else RED)
    for x in info.get("extra_lines") or []:
        c.text(_s(x), size=8, color=MUTED)
    # reserve the stamp zone on the current (last) cover page
    c.ensure(STAMP_ZONE_H + 20)
    c.heading("Contractor's Review")
    c.text("Review stamp block below is an editable markup, intentionally left unsigned and undated for the responsible reviewer.",
           size=7.5, color=MUTED)
    return {"pages": doc.page_count - start, "stamp_page": c.page.number, "stamp_y": c.y}


def table_of_contents(doc: pymupdf.Document, entries: list[dict[str, Any]], submittal_no: str = "") -> int:
    start = doc.page_count
    c = Canvas(doc, f"{submittal_no}  |  Table of Contents")
    c.title_block("AJAX BUILDING COMPANY", "Table of Contents", f"Submittal {submittal_no}")
    c.heading("Contents")
    c.table([86, 14], [[("    " * e.get("level", 0)) + _s(e["label"]), _s(e["page"])] for e in entries],
            header=["Item", "Page"], align_right={1})
    return doc.page_count - start


def deviation_summary(doc: pymupdf.Document, items: list[dict[str, Any]], submittal_no: str = "") -> int:
    start = doc.page_count
    c = Canvas(doc, f"{submittal_no}  |  Deviation Summary")
    c.title_block("AJAX BUILDING COMPANY", "Deviation Summary",
                  f"Submittal {submittal_no} - items that deviate from the Contract Documents, for A/E review")
    c.heading("Declared Deviations")
    c.table([10, 56, 20, 14], [[_s(i["id"]), _s(i["description"]), _s(i.get("spec_ref", "")), _s(i.get("page", ""))] for i in items],
            header=["ID", "Deviation", "Spec Ref.", "Page"], align_right={3})
    c.text("Each deviation is clouded in red on the referenced page(s).", size=8, color=MUTED)
    return doc.page_count - start


def divider(doc: pymupdf.Document, title: str, subtitle: str = "", submittal_no: str = "") -> pymupdf.Page:
    c = Canvas(doc, f"{submittal_no}  |  {title}")
    c.y = 300
    c.title_block("AJAX BUILDING COMPANY", title, subtitle)
    return c.first  # type: ignore[return-value]
