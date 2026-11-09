# Ajax Submittal Manager

AI-assisted first-pass submittal review for Ajax Building Company. Drop a Product Data or Shop Drawing
package, point it at the project's specs and drawings, and get back:

1. **An A/E-ready PDF** - named per Division 01, cover/transmittal, TOC, deviation summary, bookmarks, and
   markups (selections, X-outs, clouded deviations, notes, unsigned review stamp) that stay **fully editable
   in Bluebeam Revu**.
2. **A review-notes TXT** - decisions with citations, decided non-compliances, and a separate Human Review
   block (questionable compliance, RFI candidates, faulty/contradictory data), readable in under 5 minutes.

Two independent Claude agents cross-examine every requirement - a **Compliance Advocate** looking for proof
and a **Non-Compliance Examiner** hunting deviations - and an **Adjudicator** settles disagreements or sends
them to a human. A project **catalog** keeps every run to just the spec sections, articles and sheets it needs.

> ASM prepares, flags and explains. It never approves, signs, or submits anything. A qualified person
> verifies every output before it goes to the A/E or into Procore.

---

## Quick start (Windows - no install)

1. Download **`AjaxSubmittalManager.exe`** from the repo's
   [Releases](https://github.com/AjaxAgents/AjaxSubmittalManager/releases/latest) page and put it in any folder.
2. Double-click it. A small control window opens and the app opens in your browser. Keep the control window
   open while you work; **Quit** stops the app.
3. Settings opens automatically the first time: paste your **Anthropic API key** (from
   <https://console.anthropic.com>) and save.

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

1. **Create a project** - name, number, A/E, contractor (these fill every cover sheet).
2. **Zone 2 - Project information**: drop the project manual, drawing set, addenda/ASIs/RFIs once. ASM splits
   specs by section > part > article, drawings by sheet, tags amendments to what they change, and builds the
   catalog (see *Project library*).
3. **New review**: enter the spec section and submittal number; drop the submittal (zone 1), example
   submittals (zone 3) and optional Division 01 procedures (zone 4).
4. **Reference scope**: ASM pre-selects the sections, articles, sheets and amendments it expects to need.
   Add/remove items, trim pages, and choose *Flexible* (agents may pull up to 20 extra pages, each logged) or
   *Strict* (anything else becomes a Human Review item). The scope is remembered per spec section.
5. **Reviewer instructions** (e.g. "hardware sets 3-5 only", "substitution approved per RFI 112") go to every
   agent. Check the **file name preview**.
6. **Run**. Watch each stage, retrievals and live cost. When done, page through the marked-up PDF, filter the
   requirements matrix, read the Human Review list and TXT, and download both files.
7. **Re-run** with notes ("N-02 accepted per RFI 112") to redo only adjudication and assembly - fast and cheap.

Outputs are copied to `Outputs\<project>\` next to the exe (each run's full audit trail stays in the data folder).

## Cost control

Only the two cross-examining reviewers run on Opus; Intake and library cataloguing use Haiku, the Planner and
Adjudicator use Sonnet, and packaging/QA are plain code (`config/models.yaml`). The reviewers share a cached
prompt prefix, so the second one reads the submittal at 5% of the input price. The Adjudicator sees only
disputed items and their pages.

Typical estimates (verify against the *Sources & cost* tab after real runs):

| Submittal | Estimated cost |
| --- | --- |
| 10-page product data | ~$0.70-1.30 |
| 40-page product data | ~$1.20-2.50 |
| 75-page package | ~$2.00-3.90 |

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
Claude API. Confirm you are permitted to send project documents to a third-party AI service under the
owner/contract confidentiality terms. `data/` is git-ignored; keep confidential eval cases out of git.
