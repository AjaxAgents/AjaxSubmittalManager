import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, DIVISION_NAMES, Estimate, fmtBytes, Job, LibraryFile, Project, ScopeItem, Sub, TreeNode, Upload } from "../api";
import { entriesToFiles } from "../components/DropZone";
import { Combobox, FilePicker, Opt } from "../components/Pickers";
import { ScopeSelector } from "../components/ScopeSelector";
import { Badge, Button, Field, Icon, useToast } from "../components/ui";

const TYPES = ["Product Data", "Shop Drawings", "Product Data and Shop Drawings", "Other"];
const normSec = (s: string) => { const d = s.replace(/\D/g, ""); return d.length >= 6 ? `${d.slice(0, 2)} ${d.slice(2, 4)} ${d.slice(4, 6)}` : ""; };

function useDebounced<T>(v: T, ms = 450): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export interface Prefill { spec_section?: string; spec_title?: string; seq?: number; revision?: string; title?: string; type?: string; subcontractor_id?: string; library_refs?: any }

export function NewReview({ project, tree, library, defaults, subs, onSubsChanged, onStarted, settingsMock, apiKeySet, defaultMode, goLibrary, prefill }: {
  project: Project; tree: TreeNode[]; library: LibraryFile[]; defaults: any; subs: Sub[]; onSubsChanged: () => void; onStarted: (j: Job) => void;
  settingsMock: boolean; apiKeySet: boolean; defaultMode?: string; goLibrary: (tab: string) => void; prefill?: Prefill | null;
}) {
  const toast = useToast();
  const [form, setForm] = useState({ spec_section: "", spec_title: "", submittal_no: "", revision: "0", submittal_type: "Product Data", title: "", required_date: "" });
  const [subName, setSubName] = useState("");
  const [subId, setSubId] = useState("");
  const [files, setFiles] = useState<Upload[]>([]);
  const [uploading, setUploading] = useState(0);
  const [over, setOver] = useState(false);
  const [filling, setFilling] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [namingPattern, setNamingPattern] = useState("");
  const [mimic, setMimic] = useState(false);
  const [mode, setMode] = useState(defaultMode === "single" ? "single" : "cross");
  const [scope, setScope] = useState<ScopeItem[]>([]);
  const [scopeMode, setScopeMode] = useState("flexible");
  const [scopeSrc, setScopeSrc] = useState("");
  const [scopeDirty, setScopeDirty] = useState(false);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [sections, setSections] = useState<{ number: string; title: string; division: string }[]>([]);
  const [examples, setExamples] = useState<string[]>(defaults?.examples || []);
  const [div01, setDiv01] = useState<string[]>(defaults?.div01 || []);
  const [cover, setCover] = useState<string[]>(defaults?.cover ? [defaults.cover] : []);
  const [est, setEst] = useState<Estimate | null>(null);
  const [preview, setPreview] = useState<{ name: string; source: string; number: string } | null>(null);
  const [starting, setStarting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const depth = useRef(0);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => { if (defaultMode) setMode(defaultMode === "single" ? "single" : "cross"); }, [defaultMode]);
  useEffect(() => { api.sections(project.id).then(setSections).catch(() => {}); }, [project.id, tree]);
  useEffect(() => {
    if (!prefill) return;
    setForm((f) => ({ ...f, spec_section: prefill.spec_section || "", spec_title: prefill.spec_title || "", submittal_no: prefill.seq ? String(prefill.seq).padStart(2, "0") : "",
      revision: prefill.revision ?? "0", title: prefill.title || "", submittal_type: prefill.type || "Product Data" }));
    if (prefill.subcontractor_id) { const s = subs.find((x) => x.id === prefill.subcontractor_id); setSubId(prefill.subcontractor_id); setSubName(s?.name || ""); }
    const refs = prefill.library_refs || {};
    if (refs.examples) setExamples(refs.examples);
    if (refs.div01) setDiv01(refs.div01);
    if (refs.cover) setCover([refs.cover]);
  }, [prefill]);

  const secNorm = normSec(form.spec_section);
  const designated = secNorm ? `S:${secNorm}` : "";
  const secMatch = sections.find((s) => s.number === secNorm);
  useEffect(() => { if (secMatch && !form.spec_title) set("spec_title", secMatch.title); }, [secMatch?.number]);

  // next free sequence for this section (only when the user hasn't typed one)
  const seqTouched = useRef(false);
  useEffect(() => {
    if (!secNorm || seqTouched.current || prefill?.seq) return;
    api.nextSeq(project.id, secNorm).then((r) => set("submittal_no", String(r.seq).padStart(2, "0"))).catch(() => {});
  }, [secNorm]);

  // ---------------------------------------------------------------- options
  const sub = subs.find((s) => s.id === subId);
  const subOpts: Opt[] = subs.map((s) => ({ value: s.id, label: s.name, sub: (s.divisions || []).map((d) => `Div ${d}`).join(", ") }));
  const secOpts: Opt[] = useMemo(() => {
    const preferred = new Set(sub?.divisions || []);
    const all = sections.map((s) => ({ value: s.number, label: s.number, sub: s.title, group: `${preferred.has(s.division) ? "This subcontractor - " : ""}Division ${s.division} ${DIVISION_NAMES[s.division] || ""}`.trim(), d: s.division }));
    all.sort((a, b) => (preferred.has(b.d) ? 1 : 0) - (preferred.has(a.d) ? 1 : 0) || a.value.localeCompare(b.value));
    return all;
  }, [sections, sub]);
  const libOpts = (kind: string): Opt[] => library.filter((f) => f.kind === kind).map((f) => ({ value: f.id, label: f.filename, sub: `${f.pages} pp` }));

  // ---------------------------------------------------------------- uploads
  const addFiles = async (list: File[]) => {
    const pdfs = list.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) { toast("Only PDF files can be reviewed.", "bad"); return; }
    setUploading((n) => n + pdfs.length);
    try {
      const r = await api.upload(pdfs);
      setFiles((x) => { const have = new Set(x.map((u) => u.sha256)); return [...x, ...r.files.filter((u) => !have.has(u.sha256))]; });
      r.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad"));
    } catch (e: any) { toast(e.message, "bad"); } finally { setUploading((n) => n - pdfs.length); }
  };
  const move = (from: number, to: number) => setFiles((x) => { const a = [...x]; const [m] = a.splice(from, 1); a.splice(to, 0, m); return a; });
  const subIds = files.map((u) => u.id);

  const autofill = async () => {
    if (!files.length) return;
    setFilling(true);
    try {
      const r = await api.autofill(project.id, subIds);
      setForm((f) => ({
        ...f, spec_section: r.spec_section || f.spec_section, spec_title: r.spec_title || f.spec_title,
        submittal_type: r.submittal_type || f.submittal_type, title: r.title || f.title,
        revision: r.revision || f.revision,
        submittal_no: r.sequence ? String(r.sequence).replace(/\D/g, "").padStart(2, "0") || f.submittal_no : r.next_seq ? String(r.next_seq).padStart(2, "0") : f.submittal_no,
      }));
      if (r.subcontractor) { setSubName(r.subcontractor); setSubId(r.subcontractor_id || ""); }
      const got = ["spec_section", "submittal_type", "subcontractor", "title"].filter((k) => r[k]).length;
      toast(got ? `Filled ${got} field${got > 1 ? "s" : ""} from the submittal - check them before running` : "Nothing recognisable found on the first pages", got ? "ok" : "info");
    } catch (e: any) { toast(e.message, "bad"); } finally { setFilling(false); }
  };

  // ---------------------------------------------------------------- scope, estimate, naming
  const dSec = useDebounced(secNorm, 400);
  const suggest = async (reset = false) => {
    if (!dSec) return;
    setScopeLoading(true);
    try {
      const r = await api.suggestScope(project.id, { spec_section: dSec, upload_ids: subIds, instructions, reset });
      let items = r.items;
      if (!items.some((i) => i.id === `S:${dSec}`) && r.found) items = [{ id: `S:${dSec}`, locked: true, reason: "Designated section" }, ...items];
      setScope(items); setScopeSrc(r.source); if (r.mode) setScopeMode(r.mode); setScopeDirty(false);
    } catch (e: any) { toast(`Reference suggestion failed: ${e.message}`, "bad"); } finally { setScopeLoading(false); }
  };
  useEffect(() => { if (dSec && !scopeDirty) suggest(); }, [dSec, project.library.sections, subIds.length]);
  const dScope = useDebounced(JSON.stringify(scope), 500);
  useEffect(() => {
    if (!subIds.length) { setEst(null); return; }
    api.estimate(project.id, { upload_ids: subIds, scope_items: scope, review_mode: mode }).then(setEst).catch(() => {});
  }, [dScope, subIds.join(","), mode]);
  const dForm = useDebounced(JSON.stringify({ form, namingPattern, mimic, examples }), 350);
  useEffect(() => {
    if (!secNorm) { setPreview(null); return; }
    api.namingPreview(project.id, { ...form, spec_section: secNorm, naming_pattern: namingPattern, mimic_example_naming: mimic }, examples)
      .then(setPreview).catch(() => setPreview(null));
  }, [dForm]);

  const problems: string[] = [];
  if (!files.length) problems.push("Add the submittal PDF");
  if (!secNorm) problems.push("Choose the spec section");
  if (!apiKeySet && !settingsMock) problems.push("Add your Anthropic API key in Settings");

  const run = async () => {
    if (problems.length) { toast(problems[0], "bad"); return; }
    setStarting(true);
    try {
      const job = await api.createJob(project.id, {
        ...form, spec_section: secNorm, supplier: subName, subcontractor_id: subId, instructions, naming_pattern: namingPattern,
        mimic_example_naming: mimic, review_mode: mode, uploads: { submittal: subIds },
        library_refs: { examples, div01, cover: cover[0] || "" },
        scope: { mode: scopeMode, items: scope.length ? scope : [{ id: designated, locked: true }] },
      });
      onSubsChanged();
      onStarted(job);
    } catch (e: any) { toast(e.message, "bad"); } finally { setStarting(false); }
  };

  const totalPages = files.reduce((s, u) => s + u.pages, 0);
  const scopePages = scope.length;
  const pendingLib = project.library.unprocessed + project.library.processing;

  return (
    <div className="page page-narrow">
      <header className="page-head">
        <h1>New Submittal Review</h1>
        {settingsMock && <Badge tone="warn" title="Offline demo mode uses simple rules instead of the API. Turn it off in Settings.">Offline demo mode</Badge>}
      </header>

      {/* ------------------------------------------------ 1. the submittal: the only drop target on this page */}
      <section
        className={`sheet ${over ? "sheet-over" : ""} ${files.length ? "sheet-has" : ""}`}
        onDragEnter={(e) => { e.preventDefault(); depth.current++; setOver(true); }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => { depth.current--; if (depth.current <= 0) { setOver(false); depth.current = 0; } }}
        onDrop={async (e) => { e.preventDefault(); depth.current = 0; setOver(false); const viaEntries = await entriesToFiles(e.dataTransfer.items); addFiles(viaEntries.length ? viaEntries : Array.from(e.dataTransfer.files)); }}
        aria-label="Submittal"
      >
        <div className="sheet-body">
          <div className="sheet-head">
            <h2>Submittal</h2>
            <p>Product data or shop drawings to review. Several PDFs are merged in the order shown.</p>
          </div>
          <button type="button" className="sheet-drop" onClick={() => fileInput.current?.click()}>
            {uploading ? <span className="spinner spinner-brand" /> : <Icon name="upload" size={22} />}
            <span><b>Drop the submittal PDF here</b> or click to browse</span>
          </button>
          <input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
          {files.length > 0 && (
            <ul className="chips">
              {files.map((u, i) => (
                <li key={u.id} className="chip" draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", String(i))}
                  onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); e.stopPropagation(); const from = Number(e.dataTransfer.getData("text/plain")); if (!Number.isNaN(from)) move(from, i); }}>
                  <Icon name="grip" size={14} className="chip-grip" />
                  <Icon name="file" size={16} className="chip-file" />
                  <div className="chip-main"><span className="chip-name mono">{u.filename}</span>
                    <span className="chip-meta">{u.pages} pages, {fmtBytes(u.size)}{u.flags?.includes("ocr_needed") && <span className="t-warn"> - scanned, text will be recognised</span>}</span></div>
                  <button className="icon-btn" onClick={() => setFiles((x) => x.filter((y) => y.id !== u.id))} aria-label={`Remove ${u.filename}`}><Icon name="x" size={15} /></button>
                </li>
              ))}
            </ul>
          )}
          {files.length > 0 && (
            <div className="sheet-actions">
              <Button icon="wand" onClick={autofill} loading={filling}>Auto-fill details from submittal</Button>
              <span className="muted small">Reads the first pages for spec section, type, subcontractor and title.</span>
            </div>
          )}
        </div>
        <dl className="titleblock" aria-label="Submittal title block">
          <div className="tb-wide"><dt>Project</dt><dd>{project.number ? `${project.number} ` : ""}{project.name}</dd></div>
          <div><dt>Spec section</dt><dd className="mono">{secNorm || "\u2014"}</dd></div>
          <div><dt>Number</dt><dd className="mono">{preview?.number?.split(" - ").pop() || "\u2014"}</dd></div>
          <div className="tb-wide"><dt>Subcontractor</dt><dd>{subName || "\u2014"}</dd></div>
          <div><dt>Type</dt><dd>{form.submittal_type.replace("Product Data and Shop Drawings", "PD + SD")}</dd></div>
          <div><dt>Pages</dt><dd className="mono">{totalPages || "\u2014"}</dd></div>
        </dl>
      </section>

      {/* ------------------------------------------------ details */}
      <section className="panel">
        <h3 className="panel-title">Submittal details</h3>
        <div className="form-grid">
          <Field label="Subcontractor" className="c7">
            <Combobox ariaLabel="Subcontractor" value={subName} options={subOpts} placeholder="Start typing or pick from the project list"
              onChange={(v) => { setSubName(v); setSubId(""); }} onPick={(o) => { setSubName(o.label); setSubId(o.value); }}
              onCreate={(t) => { setSubName(t); setSubId(""); toast(`"${t}" will be added to this project's subcontractors when you run the review`, "info"); }} />
          </Field>
          <Field label="Spec section" className="c5" hint={secNorm ? (secMatch ? secMatch.title : "Not in the project library") : undefined}>
            <Combobox ariaLabel="Spec section" mono value={form.spec_section} options={secOpts} placeholder="08 71 00"
              emptyText="No spec sections catalogued yet - add them in Project Library"
              onChange={(v) => { set("spec_section", v); set("spec_title", ""); seqTouched.current = false; setScopeDirty(false); }}
              onPick={(o) => { set("spec_section", o.value); set("spec_title", o.sub || ""); seqTouched.current = false; setScopeDirty(false); }} />
          </Field>
          <Field label="Section title" className="c6"><input className="input" value={form.spec_title} onChange={(e) => set("spec_title", e.target.value)} placeholder="Filled from the library" /></Field>
          <Field label="Submittal no." className="c3"><input className="input mono" value={form.submittal_no} onChange={(e) => { seqTouched.current = true; set("submittal_no", e.target.value); }} placeholder="01" /></Field>
          <Field label="Revision" className="c3"><input className="input mono" value={form.revision} onChange={(e) => set("revision", e.target.value)} /></Field>
          <Field label="Type" className="c4">
            <select className="input" value={form.submittal_type} onChange={(e) => set("submittal_type", e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
          </Field>
          <Field label="Title / description" className="c8"><input className="input" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Door Hardware - Sets 1-4" /></Field>
          <Field label="Review requested by" className="c4"><input className="input" type="date" value={form.required_date} onChange={(e) => set("required_date", e.target.value)} /></Field>
        </div>
      </section>

      {/* ------------------------------------------------ reference materials: quiet rows, library-backed */}
      <section className="panel">
        <h3 className="panel-title">Reference materials <button className="link-btn small" onClick={() => goLibrary("info")}>Manage in Project Library</button></h3>
        {pendingLib > 0 && <p className="note-warn"><Icon name="alert" size={15} /> {pendingLib} project file{pendingLib > 1 ? "s are" : " is"} not processed yet, so {pendingLib > 1 ? "they" : "it"} can't be referenced. <button className="link-btn" onClick={() => goLibrary("info")}>Process in Project Library</button></p>}
        <div className="refrow">
          <div className="refrow-label"><b>Project information</b><span>Spec sections, drawing sheets and documents this review may read</span></div>
          <button className="fpick-btn has" onClick={() => setScopeOpen(!scopeOpen)} disabled={!secNorm} aria-expanded={scopeOpen}>
            <span className="fpick-label">{!secNorm ? "Choose the spec section first" : scopeLoading ? "Finding related references..." : `${scopePages} reference${scopePages === 1 ? "" : "s"} selected`}</span>
            <Icon name="down" size={15} className={scopeOpen ? "rot180" : ""} />
          </button>
        </div>
        {scopeOpen && secNorm && (
          <div className="refrow-expand">
            <p className="muted small">Add anything else the reviewer must check against - for example the concrete spec and the footing detail for a fence submittal. Search by number, title or keyword.</p>
            <ScopeSelector tree={tree} items={scope} designated={designated} mode={scopeMode} onMode={setScopeMode} loading={scopeLoading}
              source={scopeSrc} onReset={() => suggest(true)} onChange={(i) => { setScope(i); setScopeDirty(true); }} />
          </div>
        )}
        <div className="refrow">
          <div className="refrow-label"><b>Quality templates</b><span>Approved examples to copy format and order from</span></div>
          <FilePicker multiple options={libOpts("example")} selected={examples} onChange={setExamples} placeholder="None" emptyText="No quality templates in the library yet"
            footer={<button className="link-btn small" onClick={() => goLibrary("example")}>Add in Project Library</button>} />
        </div>
        <div className="refrow">
          <div className="refrow-label"><b>Division 01 submittal procedures</b><span>Naming, cover and package rules</span></div>
          <FilePicker multiple options={libOpts("div01")} selected={div01} onChange={setDiv01} placeholder="Use 01 33 00 from the spec library" emptyText="No Division 01 files in the library yet"
            footer={<button className="link-btn small" onClick={() => goLibrary("div01")}>Add in Project Library</button>} />
        </div>
        <div className="refrow">
          <div className="refrow-label"><b>Cover page</b><span>First page of the package, filled in from these details</span></div>
          <FilePicker options={libOpts("cover")} selected={cover} onChange={setCover} placeholder="Standard Ajax cover" emptyText="No cover templates in the library yet"
            footer={<button className="link-btn small" onClick={() => goLibrary("cover")}>Add in Project Library</button>} />
        </div>
      </section>

      {/* ------------------------------------------------ instructions & naming */}
      <section className="panel">
        <button className="panel-toggle" onClick={() => setMoreOpen(!moreOpen)} aria-expanded={moreOpen}>
          <span><b>Instructions and file naming</b><span className="muted small"> {instructions ? "Instructions added" : "Optional"} {preview ? `- ${preview.name}` : ""}</span></span>
          <Icon name="down" size={16} className={moreOpen ? "rot180" : ""} />
        </button>
        {moreOpen && (
          <div className="more-grid">
            <Field label="Reviewer instructions" hint={`${instructions.length}/2000 - passed to the reviewer as a high-priority note`}>
              <textarea className="input textarea" maxLength={2000} rows={5} value={instructions} onChange={(e) => setInstructions(e.target.value)}
                placeholder={'Review hardware sets 3-5 only\nSubstitution approved per RFI 112: LCN 4040XP\nInclude spec excerpt'} />
            </Field>
            <div>
              <Field label="File name pattern (optional)" hint="Tokens: {SpecSection} {Seq:02d} {Rev} {SpecTitle} {TypeAbbr} {Title} {ProjectNumber} {Date}">
                <input className="input mono" placeholder="{SpecSection} - {Seq:02d}.{Rev} {SpecTitle} - {TypeAbbr}" value={namingPattern} onChange={(e) => setNamingPattern(e.target.value)} />
              </Field>
              <label className="toggle"><input type="checkbox" checked={mimic} onChange={(e) => setMimic(e.target.checked)} /><span className="toggle-ui" /> Copy naming from the quality template's file name</label>
            </div>
          </div>
        )}
        {preview && <div className="filename"><Icon name="file" size={15} /><code>{preview.name}</code><span className="muted small">from {preview.source}</span></div>}
      </section>

      <div className="runbar">
        <div className="seg seg-mode" role="radiogroup" aria-label="Review mode">
          <button role="radio" aria-checked={mode === "cross"} className={mode === "cross" ? "on" : ""} onClick={() => setMode("cross")}
            title="Two independent reviewers - one proves compliance, one hunts deviations - and disagreements are settled. Most thorough.">
            Cross-examined <span className="seg-sub">2 reviewers</span></button>
          <button role="radio" aria-checked={mode === "single"} className={mode === "single" ? "on" : ""} onClick={() => setMode("single")}
            title="One reviewer builds the checklist and reviews in a single pass. Cheaper and faster.">
            Single pass <span className="seg-sub">faster</span></button>
        </div>
        <div className="runbar-info">
          {problems.length ? <span className="muted"><Icon name="alert" size={15} /> {problems[0]}</span> : (
            <span><b>{totalPages}</b> pages against <b>{scope.length}</b> references{est && !est.mock && <> - est. <b>${est.est_cost_low.toFixed(2)}-{est.est_cost_high.toFixed(2)}</b></>}</span>
          )}
        </div>
        <Button variant="primary" size="lg" icon="play" onClick={run} loading={starting} disabled={!!problems.length}>Run review</Button>
      </div>
    </div>
  );
}
