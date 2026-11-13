"""Cover page templates: find the fillable boxes in a company cover/transmittal PDF and fill them by
hard-coded rules (no AI) from the New Submittal Review form.

Supported "boxes":
  * AcroForm text fields (widgets)      - matched to data by field name
  * FreeText annotations (Bluebeam text boxes) - matched by their current text
  * {Token} placeholders typed anywhere in a field or text box, e.g. "Spec: {SpecSection} {SpecTitle}"
Fields stay editable after filling (nothing is flattened).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pymupdf

# token -> label shown in the mapping dropdown
TOKENS: dict[str, str] = {
    "ProjectName": "Project name", "ProjectNumber": "Project number", "Architect": "Architect / Engineer",
    "Contractor": "Contractor", "Owner": "Owner", "Address": "Project address",
    "SubmittalNo": "Submittal number (08 71 00 - 01.0)", "SpecSection": "Spec section", "SpecTitle": "Spec section title",
    "Seq": "Sequence no.", "Rev": "Revision", "Type": "Submittal type (Product Data, Shop Drawings, ...)",
    "TypeAbbr": "Submittal type abbreviation (PD, SD, PD-SD)",
    "Title": "Title / description", "Subcontractor": "Subcontractor name", "SubcontractorAddress": "Subcontractor street address",
    "SubcontractorCity": "Subcontractor city, state ZIP (Hickory, NC 28601)", "SubcontractorContact": "Subcontractor contact",
    "SubcontractorPhone": "Subcontractor phone", "SubcontractorEmail": "Subcontractor email",
    "Manufacturers": "Manufacturer(s)", "DatePrepared": "Date prepared (today)",
    "RequiredDate": "Date needed by (today + N days)", "Deviations": "Deviations statement",
    "ItemsIncluded": "Items included", "PageCount": "Total pages", "PreparedBy": "Prepared by (your name)",
}
# groups for the mapping dropdown
TOKEN_GROUPS: list[tuple[str, list[str]]] = [
    ("Project", ["ProjectName", "ProjectNumber", "Address", "Owner", "Architect", "Contractor"]),
    ("Submittal", ["SubmittalNo", "SpecSection", "SpecTitle", "Title", "Type", "TypeAbbr", "Seq", "Rev", "Manufacturers",
                   "ItemsIncluded", "Deviations", "PageCount", "PreparedBy"]),
    ("Subcontractor", ["Subcontractor", "SubcontractorAddress", "SubcontractorCity", "SubcontractorContact",
                       "SubcontractorPhone", "SubcontractorEmail"]),
    ("Dates", ["DatePrepared", "RequiredDate"]),
]
DEFAULT_NEEDED_DAYS = 14
RE_DAYS = re.compile(r"^RequiredDate\+(\d{1,3})$")

# ordered keyword rules: first match wins. Each rule = (all-of keywords, token)
RULES: list[tuple[tuple[str, ...], str]] = [
    (("project", "name"), "ProjectName"), (("project", "no"), "ProjectNumber"), (("project", "number"), "ProjectNumber"),
    (("project", "#"), "ProjectNumber"), (("job", "no"), "ProjectNumber"), (("job", "number"), "ProjectNumber"),
    (("submittal", "no"), "SubmittalNo"), (("submittal", "number"), "SubmittalNo"), (("submittal", "#"), "SubmittalNo"),
    (("spec", "title"), "SpecTitle"), (("section", "title"), "SpecTitle"), (("spec",), "SpecSection"),
    (("section",), "SpecSection"), (("rev",), "Rev"), (("architect",), "Architect"), (("engineer",), "Architect"),
    (("a/e",), "Architect"),
    (("subcontractor", "address"), "SubcontractorAddress"), (("subcontractor", "city"), "SubcontractorCity"),
    (("subcontractor", "contact"), "SubcontractorContact"), (("subcontractor", "phone"), "SubcontractorPhone"),
    (("subcontractor",), "Subcontractor"), (("supplier",), "Subcontractor"),
    (("sub", "address"), "SubcontractorAddress"), (("subcontractor", "address"), "SubcontractorAddress"),
    (("vendor", "address"), "SubcontractorAddress"), (("supplier", "address"), "SubcontractorAddress"),
    (("sub", "city"), "SubcontractorCity"), (("subcontractor", "city"), "SubcontractorCity"), (("city",), "SubcontractorCity"),
    (("zip",), "SubcontractorCity"), (("sub", "contact"), "SubcontractorContact"), (("sub", "phone"), "SubcontractorPhone"),
    (("phone",), "SubcontractorPhone"), (("email",), "SubcontractorEmail"),
    (("vendor",), "Subcontractor"), (("sub",), "Subcontractor"), (("contractor",), "Contractor"), (("gc",), "Contractor"),
    (("owner",), "Owner"), (("manufacturer",), "Manufacturers"), (("mfr",), "Manufacturers"),
    (("date", "needed"), "RequiredDate"), (("needed", "by"), "RequiredDate"), (("date", "required"), "RequiredDate"), (("date", "due"), "RequiredDate"), (("return", "by"), "RequiredDate"),
    (("requested", "by"), "RequiredDate"), (("prepared", "by"), "PreparedBy"), (("submitted", "by"), "PreparedBy"),
    (("date",), "DatePrepared"), (("deviation",), "Deviations"), (("items",), "ItemsIncluded"),
    (("contents",), "ItemsIncluded"), (("type",), "Type"), (("description",), "Title"), (("title",), "Title"),
    (("address",), "Address"), (("location",), "Address"), (("pages",), "PageCount"),
]

RE_TOKEN = re.compile(r"\{(\w+)\}")


def _norm(s: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return " " + re.sub(r"[_\-.:]+", " ", s).lower() + " "


def guess_token(name: str) -> str:
    n = _norm(name)
    for keys, tok in RULES:
        if all((f" {k} " in n) if k.isalpha() and len(k) <= 3 else (k in n) for k in keys):
            return tok
    return ""


def analyze(path: Path | str) -> dict[str, Any]:
    """List fillable boxes on the template and guess a mapping for each."""
    fields: list[dict[str, Any]] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            for w in page.widgets() or []:
                if w.field_type not in (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX):
                    continue
                key = f"w:{w.field_name}"
                if any(f["key"] == key for f in fields):
                    continue
                cur = w.field_value or ""
                label = w.field_label or w.field_name or ""
                tok = "@template" if RE_TOKEN.search(cur) else guess_token(f"{w.field_name} {label}")
                fields.append({"key": key, "kind": "field", "name": w.field_name, "label": label, "page": page.number + 1,
                               "rect": [round(v, 1) for v in w.rect], "current": cur, "mapping": tok})
            for a in page.annots(types=[pymupdf.PDF_ANNOT_FREE_TEXT]) or []:
                txt = (a.info.get("content") or "").strip()
                nm = doc.xref_get_key(a.xref, "NM")[1].strip("()") or str(a.xref)
                if RE_TOKEN.search(txt):
                    tok = "@template"
                elif txt.endswith(":") or not txt:
                    tok = ""  # a label like "Project:" - leave it unless the user maps it
                else:
                    tok = ""
                fields.append({"key": f"a:{nm}", "kind": "textbox", "name": nm, "label": txt[:60], "page": page.number + 1,
                               "rect": [round(v, 1) for v in a.rect], "current": txt, "mapping": tok})
        pages = doc.page_count
    has_tokens_in_text = False
    with pymupdf.open(path) as doc:
        has_tokens_in_text = any(RE_TOKEN.search(p.get_text("text")) for p in doc)
    return {"fields": fields, "pages": pages, "placeholders_in_page_text": has_tokens_in_text}


def _fmt_date(v: Any, fmt: str) -> str:
    if isinstance(v, (date, datetime)):
        return v.strftime(fmt)
    v = str(v or "").strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(v[:16] if "T" in v else v, f).strftime(fmt)
        except ValueError:
            continue
    return v


def city_line(city: str, state: str, zip_: str) -> str:
    """'Hickory', 'NC', '28601' -> 'Hickory, NC 28601'."""
    tail = " ".join(x for x in ((state or "").strip(), (zip_ or "").strip()) if x)
    return ", ".join(x for x in ((city or "").strip(), tail) if x)


def needed_by(info: dict[str, Any], days: int | None = None, fmt: str = "%m/%d/%Y") -> str:
    """The review form's date if one was entered, otherwise today + `days`."""
    if info.get("required_date"):
        return _fmt_date(info["required_date"], fmt)
    d = DEFAULT_NEEDED_DAYS if days is None else days
    return (date.today() + timedelta(days=d)).strftime(fmt)


def values_from(info: dict[str, Any]) -> dict[str, str]:
    """Token values from the cover info the packager builds."""
    items = info.get("items") or []
    fmt = info.get("date_format") or "%m/%d/%Y"
    sub = info.get("sub") or {}
    return {
        "ProjectName": info.get("project_name") or "", "ProjectNumber": info.get("project_number") or "",
        "Architect": info.get("ae_firm") or "", "Contractor": info.get("contractor") or "",
        "Owner": info.get("owner") or "", "Address": info.get("address") or "",
        "SubmittalNo": info.get("submittal_no") or "", "SpecSection": info.get("spec_section") or "",
        "SpecTitle": info.get("spec_title") or "", "Seq": str(info.get("seq") or ""), "Rev": str(info.get("revision") or "0"),
        "Type": info.get("submittal_type") or "", "TypeAbbr": info.get("type_abbr") or "",
        "Title": info.get("title") or info.get("spec_title") or "", "Subcontractor": info.get("supplier") or "",
        "SubcontractorAddress": sub.get("address") or "",
        "SubcontractorCity": city_line(sub.get("city", ""), sub.get("state", ""), sub.get("zip", "")),
        "SubcontractorContact": sub.get("contact") or "", "SubcontractorPhone": sub.get("phone") or "",
        "SubcontractorEmail": sub.get("email") or "",
        "Manufacturers": ", ".join(info.get("manufacturers") or []),
        "DatePrepared": _fmt_date(info.get("date_prepared") or date.today(), fmt),
        "RequiredDate": needed_by(info, info.get("needed_days"), fmt), "Deviations": info.get("deviations_statement") or "",
        "ItemsIncluded": "; ".join(f"{i + 1}. {it.get('label')} (p.{it.get('page')})" for i, it in enumerate(items)),
        "PageCount": str(info.get("page_count") or ""), "PreparedBy": info.get("prepared_by") or "",
    }


def _resolve(m: str, vals: dict[str, str], info: dict[str, Any] | None) -> str | None:
    """Value for one mapping entry, or None to leave the box alone."""
    md = RE_DAYS.match(m or "")
    if md:
        return needed_by(info or {}, int(md.group(1)), (info or {}).get("date_format") or "%m/%d/%Y")
    if m.startswith("text:"):
        return render_text(m[5:], vals)
    if m in vals:
        return vals[m]
    return None


def render_text(template: str, vals: dict[str, str]) -> str:
    return RE_TOKEN.sub(lambda m: vals.get(m.group(1), m.group(0)), template)


def fill(doc: pymupdf.Document, page_count: int, mapping: dict[str, str], vals: dict[str, str],
         info: dict[str, Any] | None = None) -> list[str]:
    """Fill the first `page_count` pages of `doc` (the inserted template) in place. Returns notes."""
    notes: list[str] = []
    for pno in range(min(page_count, doc.page_count)):
        page = doc[pno]
        for w in page.widgets() or []:
            key = f"w:{w.field_name}"
            m = mapping.get(key, "")
            cur = w.field_value or ""
            if m == "@template" or (not m and RE_TOKEN.search(cur)):
                new = render_text(cur, vals)
            else:
                new = _resolve(m, vals, info)
                if new is None:
                    continue
            try:
                w.field_value = new
                w.update()
            except Exception as exc:
                notes.append(f"Field {w.field_name}: {exc}")
        for a in page.annots(types=[pymupdf.PDF_ANNOT_FREE_TEXT]) or []:
            nm = doc.xref_get_key(a.xref, "NM")[1].strip("()") or str(a.xref)
            m = mapping.get(f"a:{nm}", "")
            cur = a.info.get("content") or ""
            if m == "@template" or (not m and RE_TOKEN.search(cur)):
                new = render_text(cur, vals)
            else:
                new = _resolve(m, vals, info)
                if new is None:
                    continue
            try:
                a.set_info(content=new)
                a.update()
            except Exception as exc:
                notes.append(f"Text box {nm}: {exc}")
        # {Token} typed directly into the page text: erase just the token, then write the value in its place
        # (inserted as text, so nothing else on the line is touched)
        writes = []
        for tok in set(RE_TOKEN.findall(page.get_text("text"))):
            if tok not in vals:
                continue
            for r in page.search_for("{" + tok + "}"):
                writes.append((r, vals[tok], max(6.0, min(14.0, r.height * 0.78))))
                page.add_redact_annot(r, fill=False)
        if writes:
            try:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=0)
            except TypeError:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
            for r, text, size in writes:
                page.insert_text((r.x0, r.y1 - r.height * 0.22), text, fontsize=size, fontname="helv")
    return notes
