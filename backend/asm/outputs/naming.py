"""Output file naming (spec V2 section 9). Priority: explicit pattern from the reviewer > mimic example
file name (toggle) > Division 01 rule found by the Planner > config/naming.yaml default."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import naming_cfg

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Ajax style: "08 71 00 - 01.0 Door Hardware - PD"
RE_EXAMPLE_AJAX = re.compile(r"^(?P<sec>\d{2} \d{2} \d{2})(?P<s1>\s*-\s*)(?P<seq>\d{1,4})\.(?P<rev>[0-9A-Za-z]{1,3})"
                             r"(?P<s3>\s+)(?P<title>.+?)(?P<s4>\s+-\s+)(?P<type>[A-Za-z][A-Za-z -]*)$")
RE_EXAMPLE = re.compile(r"^(?P<sec>\d{2}[ ._]?\d{2}[ ._]?\d{2})(?P<s1>[-_ .]+)(?P<seq>\d{1,4})(?P<s2>[-_ .]+)(?P<rev>[0-9A-Za-z]{1,3})"
                        r"(?P<s3>\s+)(?P<title>.+?)(?P<s4>\s+-\s+)(?P<type>[A-Za-z ]+)$")


def parse_seq(submittal_no: str, spec_section: str = "") -> tuple[int, str]:
    """'08 71 00-003' / '003' / '3' / '08 71 00-003-1' -> (3, '003')."""
    s = (submittal_no or "").strip()
    if spec_section:
        s = s.replace(spec_section, "").strip(" -_")
    m = re.search(r"(\d+)", s)
    if not m:
        return 1, "001"
    return int(m.group(1)), m.group(1)


def infer_pattern_from_example(filename: str) -> str | None:
    stem = Path(filename).stem
    stem = re.sub(r"^(example|template|sample)\s*[-_ ]\s*", "", stem, flags=re.I)
    m = RE_EXAMPLE_AJAX.match(stem)
    if m:
        width = len(m.group("seq"))
        type_tok = "{TypeAbbr}" if m.group("type").strip().upper() in {v.upper() for v in _abbr().values()} else "{Type}"
        return f"{{SpecSection}}{m.group('s1')}{{Seq:0{width}d}}.{{Rev}}{m.group('s3')}{{SpecTitle}}{m.group('s4')}{type_tok}"
    m = RE_EXAMPLE.match(stem)
    if not m:
        return None
    sec_sep = " " if " " in m.group("sec") else ("." if "." in m.group("sec") else ("_" if "_" in m.group("sec") else ""))
    width = len(m.group("seq"))
    sec_tok = "{SpecSection}" if sec_sep == " " else "{SpecSectionCompact}" if sec_sep == "" else "{SpecSection" + {".": "Dot", "_": "Under"}[sec_sep] + "}"
    type_tok = "{TypeAbbr}" if m.group("type").strip().upper() in {v.upper() for v in _abbr().values()} else "{Type}"
    return (f"{sec_tok}{m.group('s1')}{{Seq:0{width}d}}{m.group('s2')}{{Rev}}{m.group('s3')}{{SpecTitle}}"
            f"{m.group('s4')}{type_tok}")


def _abbr() -> dict[str, str]:
    return naming_cfg().get("type_abbreviations") or {"Product Data": "PD", "Shop Drawings": "SD"}


def tokens(params: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    sec = params.get("spec_section", "")
    seq, seq_raw = parse_seq(params.get("submittal_no", ""), sec)
    date_fmt = naming_cfg().get("date_format", "%Y-%m-%d")
    return {
        "SpecSection": sec, "SpecSectionCompact": sec.replace(" ", ""), "SpecSectionDot": sec.replace(" ", "."),
        "SpecSectionUnder": sec.replace(" ", "_"), "Seq": seq, "SeqRaw": seq_raw,
        "Rev": str(params.get("revision", "0") or "0"), "SpecTitle": params.get("spec_title") or "",
        "Type": params.get("submittal_type") or "Product Data",
        "TypeAbbr": _abbr().get(params.get("submittal_type") or "Product Data", params.get("submittal_type") or "PD"),
        "Title": params.get("title") or params.get("spec_title") or "",
        "ProjectNumber": project.get("number", ""), "ProjectName": project.get("name", ""),
        "Contractor": project.get("contractor", ""), "Date": datetime.now().strftime(date_fmt),
    }


def render(pattern: str, toks: dict[str, Any]) -> str:
    def sub(m: re.Match) -> str:
        key, fmt = m.group(1), m.group(2)
        val = toks.get(key, m.group(0))
        if fmt:
            try:
                return format(int(val) if fmt.endswith("d") else val, fmt)
            except (ValueError, TypeError):
                return str(val)
        return str(val)

    out = re.sub(r"\{(\w+)(?::([^}]+))?\}", sub, pattern)
    out = INVALID.sub("", out)
    out = re.sub(r"\s+", " ", out).strip(" .-")
    return out[: int(naming_cfg().get("max_length", 150))].rstrip(" .-")


def choose_pattern(params: dict[str, Any], div01_pattern: str = "", example_names: list[str] | None = None) -> tuple[str, str]:
    """Return (pattern, source)."""
    if params.get("naming_pattern"):
        return params["naming_pattern"], "reviewer"
    if params.get("mimic_example_naming") and example_names:
        for n in example_names:
            p = infer_pattern_from_example(n)
            if p:
                return p, f"example file name ({n})"
    if div01_pattern:
        return div01_pattern, "Division 01"
    return naming_cfg().get("pattern", "{SpecSection} - {Seq:02d}.{Rev} {SpecTitle} - {TypeAbbr}"), "Ajax default"


def submittal_number(params: dict[str, Any], project: dict[str, Any] | None = None) -> str:
    pat = naming_cfg().get("number_pattern", "{SpecSection} - {Seq:02d}.{Rev}")
    return render(pat, tokens(params, project or {}))
