# Architecture (spec V2 -> code)

```
Browser (React)  --REST/SSE-->  FastAPI (localhost:8765)  --Messages API-->  Claude
                                  |-- SQLite: users, sessions, projects, subcontractors, submittals,
                                  |           library files, catalog (FTS5), jobs, api_calls, retrievals
                                  |-- data/: library PDFs, uploads, jobs/<id>/{work,outputs,events.jsonl}
```

| Spec section | Code |
| --- | --- |
| 5 GUI: project home, submittal register, new review, library, progress, results | `frontend/src/App.tsx`, `pages/ProjectPages.tsx`, `pages/NewReview.tsx`, `components/ScopeSelector.tsx`, `pages/RunView.tsx`, `pages/Library.tsx` |
| Accounts: login, sessions, per-user encrypted API key, project ownership | `backend/asm/auth.py`, `api/app.py` (`require_login` middleware), `frontend/src/pages/Login.tsx` |
| Submittal register + subcontractors (status follows the latest run) | `backend/asm/db.py`, `pipeline.py` |
| Auto-fill review details from the submittal (rules, then Haiku if a key is set) | `backend/asm/autofill.py` |
| Cover page templates (field mapping, `{Token}` fill, no model) | `backend/asm/pdf/cover_template.py`, `components/CoverMapper.tsx` |
| 6 Library ingest (section/part/article split, sheets, amendments, Haiku summaries) | `backend/asm/library/ingest.py` |
| 6 Catalog index (Tier 0), scope tree, heuristic scope | `backend/asm/library/catalog.py` |
| 6 Retrieval tools + scope enforcement (flexible/strict, page budget, logging) | `backend/asm/library/tools.py` |
| 7 Stages: Intake, Planner, Advocate, Examiner, Adjudicator, QA | `backend/asm/agents/stages.py`, prompts in `prompts/` |
| 7 Tool loop, submit_result schema validation, caching | `backend/asm/agents/base.py` |
| 7 Verifier + adjudication table | `backend/asm/agents/adjudicate.py` |
| 8 Markup engine (annotations, /NM, /AP, layers, rotation) | `backend/asm/pdf/markup.py`, gate in `pdf/gate.py` |
| 9 Package: cover, TOC, deviation summary, bookmarks, labels, metadata, size cap | `backend/asm/outputs/packager.py`, `pdf/pages.py` |
| 9 Naming (reviewer > example > Division 01 > default) | `backend/asm/outputs/naming.py`, `config/naming.yaml` |
| 10 Review notes TXT | `backend/asm/outputs/txt_writer.py` |
| 11 Data models | `backend/asm/schemas.py` |
| 12 Models, budgets, cost logging | `config/models.yaml`, `config/pricing.yaml`, `backend/asm/agents/llm.py` |
| 15 Audit trail | `data/jobs/<id>/events.jsonl`, `work/*.json`, `api_calls`, `retrievals` tables |
| 16 Evaluation | `eval/run_eval.py` |

## Run flow (`pipeline.py`)
1. **Prepare** - merge the submittal files in order, OCR if scanned, lock scope (designated section always in).
2. **Intake** (Haiku) - page map, products, models, selected options.
3. **Planner** (Sonnet) - reads designated section + amendments via tools; atomic requirements with citations;
   package rules from Division 01 and examples.
4. **Advocate / Examiner** (Opus, parallel, independent) - identical cached prefix; only the ROLE block differs.
5. **Verify + adjudicate** - citations checked against page text; rule table settles agreements; Sonnet
   Adjudicator decides disputes or escalates to Human Review.
6. **Assemble** - markups chosen per verdict (A/E layer vs hidden internal layer), package built, named.
7. **QA** - deterministic checks always; optional Sonnet visual check (`qa.llm_check`) (blank/duplicate pages, size,
   unanchored markups). Notes go into the TXT.

Re-run reuses intake/planner/reviewer JSON and redoes 5-7 with the reviewer's notes.

**Single-agent mode** (`review_mode: single`): steps 3-4 are replaced by one Opus agent (`prompts/single.md`,
`stages.run_single`) that returns requirements + final verdicts + markups (`SingleReviewResult`). It is
converted to the two-reviewer shapes (`adjudicate.single_to_parts`) so citation verification, markup selection,
re-runs and packaging are shared; `single_verdicts` sends any unverified or low-confidence call to Human Review.

## Deviations from the written spec (deliberate, v1)
- Plain CSS with design tokens instead of Tailwind (fewer moving parts; same tokens).
- Page previews are rendered server-side by PyMuPDF (includes annotations exactly as written) instead of pdf.js.
- Library summaries use concurrent Haiku calls instead of the Message Batches API (users usually wait for ingest;
  Haiku cost is negligible). Batches can be added in `library/ingest.py::_ai_summaries`.
- Submittals are sent inline (base64 PDF, cached) instead of via the Files API; larger packages switch to
  page text + an on-demand page viewer tool.

## Accounts and library (v0.4)
- Local accounts only (scrypt hashes, 14-day httponly session cookie). Every `/api` route except auth and
  health requires a session; project-scoped routes check `owner_id`. The first account claims ownerless
  projects from earlier versions. LLM calls use the project owner's key (`auth.api_key_for_project`), falling
  back to the global `.env` key.
- Library uploads are stored with a type chosen up front (`status=uploaded`) and only split/catalogued when
  the user presses Process (`/library/process`); bulk type/division/delete via `/library/bulk`.
- Quality templates, Division 01 and cover templates are library files (`kind` example/div01/cover) picked per
  review through `params.library_refs`. A cover template is inserted as page 1 and filled by
  `cover_template.fill`; the generated cover follows only if requested.
