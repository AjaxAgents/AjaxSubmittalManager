"""Deterministic offline stand-ins for each agent (ASM_MOCK_LLM=1).

Used for demos without an API key and for end-to-end tests of the pipeline, PDF engine and outputs.
They are simple text heuristics - NOT a substitute for the Claude review.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .. import db
from ..library.ingest import norm_section
from ..schemas import (AdjudicatorDecision, AdjudicatorResult, Evidence, Finding, MarkupInstruction,
                       PackageRules, PageInfo, PlannerResult, Product, Requirement, ReviewResult, SourceRef,
                       SubmittalMap)

if TYPE_CHECKING:  # pragma: no cover
    from .stages import JobContext

FINISH = re.compile(r"\b(6\d\d|US\d{1,2}[A-Z]?)\b")
GRADE = re.compile(r"\bGrade\s*([123])\b", re.I)
SIZE = re.compile(r"\b(\d(?:\.\d|-\d/\d)?)\s*x\s*(\d(?:\.\d|-\d/\d)?)\b")
STD = re.compile(r"\b(A156\.\d{1,2}|UL\s?10C|NFPA\s?80)\b", re.I)


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if len(s) > 4 and s.upper() == s and any(c.isalpha() for c in s):
            return s
    return text.strip().splitlines()[0][:60] if text.strip() else ""


def intake(page_texts: list[str], params: dict[str, Any]) -> SubmittalMap:
    pages, products = [], []
    for i, t in enumerate(page_texts, 1):
        head = _first_heading(t)
        low = t.lower()
        if i == 1 and ("transmittal" in low or "submittal" in low):
            pages.append(PageInfo(page=i, kind="transmittal", description=head[:80]))
            continue
        if not t.strip():
            pages.append(PageInfo(page=i, kind="blank", description=""))
            continue
        pid = f"P-{len(products) + 1:02d}"
        words = head.split()
        mi = next((k for k, w in enumerate(words) if re.search(r"\d", w)), None)
        if mi is not None and mi > 0:
            manufacturer, model = " ".join(words[:mi]).title(), words[mi]
            name = " ".join(w for w in words[mi + 1:] if w.upper() != "SERIES").title() or head.title()
        else:
            manufacturer, model, name = (words[0].title() if words else ""), "", " ".join(words[1:]).title() or head.title()
        manufacturer = manufacturer.replace("Lcn", "LCN").replace("Hes", "HES")
        finishes = FINISH.findall(t)
        products.append(Product(id=pid, name=name, manufacturer=manufacturer, model=model,
                                options_selected=[f"finish {f}" for f in finishes[:2]], pages=[i]))
        pages.append(PageInfo(page=i, kind="product_data", description=head[:80], product_ref=pid))
    return SubmittalMap(submittal_type=params.get("submittal_type") or "Product Data",
                        summary=f"{len(products)} products across {len(page_texts)} pages (offline mock intake).",
                        supplier=_first_heading(page_texts[0]).title() if page_texts else "",
                        manufacturers=sorted({p.manufacturer for p in products if p.manufacturer}),
                        products=products, pages=pages)


def _amended_text(project_id: str, section: str) -> dict[str, str]:
    """Parse 'Revise Article X.Y.Z to read: ...' from amendments of a section."""
    out: dict[str, str] = {}
    sec = db.get_catalog_item(project_id, f"S:{section}")
    for am in (sec or {}).get("amended_by") or []:
        a = db.get_catalog_item(project_id, am)
        for m in re.finditer(r"Revise Article\s+(\d\.\d+\.[A-Z])\s+to read:\s*(.+)", (a or {}).get("text", "")):
            out[m.group(1)] = f"{m.group(2).strip()}|{am}"
    return out


def planner(ctx: "JobContext", smap: SubmittalMap) -> PlannerResult:
    sec_num = norm_section(ctx.params.get("spec_section", ""))
    sec = db.get_catalog_item(ctx.project_id, f"S:{sec_num}")
    reqs: list[Requirement] = []
    amended = _amended_text(ctx.project_id, sec_num)
    if sec:
        ctx.guard.admit("planner", "get_spec", sec["id"], max(1, sec["page_end"] - sec["page_start"] + 1), "designated section")
        article = ""
        part = ""
        page = sec["page_start"]
        for line in sec.get("text", "").splitlines():
            s = line.strip()
            m = re.match(r"^\[p\.(\d+)\]$", s)
            if m:
                page = int(m.group(1))
                continue
            mp = re.match(r"^PART\s+(\d)", s)
            if mp:
                part = mp.group(1)
                continue
            ma = re.match(r"^(\d\.\d+)\s+[A-Z]", s)
            if ma:
                article = ma.group(1)
                continue
            mpar = re.match(r"^([A-H])\.\s+(.*)", s)
            if mpar and part in ("1", "2") and article:
                art = article
                if part == "1" and not art.startswith("1.3"):
                    continue  # only the submittals article from Part 1
                pid = f"{art}.{mpar.group(1)}"
                text = mpar.group(2)
                am = []
                if pid in amended:
                    text, am_id = amended[pid].split("|")
                    am = [am_id]
                rtype = "submittal_content" if part == "1" else ("material_finish" if FINISH.search(text) else
                                                                  "standard" if STD.search(text) else "product")
                reqs.append(Requirement(id=f"R-{len(reqs) + 1:03d}", source=SourceRef(section=sec_num, article=pid, page=page),
                                        type=rtype, text=text[:300], amended_by=am))
            elif reqs and s and not re.match(r"^(END OF|SECTION)", s) and not s.startswith("["):
                if reqs[-1].source.article.startswith(article) and part in ("1", "2") and not reqs[-1].amended_by:
                    reqs[-1].text = (reqs[-1].text + " " + s)[:400]
    rules = PackageRules(include_toc=True, order=["cover", "toc", "deviations", "product_data", "shop_drawings"])
    for p in ctx.div01_paths:
        import pymupdf
        with pymupdf.open(p) as d:
            t = " ".join(pg.get_text("text") for pg in d)
        t = " ".join(t.split())
        if "[Section Number] - [Sequence].[Revision]" in t:
            rules.naming_pattern = "{SpecSection} - {Seq:02d}.{Rev} {SpecTitle} - {TypeAbbr}"
            rules.naming_source = f"{p.name} 1.2.B"
        if re.search(r"deviation summary", t, re.I):
            rules.other_rules.append("Deviation summary page required; cloud each deviation (01 33 00 1.2.E)")
        if re.search(r"Cross out", t, re.I):
            rules.other_rules.append("Mark applicable products/options; cross out non-applicable (01 33 00 1.2.D)")
    return PlannerResult(spec_section_title=(sec or {}).get("title", ""), requirements=reqs, package_rules=rules,
                         scope_notes=["Offline mock planner: requirements parsed from Part 1 submittals article and Part 2 paragraphs."])


PRODUCT_WORDS = ["hinge", "closer", "exit device", "strike", "kick plate", "plate", "lockset", "door", "frame",
                 "panel", "fixture", "fan", "pump", "valve", "damper", "switch", "breaker"]


def _topic_pages(req: Requirement, ctx: "JobContext", smap: SubmittalMap) -> list[int]:
    """Pages of the submitted product a requirement is about (skips transmittal/cover pages)."""
    lead = req.text.split(":")[0].lower() if ":" in req.text[:40] else ""
    sec = db.get_catalog_item(ctx.project_id, f"S:{req.source.section}/{'.'.join(req.source.article.split('.')[:2])}")
    title = (sec or {}).get("title", "").lower()
    stems = [w for w in PRODUCT_WORDS if w in lead] or [w for w in PRODUCT_WORDS if w in title] or \
            [w.rstrip("s") for w in re.findall(r"[a-z]{4,}", title) if w not in ("door", "doors", "finishes")]
    skip = {p.page for p in smap.pages if p.kind in ("cover", "transmittal", "toc")}
    hits = []
    for i, t in enumerate(ctx.page_texts, 1):
        if i in skip:
            continue
        head = _first_heading(t).lower()
        low = t.lower()
        if stems and any(st in head for st in stems):
            hits.insert(0, i)
        elif stems and any(st in low for st in stems):
            hits.append(i)
    return hits


def _tokens(text: str) -> dict[str, list[str]]:
    return {"finish": FINISH.findall(text), "grade": GRADE.findall(text), "std": [s.upper().replace(" ", "") for s in STD.findall(text)]}


def reviewer(role: str, ctx: "JobContext", smap: SubmittalMap, plan: PlannerResult) -> ReviewResult:
    findings: list[Finding] = []
    general: list[MarkupInstruction] = []
    extra = []
    for req in plan.requirements:
        pages = _topic_pages(req, ctx, smap) if req.type != "submittal_content" else []
        want = _tokens(req.text)
        if req.type == "submittal_content":
            findings.append(Finding(requirement_id=req.id, agent=role, status="no_evidence" if role == "advocate" else "unclear",
                                    issue_kind="missing_info" if role == "examiner" else "none", confidence="low",
                                    evidence=[], reason="Submittal-content requirement; offline mock cannot verify."))
            continue
        if not pages:
            findings.append(Finding(requirement_id=req.id, agent=role, status="no_evidence", confidence="low",
                                    issue_kind="missing_info" if role == "examiner" else "none", evidence=[],
                                    reason="No submittal page addresses this item."))
            continue
        page = pages[0]
        text = ctx.page_texts[page - 1]
        have = _tokens(text)
        wanted_finish = [f for f in want["finish"] if re.match(r"6\d\d", f)]
        have_finish = [f for f in have["finish"] if re.match(r"6\d\d", f)]
        dev = None
        if wanted_finish and have_finish and not set(wanted_finish) & set(have_finish[:1]):
            dev = (f"Finish {have_finish[0]} shown", have_finish[0], f"requires {wanted_finish[0]}")
        elif want["grade"] and have["grade"] and want["grade"][0] not in have["grade"]:
            dev = (f"Grade {have['grade'][0]} shown", f"Grade {have['grade'][0]}", f"requires Grade {want['grade'][0]}")
        elif "5 x 4-1/2" in req.text and "4.5 x 4.5" in text and "X" in text:
            dev = ("4.5 x 4.5 standard weight selected", "4.5 x 4.5", "requires 5 x 4-1/2 heavy weight for doors over 36 in.")
        cite = f"{req.source.section} {req.source.article}" + (f" ({', '.join(a.split(":", 1)[-1] for a in req.amended_by)})" if req.amended_by else "")
        if dev:
            quote = dev[1]
            if role == "advocate":
                findings.append(Finding(requirement_id=req.id, agent=role, status="not_met", issue_kind="deviation",
                                        confidence="high", evidence=[Evidence(page=page, quote=quote)],
                                        reason=f"{dev[0]} p.{page}; {cite} {dev[2].rstrip('.')}."))
            else:
                findings.append(Finding(requirement_id=req.id, agent=role, status="deviation", issue_kind="deviation",
                                        confidence="high", evidence=[Evidence(page=page, quote=quote)],
                                        reason=f"{dev[0]} p.{page}; {cite} {dev[2].rstrip('.')}.",
                                        proposed_markups=[MarkupInstruction(page=page, type="cloud", subject="deviation",
                                                                            anchor_text=quote, text=f"{dev[0]}; {cite} {dev[2]}",
                                                                            finding_ref=req.id)]))
            continue
        found = [t for t in want["std"] if t in have["std"]] + [f"Grade {g}" for g in want["grade"] if g in have["grade"]]
        found += [f for f in wanted_finish if f in have_finish]
        if found:
            q = found[0] if not found[0].startswith("A156") else found[0]
            anchor = q if q.lower() in text.lower() else ""
            if role == "advocate":
                mk = [MarkupInstruction(page=page, type="check", subject="compliant", anchor_text=anchor or q,
                                        text=f"Meets {cite}", layer="internal", finding_ref=req.id)]
                findings.append(Finding(requirement_id=req.id, agent=role, status="met", confidence="high",
                                        evidence=[Evidence(page=page, quote=q)], reason=f"{q} shown p.{page}; meets {cite}.",
                                        proposed_markups=mk))
            else:
                findings.append(Finding(requirement_id=req.id, agent=role, status="no_issue", confidence="high",
                                        evidence=[Evidence(page=page, quote=q)], reason=f"{q} shown p.{page}."))
        else:
            findings.append(Finding(requirement_id=req.id, agent=role,
                                    status="met" if role == "advocate" and req.type == "product" and False else ("no_evidence" if role == "advocate" else "unclear"),
                                    issue_kind="none" if role == "advocate" else "questionable",
                                    confidence="medium" if role == "advocate" and req.type == "product" else "low",
                                    evidence=[Evidence(page=page, quote=_first_heading(text)[:40])],
                                    reason=f"Related product on p.{page}; offline mock could not confirm '{req.text[:60]}'."))
    if role == "advocate":
        for pr in smap.products:
            if pr.model and pr.pages:
                general.append(MarkupInstruction(page=pr.pages[0], type="highlight", subject="selection",
                                                 anchor_text=pr.model, text=f"Submitted: {pr.manufacturer} {pr.model}"))
        for i, t in enumerate(ctx.page_texts, 1):
            for m in re.finditer(r"Optional:\s*([^\n.]+)", t):
                general.append(MarkupInstruction(page=i, type="strikeout", subject="not_submitted",
                                                 anchor_text=m.group(0).strip(), text="Not submitted"))
    if role == "examiner":
        for i, t in enumerate(ctx.page_texts, 1):
            uls = re.findall(r"(\d(?:\.\d)?) hour", t)
            if len(set(uls)) > 1:
                extra.append({"kind": "faulty_data", "summary": f"p.{i} lists conflicting UL ratings ({', '.join(sorted(set(uls)))} hour) for the same device.",
                              "evidence": [Evidence(page=i, quote=f"{uls[0]} hour")]})
    return ReviewResult(findings=findings, extra_issues=extra, general_markups=general)


def adjudicator(disputes: list[dict[str, Any]], notes: str = "") -> AdjudicatorResult:
    out = []
    for d in disputes:
        rid = d["requirement_id"]
        txt_id = d.get("txt_id", "")
        if notes and (rid in notes or (txt_id and txt_id in notes)) and re.search(r"accept|approved|ok\b", notes, re.I):
            out.append(AdjudicatorDecision(requirement_id=rid, result="compliant",
                                           summary=f"Accepted per reviewer note: {notes.strip()[:80]}",
                                           pages=d.get("pages", []), keep_markups_from="advocate"))
            continue
        if d.get("previous_result"):  # re-opened on a re-run but not mentioned in the notes: keep it
            out.append(AdjudicatorDecision(requirement_id=rid, result=d["previous_result"],
                                           human_review_kind="" if d["previous_result"] != "human_review" else "questionable",
                                           summary=d.get("previous_summary", ""), pages=d.get("pages", []),
                                           keep_markups_from="examiner" if d["previous_result"] == "non_compliant" else "none"))
            continue
        out.append(AdjudicatorDecision(requirement_id=rid, result="human_review", human_review_kind="questionable",
                                       summary=f"Advocate: {d.get('advocate', {}).get('reason', '-')[:70]} / Examiner: {d.get('examiner', {}).get('reason', '-')[:70]}",
                                       pages=d.get("pages", [])))
    return AdjudicatorResult(decisions=out)


def single(ctx: "JobContext", smap: SubmittalMap):
    """Offline stand-in for single-agent mode: mock planner + both mock reviewers folded into one result."""
    from ..schemas import SingleFinding, SingleReviewResult
    from .adjudicate import rule_verdicts
    plan = planner(ctx, smap)
    adv = reviewer("advocate", ctx, smap, plan)
    exa = reviewer("examiner", ctx, smap, plan)
    verdicts, disputes = rule_verdicts(plan, adv, exa)
    A = {f.requirement_id: f for f in adv.findings}
    B = {f.requirement_id: f for f in exa.findings}
    by = {v.requirement_id: v for v in verdicts}
    findings = []
    for r in plan.requirements:
        v = by.get(r.id)
        a, b = A.get(r.id), B.get(r.id)
        if v is None:  # disputed -> a single agent would have to call it; mock sends it to a human
            result, kind, reason = "human_review", "questionable", (b.reason if b else "Unclear")
        else:
            result, kind, reason = v.result, v.human_review_kind, v.summary
        src = b if result == "non_compliant" else a
        findings.append(SingleFinding(requirement_id=r.id, result=result, human_review_kind=kind,
                                      confidence=(src.confidence if src else "low") if result != "human_review" else "medium",
                                      evidence=(src.evidence if src else []), reason=reason,
                                      markups=(src.proposed_markups if src else [])))
    return SingleReviewResult(spec_section_title=plan.spec_section_title, requirements=plan.requirements,
                              findings=findings, package_rules=plan.package_rules, scope_notes=plan.scope_notes,
                              extra_issues=adv.extra_issues + exa.extra_issues, general_markups=adv.general_markups)
