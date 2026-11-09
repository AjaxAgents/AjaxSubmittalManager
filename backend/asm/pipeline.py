"""Job orchestrator: runs the six-stage review, streams progress events, persists every artifact.

Job folder (data/jobs/<job_id>/): inputs/, work/ (combined submittal, stage JSON), outputs/ (PDF + TXT),
events.jsonl (audit trail). Final outputs are also copied to data/outputs/<project>/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf

from . import db
from .agents import stages
from .agents.adjudicate import rule_verdicts, single_to_parts, single_verdicts, verify_findings
from .agents.llm import BudgetExceeded, Cancelled, CostTracker
from .config import data_dir, markup_cfg, mock_mode, outputs_dir, settings_cfg
from .library.catalog import section_lookup
from .library.ingest import norm_section
from .library.tools import ScopeGuard
from .outputs import naming
from .outputs.packager import build_package
from .outputs.txt_writer import build_txt
from .pdf.util import inspect_pdf, ocr_pdf
from .schemas import (AdjudicatorResult, MarkupInstruction, PackageRules, PlannerResult, QAResult, ReviewResult,
                      SingleReviewResult, SubmittalMap, Verdict)

log = logging.getLogger("asm.pipeline")

STAGES_SINGLE = [
    ("prepare", "Prepare & combine"),
    ("intake", "Intake"),
    ("single", "Reviewer (single agent)"),
    ("adjudicator", "Verify citations"),
    ("assemble", "Markup & Assemble"),
    ("qa", "QA Check"),
]

STAGES = [
    ("prepare", "Prepare & combine"),
    ("intake", "Intake"),
    ("planner", "Context Planner"),
    ("advocate", "Compliance Advocate"),
    ("examiner", "Non-Compliance Examiner"),
    ("adjudicator", "Adjudicator"),
    ("assemble", "Markup & Assemble"),
    ("qa", "QA Check"),
]


# ----------------------------------------------------------------------------- event bus

@dataclass
class JobEvents:
    job_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    queues: list[asyncio.Queue] = field(default_factory=list)
    loop: asyncio.AbstractEventLoop | None = None
    done: bool = False

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        ev = {"t": round(time.time(), 3), "kind": kind, **data}
        self.history.append(ev)
        try:
            with open(job_dir(self.job_id) / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, default=str) + "\n")
        except Exception:
            pass
        for q in list(self.queues):
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(q.put_nowait, ev)


_EVENTS: dict[str, JobEvents] = {}
_TRACKERS: dict[str, CostTracker] = {}
_RUNNING: dict[str, asyncio.Task] = {}


def events_for(job_id: str) -> JobEvents:
    if job_id not in _EVENTS:
        ev = JobEvents(job_id)
        p = job_dir(job_id) / "events.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    ev.history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            j = db.get_job(job_id)
            ev.done = bool(j and j["status"] in ("done", "error", "cancelled"))
        _EVENTS[job_id] = ev
    return _EVENTS[job_id]


def job_dir(job_id: str) -> Path:
    p = data_dir() / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def cancel(job_id: str) -> bool:
    t = _TRACKERS.get(job_id)
    if t:
        t.cancelled = True
    task = _RUNNING.get(job_id)
    if task and not task.done():
        return True
    return bool(t)


def _save(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(obj, "model_dump_json"):
        path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


# ----------------------------------------------------------------------------- helpers

class Stepper:
    def __init__(self, ev: JobEvents, job_id: str):
        self.ev, self.job_id = ev, job_id
        self.t0: dict[str, float] = {}

    def start(self, key: str, msg: str = "") -> None:
        self.t0[key] = time.time()
        db.update_job(self.job_id, stage=key)
        self.ev.emit("stage", {"stage": key, "status": "running", "msg": msg})

    def done(self, key: str, msg: str = "", **extra: Any) -> None:
        el = round(time.time() - self.t0.get(key, time.time()), 1)
        self.ev.emit("stage", {"stage": key, "status": "done", "msg": msg, "elapsed": el, **extra})

    def skip(self, key: str, msg: str = "") -> None:
        self.ev.emit("stage", {"stage": key, "status": "skipped", "msg": msg})

    def fail(self, key: str, msg: str) -> None:
        el = round(time.time() - self.t0.get(key, time.time()), 1)
        self.ev.emit("stage", {"stage": key, "status": "error", "msg": msg, "elapsed": el})


def combine_submittal(paths: list[Path], out: Path) -> tuple[int, list[dict[str, Any]]]:
    doc = pymupdf.open()
    sources = []
    for p in paths:
        with pymupdf.open(p) as s:
            start = doc.page_count + 1
            doc.insert_pdf(s, annots=True, links=True)
            sources.append({"file": p.name, "first": start, "last": doc.page_count})
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, garbage=3, deflate=True)
    n = doc.page_count
    doc.close()
    return n, sources


def _markups_for(v: Verdict, adv: dict[str, Any], exa: dict[str, Any], decision_keep: str | None,
                 include_internal: bool) -> list[MarkupInstruction]:
    """Choose markups for a verdict. A/E layer gets only what a contractor would send."""
    a = adv.get(v.requirement_id)
    b = exa.get(v.requirement_id)
    out: list[MarkupInstruction] = []
    keep = decision_keep
    if v.result == "compliant":
        src = [*(a.proposed_markups if a else [])] if keep in (None, "advocate", "both") else []
        for m in src:
            if m.subject in ("selection", "not_submitted", "note") and m.layer == "ae":
                out.append(m)
            elif include_internal:
                out.append(m.model_copy(update={"layer": "internal"}))
    elif v.result == "non_compliant":
        src = [*(b.proposed_markups if b else [])] if keep in (None, "examiner", "both") else []
        if keep in ("advocate", "both") and a:
            src += a.proposed_markups
        devs = [m.model_copy(update={"layer": "ae"}) for m in src if m.subject in ("deviation", "note", "not_submitted")]
        if not devs:
            ev = (b.evidence if b and b.evidence else (a.evidence if a else []))
            if ev:
                devs = [MarkupInstruction(page=ev[0].page, type="cloud", subject="deviation", anchor_text=ev[0].quote,
                                          text=v.summary[:220], layer="ae")]
        out += devs
    elif v.result == "human_review" and include_internal:
        ev = (b.evidence if b and b.evidence else (a.evidence if a else []))
        if ev:
            out.append(MarkupInstruction(page=ev[0].page, type="cloud", subject="human_review", anchor_text=ev[0].quote,
                                         text=f"HUMAN REVIEW: {v.summary[:200]}", layer="internal"))
    for m in out:
        m.finding_ref = v.requirement_id
    return out


def _assign_txt_ids(verdicts: list[Verdict], previous: dict[str, str] | None = None) -> None:
    prefix = {"compliant": "C", "non_compliant": "N", "human_review": "H", "not_applicable": "NA"}
    previous = previous or {}
    counters: dict[str, int] = {}
    for v in verdicts:  # keep stable ids when the category is unchanged
        old = previous.get(v.requirement_id, "")
        if old and old.split("-")[0] == prefix[v.result]:
            n = int(old.split("-")[1])
            counters[prefix[v.result]] = max(counters.get(prefix[v.result], 0), n)
    for v in verdicts:
        p = prefix[v.result]
        old = previous.get(v.requirement_id, "")
        if old and old.split("-")[0] == p:
            v.txt_id = old
            continue
        counters[p] = counters.get(p, 0) + 1
        v.txt_id = f"{p}-{counters[p]:02d}"


# ----------------------------------------------------------------------------- main run

async def run_job(job_id: str, rerun_notes: str = "") -> None:
    ev = events_for(job_id)
    ev.loop = asyncio.get_running_loop()
    job = db.get_job(job_id)
    if not job:
        return
    project = db.get_project(job["project_id"])
    params: dict[str, Any] = dict(job["params"])
    tracker = CostTracker.for_job(job_id, project["id"], usd_cap=params.get("usd_cap"))
    tracker.on_usage = lambda u: ev.emit("cost", u)
    _TRACKERS[job_id] = tracker
    step = Stepper(ev, job_id)
    jd = job_dir(job_id)
    work = jd / "work"
    db.update_job(job_id, status="running", error="")
    mode = params.get("review_mode") or (settings_cfg().get("review") or {}).get("default_mode", "cross")
    mode = "single" if mode == "single" else "cross"
    params["review_mode"] = mode
    ev.emit("job", {"status": "running", "mock": mock_mode(), "mode": mode,
                    "stages": STAGES_SINGLE if mode == "single" else STAGES})
    single: SingleReviewResult | None = None
    budget_hit = False
    current = "prepare"
    try:
        # ---------------------------------------------------------------- prepare
        current = "prepare"
        step.start("prepare")
        parent = params.get("_rerun_of")
        params["spec_section"] = norm_section(params.get("spec_section", ""))
        sec = section_lookup(project["id"], params["spec_section"])
        if not params.get("spec_title") and sec:
            params["spec_title"] = sec["title"]
        if parent:
            pwork = job_dir(parent) / "work"
            shutil.copytree(pwork, work, dirs_exist_ok=True)
            n_pages = inspect_pdf(work / "submittal.pdf")["pages"]
        else:
            ups = [db.get_upload(u) for u in params.get("uploads", {}).get("submittal", [])]
            sub_paths = [Path(u["path"]) for u in ups if u]
            if not sub_paths:
                raise ValueError("No submittal files were provided (zone 1).")
            n_pages, sources = combine_submittal(sub_paths, work / "submittal.pdf")
            info = inspect_pdf(work / "submittal.pdf")
            if "ocr_needed" in info["flags"]:
                ev.emit("log", {"msg": "Scanned pages detected - running OCR"})
                ok, how = await asyncio.to_thread(ocr_pdf, work / "submittal.pdf", work / "submittal.ocr.pdf")
                if ok:
                    (work / "submittal.ocr.pdf").replace(work / "submittal.pdf")
                else:
                    ev.emit("log", {"msg": how})
            _save(work / "sources.json", sources)
        sub_path = work / "submittal.pdf"
        with pymupdf.open(sub_path) as d:
            page_texts = [pg.get_text("text") for pg in d]
        ex_paths = [Path(db.get_upload(u)["path"]) for u in params.get("uploads", {}).get("examples", []) if db.get_upload(u)]
        d1_paths = [Path(db.get_upload(u)["path"]) for u in params.get("uploads", {}).get("div01", []) if db.get_upload(u)]
        scope = params.get("scope") or {"mode": "flexible", "items": [{"id": f"S:{params['spec_section']}", "locked": True}]}
        if not any(i["id"] == f"S:{params['spec_section']}" for i in scope.get("items", [])):
            scope.setdefault("items", []).insert(0, {"id": f"S:{params['spec_section']}", "locked": True,
                                                    "reason": "Designated section"})
        db.save_scope_preset(project["id"], params["spec_section"], scope)
        guard = ScopeGuard.from_scope(project["id"], job_id, scope)
        ctx = stages.JobContext(job_id=job_id, project=project, params=params, workdir=jd, submittal_path=sub_path,
                                submittal_pages=n_pages, example_paths=ex_paths, div01_paths=d1_paths,
                                tracker=tracker, guard=guard, emit=ev.emit, page_texts=page_texts)
        db.update_job(job_id, params=params)
        if not sec:
            ev.emit("log", {"msg": f"Section {params['spec_section']} is not in the project library; review will rely on Division 01 / instructions only.",
                            "level": "warn"})
        step.done("prepare", f"{n_pages} submittal pages; scope {len(guard.items)} item(s), {guard.mode} mode")

        # ---------------------------------------------------------------- intake / planner (or reuse)
        if parent:
            smap = SubmittalMap.model_validate_json((work / "intake.json").read_text(encoding="utf-8"))
            if (work / "single.json").exists():
                mode = params["review_mode"] = "single"
                single = SingleReviewResult.model_validate_json((work / "single.json").read_text(encoding="utf-8"))
                plan, adv, exa = single_to_parts(single)
                reused = ("intake", "single")
            else:
                plan = PlannerResult.model_validate_json((work / "planner.json").read_text(encoding="utf-8"))
                adv = ReviewResult.model_validate_json((work / "advocate.json").read_text(encoding="utf-8"))
                exa = ReviewResult.model_validate_json((work / "examiner.json").read_text(encoding="utf-8"))
                reused = ("intake", "planner", "advocate", "examiner")
            for k in reused:
                step.skip(k, "reused from previous run")
        elif mode == "single":
            current = "intake"
            step.start("intake")
            smap = await stages.run_intake(ctx)
            _save(work / "intake.json", smap)
            step.done("intake", f"{len(smap.products)} products, {len(smap.pages)} pages mapped")
            current = "single"
            step.start("single")
            try:
                single = await stages.run_single(ctx, smap)
            except BudgetExceeded as exc:
                raise RuntimeError(f"{exc} before the single-agent review finished. Raise the job cap in Settings.") from exc
            _save(work / "single.json", single)
            plan, adv, exa = single_to_parts(single)
            if not params.get("spec_title") and plan.spec_section_title:
                params["spec_title"] = plan.spec_section_title
                db.update_job(job_id, params=params)
            _save(work / "planner.json", plan)
            step.done("single", f"{len(plan.requirements)} requirements reviewed")
        else:
            current = "intake"
            step.start("intake")
            smap = await stages.run_intake(ctx)
            _save(work / "intake.json", smap)
            step.done("intake", f"{len(smap.products)} products, {len(smap.pages)} pages mapped")

            current = "planner"
            step.start("planner")
            plan = await stages.run_planner(ctx, smap)
            if not params.get("spec_title") and plan.spec_section_title:
                params["spec_title"] = plan.spec_section_title
                db.update_job(job_id, params=params)
            _save(work / "planner.json", plan)
            step.done("planner", f"{len(plan.requirements)} requirements")

            current = "advocate"
            step.start("advocate")
            step.start("examiner")
            prefix_ready = asyncio.Event()
            results = await asyncio.gather(
                stages.run_reviewer(ctx, "advocate", smap, plan, prefix_ready=prefix_ready),
                stages.run_reviewer(ctx, "examiner", smap, plan, wait_for=prefix_ready),
                return_exceptions=True)
            adv_r, exa_r = results
            for role, r in (("advocate", adv_r), ("examiner", exa_r)):
                if isinstance(r, Cancelled):
                    raise r
                if isinstance(r, BudgetExceeded):
                    budget_hit = True
                    step.fail(role, str(r))
                elif isinstance(r, Exception):
                    step.fail(role, f"{type(r).__name__}: {r}")
                    ev.emit("log", {"msg": f"{role} failed: {r}", "level": "error"})
                else:
                    step.done(role, f"{len(r.findings)} findings")
            adv = adv_r if isinstance(adv_r, ReviewResult) else ReviewResult(findings=[])
            exa = exa_r if isinstance(exa_r, ReviewResult) else ReviewResult(findings=[])
            if not adv.findings and not exa.findings and not budget_hit:
                raise RuntimeError("Both reviewers failed; see the run log.")
            _save(work / "advocate.json", adv)
            _save(work / "examiner.json", exa)

        # ---------------------------------------------------------------- verify + adjudicate
        current = "adjudicator"
        step.start("adjudicator")
        vnotes = verify_findings(adv, page_texts) + verify_findings(exa, page_texts)
        if single is not None:  # both shapes carry the same evidence: report each note once
            vnotes = list(dict.fromkeys(n.split(" ", 1)[1] for n in vnotes))
            verdicts, disputes = single_verdicts(single, adv, exa), []
        else:
            verdicts, disputes = rule_verdicts(plan, adv, exa)
        prev_ids: dict[str, str] = {}
        if parent and (work / "verdicts.json").exists():
            prev = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
            prev_ids = {v["requirement_id"]: v.get("txt_id", "") for v in prev}
            if rerun_notes:  # re-open every non-compliant / human-review item for the adjudicator
                reopen = {v["requirement_id"] for v in prev if v["result"] in ("non_compliant", "human_review")}
                keep = []
                for v in verdicts:
                    if v.requirement_id in reopen and v.requirement_id not in {d["requirement_id"] for d in disputes}:
                        a = next((f for f in adv.findings if f.requirement_id == v.requirement_id), None)
                        b = next((f for f in exa.findings if f.requirement_id == v.requirement_id), None)
                        disputes.append({"requirement_id": v.requirement_id,
                            "requirement": next((r.text for r in plan.requirements if r.id == v.requirement_id), ""),
                            "previous_result": v.result, "previous_summary": v.summary,
                            "txt_id": prev_ids.get(v.requirement_id, ""),
                            "advocate": a.model_dump(include={"status", "confidence", "reason", "evidence"}) if a else None,
                            "examiner": b.model_dump(include={"status", "issue_kind", "confidence", "reason", "evidence"}) if b else None,
                            "pages": v.pages})
                    else:
                        keep.append(v)
                verdicts = keep
        keep_from: dict[str, str] = {}
        new_markups: list[MarkupInstruction] = []
        try:
            adj = await stages.run_adjudicator(ctx, plan, disputes, notes=rerun_notes) if disputes else AdjudicatorResult(decisions=[])
        except BudgetExceeded as exc:
            budget_hit = True
            ev.emit("log", {"msg": str(exc), "level": "warn"})
            adj = AdjudicatorResult(decisions=[])
        decided = {d.requirement_id for d in adj.decisions}
        A = {f.requirement_id: f for f in adv.findings}
        B = {f.requirement_id: f for f in exa.findings}
        for d in adj.decisions:
            src = next((x for x in disputes if x["requirement_id"] == d.requirement_id), {})
            verdicts.append(Verdict(requirement_id=d.requirement_id, result=d.result, human_review_kind=d.human_review_kind,
                                    summary=d.summary, pages=d.pages or src.get("pages", []), decided_by="adjudicator",
                                    advocate_position=json.dumps(src.get("advocate"))[:300] if src else "",
                                    examiner_position=json.dumps(src.get("examiner"))[:300] if src else ""))
            keep_from[d.requirement_id] = d.keep_markups_from
            for m in d.new_markups:
                m.finding_ref = d.requirement_id
                new_markups.append(m)
        for d in disputes:  # anything the adjudicator skipped
            if d["requirement_id"] not in decided:
                verdicts.append(Verdict(requirement_id=d["requirement_id"], result="human_review",
                                        human_review_kind="budget_hit" if budget_hit else "questionable",
                                        summary=("Not adjudicated (job budget reached). " if budget_hit else "Reviewers disagree. ")
                                        + f"A: {(d.get('advocate') or {}).get('reason', '-')[:80]} / B: {(d.get('examiner') or {}).get('reason', '-')[:80]}",
                                        pages=d.get("pages", []), decided_by="budget" if budget_hit else "rules"))
        if budget_hit:
            for v in verdicts:
                if v.summary.startswith("Not evaluated by either reviewer"):
                    v.human_review_kind = "budget_hit"
                    v.decided_by = "budget"
        order = {r.id: i for i, r in enumerate(plan.requirements)}
        verdicts.sort(key=lambda v: order.get(v.requirement_id, 9999))
        _assign_txt_ids(verdicts, prev_ids)
        _save(work / "verdicts.json", [v.model_dump() for v in verdicts])
        counts = {k: sum(1 for v in verdicts if v.result == k) for k in ("compliant", "non_compliant", "human_review", "not_applicable")}
        step.done("adjudicator", f"{counts['compliant']} compliant, {counts['non_compliant']} non-compliant, "
                                 f"{counts['human_review']} human review; {len(disputes)} disputes adjudicated",
                  counts=counts)

        # ---------------------------------------------------------------- assemble
        current = "assemble"
        step.start("assemble")
        include_internal = bool(markup_cfg().get("include_internal_markups", True))
        markups: list[MarkupInstruction] = []
        for v in verdicts:
            ms = _markups_for(v, A, B, keep_from.get(v.requirement_id), include_internal)
            ms += [m for m in new_markups if m.finding_ref == v.requirement_id]
            for i, m in enumerate(ms, 1):
                m.id = f"{v.txt_id}-M{i}"
                v.markup_ids.append(m.id)
            markups += ms
        for i, m in enumerate(adv.general_markups, 1):
            m.id = f"SEL-{i:02d}"
            m.layer = "ae" if m.subject in ("selection", "not_submitted", "note") else "internal"
            markups.append(m)
        # extra issues -> human review items (+ internal markups)
        extra_hr: list[dict[str, Any]] = []
        n_h = sum(1 for v in verdicts if v.result == "human_review")
        for issue in [*adv.extra_issues, *exa.extra_issues]:
            n_h += 1
            pg = issue.evidence[0].page if issue.evidence else None
            kind = issue.kind if issue.kind in ("rfi", "faulty_data", "missing_info") else "questionable"
            item = {"txt_id": f"H-{n_h:02d}", "kind": kind, "summary": issue.summary + (f" (p.{pg})" if pg and f"p.{pg}" not in issue.summary else "")}
            extra_hr.append(item)
            if include_internal and issue.evidence:
                markups.append(MarkupInstruction(id=f"{item['txt_id']}-M1", page=issue.evidence[0].page, type="cloud",
                                                 subject="human_review", anchor_text=issue.evidence[0].quote,
                                                 text=f"HUMAN REVIEW: {issue.summary[:200]}", layer="internal"))
        for gap in guard.gaps:
            n_h += 1
            extra_hr.append({"txt_id": f"H-{n_h:02d}", "kind": "scope",
                             "summary": f"Needs {gap['item']} to confirm ({gap.get('reason') or 'requested by ' + gap['stage']}); {gap['why']}."})
        for flag in smap.injection_flags:
            n_h += 1
            extra_hr.append({"txt_id": f"H-{n_h:02d}", "kind": "questionable",
                             "summary": f"Document text attempted to instruct the reviewer (ignored): \"{flag[:120]}\""})

        # naming
        pattern, nsrc = naming.choose_pattern(params, plan.package_rules.naming_pattern,
                                              [p.name for p in ex_paths])
        toks = naming.tokens(params, project)
        base = naming.render(pattern, toks) or f"{params['spec_section']} Submittal"
        sub_no = naming.submittal_number(params, project)
        out_dir = jd / "outputs"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        pdf_path = out_dir / f"{base}.pdf"
        txt_path = out_dir / f"{base}{naming.naming_cfg().get('review_notes_suffix', '_ReviewNotes')}.txt"

        deviations = []
        for v in verdicts:
            if v.result == "non_compliant":
                deviations.append({"id": v.txt_id, "description": v.summary,
                                   "spec_ref": next((f"{r.source.section} {r.source.article}" for r in plan.requirements
                                                     if r.id == v.requirement_id), ""), "pages": v.pages})
        incl = settings_cfg().get("output", {}).get("include_spec_excerpt", "auto")
        want_excerpt = incl == "always" or (incl == "auto" and (plan.package_rules.include_spec_excerpt
                                                                 or "include spec" in ctx.instructions.lower()
                                                                 or "include the spec" in ctx.instructions.lower()))
        if "do not include spec" in ctx.instructions.lower():
            want_excerpt = False
        excerpt = None
        if want_excerpt and sec:
            lf = db.get_library_file(sec["file_id"])
            if lf:
                excerpt = (Path(lf["path"]), sec["page_start"], sec["page_end"], sec["number"])
        cover_info = {
            "project_name": project.get("name"), "project_number": project.get("number"),
            "ae_firm": project.get("ae_firm"), "contractor": project.get("contractor"),
            "submittal_no": sub_no, "revision": params.get("revision", "0"),
            "spec_section": params["spec_section"], "spec_title": params.get("spec_title", ""),
            "submittal_type": params.get("submittal_type") or smap.submittal_type, "title": params.get("title"),
            "date_prepared": datetime.now().strftime(naming.naming_cfg().get("date_format", "%Y-%m-%d")),
            "required_date": params.get("required_date"), "supplier": params.get("supplier") or smap.supplier,
            "manufacturers": smap.manufacturers,
        }
        meta = {"title": base, "author": project.get("contractor") or "Ajax Building Company",
                "subject": f"{params['spec_section']} {params.get('spec_title', '')} - Submittal {sub_no}",
                "keywords": ", ".join([params["spec_section"], params.get("submittal_type") or "", *smap.manufacturers])}
        pkg = await asyncio.to_thread(
            build_package, out_path=pdf_path, submittal_path=sub_path, smap=smap, cover_info=cover_info,
            markups=markups, deviations=deviations, spec_excerpt=excerpt,
            include_toc=plan.package_rules.include_toc, metadata=meta)
        step.done("assemble", f"{pkg.page_count} pages, {len(pkg.applied)} markups")

        # ---------------------------------------------------------------- TXT + QA
        current = "qa"
        step.start("qa")
        fmt = [f"File name per {nsrc}: {base}.pdf"]
        fmt.append("Review mode: single agent (one Opus pass, no cross-examination)." if mode == "single" else
                   "Review mode: cross-examined (Compliance Advocate + Non-Compliance Examiner, disputes adjudicated).")
        fmt += pkg.format_decisions
        fmt += [f"Package rule applied: {r}" for r in plan.package_rules.other_rules[:6]]
        if ex_paths:
            fmt.append(f"Format referenced from example submittal(s): {', '.join(p.name for p in ex_paths)}")
        if excerpt:
            fmt.append(f"Spec section {excerpt[3]} excerpt included (pp. {excerpt[1]}-{excerpt[2]} of spec book).")
        scope_checked, not_checked = _scope_lines(guard, plan)
        det_qa = _deterministic_qa(pkg.path, base, pkg.warnings)

        def make_txt(qa_lines: list[str]) -> str:
            return build_txt(project=project, params=params, file_base=base, run_id=job_id[-6:],
                             models=sorted(tracker.models_used), cost=tracker.spent_usd,
                             verdicts=[v.model_dump() for v in verdicts], format_decisions=fmt, excluded=[],
                             extra_hr=extra_hr, scope_checked=scope_checked, scope_not_checked=not_checked,
                             qa_lines=qa_lines, verifier_notes=vnotes)

        draft = make_txt(det_qa)
        qa_res = QAResult(passed=True, issues=[])
        if bool((settings_cfg().get("qa") or {}).get("llm_check", False)):
            try:
                pages_for_qa = [0] + ([pkg.deviation_page - 1] if pkg.deviation_page else []) + list(pkg.marked_pages)[:6]
                qa_res = await stages.run_qa(ctx, pkg.path, pages_for_qa, pdf_path.name, plan.package_rules, draft)
            except (BudgetExceeded, Exception) as exc:  # QA must never sink a finished package
                ev.emit("log", {"msg": f"QA check skipped: {exc}", "level": "warn"})
        qa_lines = det_qa + [f"[QA {i.severity.upper()}] {i.where}: {i.issue}" for i in qa_res.issues]
        txt = make_txt(qa_lines)
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(txt, encoding="ascii", errors="ignore")
        step.done("qa", "passed" if qa_res.passed and not any("FIX" in q for q in qa_lines) else f"{len(qa_lines)} note(s)")

        # ---------------------------------------------------------------- publish
        proj_out = outputs_dir() / _safe(project.get("name") or project["id"])
        proj_out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, proj_out / pdf_path.name)
        shutil.copy2(txt_path, proj_out / txt_path.name)
        result = {
            "pdf": str(pdf_path), "txt": str(txt_path), "pdf_name": pdf_path.name, "txt_name": txt_path.name,
            "output_folder": str(proj_out), "pages": pkg.page_count, "counts": counts,
            "extra_human_review": len(extra_hr), "verdicts": [v.model_dump() for v in verdicts],
            "requirements": [r.model_dump() for r in plan.requirements], "markups": pkg.applied,
            "retrievals": guard.log, "scope_gaps": guard.gaps, "cost": round(tracker.spent_usd, 4),
            "cost_by_stage": tracker.by_stage, "models": sorted(tracker.models_used), "qa": qa_res.model_dump(),
            "qa_lines": qa_lines, "warnings": pkg.warnings, "naming_source": nsrc, "toc": pkg.toc,
            "deviation_page": pkg.deviation_page, "extra_hr": extra_hr, "mock": mock_mode(),
            "budget_hit": budget_hit, "src_to_out": pkg.src_to_out, "review_mode": mode,
        }
        _save(work / "result.json", result)
        db.update_job(job_id, status="done", stage="done", result=result, cost=round(tracker.spent_usd, 4))
        ev.emit("job", {"status": "done", "pdf_name": pdf_path.name, "txt_name": txt_path.name,
                        "cost": round(tracker.spent_usd, 4), "counts": counts})
    except Cancelled:
        step.fail(current, "Cancelled")
        db.update_job(job_id, status="cancelled", error="Cancelled by user", cost=round(tracker.spent_usd, 4))
        ev.emit("job", {"status": "cancelled"})
    except Exception as exc:
        log.exception("job failed")
        step.fail(current, f"{type(exc).__name__}: {exc}")
        db.update_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}", cost=round(tracker.spent_usd, 4))
        ev.emit("job", {"status": "error", "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]})
    finally:
        ev.done = True
        _TRACKERS.pop(job_id, None)
        _RUNNING.pop(job_id, None)


def _safe(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "project"


def _scope_lines(guard: ScopeGuard, plan: PlannerResult) -> tuple[list[str], list[str]]:
    specs, sheets, other, extra = [], [], [], []
    for it in guard.items.values():
        label = it.id.split(":", 1)[-1] + (f" (pp. {it.pages[0]}-{it.pages[-1]})" if it.pages else "")
        (specs if it.id.startswith("S:") else sheets if it.id.startswith("D:") else other).append(label)
    for e in guard.log:
        if e["allowed"] and not e["in_scope"]:
            extra.append(e["item"].split(":", 1)[-1])
    checked = []
    if specs:
        checked.append("Spec: " + ", ".join(specs))
    if sheets:
        checked.append("Drawings: " + ", ".join(sheets))
    if other:
        checked.append("Amendments/other: " + ", ".join(other))
    if extra:
        checked.append("Pulled outside scope (flexible mode): " + ", ".join(dict.fromkeys(extra)))
    checked.append(f"Library pages sent: {guard.pages_served} of {guard.page_budget} budget; mode {guard.mode}")
    not_checked = [n for n in plan.scope_notes if n.lower().startswith(("not ", "skip", "excluded"))]
    not_checked += [f"{g['item'].split(':', 1)[-1]} ({g['why']})" for g in guard.gaps]
    return checked, not_checked


def _deterministic_qa(pdf: Path, base: str, warnings: list[str]) -> list[str]:
    lines: list[str] = []
    if len(base) > 150:
        lines.append("[QA FIX] File name exceeds 150 characters.")
    with pymupdf.open(pdf) as d:
        seen: dict[int, int] = {}
        for p in d:
            txt = p.get_text("text").strip()
            if not txt and not p.get_images() and not p.get_drawings():
                lines.append(f"[QA NOTE] Output page {p.number + 1} appears blank.")
            h = hash((txt[:2000], len(p.get_images())))
            if txt and h in seen:
                lines.append(f"[QA NOTE] Output page {p.number + 1} duplicates page {seen[h] + 1}.")
            seen.setdefault(h, p.number)
            if p.rotation not in (0,):
                lines.append(f"[QA NOTE] Output page {p.number + 1} carries /Rotate {p.rotation} (displays rotated).")
    size_mb = pdf.stat().st_size / 1024 / 1024
    if size_mb > float(settings_cfg().get("output", {}).get("max_file_mb", 50)):
        lines.append(f"[QA FIX] PDF is {size_mb:.1f} MB.")
    unanchored = [w for w in warnings if "anchor not found" in w]
    if unanchored:
        lines.append(f"[QA NOTE] {len(unanchored)} markup(s) could not be anchored to text and were placed as page notes; "
                     "check placement in Bluebeam.")
    for w in warnings:
        if "anchor not found" not in w:
            lines.append(f"[QA NOTE] {w}")
    return lines[:20]


def start(job_id: str, rerun_notes: str = "") -> asyncio.Task:
    task = asyncio.create_task(run_job(job_id, rerun_notes))
    _RUNNING[job_id] = task
    return task
