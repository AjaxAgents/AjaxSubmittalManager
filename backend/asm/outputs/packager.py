"""Assembler: builds the A/E-ready submittal PDF (spec V2 section 9).

Order (default): cover / transmittal, TOC (more than one item), spec excerpt (when Division 01 or the
reviewer asks), deviation summary (when deviations are declared), product data grouped per product in spec
article order, shop drawings in sheet order. Markups are applied by the deterministic MarkupEngine after the
pages are placed; review stamp is an editable, unsigned markup on the cover.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from ..config import markup_cfg, settings_cfg
from ..pdf import cover_template as ctpl
from ..pdf import pages as gen
from ..pdf.markup import MarkupEngine
from ..schemas import MarkupInstruction, SubmittalMap

log = logging.getLogger("asm.package")


@dataclass
class PackageResult:
    path: Path
    page_count: int
    src_to_out: dict[int, int]
    toc: list[dict[str, Any]]
    applied: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    format_decisions: list[str] = field(default_factory=list)
    deviation_page: int | None = None
    marked_pages: list[int] = field(default_factory=list)


def _article_key(a: str) -> tuple:
    nums = re.findall(r"\d+", a or "")
    return tuple(int(n) for n in nums) if nums else (99,)


def plan_content_order(smap: SubmittalMap, n_pages: int, reorder: bool = True) -> tuple[list[int], list[dict[str, Any]], str]:
    """Return (ordered source pages 1-based, groups [{label, pages}], decision text)."""
    by_page = {p.page: p for p in smap.pages}
    groups: list[dict[str, Any]] = []
    lead = [p for p in range(1, n_pages + 1) if by_page.get(p) and by_page[p].kind in ("cover", "transmittal", "toc")]
    blanks = {p for p in range(1, n_pages + 1) if by_page.get(p) and by_page[p].kind == "blank"}
    products = [pr for pr in smap.products if pr.pages]
    contiguous = all(sorted(pr.pages) == list(range(min(pr.pages), max(pr.pages) + 1)) for pr in products)
    used: set[int] = set(lead) | blanks
    decision = ""
    if reorder and products and contiguous:
        ordered_products = sorted(products, key=lambda pr: (_article_key(pr.spec_article_guess), min(pr.pages)))
        if [p.id for p in ordered_products] != [p.id for p in sorted(products, key=lambda x: min(x.pages))]:
            decision = "Product data regrouped in specification article order."
    else:
        ordered_products = sorted(products, key=lambda pr: min(pr.pages)) if products else []
        if products and not contiguous:
            decision = "Submitted page order kept (product pages are interleaved)."
    order: list[int] = []
    if lead:
        groups.append({"label": "Subcontractor transmittal", "pages": lead})
        order += lead
    shop: list[int] = []
    for pr in ordered_products:
        pg = [p for p in sorted(pr.pages) if p not in used and 1 <= p <= n_pages]
        if not pg:
            continue
        if all(by_page.get(p) and by_page[p].kind == "shop_drawing" for p in pg):
            shop += pg
            used.update(pg)
            continue
        label = f"{pr.name}" + (f" - {pr.manufacturer} {pr.model}".rstrip() if (pr.manufacturer or pr.model) else "")
        groups.append({"label": label, "pages": pg, "product": pr.id})
        order += pg
        used.update(pg)
    rest = [p for p in range(1, n_pages + 1) if p not in used]
    shop += [p for p in rest if by_page.get(p) and by_page[p].kind == "shop_drawing"]
    others = [p for p in rest if p not in shop]
    if others:
        groups.append({"label": "Additional product data", "pages": others})
        order += others
    if shop:
        groups.append({"label": "Shop drawings", "pages": shop})
        order += shop
    if blanks:
        decision = (decision + " " if decision else "") + f"Removed {len(blanks)} blank page(s): {sorted(blanks)}."
    return order, groups, decision


def build_package(*, out_path: Path, submittal_path: Path, smap: SubmittalMap, cover_info: dict[str, Any],
                  markups: list[MarkupInstruction], deviations: list[dict[str, Any]],
                  spec_excerpt: tuple[Path, int, int, str] | None = None, include_toc: bool = True,
                  metadata: dict[str, str] | None = None, cover_template: dict[str, Any] | None = None) -> PackageResult:
    """cover_template: {"path", "mapping", "include_generated"} - the company cover page becomes page 1 and its
    boxes are filled by rule; the generated transmittal follows only if include_generated."""
    src = pymupdf.open(submittal_path)
    n = src.page_count
    reorder = bool(settings_cfg().get("output", {}).get("reorder_products", True))
    order, groups, decision = plan_content_order(smap, n, reorder)
    warnings: list[str] = []
    fmt: list[str] = []
    if decision:
        fmt.append(decision)

    excerpt_pages = (spec_excerpt[2] - spec_excerpt[1] + 1) if spec_excerpt else 0
    use_toc = include_toc and len(groups) > 1
    sub_no = cover_info.get("submittal_no", "")

    def front_matter(doc: pymupdf.Document, src_to_out: dict[int, int], counts: dict[str, int]) -> dict[str, Any]:
        """Render cover/TOC/excerpt/deviations; returns page counts and stamp position."""
        toc_entries: list[dict[str, Any]] = [{"label": "Cover / Transmittal", "page": 1, "level": 0}]
        pno = 1 + counts.get("cover", 1)
        if use_toc:
            toc_entries.append({"label": "Table of Contents", "page": pno, "level": 0})
            pno += counts.get("toc", 1)
        if spec_excerpt:
            toc_entries.append({"label": f"Specification Section {spec_excerpt[3]} (excerpt)", "page": pno, "level": 0})
            pno += excerpt_pages
        dev_page = None
        if deviations:
            dev_page = pno
            toc_entries.append({"label": "Deviation Summary", "page": pno, "level": 0})
            pno += counts.get("dev", 1)
        for g in groups:
            toc_entries.append({"label": g["label"], "page": src_to_out.get(g["pages"][0], 0), "level": 0})
        info = dict(cover_info)
        info["items"] = [{"label": g["label"], "page": src_to_out.get(g["pages"][0], 0)} for g in groups]
        info["deviations_statement"] = (f"Deviations listed on page {dev_page} ({len(deviations)} item(s)); clouded on the referenced pages."
                                        if deviations else "No deviations.")
        if cover_template:
            start = doc.page_count
            with pymupdf.open(cover_template["path"]) as tdoc:
                doc.insert_pdf(tdoc, annots=True, widgets=True)
            n_tpl = doc.page_count - start
            full = {**info, "page_count": pno - 1 + len(order)}
            tpl_notes = ctpl.fill(doc, n_tpl, cover_template.get("mapping") or {}, ctpl.values_from(full), full)
            cv = {"pages": n_tpl, "stamp_page": 0, "stamp_y": None, "notes": tpl_notes}
            if cover_template.get("include_generated"):
                g = gen.cover_sheet(doc, info)
                cv = {"pages": n_tpl + g["pages"], "stamp_page": g["stamp_page"], "stamp_y": g["stamp_y"], "notes": tpl_notes}
        else:
            cv = gen.cover_sheet(doc, info)
        n_toc = gen.table_of_contents(doc, toc_entries, sub_no) if use_toc else 0
        if spec_excerpt:
            with pymupdf.open(spec_excerpt[0]) as sdoc:
                doc.insert_pdf(sdoc, from_page=spec_excerpt[1] - 1, to_page=spec_excerpt[2] - 1, annots=False)
        n_dev = 0
        if deviations:
            rows = []
            for d in deviations:
                pg = [src_to_out.get(p) for p in d.get("pages", []) if src_to_out.get(p)]
                rows.append({**d, "page": ", ".join(str(x) for x in pg[:3])})
            n_dev = gen.deviation_summary(doc, rows, sub_no)
        return {"cover": cv["pages"], "toc": n_toc, "dev": n_dev, "stamp_page": cv["stamp_page"],
                "stamp_y": cv["stamp_y"], "toc_entries": toc_entries, "dev_page": dev_page,
                "tpl_notes": cv.get("notes", [])}

    # pass 1: measure front matter with provisional numbers; pass 2: render for real
    scratch = pymupdf.open()
    provisional = {sp: 900 + i for i, sp in enumerate(order)}
    m = front_matter(scratch, provisional, {})
    scratch.close()
    front = m["cover"] + m["toc"] + excerpt_pages + m["dev"]
    src_to_out = {sp: front + i + 1 for i, sp in enumerate(order)}  # 1-based both
    out = pymupdf.open()
    fm = front_matter(out, src_to_out, m)
    if out.page_count != front:  # numbers changed a wrap -> rebuild once with the new counts
        out.close()
        front = fm["cover"] + fm["toc"] + excerpt_pages + fm["dev"]
        src_to_out = {sp: front + i + 1 for i, sp in enumerate(order)}
        out = pymupdf.open()
        fm = front_matter(out, src_to_out, fm)
    toc_entries, dev_page = fm["toc_entries"], fm["dev_page"]
    for sp in order:
        out.insert_pdf(src, from_page=sp - 1, to_page=sp - 1, annots=True, links=True)
    src.close()
    assert out.page_count == front + len(order), "page accounting mismatch"

    # --- markups
    eng = MarkupEngine(out)
    applied: list[dict[str, Any]] = []
    marked: set[int] = set()
    seen: set[tuple] = set()
    for m in markups:
        if m.page not in src_to_out:
            warnings.append(f"Markup {m.id} references removed page {m.page}; skipped.")
            continue
        key = (m.page, m.type, m.anchor_text.strip().lower(), m.layer, m.subject)
        if key in seen and m.anchor_text:
            continue
        seen.add(key)
        mi = m.model_copy(update={"page": src_to_out[m.page]})
        try:
            a = eng.apply(mi)
            applied.append({"id": a.id, "page": a.page + 1, "type": a.type, "subject": a.subject, "layer": a.layer,
                            "anchored": a.anchored, "text": a.text, "warning": a.warning, "finding_ref": m.finding_ref,
                            "src_page": m.page})
            if a.layer == "ae":
                marked.add(a.page)
            if a.warning:
                warnings.append(f"{a.id} p.{a.page + 1}: {a.warning} ({m.anchor_text[:40]})")
        except Exception as exc:
            warnings.append(f"Markup {m.id} failed: {exc}")
    # review stamp on the last cover page, in the reserved zone under the cover content
    if fm["stamp_y"] is None:  # company cover template: nearest whitespace to the bottom-right corner
        tp = out[fm["stamp_page"]]
        corner = pymupdf.Rect(tp.rect.width - 2, tp.rect.height - 2, tp.rect.width - 1, tp.rect.height - 1)
        slot = eng._free_slot(tp, corner, 258, 100)
        stamp_rect = [slot.x0, slot.y0, slot.x1, slot.y1]
    else:
        sy = min(fm["stamp_y"] + 6, 792 - 60 - 100)
        stamp_rect = [42, sy, 300, sy + 100]
    warnings += [f"Cover template: {n}" for n in fm.get("tpl_notes", [])]
    eng.apply(MarkupInstruction(id="STAMP-01", page=fm["stamp_page"] + 1, type="stamp", subject="review_stamp",
                                rect=stamp_rect, text=markup_cfg().get("stamp_text", "")))

    # --- bookmarks, labels, metadata
    toc = [[1, e["label"], e["page"]] for e in toc_entries]
    out.set_toc(toc)
    labels = [{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}]
    if order:
        labels.append({"startpage": front, "prefix": "", "style": "D", "firstpagenum": 1})
    try:
        out.set_page_labels(labels)
    except Exception as exc:  # labels are nice-to-have
        warnings.append(f"page labels not set: {exc}")
    meta = {"producer": "Ajax Submittal Manager", "creator": "Ajax Submittal Manager"}
    meta.update(metadata or {})
    out.set_metadata(meta)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, garbage=3, deflate=True, clean=False)
    out.close()
    _enforce_size(out_path, warnings)
    with pymupdf.open(out_path) as chk:
        count = chk.page_count
    return PackageResult(out_path, count, src_to_out, toc_entries, applied, warnings, fmt, dev_page, sorted(marked))


def _enforce_size(path: Path, warnings: list[str]) -> None:
    cfg = settings_cfg().get("output", {})
    max_mb = float(cfg.get("max_file_mb", 50))
    if path.stat().st_size <= max_mb * 1024 * 1024:
        return
    try:
        doc = pymupdf.open(path)
        target = int(cfg.get("min_image_dpi", 150))
        doc.rewrite_images(dpi_threshold=target + 50, dpi_target=target, quality=80)
        tmp = path.with_suffix(".tmp.pdf")
        doc.save(tmp, garbage=4, deflate=True)
        doc.close()
        tmp.replace(path)
        warnings.append(f"Images re-compressed to {target} dpi to stay under {max_mb:.0f} MB.")
    except Exception as exc:
        warnings.append(f"Output exceeds {max_mb:.0f} MB and could not be compressed: {exc}")
