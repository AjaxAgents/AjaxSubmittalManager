"""Project library ingest: split spec books by section / part / article, drawings by sheet, register
amendments, summarize, and index everything in the SQLite FTS catalog (spec V2 section 6).

Ingest is idempotent per file hash. Text-layer extraction first, OCR when a file has no text, and an
optional Claude Haiku pass for summaries and drawing title blocks when an API key is configured.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pymupdf

from .. import db
from ..config import api_key, mock_mode, models_cfg, settings_cfg
from ..pdf.util import ocr_pdf, render_png

log = logging.getLogger("asm.ingest")

SEC_NUM = r"(\d{2}\s?\d{2}\s?\d{2}(?:\.\d{2})?)"
RE_SECTION = re.compile(rf"^\s*SECTION\s+{SEC_NUM}\b\s*[-–—:.]?\s*(.*)$", re.I)
RE_END = re.compile(r"END\s+OF\s+SECTION", re.I)
RE_PART = re.compile(r"^\s*PART\s+([1-3])\b\s*[-–—.:]?\s*(.*)$", re.I)
RE_ARTICLE = re.compile(r"^\s*(\d{1,2}\.\d{1,2})\s+([A-Z][A-Z0-9 ,/&\-\(\)'\.]{2,})\s*$")
RE_FOOTER = re.compile(rf"\b{SEC_NUM}\s*[-–]\s*(\d{{1,3}})\s*$", re.M)
RE_SECREF = re.compile(r"\b(\d{2} \d{2} \d{2}(?:\.\d{2})?)\b")
RE_STANDARD = re.compile(
    r"\b(ASTM\s?[A-Z]\s?\d{1,5}(?:/[A-Z]\d+)?M?|ANSI(?:/BHMA)?\s?A\d{2,3}(?:\.\d{1,3})?|BHMA\s?A\d{3}\.\d{1,2}|"
    r"UL\s?\d{2,4}[A-Z]?|NFPA\s?\d{1,4}|IEEE\s?\d{2,5}|NEMA\s?[A-Z]{1,3}\s?\d{0,4}|ASHRAE\s?\d{2,3}(?:\.\d)?|"
    r"AWS\s?D\d\.\d|AISC\s?\d{3}|ACI\s?\d{3}(?:\.\d)?|SMACNA|AAMA\s?\d{3,4}|NFRC\s?\d{3}|ICC[- ]ES\s?\w+|"
    r"FM\s?\d{4}|CSA\s?[A-Z]\d+)\b", re.I)
RE_SHEET = re.compile(r"^([A-Z]{1,3})\s?[-.]?\s?(\d{1,3}(?:\.\d{1,3})?[A-Z]?)$")

DIVISIONS = {
    "00": "Procurement and Contracting", "01": "General Requirements", "02": "Existing Conditions",
    "03": "Concrete", "04": "Masonry", "05": "Metals", "06": "Wood, Plastics, and Composites",
    "07": "Thermal and Moisture Protection", "08": "Openings", "09": "Finishes", "10": "Specialties",
    "11": "Equipment", "12": "Furnishings", "13": "Special Construction", "14": "Conveying Equipment",
    "21": "Fire Suppression", "22": "Plumbing", "23": "HVAC", "25": "Integrated Automation",
    "26": "Electrical", "27": "Communications", "28": "Electronic Safety and Security", "31": "Earthwork",
    "32": "Exterior Improvements", "33": "Utilities", "34": "Transportation", "35": "Waterway and Marine",
    "40": "Process Interconnections", "41": "Material Processing and Handling", "42": "Process Heating, Cooling, and Drying",
    "43": "Process Gas and Liquid Handling", "44": "Pollution and Waste Control", "46": "Water and Wastewater",
    "48": "Electrical Power Generation",
}
DISCIPLINES = {
    "G": "General", "GI": "General", "C": "Civil", "L": "Landscape", "S": "Structural", "A": "Architectural",
    "AD": "Architectural", "ID": "Interiors", "I": "Interiors", "Q": "Equipment", "F": "Fire Protection",
    "FP": "Fire Protection", "FA": "Fire Alarm", "P": "Plumbing", "PL": "Plumbing", "M": "Mechanical",
    "H": "Mechanical", "E": "Electrical", "EP": "Electrical", "EL": "Electrical", "T": "Telecommunications",
    "TC": "Telecommunications", "TY": "Technology", "LV": "Low Voltage", "SEC": "Security", "V": "Survey",
    "D": "Demolition", "X": "Other",
}


def has_section_header(text: str, n_lines: int = 15) -> bool:
    return any(RE_SECTION.match(l) for l in text.splitlines()[:n_lines])


def norm_section(num: str) -> str:
    digits = re.sub(r"\D", "", num.split(".")[0])
    out = f"{digits[0:2]} {digits[2:4]} {digits[4:6]}" if len(digits) >= 6 else num.strip()
    if "." in num:
        out += "." + num.split(".")[1][:2]
    return out


SMALL_WORDS = {"and", "or", "of", "for", "the", "a", "an", "in", "on", "to", "with", "at", "by"}


def smart_title(s: str) -> str:
    """'HOLLOW METAL DOORS AND FRAMES' -> 'Hollow Metal Doors and Frames' (keeps acronyms like HVAC short words)."""
    if not s.isupper():
        return s
    out = []
    for i, w in enumerate(s.split()):
        lw = w.lower()
        if i and lw in SMALL_WORDS:
            out.append(lw)
        elif len(w) <= 4 and w.isalpha() and w in {"HVAC", "BAS", "DDC", "UPS", "LED", "HM", "PVC", "CMU", "EIFS", "ACT", "AV", "IT"}:
            out.append(w)
        else:
            out.append(w[:1] + w[1:].lower() if "/" not in w else "/".join(x.capitalize() for x in w.split("/")))
    return " ".join(out)


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _standards(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in RE_STANDARD.finditer(text):
        seen[re.sub(r"\s+", " ", m.group(1).upper())] = None
    return list(seen)[:40]


def _sort_key(kind: str, number: str, extra: str = "") -> str:
    order = {"spec_section": "1", "spec_part": "1", "spec_article": "1", "drawing_sheet": "2", "amendment": "3", "document": "4"}
    return f"{order.get(kind, '9')}|{number}|{extra}"


# ----------------------------------------------------------------------------- file classification

def classify(path: Path, filename: str) -> str:
    name = filename.lower()
    if re.search(r"addend|\basi\b|asi[-_ ]?\d|bulletin|\brfi\b|rfi[-_ ]?\d|\bccd\b|change directive|\bpr[-_ ]?\d", name):
        return "amendment"
    with pymupdf.open(path) as doc:
        n = doc.page_count
        sample = list(range(min(n, 40)))
        sec_hits = footer_hits = large = 0
        for i in sample:
            page = doc[i]
            if max(page.rect.width, page.rect.height) >= 1000:
                large += 1
            txt = page.get_text("text")
            if has_section_header(txt):
                sec_hits += 1
            if RE_FOOTER.search(txt[-400:]):
                footer_hits += 1
        if sample and large / len(sample) > 0.5:
            return "drawing"
        if sec_hits >= 1 or footer_hits / max(1, len(sample)) > 0.3:
            return "spec"
    if re.search(r"spec|project manual", name):
        return "spec"
    if re.search(r"drawing|sheet|dwg|plans?\b", name):
        return "drawing"
    return "document"


# ----------------------------------------------------------------------------- spec splitting

def split_spec(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """Return sections: {number, title, page_start, page_end (1-based), pages:[(pno, text)], parts, articles}."""
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    page_texts = [doc[i].get_text("text") for i in range(doc.page_count)]
    explicit = any(has_section_header(t) for t in page_texts)

    for pno, text in enumerate(page_texts):
        lines = [l.rstrip() for l in text.splitlines()]
        start = None
        if explicit:
            for idx, line in enumerate(lines[:15]):
                m = RE_SECTION.match(line)
                if m:
                    title = m.group(2).strip(" -–—:")
                    if not title:
                        for nxt in lines[idx + 1: idx + 4]:
                            if nxt.strip() and not RE_PART.match(nxt):
                                title = nxt.strip()
                                break
                    start = (norm_section(m.group(1)), title)
                    break
        else:
            fm = RE_FOOTER.findall(text[-400:])
            if fm:
                num = norm_section(fm[-1][0])
                if cur is None or cur["number"] != num:
                    start = (num, "")
        if start and (cur is None or cur["number"] != start[0] or cur.get("ended")):
            cur = {"number": start[0], "title": smart_title(start[1]),
                   "page_start": pno + 1, "page_end": pno + 1, "pages": [], "ended": False}
            sections.append(cur)
        if cur is None:
            continue  # front matter before the first section (TOC etc.)
        if cur.get("ended") and not start:
            continue
        cur["pages"].append((pno + 1, text))
        cur["page_end"] = pno + 1
        if RE_END.search(text):
            cur["ended"] = True

    for sec in sections:
        _split_parts_articles(sec)
        if not sec["title"]:
            sec["title"] = f"Section {sec['number']}"
    return sections


def _split_parts_articles(sec: dict[str, Any]) -> None:
    parts: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    cur_part: dict[str, Any] | None = None
    cur_art: dict[str, Any] | None = None
    for pno, text in sec["pages"]:
        for line in text.splitlines():
            mp = RE_PART.match(line)
            if mp:
                cur_part = {"num": mp.group(1), "title": (mp.group(2) or "").strip().title() or
                            {"1": "General", "2": "Products", "3": "Execution"}[mp.group(1)],
                            "page_start": pno, "page_end": pno, "lines": []}
                parts.append(cur_part)
                cur_art = None
                continue
            ma = RE_ARTICLE.match(line)
            if ma and (cur_part is None or ma.group(1).split(".")[0] == cur_part["num"]):
                cur_art = {"num": ma.group(1), "title": smart_title(ma.group(2).strip()), "page_start": pno,
                           "page_end": pno, "lines": [f"[p.{pno}]", line.strip()], "part": cur_part["num"] if cur_part else ma.group(1).split(".")[0]}
                articles.append(cur_art)
                if cur_part:
                    cur_part["lines"].append(line)
                continue
            if cur_part is not None:
                cur_part["lines"].append(line)
                cur_part["page_end"] = pno
            if cur_art is not None:
                if cur_art["page_end"] != pno:
                    cur_art["lines"].append(f"[p.{pno}]")
                cur_art["lines"].append(line)
                cur_art["page_end"] = pno
    sec["parts"] = parts
    sec["articles"] = articles


def _section_text(sec: dict[str, Any]) -> str:
    return "\n".join(f"[p.{p}]\n{t.strip()}" for p, t in sec["pages"])


def _heuristic_summary(sec: dict[str, Any]) -> str:
    for art in sec.get("articles", []):
        if "SUMMARY" in art["title"].upper():
            body = " ".join(l.strip() for l in art["lines"][2:] if l.strip() and not l.startswith("[p."))
            return body[:320]
    body = " ".join(_section_text(sec).split())
    return body[:240]


# ----------------------------------------------------------------------------- drawing sheets

def detect_sheet(page: pymupdf.Page) -> dict[str, Any]:
    """Heuristic title-block read: sheet number = sheet-number-like span, scored toward the bottom-right
    and larger fonts; title = largest other text in the title-block region."""
    W, H = page.rect.width, page.rect.height
    best = None
    spans = []
    try:
        d = page.get_text("dict")
    except Exception:
        d = {"blocks": []}
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                t = sp.get("text", "").strip()
                if not t:
                    continue
                x0, y0, x1, y1 = sp["bbox"]
                spans.append((t, sp.get("size", 0), x0, y0, x1, y1))
                m = RE_SHEET.match(t)
                if m and len(t) <= 9:
                    score = sp.get("size", 0) * 2 + (x0 / W) * 30 + (y0 / H) * 30
                    if best is None or score > best[0]:
                        best = (score, f"{m.group(1)}-{m.group(2)}", sp.get("size", 0), (x0, y0, x1, y1))
    number = best[1] if best else ""
    title = ""
    if best:
        bx0, by0 = best[3][0], best[3][1]
        cands = [s for s in spans if s[2] > W * 0.55 and s[3] > H * 0.55 and not RE_SHEET.match(s[0])
                 and len(s[0]) > 3 and not re.search(r"sheet|project|date|scale|drawn|checked|no\.|rev", s[0], re.I)]
        cands.sort(key=lambda s: (-s[1], abs(s[3] - by0)))
        if cands:
            title = cands[0][0]
    prefix = re.match(r"[A-Z]+", number).group(0) if number else ""
    return {"number": number, "title": smart_title(title),
            "discipline": DISCIPLINES.get(prefix, DISCIPLINES.get(prefix[:1], "Other")) if prefix else "Other"}


# ----------------------------------------------------------------------------- AI summaries (optional)

def ai_enabled() -> bool:
    return bool(api_key()) and not mock_mode() and bool(settings_cfg().get("library", {}).get("summarize_with_ai", True))


async def _ai_summaries(project_id: str, items: list[dict[str, Any]], doc_by_file: dict[str, pymupdf.Document],
                        progress: Callable[[float], None]) -> None:
    """Haiku summary per section / sheet. Failures fall back silently to heuristic summaries."""
    from ..agents.base import image_block, run_agent, text_block
    from ..agents.llm import CostTracker
    from ..config import prompt
    from ..schemas import CatalogSummary, SheetInfo

    tracker = CostTracker.for_job(None, project_id, usd_cap=50.0)
    sem = asyncio.Semaphore(int(models_cfg().get("limits", {}).get("max_parallel_catalog_calls", 6)))
    targets = [it for it in items if it["kind"] in ("spec_section", "drawing_sheet", "amendment")]
    done = 0

    async def one(it: dict[str, Any]) -> None:
        nonlocal done
        async with sem:
            try:
                if it["kind"] == "drawing_sheet":
                    doc = doc_by_file[it["file_id"]]
                    png = render_png(doc, it["page_start"] - 1, dpi=60, max_px=1400)
                    content = [image_block(base64.b64encode(png).decode()),
                               text_block(f"Extracted text (may be partial):\n{it['text'][:4000]}\n\n"
                                          f"Heuristic sheet number: {it['number'] or 'unknown'}")]
                    run = await run_agent(stage="catalog", tracker=tracker, system=prompt("catalog_sheet"),
                                          user_content=content, result_model=SheetInfo, max_turns=3)
                    r = run.result
                    if r.sheet_number:
                        it["number"] = r.sheet_number.strip()
                    it["title"] = r.title or it["title"]
                    it["discipline"] = r.discipline or it["discipline"]
                    it["summary"] = r.summary
                    it["keywords"] = list(dict.fromkeys((it.get("keywords") or []) + r.keywords + r.schedules))[:40]
                    it.setdefault("meta", {})["schedules"] = r.schedules
                    it["meta"]["revision"] = r.revision
                else:
                    content = [text_block(f"Catalog item: {it['number']} {it['title']}\n\n{it['text'][:48000]}")]
                    run = await run_agent(stage="catalog", tracker=tracker, system=prompt("catalog_section"),
                                          user_content=content, result_model=CatalogSummary, max_turns=3)
                    r = run.result
                    it["summary"] = r.summary
                    if r.title and not it.get("title"):
                        it["title"] = r.title
                    it["keywords"] = list(dict.fromkeys((it.get("keywords") or []) + r.keywords))[:40]
                    refs = [norm_section(x) for x in r.cross_references if RE_SECREF.search(x)]
                    it["xrefs"] = sorted(set((it.get("xrefs") or []) + refs) - {it["number"]})
            except Exception as exc:
                log.warning("AI summary failed for %s: %s", it["id"], exc)
            finally:
                done += 1
                progress(done / max(1, len(targets)))

    await asyncio.gather(*(one(it) for it in targets))


# ----------------------------------------------------------------------------- main entry

def ingest_file(project_id: str, file_id: str, on_progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Parse + catalog one library file. Safe to call again (replaces this file's catalog rows)."""
    f = db.get_library_file(file_id)
    if not f:
        raise ValueError("library file not found")

    def prog(p: float, msg: str) -> None:
        db.update_library_file(file_id, progress=round(p, 3), status="processing", notes=msg)
        if on_progress:
            on_progress(p, msg)

    path = Path(f["path"])
    prog(0.02, "Opening")
    doc = pymupdf.open(path)
    try:
        # OCR scanned files once, then re-open the OCR'd copy
        no_text = sum(1 for i in range(min(doc.page_count, 30)) if len(doc[i].get_text("text").strip()) < 20)
        if doc.page_count and no_text / min(doc.page_count, 30) > 0.25 and settings_cfg().get("library", {}).get("ocr_if_no_text", True):
            prog(0.05, "Running OCR on scanned pages")
            out = path.with_name(path.stem + ".ocr.pdf")
            ok, how = ocr_pdf(path, out)
            if ok:
                doc.close()
                path = out
                doc = pymupdf.open(path)
                db.update_library_file(file_id, path=str(path))
            else:
                db.update_library_file(file_id, error=how)

        kind = f["kind"]
        if kind == "auto":
            kind = classify(path, f["filename"])
            db.update_library_file(file_id, kind=kind)
        db.delete_catalog_for_file(project_id, file_id)
        items: list[dict[str, Any]] = []
        prog(0.15, f"Splitting ({kind})")

        if kind == "spec":
            items += _spec_items(project_id, file_id, doc)
        elif kind == "drawing":
            items += _drawing_items(project_id, file_id, doc)
        elif kind == "amendment":
            items += _amendment_items(project_id, file_id, f["filename"], doc)
        else:
            items += _document_items(project_id, file_id, f["filename"], doc)

        if ai_enabled() and items:
            prog(0.35, "Summarizing with Claude (Haiku)")
            asyncio.run(_ai_summaries(project_id, items, {file_id: doc},
                                      lambda p: prog(0.35 + 0.55 * p, "Summarizing with Claude (Haiku)")))
        for it in items:
            it.setdefault("token_est", est_tokens(it.get("text", "")))
        prog(0.92, "Indexing")
        db.upsert_catalog(items)
        relink_amendments(project_id)
        n_sections = sum(1 for i in items if i["kind"] == "spec_section")
        n_sheets = sum(1 for i in items if i["kind"] == "drawing_sheet")
        note = (f"{n_sections} spec sections" if kind == "spec" else
                f"{n_sheets} sheets" if kind == "drawing" else f"{len(items)} item(s)")
        db.update_library_file(file_id, status="ready", progress=1.0, notes=note, kind=kind)
        return {"kind": kind, "items": len(items), "note": note}
    except Exception as exc:
        log.exception("ingest failed")
        db.update_library_file(file_id, status="error", error=str(exc)[:500])
        raise
    finally:
        doc.close()


def _spec_items(project_id: str, file_id: str, doc: pymupdf.Document) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sec in split_spec(doc):
        text = _section_text(sec)
        sid = f"S:{sec['number']}"
        xrefs = sorted(set(norm_section(x) for x in RE_SECREF.findall(text)) - {sec["number"]})
        items.append({
            "id": sid, "project_id": project_id, "kind": "spec_section", "number": sec["number"],
            "title": sec["title"], "parent_id": f"DIV:{sec['number'][:2]}", "file_id": file_id,
            "page_start": sec["page_start"], "page_end": sec["page_end"],
            "summary": _heuristic_summary(sec), "keywords": _standards(text), "xrefs": xrefs,
            "discipline": DIVISIONS.get(sec["number"][:2], ""), "text": text,
            "sort_key": _sort_key("spec_section", sec["number"]), "meta": {"ended": sec.get("ended", False)},
        })
        for part in sec["parts"]:
            ptext = "\n".join(part["lines"])
            items.append({
                "id": f"{sid}/P{part['num']}", "project_id": project_id, "kind": "spec_part",
                "number": f"{sec['number']} PART {part['num']}", "title": part["title"], "parent_id": sid,
                "file_id": file_id, "page_start": part["page_start"], "page_end": part["page_end"],
                "summary": "", "keywords": [], "text": ptext,
                "sort_key": _sort_key("spec_part", sec["number"], part["num"]),
            })
        for art in sec["articles"]:
            atext = "\n".join(art["lines"])
            items.append({
                "id": f"{sid}/{art['num']}", "project_id": project_id, "kind": "spec_article",
                "number": f"{sec['number']} {art['num']}", "title": art["title"],
                "parent_id": f"{sid}/P{art['part']}", "file_id": file_id,
                "page_start": art["page_start"], "page_end": art["page_end"], "summary": "",
                "keywords": _standards(atext), "text": atext,
                "sort_key": _sort_key("spec_article", sec["number"], art["num"].zfill(5)),
            })
    return items


def _drawing_items(project_id: str, file_id: str, doc: pymupdf.Document) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: dict[str, int] = defaultdict(int)
    for pno in range(doc.page_count):
        page = doc[pno]
        info = detect_sheet(page)
        number = info["number"] or f"Sheet p{pno + 1}"
        seen[number] += 1
        if seen[number] > 1:
            number = f"{number} ({seen[number]})"
        text = page.get_text("text")
        sec_refs = sorted(set(norm_section(x) for x in RE_SECREF.findall(text)))
        items.append({
            "id": f"D:{number}", "project_id": project_id, "kind": "drawing_sheet", "number": number,
            "title": info["title"] or "", "parent_id": f"DISC:{info['discipline']}", "file_id": file_id,
            "page_start": pno + 1, "page_end": pno + 1, "summary": " ".join(text.split())[:200],
            "keywords": _standards(text) + [s for s in re.findall(r"[A-Z][A-Z ]{3,} SCHEDULE", text)][:10],
            "xrefs": sec_refs, "discipline": info["discipline"], "text": text[:12000],
            "token_est": 1800, "sort_key": _sort_key("drawing_sheet", number),
        })
    return items


def _amendment_items(project_id: str, file_id: str, filename: str, doc: pymupdf.Document) -> list[dict[str, Any]]:
    text = "\n".join(f"[p.{i + 1}]\n{doc[i].get_text('text')}" for i in range(doc.page_count))
    stem = Path(filename).stem
    m = re.search(r"(addendum|asi|rfi|bulletin|ccd|pr)\s*[-_#no.]*\s*(\d+)", stem, re.I)
    label = f"{m.group(1).upper() if m.group(1).lower() in ('asi', 'rfi', 'ccd', 'pr') else m.group(1).title()} {m.group(2)}" if m else stem
    date = ""
    dm = re.search(r"(?:date[d]?:?\s*)(\w+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})", text, re.I)
    if dm:
        date = dm.group(1)
    targets = sorted(set(norm_section(x) for x in RE_SECREF.findall(text)))
    sheets = sorted(set(f"{a}-{b}" for a, b in re.findall(r"\b([A-Z]{1,2})[-.]?(\d{3}(?:\.\d{1,2})?)\b", text)))
    return [{
        "id": f"AM:{label}", "project_id": project_id, "kind": "amendment", "number": label,
        "title": f"{label}" + (f" ({date})" if date else ""), "parent_id": "AMENDMENTS", "file_id": file_id,
        "page_start": 1, "page_end": doc.page_count, "summary": " ".join(text.split())[:300],
        "keywords": _standards(text), "xrefs": targets + [f"D:{s}" for s in sheets],
        "text": text[:60000], "sort_key": _sort_key("amendment", label), "meta": {"date": date},
    }]


def _document_items(project_id: str, file_id: str, filename: str, doc: pymupdf.Document) -> list[dict[str, Any]]:
    text = "\n".join(f"[p.{i + 1}]\n{doc[i].get_text('text')}" for i in range(doc.page_count))
    stem = Path(filename).stem
    return [{
        "id": f"DOC:{stem[:60]}", "project_id": project_id, "kind": "document", "number": stem[:60],
        "title": stem, "parent_id": "DOCUMENTS", "file_id": file_id, "page_start": 1,
        "page_end": doc.page_count, "summary": " ".join(text.split())[:300], "keywords": _standards(text),
        "xrefs": sorted(set(norm_section(x) for x in RE_SECREF.findall(text))), "text": text[:80000],
        "sort_key": _sort_key("document", stem),
    }]


def relink_amendments(project_id: str) -> None:
    """Tag each section / sheet with the amendments that modify it (amendments outrank base text)."""
    cat = db.list_catalog(project_id)
    by_id = {c["id"]: c for c in cat}
    links: dict[str, list[str]] = defaultdict(list)
    for am in (c for c in cat if c["kind"] == "amendment"):
        for ref in am.get("xrefs") or []:
            target = ref if ref.startswith("D:") else f"S:{ref}"
            if target in by_id:
                links[target].append(am["id"])
    for c in cat:
        if c["kind"] in ("spec_section", "drawing_sheet"):
            new = sorted(set(links.get(c["id"], [])))
            if new != sorted(c.get("amended_by") or []):
                db.set_amended_by(project_id, c["id"], new)
