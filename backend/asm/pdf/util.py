"""Small PDF helpers: inspection, hashing, rendering, slicing, OCR."""
from __future__ import annotations

import hashlib
import io
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pymupdf

log = logging.getLogger("asm.pdf")


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_pdf(path: Path | str) -> dict[str, Any]:
    """Validate a PDF and report page count and flags shown on the GUI file chip."""
    flags: list[str] = []
    pages = 0
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # corrupt / not a PDF
        return {"ok": False, "pages": 0, "flags": ["corrupt"], "error": f"Cannot open PDF: {exc}"}
    try:
        if doc.needs_pass or doc.is_encrypted:
            return {"ok": False, "pages": doc.page_count, "flags": ["encrypted"],
                    "error": "PDF is password-protected/encrypted. Remove the password and re-upload."}
        if not doc.is_pdf:
            return {"ok": False, "pages": 0, "flags": ["not_pdf"], "error": "Not a PDF file."}
        pages = doc.page_count
        no_text = 0
        sample = range(pages) if pages <= 60 else range(0, pages, max(1, pages // 40))
        checked = 0
        for i in sample:
            checked += 1
            if len(doc[i].get_text("text").strip()) < 20:
                no_text += 1
        if checked and no_text / checked > 0.25:
            flags.append("ocr_needed")
        if any(doc[i].rotation for i in range(min(pages, 200))):
            flags.append("rotated_pages")
        if any(doc[i].first_annot is not None for i in range(min(pages, 200))):
            flags.append("has_annotations")
        w, h = doc[0].rect.width, doc[0].rect.height
        if max(w, h) >= 1000:
            flags.append("large_format")
    finally:
        doc.close()
    return {"ok": True, "pages": pages, "flags": flags, "error": ""}


def page_text(doc: pymupdf.Document, pno: int) -> str:
    return doc[pno].get_text("text")


def render_png(doc: pymupdf.Document, pno: int, dpi: int = 110, clip: pymupdf.Rect | None = None,
               max_px: int | None = None, annots: bool = True) -> bytes:
    page = doc[pno]
    zoom = dpi / 72
    if max_px:
        r = clip or page.rect
        longest = max(r.width, r.height) * zoom
        if longest > max_px:
            zoom *= max_px / longest
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, annots=annots, alpha=False)
    return pix.tobytes("png")


def slice_pdf_bytes(src: Path | str | pymupdf.Document, pages: list[int]) -> bytes:
    """Return a new PDF (bytes) containing the given 0-based pages."""
    own = not isinstance(src, pymupdf.Document)
    doc = pymupdf.open(src) if own else src
    out = pymupdf.open()
    try:
        for p in pages:
            if 0 <= p < doc.page_count:
                out.insert_pdf(doc, from_page=p, to_page=p, annots=True)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()
        if own:
            doc.close()


def ocr_available() -> str:
    if shutil.which("ocrmypdf"):
        return "ocrmypdf"
    if shutil.which("tesseract"):
        return "tesseract"
    return ""


def ocr_pdf(path: Path, out: Path) -> tuple[bool, str]:
    """Add a text layer to scanned pages. Prefers OCRmyPDF; falls back to PyMuPDF+Tesseract per page."""
    tool = ocr_available()
    if tool == "ocrmypdf":
        try:
            subprocess.run(["ocrmypdf", "--skip-text", "--optimize", "0", str(path), str(out)],
                           check=True, capture_output=True, timeout=1800)
            return True, "ocrmypdf"
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("ocrmypdf failed: %s", exc)
    if tool:
        try:
            doc = pymupdf.open(path)
            result = pymupdf.open()
            for page in doc:
                if len(page.get_text("text").strip()) >= 20:
                    result.insert_pdf(doc, from_page=page.number, to_page=page.number)
                    continue
                pix = page.get_pixmap(dpi=300)
                ocr_bytes = pix.pdfocr_tobytes(compress=True, language="eng")
                one = pymupdf.open("pdf", ocr_bytes)
                # keep the original page size
                newp = result.new_page(width=page.rect.width, height=page.rect.height)
                newp.show_pdf_page(newp.rect, one, 0)
                one.close()
            result.save(out, garbage=3, deflate=True)
            result.close()
            doc.close()
            return True, "tesseract"
        except Exception as exc:  # pragma: no cover
            log.warning("PyMuPDF OCR failed: %s", exc)
            return False, f"OCR failed: {exc}"
    return False, "No OCR engine installed (install OCRmyPDF or Tesseract); scanned pages will rely on Claude vision."
