import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, Estimate, Job, LibraryFile, Project, ScopeItem, TreeNode, Upload } from "../api";
import { Chip, DropZone } from "../components/DropZone";
import { ScopeSelector } from "../components/ScopeSelector";
import { Badge, Button, Card, Field, Icon, useToast } from "../components/ui";

type Zone = "submittal" | "examples" | "div01";
type Pending = { key: string; name: string; status: "uploading" | "error"; error?: string };

const TYPES = ["Product Data", "Shop Drawings", "Product Data and Shop Drawings", "Other"];
const SEC_RE = /^\d{2}\s?\d{2}\s?\d{2}(\.\d{2})?$/;
const normSec = (s: string) => { const d = s.replace(/\D/g, ""); return d.length >= 6 ? `${d.slice(0, 2)} ${d.slice(2, 4)} ${d.slice(4, 6)}` : s.trim(); };

function useDebounced<T>(v: T, ms = 450): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function NewReview({ project, tree, library, onLibraryChanged, onStarted, settingsMock, apiKeySet }: {
  project: Project; tree: TreeNode[]; library: LibraryFile[]; onLibraryChanged: () => void; onStarted: (j: Job) => void;
  settingsMock: boolean; apiKeySet: boolean;
}) {
  const toast = useToast();
  const [form, setForm] = useState({ spec_section: "", spec_title: "", submittal_no: "", revision: "0", submittal_type: "Product Data",
    title: "", required_date: "", supplier: "" });
  const [files, setFiles] = useState<Record<Zone, Upload[]>>({ submittal: [], examples: [], div01: [] });
  const [pending, setPending] = useState<Record<Zone, Pending[]>>({ submittal: [], examples: [], div01: [] });
  const [libBusy, setLibBusy] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [namingPattern, setNamingPattern] = useState("");
  const [mimic, setMimic] = useState(true);
  const [scope, setScope] = useState<ScopeItem[]>([]);
  const [scopeMode, setScopeMode] = useState("flexible");
  const [scopeSrc, setScopeSrc] = useState("");
  const [scopeDirty, setScopeDirty] = useState(false);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [sections, setSections] = useState<{ number: string; title: string }[]>([]);
  const [est, setEst] = useState<Estimate | null>(null);
  const [preview, setPreview] = useState<{ name: string; source: string } | null>(null);
  const [starting, setStarting] = useState(false);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => { api.sections(project.id).then(setSections).catch(() => {}); }, [project.id, tree]);

  const secNorm = SEC_RE.test(form.spec_section.trim()) || form.spec_section.replace(/\D/g, "").length === 6 ? normSec(form.spec_section) : "";
  const secMatch = sections.find((s) => s.number === secNorm);
  const designated = secNorm ? `S:${secNorm}` : "";
  useEffect(() => { if (secMatch && !form.spec_title) set("spec_title", secMatch.title); }, [secMatch?.number]);

  // ---- uploads
  const addFiles = async (zone: Zone, list: File[]) => {
    const pend = list.map((f, i) => ({ key: `p${Date.now()}${i}`, name: f.name, status: "uploading" as const }));
    setPending((p) => ({ ...p, [zone]: [...p[zone], ...pend] }));
    try {
      const res = await api.upload(list);
      setFiles((x) => {
        const have = new Set(x[zone].map((u) => u.sha256));
        return { ...x, [zone]: [...x[zone], ...res.files.filter((u) => !have.has(u.sha256))] };
      });
      const dupes = res.files.length - res.files.filter((u) => !files[zone].some((h) => h.sha256 === u.sha256)).length;
      if (dupes > 0) toast(`${dupes} duplicate file(s) skipped`, "info");
      res.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad"));
      setPending((p) => ({ ...p, [zone]: p[zone].filter((x) => !pend.some((q) => q.key === x.key)) }));
    } catch (e: any) {
      toast(e.message, "bad");
      setPending((p) => ({ ...p, [zone]: p[zone].map((x) => pend.some((q) => q.key === x.key) ? { ...x, status: "error", error: "Upload failed" } : x) }));
    }
  };
  const addLibrary = async (list: File[]) => {
    setLibBusy(true);
    try {
      const r = await api.uploadLibrary(project.id, list);
      const dup = r.files.filter((f) => f.duplicate).length;
      if (r.files.length - dup) toast(`${r.files.length - dup} file(s) added to the project library - cataloguing in the background`, "ok");
      if (dup) toast(`${dup} file(s) already in the library`, "info");
      r.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad"));
      onLibraryChanged();
    } catch (e: any) { toast(e.message, "bad"); } finally { setLibBusy(false); }
  };

  const chipsFor = (zone: Zone): Chip[] => [
    ...files[zone].map((u) => ({ key: u.id, name: u.filename, pages: u.pages, size: u.size, flags: u.flags })),
    ...pending[zone].map((p) => ({ key: p.key, name: p.name, status: p.status, error: p.error })),
  ];
  const libChips: Chip[] = library.map((f) => ({
    key: f.id, name: f.filename, pages: f.pages, size: f.size, error: f.status === "error" ? f.error || "Cataloguing failed" : undefined,
    status: f.status === "ready" ? "ready" : f.status === "error" ? "error" : "processing", progress: f.progress,
    extra: <> · <span className={`kind kind-${f.kind}`}>{f.kind === "auto" ? "detecting…" : f.kind}</span>
      {f.status === "ready" ? <> · {f.notes}</> : f.status !== "error" ? <> · {f.notes || "queued"}</> : null}</>,
  }));

  // ---- scope suggestion
  const subIds = files.submittal.map((u) => u.id);
  const dSec = useDebounced(secNorm, 500);
  const suggest = async (reset = false) => {
    if (!dSec) return;
    setScopeLoading(true);
    try {
      const r = await api.suggestScope(project.id, { spec_section: dSec, upload_ids: subIds, instructions, reset });
      let items = r.items;
      if (!items.some((i) => i.id === `S:${dSec}`) && r.found) items = [{ id: `S:${dSec}`, locked: true, reason: "Designated section" }, ...items];
      setScope(items); setScopeSrc(r.source); if (r.mode) setScopeMode(r.mode); setScopeDirty(false);
      if (r.section_title && !form.spec_title) set("spec_title", r.section_title);
    } catch (e: any) { toast(`Scope suggestion failed: ${e.message}`, "bad"); } finally { setScopeLoading(false); }
  };
  const readySections = project.library.sections;
  useEffect(() => { if (dSec && !scopeDirty) suggest(); }, [dSec, readySections, subIds.length]);

  // ---- estimate + naming preview
  const dScope = useDebounced(JSON.stringify(scope), 500);
  useEffect(() => {
    if (!subIds.length) { setEst(null); return; }
    api.estimate(project.id, { upload_ids: subIds, scope_items: scope }).then(setEst).catch(() => {});
  }, [dScope, subIds.join(",")]);
  const dForm = useDebounced(JSON.stringify({ form, namingPattern, mimic, ex: files.examples.map((u) => u.id) }), 400);
  useEffect(() => {
    if (!form.spec_section) { setPreview(null); return; }
    api.namingPreview(project.id, { ...form, spec_section: secNorm || form.spec_section, naming_pattern: namingPattern, mimic_example_naming: mimic },
      files.examples.map((u) => u.id)).then(setPreview).catch(() => setPreview(null));
  }, [dForm]);

  const problems: string[] = [];
  if (!secNorm) problems.push("Enter the spec section number (e.g. 08 71 00)");
  if (!files.submittal.length) problems.push("Add the submittal in zone 1");
  if (!apiKeySet && !settingsMock) problems.push("Add your Anthropic API key in Settings");
  if (library.some((f) => f.status !== "ready" && f.status !== "error")) problems.push("Wait for the project library to finish cataloguing");
  const libEmpty = !library.length;

  const run = async () => {
    if (problems.length) { toast(problems[0], "bad"); return; }
    setStarting(true);
    try {
      const job = await api.createJob(project.id, {
        ...form, spec_section: secNorm, instructions, naming_pattern: namingPattern, mimic_example_naming: mimic,
        uploads: { submittal: subIds, examples: files.examples.map((u) => u.id), div01: files.div01.map((u) => u.id) },
        scope: { mode: scopeMode, items: scope.length ? scope : [{ id: designated, locked: true }] },
      });
      onStarted(job);
    } catch (e: any) { toast(e.message, "bad"); } finally { setStarting(false); }
  };

  const remove = (zone: Zone) => (key: string) => {
    setFiles((x) => ({ ...x, [zone]: x[zone].filter((u) => u.id !== key) }));
    setPending((p) => ({ ...p, [zone]: p[zone].filter((u) => u.key !== key) }));
  };
  const reorder = (from: number, to: number) => setFiles((x) => {
    const a = [...x.submittal]; const [m] = a.splice(from, 1); a.splice(to, 0, m); return { ...x, submittal: a };
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>New submittal review</h1>
          <p className="muted">Drop the package, confirm the reference scope, and run. Claude reviews against <b>{project.name}</b>'s own specs and drawings.</p>
        </div>
        {settingsMock && <Badge tone="warn" title="Offline demo mode: deterministic heuristics instead of Claude. Turn off in Settings.">Offline demo mode</Badge>}
      </div>

      <Card title="Submittal setup" icon="rules">
        <div className="grid-form">
          <Field label="Spec section" hint={secNorm ? (secMatch ? `In library: ${secMatch.title}` : libEmpty ? "Library empty" : "Not found in library") : "CSI number, e.g. 08 71 00"} className="f-sec">
            <input className={`input mono ${secNorm && secMatch ? "input-ok" : ""}`} list="asm-sections" placeholder="08 71 00" value={form.spec_section}
              onChange={(e) => { set("spec_section", e.target.value); set("spec_title", ""); setScopeDirty(false); }}
              onBlur={() => secNorm && set("spec_section", secNorm)} />
            <datalist id="asm-sections">{sections.map((s) => <option key={s.number} value={s.number}>{s.title}</option>)}</datalist>
          </Field>
          <Field label="Section title" className="f-title"><input className="input" placeholder="Auto-filled from the catalog" value={form.spec_title} onChange={(e) => set("spec_title", e.target.value)} /></Field>
          <Field label="Submittal no." hint="Sequence, e.g. 003"><input className="input mono" placeholder="003" value={form.submittal_no} onChange={(e) => set("submittal_no", e.target.value)} /></Field>
          <Field label="Revision"><input className="input mono" value={form.revision} onChange={(e) => set("revision", e.target.value)} /></Field>
          <Field label="Type">
            <select className="input" value={form.submittal_type} onChange={(e) => set("submittal_type", e.target.value)}>
              {TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Title / description" className="f-wide"><input className="input" placeholder="Optional, e.g. Door Hardware - Sets 1-4" value={form.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <Field label="Supplier / subcontractor"><input className="input" placeholder="From the package if blank" value={form.supplier} onChange={(e) => set("supplier", e.target.value)} /></Field>
          <Field label="Review requested by"><input className="input" type="date" value={form.required_date} onChange={(e) => set("required_date", e.target.value)} /></Field>
        </div>
      </Card>

      <div className="zones">
        <DropZone index={1} title="Submittal" required icon="file" subtitle="Product Data and/or Shop Drawings under review. Multiple files merge in this order - drag to reorder."
          chips={chipsFor("submittal")} onFiles={(f) => addFiles("submittal", f)} onRemove={remove("submittal")} onReorder={reorder} emptyHint="Nothing yet." />
        <DropZone index={2} title="Project information" icon="book" busy={libBusy}
          subtitle="Specs, drawings, addenda, ASIs, RFIs. Stored in the project library and catalogued once - reused by every review."
          chips={libChips} onFiles={addLibrary} emptyHint="Library is empty. Drop the project manual and drawing set once per project."
          footer={library.length ? <p className="dz-foot"><Icon name="layers" size={14} /> {project.library.sections} spec sections · {project.library.sheets} sheets · {project.library.amendments} amendments catalogued</p> : null} />
        <DropZone index={3} title="Quality template examples" icon="layers" subtitle="Approved example submittals. Used for format, order, cover and naming only - never as compliance evidence."
          chips={chipsFor("examples")} onFiles={(f) => addFiles("examples", f)} onRemove={remove("examples")} emptyHint="Recommended." />
        <DropZone index={4} title="Division 01 submittal procedures" optional icon="rules" subtitle="Usually 01 33 00. Overrides naming, cover sheet and package rules. If empty, the library's Division 01 section is used."
          chips={chipsFor("div01")} onFiles={(f) => addFiles("div01", f)} onRemove={remove("div01")} />
      </div>

      <Card title="Reference scope" icon="search" sub="Exactly what the agents may read for this submittal - no run reads the whole spec book.">
        {secNorm ? (
          <ScopeSelector tree={tree} items={scope} designated={designated} mode={scopeMode} onMode={setScopeMode} loading={scopeLoading}
            source={scopeSrc} onReset={() => suggest(true)} onChange={(i) => { setScope(i); setScopeDirty(true); }}
            footer={est && (
              <div className="scope-foot">
                <span><b>{est.library_pages}</b> library pages</span>
                <span><b>{est.submittal_pages}</b> submittal pages</span>
                <span>~<b>{(est.est_input_tokens / 1000).toFixed(0)}k</b> input tokens</span>
                <span>est. <b>${est.est_cost_low.toFixed(2)}-${est.est_cost_high.toFixed(2)}</b> {est.mock && "(offline: $0)"}</span>
                <span className="muted">job cap ${est.usd_cap?.toFixed(2)}</span>
              </div>
            )} />
        ) : <p className="muted">Enter the spec section above to load the suggested scope.</p>}
      </Card>

      <div className="two-col">
        <Card title="Reviewer instructions" icon="spark" sub="Passed to every agent as a high-priority note.">
          <textarea className="input textarea" maxLength={2000} rows={6} value={instructions} onChange={(e) => setInstructions(e.target.value)}
            placeholder={'e.g. "Review hardware sets 3-5 only"\n"Approved substitution per RFI 112: LCN 4040XP"\n"Owner standard is Schlage"\n"Include spec excerpt"'} />
          <div className="count">{instructions.length}/2000</div>
        </Card>
        <Card title="File naming" icon="file" sub="Division 01 rules found during the run take precedence over the company default.">
          <Field label="Naming pattern (optional override)" hint="Tokens: {SpecSection} {Seq:03d} {Rev} {SpecTitle} {Type} {Title} {ProjectNumber} {Date}">
            <input className="input mono" placeholder="{SpecSection}-{Seq:03d}-{Rev} {SpecTitle} - {Type}" value={namingPattern} onChange={(e) => setNamingPattern(e.target.value)} />
          </Field>
          <label className="toggle">
            <input type="checkbox" checked={mimic} onChange={(e) => setMimic(e.target.checked)} />
            <span className="toggle-ui" /> Mimic naming from the quality template file name
          </label>
          <div className="preview-name">
            <span className="muted small">Preview</span>
            <code>{preview?.name || "—"}</code>
            {preview && <span className="muted small">from {preview.source}</span>}
          </div>
        </Card>
      </div>

      <div className="runbar">
        <div className="runbar-info">
          {problems.length ? (
            <span className="muted"><Icon name="alert" size={15} /> {problems[0]}</span>
          ) : (
            <span><Icon name="check" size={15} className="t-ok" /> Ready: {files.submittal.reduce((s, u) => s + u.pages, 0)} submittal pages · {scope.length} reference items · {scopeMode} scope
              {est && !est.mock && <> · est. ${est.est_cost_low.toFixed(2)}-${est.est_cost_high.toFixed(2)}</>}</span>
          )}
        </div>
        <Button variant="primary" size="lg" icon="play" onClick={run} loading={starting} disabled={!!problems.length}>Run review</Button>
      </div>
    </div>
  );
}
