# Ajax Submittal Manager

First-pass submittal review for Ajax Building Company. Drop a Product Data or Shop Drawing
package, point it at the project's specs and drawings, and get back:

1. **An A/E-ready PDF** - named per Division 01, cover/transmittal, TOC, deviation summary, bookmarks, and
   markups (selections, X-outs, clouded deviations, notes, unsigned review stamp) that stay **fully editable
   in Bluebeam Revu**.
2. **A review-notes TXT** - decisions with citations, decided non-compliances, and a separate Human Review
   block (questionable compliance, RFI candidates, faulty/contradictory data), readable in under 5 minutes.

Two independent Claude agents cross-examine every requirement - a **Compliance Advocate** looking for proof
and a **Non-Compliance Examiner** hunting deviations - and an **Adjudicator** settles disagreements or sends
them to a human. A project **catalog** keeps every run to just the spec sections, articles and sheets it needs.

---

## Quick start (Windows - no install)

1. Download **`AjaxSubmittalManager.exe`** from the repo's
   [Releases](https://github.com/AjaxAgents/AjaxSubmittalManager/releases/latest) page and put it in any folder.
2. Double-click it. A small control window opens and the app opens in your browser. Keep the control window
   open while you work; **Quit** stops the app.
3. Create your account (the first account becomes the admin and takes over any projects from earlier
   versions). Settings then opens: paste your **Anthropic API key** (from <https://console.anthropic.com>)
   and save. Each account keeps its own key (encrypted with Windows DPAPI), projects, submittals and theme.

Where things live:
- Finished PDFs/TXTs: an **`Outputs`** folder next to the exe (changeable in `config/settings.yaml` > `output.folder`).
- API key, database, project libraries, run history: `%LOCALAPPDATA%\AjaxSubmittalManager` - kept out of
  OneDrive on purpose (the key should not sync, and the database does not like synced folders).
- Optional overrides: put edited `config/*.yaml` or `prompts/*.md` files in `config\` / `prompts\` folders next
  to the exe; anything not overridden uses the built-in defaults.

If IT policy blocks single-file executables, use **`AjaxSubmittalManager-folder.zip`** from the same release
(unzip, run `AjaxSubmittalManager.exe` inside). The exe is unsigned, so Windows SmartScreen may ask you to
click *More info > Run anyway* the first time; ask IT to allow-list it if needed.

No API key yet? Turn on **Offline demo mode** in Settings to click through with heuristic stand-ins (not a real review).

### Running from source (developers)
Python 3.11+: `start.bat` (Windows) or `./start.sh`, or `make backend`. Docker: `docker compose up --build`.
Build the exe yourself on Windows: `pip install -r backend/requirements.txt pyinstaller` then
`pyinstaller packaging/AjaxSubmittalManager.spec --noconfirm` (CI does this on every merge to `main` and
publishes it to Releases).

## Using it

Each project has a **Home** page (counts, recent submittals), a **Submittals** register (grouped and
searchable by division, with status, subcontractor and revision), **New Submittal Review**, and a
**Project Library**.

1. **Create a project** - name, number, A/E, contractor, owner, address (these fill every cover page).
2. **Project Library** (load once per project):
   - *Project information* - upload the project manual, drawings, addenda/ASIs/RFIs. Pick the type
     (Spec / Drawings / Amendment / Document, or Auto) and division **before** uploading; files are stored
     but not processed until you press **Process**. Select rows to bulk-change type or division, re-process,
     or delete; **Stop processing** puts anything queued or running back to *Not processed*. Processing splits specs by section > part > article and drawings by sheet, tags amendments,
     and builds the catalog.
   - *Quality templates* (example submittals), *Division 01* procedures, and *Cover page* templates. Mark the
     ones to use by default. For a cover template, **Map fields** links each form field or text box to
     submittal data; you can also type `{ProjectName}`, `{SubmittalNo}`, `{SpecSection}`, `{Subcontractor}`
     etc. anywhere on the template. Covers are filled by fixed rules, not by the model.
   - *Divisions & subcontractors* - preload the divisions in play and who holds each scope.
   - *Catalog* - filter by specs, drawings, amendments or documents (and by division or discipline), sort, and click
     an item to read its pages beside the list or full screen.
3. **New Submittal Review**: drop the submittal PDF, then **Auto-fill details** to read the spec section,
   type, subcontractor and title from its first pages. The subcontractor, spec section, submittal number and
   revision autocomplete from the project. Under *Reference materials*, the project-information scope is
   pre-selected (add anything else the review needs, e.g. the concrete spec and a footing detail for a fence
   submittal); quality template, Division 01 and cover page default from the library.
4. Choose **Cross-examined** (two reviewers + adjudicator) or **Single pass**, and **Run review**. Watch each
   stage and live cost; then page through the marked-up PDF, the requirements matrix and the TXT notes.
5. **Re-run** with notes ("N-02 accepted per RFI 112") to redo only adjudication and assembly.

Settings has light/dark mode, your API key, password, and review defaults.

Outputs are copied to `Outputs\<project>\` next to the exe (each run's full audit trail stays in the data folder).

## Cost control

Only the two cross-examining reviewers run on Opus; Intake and library cataloguing use Haiku, the Planner and
Adjudicator use Sonnet, and packaging/QA are plain code (`config/models.yaml`). The reviewers share a cached
prompt prefix, so the second one reads the submittal at 5% of the input price. The Adjudicator sees only
disputed items and their pages.

**Review mode** (toggle next to the Run button; default in Settings):
- **Cross-examined** (default) - Compliance Advocate + Non-Compliance Examiner (2 Opus agents, independent),
  disagreements adjudicated by Sonnet. Most thorough; use for packages going to the A/E.
- **Single agent** - one Opus agent builds the checklist and reviews in one pass, arguing both sides itself;
  citations are still machine-verified and anything unverified goes to Human Review. ~35-40% cheaper and faster;
  good for resubmittals and simple product data. `python eval/run_eval.py --mode both` compares the two on
  your past submittals.

Typical estimates (verify against the *Sources & cost* tab after real runs):

| Submittal | Cross-examined | Single agent |
| --- | --- | --- |
| 10-page product data | ~$0.85-1.70 | ~$0.50-1.00 |
| 40-page product data | ~$1.20-2.50 | ~$0.80-1.60 |
| 75-page package | ~$1.60-3.30 | ~$1.15-2.30 |

Every job has a dollar cap (default $6, Settings): at 80% Opus stages fall back to Sonnet; at 100% the job
stops and unreviewed items go to Human Review. The pre-run estimate is shown next to the Run button.
Prices live in `config/pricing.yaml`.

## Bluebeam compatibility

`make gate` (or `python scripts/make_bluebeam_gate.py`) writes `data/bluebeam_gate.pdf` with every markup type
on an upright and a rotated page. Follow `docs/BLUEBEAM_GATE.md` and record your Revu version.

## Development

```
make install   # backend + frontend deps
make test      # backend tests (offline)
make dev       # backend :8765 + Vite :5173 with hot reload
make ui        # rebuild frontend/dist (committed so end users need no Node)
make eval      # evaluation set in eval/cases (real API)
```

See `CLAUDE.md` for conventions and `docs/ARCHITECTURE.md` for how the spec maps to code.

## Licensing

Repository: Apache-2.0. **PyMuPDF is AGPL-3.0 / commercial.** Internal use is generally fine; confirm with
Ajax/STO legal before distributing ASM or hosting it for outside users (or buy a PyMuPDF commercial license).

## Data handling

Runs locally and binds to localhost. Project documents leave the machine only as the slices sent to the
Claude API. Confirm you are permitted to send project documents to a third-party service under the
owner/contract confidentiality terms. `data/` is git-ignored; keep confidential eval cases out of git.
