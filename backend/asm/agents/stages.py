"""Pipeline stages backed by Claude (with deterministic offline stand-ins for ASM_MOCK_LLM=1).

Stage map (spec V2 section 7):
  1 Intake (Haiku)  2 Context Planner (Sonnet)  3a Compliance Advocate (Opus)  3b Non-Compliance
  Examiner (Opus)  4 Adjudicator (Opus)  5 Assembler (code)  6 QA Checker (Sonnet)
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pymupdf

from .. import db
from ..config import mock_mode, models_cfg, prompt
from ..library.catalog import catalog_index_text
from ..library.tools import LibraryTools, ScopeGuard
from ..schemas import (AdjudicatorResult, Evidence, Finding, MarkupInstruction, PackageRules, PageInfo,
                       PlannerResult, Product, QAResult, Requirement, ReviewResult, ScopeResult, SingleReviewResult,
                       SourceRef, SubmittalMap)
from . import mock
from .base import image_block, pdf_block, run_agent, text_block
from .llm import CostTracker


@dataclass
class JobContext:
    job_id: str
    project: dict[str, Any]
    params: dict[str, Any]
    workdir: Path
    submittal_path: Path
    submittal_pages: int
    example_paths: list[Path]
    div01_paths: list[Path]
    tracker: CostTracker
    guard: ScopeGuard
    emit: Callable[[str, dict[str, Any]], None]
    page_texts: list[str] = field(default_factory=list)
    _submittal_b64: str | None = None

    @property
    def project_id(self) -> str:
        return self.project["id"]

    @property
    def instructions(self) -> str:
        return (self.params.get("instructions") or "").strip()

    @property
    def inline_submittal(self) -> bool:
        lim = int(models_cfg().get("limits", {}).get("submittal_inline_max_pages", 90))
        size_ok = self.submittal_path.stat().st_size < 30 * 1024 * 1024
        return self.submittal_pages <= lim and size_ok

    def submittal_b64(self) -> str:
        if self._submittal_b64 is None:
            self._submittal_b64 = base64.b64encode(self.submittal_path.read_bytes()).decode()
        return self._submittal_b64

    def submittal_block(self, cache: bool = False) -> dict[str, Any]:
        """The submittal as a PDF document, or (if too large) its page text with a viewer tool."""
        if self.inline_submittal:
            return pdf_block(self.submittal_b64(), title="SUBMITTAL UNDER REVIEW", cache=cache)
        pages = "\n".join(f"--- page {i + 1} ---\n{t[:1800]}" for i, t in enumerate(self.page_texts))
        return text_block("SUBMITTAL UNDER REVIEW (text layer only; call view_submittal_pages to see pages):\n" + pages,
                          cache=cache)

    def header_text(self) -> str:
        p, pr = self.params, self.project
        lines = [
            f"PROJECT: {pr.get('name')} (No. {pr.get('number') or '-'}); A/E: {pr.get('ae_firm') or '-'}; Contractor: {pr.get('contractor') or '-'}",
            f"DESIGNATED SPEC SECTION: {p.get('spec_section')} {p.get('spec_title') or ''}",
            f"SUBMITTAL: No. {p.get('submittal_no') or '-'} Rev {p.get('revision', '0')} | Type: {p.get('submittal_type')} | Title: {p.get('title') or '-'}",
        ]
        if self.instructions:
            lines.append(f"\nREVIEWER INSTRUCTIONS (high priority, from the human reviewer):\n{self.instructions}")
        return "\n".join(lines)

    def on_tool(self, kind: str, data: dict[str, Any]) -> None:
        self.emit("tool", data)


def _page_map_text(smap: SubmittalMap) -> str:
    lines = ["PAGE MAP (from Intake)"]
    for pg in smap.pages:
        lines.append(f"p.{pg.page}: {pg.kind} - {pg.description}" + (f" [{pg.product_ref}]" if pg.product_ref else ""))
    lines.append("\nPRODUCTS")
    for pr in smap.products:
        lines.append(f"{pr.id}: {pr.name} | {pr.manufacturer} {pr.model} | options: {', '.join(pr.options_selected) or '-'} | pages {pr.pages}")
    return "\n".join(lines)


def _req_text(reqs: list[Requirement]) -> str:
    lines = ["REQUIREMENTS CHECKLIST"]
    for r in reqs:
        src = f"{r.source.section} {r.source.article}".strip() + (f" p.{r.source.page}" if r.source.page else "")
        am = f" (amended by {', '.join(r.amended_by)})" if r.amended_by else ""
        lines.append(f"{r.id} [{r.type}] {r.text} -- source: {src} ({r.source.doc}){am}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- scope suggestion (pre-run)

async def suggest_scope_ai(project_id: str, spec_section: str, submittal_text: str, instructions: str) -> ScopeResult:
    tracker = CostTracker.for_job(None, project_id, usd_cap=2.0)
    content = [text_block(
        f"DESIGNATED SPEC SECTION: {spec_section}\nREVIEWER INSTRUCTIONS: {instructions or '-'}\n\n"
        f"SUBMITTAL (first pages, text):\n{submittal_text[:12000]}\n\n{catalog_index_text(project_id)}")]
    run = await run_agent(stage="scope", tracker=tracker, system=prompt("scope"), user_content=content,
                          result_model=ScopeResult, max_turns=3)
    return run.result


# ----------------------------------------------------------------------------- 1 intake

async def run_intake(ctx: JobContext) -> SubmittalMap:
    if mock_mode():
        return mock.intake(ctx.page_texts, ctx.params)
    content = [ctx.submittal_block(), text_block(ctx.header_text() + "\n\nPage-map this submittal.")]
    tools = []
    if not ctx.inline_submittal:
        tools = [t for t in LibraryTools(ctx.guard, "intake", ctx.submittal_path, ctx.submittal_pages).tools()
                 if t.name == "view_submittal_pages"]
    run = await run_agent(stage="intake", tracker=ctx.tracker, system=prompt("intake"), user_content=content,
                          result_model=SubmittalMap, tools=tools, on_event=ctx.on_tool)
    smap = run.result
    # guarantee one entry per page
    known = {p.page for p in smap.pages}
    for i in range(1, ctx.submittal_pages + 1):
        if i not in known:
            smap.pages.append(PageInfo(page=i, kind="other", description=""))
    smap.pages.sort(key=lambda p: p.page)
    smap.pages = [p for p in smap.pages if 1 <= p.page <= ctx.submittal_pages]
    return smap


# ----------------------------------------------------------------------------- 2 planner

def _first_pages_text(paths: list[Path], n: int = 3, chars: int = 5000) -> str:
    out = []
    for p in paths:
        try:
            with pymupdf.open(p) as d:
                t = "\n".join(d[i].get_text("text") for i in range(min(n, d.page_count)))
                total = d.page_count
            out.append(f"--- {p.name} ({total} pages) ---\n{t[:chars]}")
        except Exception:
            continue
    return "\n".join(out)


async def run_planner(ctx: JobContext, smap: SubmittalMap) -> PlannerResult:
    if mock_mode():
        return mock.planner(ctx, smap)
    tools = LibraryTools(ctx.guard, "planner").tools(include_submittal_viewer=False)
    content: list[dict[str, Any]] = [text_block(ctx.header_text()), text_block(_page_map_text(smap))]
    if ctx.div01_paths:
        div = "\n".join(f"--- {p.name} ---\n" + "\n".join(pg.get_text("text") for pg in pymupdf.open(p)) for p in ctx.div01_paths)
        content.append(text_block(f"DIVISION 01 SUBMITTAL PROCEDURES (provided for this run):\n<document_content>\n{div[:60000]}\n</document_content>"))
    if ctx.example_paths:
        names = ", ".join(p.name for p in ctx.example_paths)
        content.append(text_block(
            f"EXAMPLE SUBMITTALS (format reference only, never compliance evidence). File names: {names}\n"
            f"First pages:\n<document_content>\n{_first_pages_text(ctx.example_paths)}\n</document_content>"))
    content.append(text_block(catalog_index_text(ctx.project_id, ctx.guard.scope_ids())))
    content.append(text_block("Read the designated section (and its amendments) with the tools, then build the "
                              "requirements checklist and package rules."))
    run = await run_agent(stage="planner", tracker=ctx.tracker, system=prompt("planner"), user_content=content,
                          result_model=PlannerResult, tools=tools, on_event=ctx.on_tool)
    res = run.result
    # renumber defensively so ids are unique and sequential
    for i, r in enumerate(res.requirements, 1):
        r.id = f"R-{i:03d}"
    return res


# ----------------------------------------------------------------------------- 3a / 3b reviewers

async def run_reviewer(ctx: JobContext, role: str, smap: SubmittalMap, plan: PlannerResult,
                       prefix_ready: asyncio.Event | None = None, wait_for: asyncio.Event | None = None) -> ReviewResult:
    """role: advocate | examiner. Both share an identical cached prefix (system, tools, submittal,
    checklist); only the trailing ROLE block differs, so the second reviewer runs mostly on cache reads."""
    if mock_mode():
        return mock.reviewer(role, ctx, smap, plan)
    if wait_for is not None:
        try:
            await asyncio.wait_for(wait_for.wait(), timeout=240)
        except asyncio.TimeoutError:
            pass
    tools = LibraryTools(ctx.guard, role, ctx.submittal_path, ctx.submittal_pages).tools(
        include_submittal_viewer=True)
    content = [
        text_block(ctx.header_text()),
        text_block(_page_map_text(smap)),
        text_block(catalog_index_text(ctx.project_id, ctx.guard.scope_ids())),
        text_block(_req_text(plan.requirements)),
        ctx.submittal_block(cache=True),
        text_block(prompt(role) + "\n\nEvaluate every requirement now."),
    ]
    run = await run_agent(stage=role, tracker=ctx.tracker, system=prompt("reviewer_common"), user_content=content,
                          result_model=ReviewResult, tools=tools, on_event=ctx.on_tool, web_search=True,
                          first_call_done=prefix_ready)
    for f in run.result.findings:
        f.agent = role  # type: ignore[assignment]
    return run.result


# ----------------------------------------------------------------------------- single-agent mode

async def run_single(ctx: JobContext, smap: SubmittalMap) -> SingleReviewResult:
    """One Opus agent: builds the checklist and reviews it in one pass (no cross-examination)."""
    if mock_mode():
        return mock.single(ctx, smap)
    tools = LibraryTools(ctx.guard, "single", ctx.submittal_path, ctx.submittal_pages).tools(include_submittal_viewer=True)
    content: list[dict[str, Any]] = [text_block(ctx.header_text()), text_block(_page_map_text(smap))]
    if ctx.div01_paths:
        div = "\n".join(f"--- {p.name} ---\n" + "\n".join(pg.get_text("text") for pg in pymupdf.open(p)) for p in ctx.div01_paths)
        content.append(text_block(f"DIVISION 01 SUBMITTAL PROCEDURES (provided for this run):\n<document_content>\n{div[:60000]}\n</document_content>"))
    if ctx.example_paths:
        names = ", ".join(p.name for p in ctx.example_paths)
        content.append(text_block(
            f"EXAMPLE SUBMITTALS (format reference only, never compliance evidence). File names: {names}\n"
            f"First pages:\n<document_content>\n{_first_pages_text(ctx.example_paths)}\n</document_content>"))
    content.append(text_block(catalog_index_text(ctx.project_id, ctx.guard.scope_ids())))
    content.append(ctx.submittal_block(cache=True))
    content.append(text_block("Build the requirements, review every one against the submittal, and call submit_result."))
    run = await run_agent(stage="single", tracker=ctx.tracker, system=prompt("single"), user_content=content,
                          result_model=SingleReviewResult, tools=tools, on_event=ctx.on_tool, web_search=True)
    res = run.result
    # renumber requirements defensively and remap findings
    remap = {}
    for i, r in enumerate(res.requirements, 1):
        remap[r.id] = f"R-{i:03d}"
        r.id = remap[r.id]
    for f in res.findings:
        f.requirement_id = remap.get(f.requirement_id, f.requirement_id)
    return res


# ----------------------------------------------------------------------------- 4 adjudicator

async def run_adjudicator(ctx: JobContext, plan: PlannerResult, disputes: list[dict[str, Any]],
                          notes: str = "") -> AdjudicatorResult:
    if not disputes:
        return AdjudicatorResult(decisions=[])
    if mock_mode():
        return mock.adjudicator(disputes, notes)
    tools = LibraryTools(ctx.guard, "adjudicator", ctx.submittal_path, ctx.submittal_pages).tools()
    body = ["DISPUTED REQUIREMENTS"]
    for d in disputes:
        body.append(json.dumps(d, ensure_ascii=False))
    content = [text_block(ctx.header_text())]
    if notes:
        content.append(text_block(f"REVIEWER RE-RUN NOTES (high priority, from the human reviewer):\n{notes}"))
    # Only the pages the disputes cite (cheap); the page viewer tool can fetch anything else.
    pages = sorted({p for d in disputes for p in (d.get("pages") or []) if 1 <= p <= ctx.submittal_pages})[:20]
    if pages:
        from ..pdf.util import slice_pdf_bytes
        excerpt = base64.b64encode(slice_pdf_bytes(ctx.submittal_path, [p - 1 for p in pages])).decode()
        mapping = ", ".join(f"excerpt p.{i + 1} = submittal p.{p}" for i, p in enumerate(pages))
        content += [text_block(f"SUBMITTAL EXCERPT (cited pages only). Always cite ORIGINAL submittal page numbers: {mapping}. "
                               f"The full submittal has {ctx.submittal_pages} pages; use view_submittal_pages for others."),
                    pdf_block(excerpt, title="Submittal excerpt (disputed pages)", cache=True)]
    else:
        content.append(ctx.submittal_block(cache=True))
    content += [text_block("\n".join(body)), text_block("Decide each disputed requirement and call submit_result.")]
    run = await run_agent(stage="adjudicator", tracker=ctx.tracker, system=prompt("adjudicator"),
                          user_content=content, result_model=AdjudicatorResult, tools=tools,
                          on_event=ctx.on_tool, web_search=True)
    return run.result


# ----------------------------------------------------------------------------- 6 QA

async def run_qa(ctx: JobContext, pdf_path: Path, page_numbers: list[int], filename: str, rules: PackageRules,
                 txt: str) -> QAResult:
    if mock_mode():
        return QAResult(passed=True, issues=[])
    from ..pdf.util import render_png
    content: list[dict[str, Any]] = []
    with pymupdf.open(pdf_path) as doc:
        for pno in page_numbers[:8]:
            if 0 <= pno < doc.page_count:
                png = render_png(doc, pno, dpi=72, max_px=1200, annots=True)
                content.append(text_block(f"Output page {pno + 1}"))
                content.append(image_block(base64.b64encode(png).decode()))
    content.append(text_block(f"OUTPUT FILE NAME: {filename}\nPACKAGE RULES: {rules.model_dump_json()}\n\n"
                              f"REVIEW NOTES TXT:\n{txt[:20000]}"))
    run = await run_agent(stage="qa", tracker=ctx.tracker, system=prompt("qa"), user_content=content,
                          result_model=QAResult, max_turns=4)
    return run.result
