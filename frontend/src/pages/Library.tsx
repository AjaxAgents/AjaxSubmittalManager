import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, Division, DIVISION_CODES, DIVISION_NAMES, fmtBytes, fmtTime, LibraryFile, Project, Sub, TreeNode } from "../api";
import { entriesToFiles } from "../components/DropZone";
import { CoverMapper } from "../components/CoverMapper";
import { PageViewer } from "../components/PageViewer";
import { Badge, Button, Field, Icon, Modal, useToast } from "../components/ui";
import { SortSelect } from "../components/Pickers";

export type LibTab = "info" | "example" | "div01" | "cover" | "divisions" | "catalog";
const INFO_TYPES = [
  { value: "auto", label: "Detect automatically" }, { value: "spec", label: "Specifications" },
  { value: "drawing", label: "Drawings" }, { value: "amendment", label: "Amendments (addenda, ASIs, RFIs)" }, { value: "document", label: "Other documents" },
];
const GROUPS: { kind: string; label: string }[] = [
  { kind: "spec", label: "Specifications" }, { kind: "drawing", label: "Drawings" }, { kind: "amendment", label: "Amendments" },
  { kind: "document", label: "Other documents" }, { kind: "auto", label: "Type to be detected" },
];
const DIV_CODES = DIVISION_CODES;
const STATUS_ORDER: Record<string, number> = { processing: 0, queued: 1, error: 2, uploaded: 3, ready: 4, stored: 5 };
export const FILE_SORTS: [string, string][] = [["newest", "Newest first"], ["oldest", "Oldest first"], ["name", "Name (A-Z)"], ["name_desc", "Name (Z-A)"],
  ["pages", "Most pages"], ["status", "Status"], ["division", "Division"]];
export function sortFiles(fs: LibraryFile[], by: string): LibraryFile[] {
  const n = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  const c: Record<string, (a: LibraryFile, b: LibraryFile) => number> = {
    newest: (a, b) => b.uploaded_at - a.uploaded_at, oldest: (a, b) => a.uploaded_at - b.uploaded_at,
    name: (a, b) => n(a.filename, b.filename), name_desc: (a, b) => n(b.filename, a.filename), pages: (a, b) => b.pages - a.pages,
    status: (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || n(a.filename, b.filename),
    division: (a, b) => n(a.division || "99", b.division || "99") || n(a.filename, b.filename),
  };
  return [...fs].sort(c[by] || c.name);
}
const sortKey = (k: string) => `asm.sort.${k}`;
function useStoredSort(key: string, init: string): [string, (v: string) => void] {
  const [v, setV] = useState<string>(() => { try { return localStorage.getItem(sortKey(key)) || init; } catch { return init; } });
  return [v, (x: string) => { setV(x); try { localStorage.setItem(sortKey(key), x); } catch { /* ignore */ } }];
}

function UploadBox({ onFiles, busy, children, label }: { onFiles: (f: File[]) => void; busy?: boolean; children?: React.ReactNode; label: string }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const depth = useRef(0);
  const accept = (fs: File[]) => { const p = fs.filter((f) => f.name.toLowerCase().endsWith(".pdf")); if (p.length) onFiles(p); };
  return (
    <div className={`upbox ${over ? "upbox-over" : ""}`}
      onDragEnter={(e) => { e.preventDefault(); depth.current++; setOver(true); }} onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => { depth.current--; if (depth.current <= 0) { setOver(false); depth.current = 0; } }}
      onDrop={async (e) => { e.preventDefault(); depth.current = 0; setOver(false); const v = await entriesToFiles(e.dataTransfer.items); accept(v.length ? v : Array.from(e.dataTransfer.files)); }}>
      {children}
      <button type="button" className="upbox-drop" onClick={() => input.current?.click()}>
        {busy ? <span className="spinner spinner-brand" /> : <Icon name="upload" size={18} />}<span><b>{label}</b> or click to browse</span>
      </button>
      <button type="button" className="link-btn small" onClick={() => folder.current?.click()}>Upload a folder</button>
      <input ref={input} type="file" accept=".pdf,application/pdf" multiple hidden onChange={(e) => { accept(Array.from(e.target.files || [])); e.target.value = ""; }} />
      <input ref={folder} type="file" multiple hidden {...({ webkitdirectory: "", directory: "" } as any)} onChange={(e) => { accept(Array.from(e.target.files || [])); e.target.value = ""; }} />
    </div>
  );
}

const statusBadge = (f: LibraryFile) => {
  if (f.status === "ready") return <Badge tone="ok">{f.notes || "Ready"}</Badge>;
  if (f.status === "uploaded") return <Badge>Not processed</Badge>;
  if (f.status === "stored") return <Badge tone="ok">Stored</Badge>;
  if (f.status === "error") return <Badge tone="bad" title={f.error}>Failed</Badge>;
  return <span className="proc"><span className="spinner spinner-brand" /> {f.status === "queued" ? "Queued" : `${f.notes || "Processing"} ${Math.round(f.progress * 100)}%`}</span>;
};

export function Library({ project, files, tree, defaults, pending, onChanged, tab, setTab }: {
  project: Project; files: LibraryFile[]; tree: TreeNode[]; defaults: any; pending: { files: number; pages: number; est_ai_cost: number };
  onChanged: () => void; tab: LibTab; setTab: (t: LibTab) => void;
}) {
  const tabs: [LibTab, string, number | null][] = [
    ["info", "Project information", files.filter((f) => f.category === "info").length],
    ["example", "Quality templates", files.filter((f) => f.kind === "example").length],
    ["div01", "Division 01 procedures", files.filter((f) => f.kind === "div01").length],
    ["cover", "Cover pages", files.filter((f) => f.kind === "cover").length],
    ["divisions", "Divisions and subcontractors", null],
    ["catalog", "Catalog", project.library.sections + project.library.sheets],
  ];
  return (
    <div className="page">
      <header className="page-head"><h1>Project Library</h1></header>
      <nav className="tabs tabs-page" role="tablist">
        {tabs.map(([k, l, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}{n !== null && <span className="tab-count">{n}</span>}</button>
        ))}
      </nav>
      {tab === "info" && <InfoTab project={project} files={files.filter((f) => f.category === "info")} pending={pending} onChanged={onChanged} />}
      {tab === "example" && <SimpleTab project={project} kind="example" files={files.filter((f) => f.kind === "example")} defaults={defaults} onChanged={onChanged}
        intro="Approved submittals from this project. Reviews copy their package order, cover format and naming - never their content." />}
      {tab === "div01" && <SimpleTab project={project} kind="div01" files={files.filter((f) => f.kind === "div01")} defaults={defaults} onChanged={onChanged}
        intro="Project-specific submittal procedures (usually 01 33 00). They set file naming, cover requirements and package rules." />}
      {tab === "cover" && <SimpleTab project={project} kind="cover" files={files.filter((f) => f.kind === "cover")} defaults={defaults} onChanged={onChanged}
        intro="Your company's cover or transmittal page. Its form fields and text boxes are filled in by fixed rules from the submittal details, and it becomes page 1 of the package." />}
      {tab === "divisions" && <DivisionsTab project={project} />}
      {tab === "catalog" && <CatalogTab project={project} tree={tree} files={files} />}
    </div>
  );
}

// ===================================================================== project information

function InfoTab({ project, files, pending, onChanged }: { project: Project; files: LibraryFile[]; pending: { files: number; pages: number; est_ai_cost: number }; onChanged: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState("auto");
  const [division, setDivision] = useState("");
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Set<string>>(new Set(["auto"]));
  const [q, setQ] = useState("");
  const [useAi, setUseAi] = useState(true);
  const [bulkKind, setBulkKind] = useState("spec");
  const [bulkDiv, setBulkDiv] = useState("");
  const [sort, setSort] = useStoredSort("info", "name");
  const working = files.filter((f) => f.status === "queued" || f.status === "processing").length;
  const stop = async (ids: string[] = []) => {
    try { const r = await api.cancelProcessing(project.id, ids); toast(r.cancelled ? `Stopped ${r.cancelled} file${r.cancelled === 1 ? "" : "s"}. They're back to Not processed.` : "Nothing was processing", "ok"); onChanged(); }
    catch (e: any) { toast(e.message, "bad"); }
  };

  const upload = async (fs: File[]) => {
    setBusy(true);
    try {
      let added = 0, dup = 0;
      for (let i = 0; i < fs.length; i += 20) { // batches keep big folders responsive
        const r = await api.uploadLibrary(project.id, fs.slice(i, i + 20), kind, division);
        added += r.files.filter((f) => !f.duplicate).length; dup += r.files.filter((f) => f.duplicate).length;
        r.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad"));
      }
      toast(`${added} file${added === 1 ? "" : "s"} uploaded${dup ? `, ${dup} already in the library` : ""}. Press Process when you're ready.`, "ok");
      setOpen((o) => new Set([...Array.from(o), kind]));
      onChanged();
    } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  const process = async (ids: string[]) => {
    try { const r = await api.process(project.id, ids, useAi); toast(`${r.queued} file${r.queued === 1 ? "" : "s"} queued for processing`, "ok"); setSel(new Set()); onChanged(); }
    catch (e: any) { toast(e.message, "bad"); }
  };
  const bulk = async (action: string, value = "") => {
    const ids = Array.from(sel);
    if (action === "delete" && !confirm(`Delete ${ids.length} file${ids.length > 1 ? "s" : ""} and their catalog entries?`)) return;
    try { const r = await api.bulk(project.id, ids, action, value); toast(`${r.updated} file${r.updated === 1 ? "" : "s"} updated`, "ok"); if (action === "delete") setSel(new Set()); onChanged(); }
    catch (e: any) { toast(e.message, "bad"); }
  };
  const ql = q.trim().toLowerCase();
  const shown = sortFiles(files.filter((f) => !ql || f.filename.toLowerCase().includes(ql)), sort);
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="col gap">
      <section className="panel">
        <h3 className="panel-title">Add project information</h3>
        <UploadBox onFiles={upload} busy={busy} label="Drop specs, drawings or addenda">
          <div className="upbox-opts">
            <Field label="These files are"><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{INFO_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></Field>
            <Field label="Division (optional)"><select className="input" value={division} onChange={(e) => setDivision(e.target.value)}>
              <option value="">From the file</option>{DIV_CODES.map((c) => <option key={c} value={c}>{c} {DIVISION_NAMES[c]}</option>)}</select></Field>
          </div>
        </UploadBox>
        <p className="muted small">Uploading only stores the files. Nothing is read or charged until you press Process.</p>
      </section>

      <section className={`processbar ${pending.files || working ? "has" : ""}`}>
        <div>
          <b>{working ? `Processing ${working} file${working > 1 ? "s" : ""}` : pending.files ? `${pending.files} file${pending.files > 1 ? "s" : ""} waiting to be processed` : "Everything is processed"}</b>
          {working > 0 && pending.files > 0 && <span className="muted small"> {pending.files} more waiting</span>}
          {pending.files > 0 && <span className="muted small"> {pending.pages.toLocaleString()} pages</span>}
          <label className="toggle small"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /><span className="toggle-ui" />
            Write a short summary of each section and sheet{pending.files > 0 && ` (about $${Math.max(0.01, pending.est_ai_cost).toFixed(2)})`}</label>
        </div>
        <div className="row gap">
          {working > 0 && <Button variant="ghost" icon="x" onClick={() => stop([])}>Stop processing</Button>}
          <Button variant="primary" icon="play" disabled={!pending.files} onClick={() => process([])}>Process {pending.files || ""} file{pending.files === 1 ? "" : "s"}</Button>
        </div>
      </section>

      <section className="panel">
        <div className="lib-toolbar">
          <div className="search"><Icon name="search" size={15} /><input placeholder="Filter files" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <SortSelect value={sort} onChange={setSort} options={FILE_SORTS} />
          <span className="muted small">{files.length} files</span>
        </div>
        {sel.size > 0 && (
          <div className="bulkbar" role="region" aria-label="Bulk actions">
            <b>{sel.size} selected</b>
            <span className="bulk-group"><select className="input input-sm" value={bulkKind} onChange={(e) => setBulkKind(e.target.value)}>{INFO_TYPES.slice(1).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
              <Button size="sm" onClick={() => bulk("set_kind", bulkKind)}>Set type</Button></span>
            <span className="bulk-group"><select className="input input-sm" value={bulkDiv} onChange={(e) => setBulkDiv(e.target.value)}><option value="">No division</option>{DIV_CODES.map((c) => <option key={c} value={c}>{c} {DIVISION_NAMES[c]}</option>)}</select>
              <Button size="sm" onClick={() => bulk("set_division", bulkDiv)}>Set division</Button></span>
            <Button size="sm" icon="play" onClick={() => process(Array.from(sel))}>Process</Button>
            {files.some((f) => sel.has(f.id) && (f.status === "queued" || f.status === "processing")) && <Button size="sm" icon="x" onClick={() => stop(Array.from(sel))}>Stop processing</Button>}
            <Button size="sm" onClick={() => bulk("reset")}>Mark unprocessed</Button>
            <Button size="sm" variant="danger" icon="trash" onClick={() => bulk("delete")}>Delete</Button>
            <button className="link-btn small" onClick={() => setSel(new Set())}>Clear</button>
          </div>
        )}
        {GROUPS.map((g) => {
          const gf = shown.filter((f) => f.kind === g.kind);
          if (!gf.length) return null;
          const isOpen = open.has(g.kind) || !!ql;
          const allSel = gf.every((f) => sel.has(f.id));
          const ready = gf.filter((f) => f.status === "ready").length;
          return (
            <div key={g.kind} className="acc">
              <div className="acc-head">
                <input type="checkbox" aria-label={`Select all ${g.label}`} checked={allSel} onChange={() => setSel((s) => { const n = new Set(s); gf.forEach((f) => allSel ? n.delete(f.id) : n.add(f.id)); return n; })} />
                <button className="acc-toggle" onClick={() => setOpen((o) => { const n = new Set(o); n.has(g.kind) ? n.delete(g.kind) : n.add(g.kind); return n; })} aria-expanded={isOpen}>
                  <Icon name="chevron" size={14} className={isOpen ? "rot90" : ""} /><b>{g.label}</b><span className="tab-count">{gf.length}</span>
                  <span className="muted small acc-meta">{ready} processed, {gf.reduce((s, f) => s + f.pages, 0).toLocaleString()} pages</span>
                </button>
              </div>
              {isOpen && (
                <table className="matrix compact files-table">
                  <tbody>
                    {gf.map((f) => (
                      <tr key={f.id} className={sel.has(f.id) ? "selected" : ""}>
                        <td className="w-check"><input type="checkbox" checked={sel.has(f.id)} onChange={() => toggle(f.id)} aria-label={`Select ${f.filename}`} /></td>
                        <td><div className="mono small">{f.filename}</div><div className="muted tiny">{f.pages} pages, {fmtBytes(f.size)}, uploaded {fmtTime(f.uploaded_at)}</div>
                          {f.status === "error" && <div className="t-bad tiny">{f.error}</div>}</td>
                        <td className="nowrap small">{f.division ? `Div ${f.division}` : ""}</td>
                        <td className="nowrap">{statusBadge(f)}
                          {(f.status === "queued" || f.status === "processing") && <button className="icon-btn stop-btn" title="Stop processing this file" aria-label={`Stop processing ${f.filename}`} onClick={() => stop([f.id])}><Icon name="x" size={14} /></button>}</td>
                        <td className="w-act">
                          <select className="input input-sm" value={f.kind} aria-label="Type" onChange={(e) => api.patchFile(f.id, { kind: e.target.value }).then(onChanged)}>
                            {f.kind === "auto" && <option value="auto">Detect</option>}{INFO_TYPES.slice(1).map((t) => <option key={t.value} value={t.value}>{t.label.split(" (")[0]}</option>)}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
        {!files.length && <p className="empty">No project information yet. Upload the spec book and drawing set above.</p>}
      </section>
    </div>
  );
}

// ===================================================================== templates / div01 / covers

function SimpleTab({ project, kind, files, defaults, onChanged, intro }: { project: Project; kind: "example" | "div01" | "cover"; files: LibraryFile[]; defaults: any; onChanged: () => void; intro: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [mapping, setMapping] = useState<LibraryFile | null>(null);
  const defKey = kind === "example" ? "examples" : kind;
  const [sort, setSort] = useStoredSort(kind, "newest");
  const defList: string[] = kind === "cover" ? (defaults?.cover ? [defaults.cover] : []) : (defaults?.[defKey] || []);
  const upload = async (fs: File[]) => {
    setBusy(true);
    try { const r = await api.uploadLibrary(project.id, fs, kind); r.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad"));
      const dups = r.files.filter((f) => f.duplicate), added = r.files.length - dups.length;
      if (added) toast(`${added} file${added === 1 ? "" : "s"} added`, "ok");
      if (dups.length) toast(`${dups.map((d) => d.filename).join(", ")} ${dups.length === 1 ? "is" : "are"} already in this list`, "info");
      onChanged();
      if (kind === "cover" && r.files[0] && !r.files[0].duplicate) setMapping(r.files[0]); }
    catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  const setDefault = async (fid: string, on: boolean) => {
    const body: any = kind === "cover" ? { cover: on ? fid : "" } : { [defKey]: on ? [...defList, fid] : defList.filter((x) => x !== fid) };
    await api.setDefaults(project.id, body); onChanged();
  };
  return (
    <div className="col gap">
      <section className="panel">
        <p className="muted">{intro}</p>
        <UploadBox onFiles={upload} busy={busy} label={kind === "cover" ? "Drop your cover page PDF" : "Drop PDFs"} />
      </section>
      <section className="panel">
        {files.length > 1 && <div className="lib-toolbar"><span className="grow" /><SortSelect value={sort} onChange={setSort} options={FILE_SORTS.filter(([k]) => !["status", "division"].includes(k))} /></div>}
        <table className="matrix compact">
          <thead><tr><th>File</th><th>{kind === "cover" ? "Fillable boxes" : "Pages"}</th><th>Use by default</th><th /></tr></thead>
          <tbody>
            {sortFiles(files, sort).map((f) => (
              <tr key={f.id}>
                <td><a className="mono small" href={`/api/library/${f.id}/file.pdf`} target="_blank" rel="noreferrer">{f.filename}</a><div className="muted tiny">{fmtTime(f.uploaded_at)}</div></td>
                <td className="small">{kind === "cover" ? `${(f.meta?.fields || []).length} found` : f.pages}</td>
                <td><label className="toggle small"><input type="checkbox" checked={defList.includes(f.id)} onChange={(e) => setDefault(f.id, e.target.checked)} /><span className="toggle-ui" /></label></td>
                <td className="w-act">
                  {kind === "cover" && <Button size="sm" onClick={() => setMapping(f)}>Map fields</Button>}
                  <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Delete ${f.filename}?`)) api.deleteFile(f.id).then(onChanged); }}><Icon name="trash" size={15} /></button>
                </td>
              </tr>
            ))}
            {!files.length && <tr><td colSpan={4} className="empty">Nothing here yet.</td></tr>}
          </tbody>
        </table>
      </section>
      {mapping && <CoverMapper file={mapping} onClose={() => { setMapping(null); onChanged(); }} />}
    </div>
  );
}

// ===================================================================== divisions & subcontractors

function DivisionsTab({ project }: { project: Project }) {
  const toast = useToast();
  const [divs, setDivs] = useState<Division[]>([]);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [edit, setEdit] = useState<Partial<Sub> | null>(null);
  const [showAll, setShowAll] = useState(false);
  const load = () => { api.divisions(project.id).then((r) => setDivs(r.divisions)); api.subs(project.id).then(setSubs); };
  useEffect(load, [project.id]);
  const saveDivs = async (next: Division[]) => {
    const r = await api.saveDivisions(project.id, next.filter((d) => d.custom).map((d) => ({ code: d.code, name: d.name })));
    setDivs(r.divisions);
  };
  const toggleDiv = (d: Division) => saveDivs(divs.map((x) => x.code === d.code ? { ...x, custom: !x.custom, enabled: !x.custom } : x));
  const saveSub = async () => {
    if (!edit?.name?.trim()) { toast("Give the subcontractor a name", "bad"); return; }
    try { edit.id ? await api.updateSub(project.id, edit.id, edit) : await api.createSub(project.id, edit); setEdit(null); load(); toast("Subcontractor saved", "ok"); }
    catch (e: any) { toast(e.message, "bad"); }
  };
  const active = divs.filter((d) => d.enabled);
  return (
    <div className="two-col wide-right">
      <section className="panel">
        <h3 className="panel-title">Divisions <button className="link-btn small" onClick={() => setShowAll(!showAll)}>{showAll ? "Show active only" : "Add divisions"}</button></h3>
        <p className="muted small">Divisions with spec sections or subcontractors are active automatically. Tick others to use them for planning.</p>
        <ul className="divlist">
          {(showAll ? divs : active).map((d) => (
            <li key={d.code} className={d.enabled ? "on" : ""}>
              <label><input type="checkbox" checked={d.enabled} disabled={d.enabled && !d.custom} onChange={() => toggleDiv(d)} />
                <span className="mono">{d.code}</span><span>{d.name || DIVISION_NAMES[d.code]}</span></label>
              <span className="muted tiny">{d.sections.length} sections{d.subcontractors.length ? `, ${d.subcontractors.map((s) => s.name).join(", ")}` : ""}</span>
            </li>
          ))}
          {!active.length && !showAll && <li className="empty">No active divisions yet.</li>}
        </ul>
      </section>
      <section className="panel">
        <h3 className="panel-title">Subcontractors <Button size="sm" icon="plus" onClick={() => setEdit({ name: "", divisions: [] })}>Add subcontractor</Button></h3>
        <table className="matrix compact">
          <thead><tr><th>Company</th><th>Divisions</th><th>Contact</th><th>Location</th><th /></tr></thead>
          <tbody>
            {subs.map((s) => (
              <tr key={s.id}>
                <td><b>{s.name}</b></td>
                <td>{(s.divisions || []).map((d) => <span key={d} className="kwc mono">{d}</span>)}</td>
                <td className="small">{s.contact}{s.email && <div className="muted tiny">{s.email}</div>}</td>
                <td className="small">{s.address}{(s.city || s.state || s.zip) && <div className="muted tiny">{[s.city, [s.state, s.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ")}</div>}</td>
                <td className="w-act"><Button size="sm" variant="ghost" onClick={() => setEdit(s)}>Edit</Button>
                  <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Delete ${s.name}?`)) api.deleteSub(project.id, s.id).then(load); }}><Icon name="trash" size={15} /></button></td>
              </tr>
            ))}
            {!subs.length && <tr><td colSpan={5} className="empty">No subcontractors yet. They are also added automatically when you run a review for a new one.</td></tr>}
          </tbody>
        </table>
      </section>
      {edit && (
        <Modal title={edit.id ? "Edit subcontractor" : "Add subcontractor"} onClose={() => setEdit(null)}
          footer={<><Button variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={saveSub}>Save</Button></>}>
          <div className="grid-2">
            <Field label="Company name"><input className="input" value={edit.name || ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Contact"><input className="input" value={edit.contact || ""} onChange={(e) => setEdit({ ...edit, contact: e.target.value })} /></Field>
            <Field label="Email"><input className="input" value={edit.email || ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="Phone"><input className="input" value={edit.phone || ""} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
          </div>
          <Field label="Street address"><input className="input" value={edit.address || ""} autoComplete="off" placeholder="1250 Industrial Blvd" onChange={(e) => setEdit({ ...edit, address: e.target.value })} /></Field>
          <div className="addr-row">
            <Field label="City"><input className="input" value={edit.city || ""} autoComplete="off" placeholder="Hickory" onChange={(e) => setEdit({ ...edit, city: e.target.value })} /></Field>
            <Field label="State"><input className="input" value={edit.state || ""} autoComplete="off" placeholder="NC" maxLength={2} onChange={(e) => setEdit({ ...edit, state: e.target.value.toUpperCase() })} /></Field>
            <Field label="ZIP"><input className="input" value={edit.zip || ""} autoComplete="off" placeholder="28601" inputMode="numeric" maxLength={10} onChange={(e) => setEdit({ ...edit, zip: e.target.value })} /></Field>
          </div>
          <Field label="Divisions they submit for">
            <div className="divpick">
              {DIV_CODES.map((c) => {
                const on = (edit.divisions || []).includes(c);
                return <button type="button" key={c} className={`divchip ${on ? "on" : ""}`} onClick={() => setEdit({ ...edit, divisions: on ? (edit.divisions || []).filter((x) => x !== c) : [...(edit.divisions || []), c] })}>
                  <span className="mono">{c}</span> {DIVISION_NAMES[c]}</button>;
              })}
            </div>
          </Field>
        </Modal>
      )}
    </div>
  );
}

// ===================================================================== catalog browser

const CAT_KINDS: [string, string][] = [["", "All"], ["spec_section", "Specifications"], ["drawing_sheet", "Drawings"], ["amendment", "Amendments"], ["document", "Documents"]];
const CAT_SORTS: [string, string][] = [["number", "Number"], ["title", "Title"], ["pages", "Most pages"], ["newest", "Newest upload"], ["oldest", "Oldest upload"], ["file", "Source file"]];
const discipline = (n: string) => (n.match(/^[A-Z]+/i)?.[0] || "").toUpperCase();

function CatalogTab({ project, tree, files }: { project: Project; tree: TreeNode[]; files: LibraryFile[] }) {
  const [sel, setSel] = useState<string | null>(null);
  const [item, setItem] = useState<any>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [group, setGroup] = useState("");
  const [amendedOnly, setAmendedOnly] = useState(false);
  const [showText, setShowText] = useState(false);
  const [sort, setSort] = useStoredSort("catalog", "number");
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => { setItem(null); setShowText(false); if (sel) api.catalogItem(project.id, sel).then(setItem).catch(() => setItem(null)); }, [sel]);
  useEffect(() => { setGroup(""); }, [kind]);
  const fileById = useMemo(() => Object.fromEntries(files.map((f) => [f.id, f])), [files]);
  const leaves = useMemo(() => {
    const out: TreeNode[] = [];
    const walk = (ns: TreeNode[]) => ns.forEach((n) => ["spec_section", "drawing_sheet", "amendment", "document"].includes(n.kind) ? out.push(n) : walk(n.children || []));
    walk(tree);
    return out;
  }, [tree]);
  const counts = useMemo(() => leaves.reduce<Record<string, number>>((c, l) => { c[l.kind] = (c[l.kind] || 0) + 1; return c; }, {}), [leaves]);
  const groups = useMemo(() => {
    if (kind === "spec_section") return Array.from(new Set(leaves.filter((l) => l.kind === kind).map((l) => (l.number || "").slice(0, 2)))).filter(Boolean).sort();
    if (kind === "drawing_sheet") return Array.from(new Set(leaves.filter((l) => l.kind === kind).map((l) => discipline(l.number || "")))).filter(Boolean).sort();
    return [];
  }, [leaves, kind]);
  const ql = q.trim().toLowerCase();
  const shown = useMemo(() => {
    const n = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
    const up = (l: TreeNode) => fileById[l.file_id || ""]?.uploaded_at || 0;
    const cmp: Record<string, (a: TreeNode, b: TreeNode) => number> = {
      number: (a, b) => n(a.number || a.label, b.number || b.label), title: (a, b) => n(a.title || a.label, b.title || b.label),
      pages: (a, b) => (b.pages || 0) - (a.pages || 0), newest: (a, b) => up(b) - up(a) || n(a.number || "", b.number || ""),
      oldest: (a, b) => up(a) - up(b) || n(a.number || "", b.number || ""),
      file: (a, b) => n(fileById[a.file_id || ""]?.filename || "", fileById[b.file_id || ""]?.filename || "") || (a.page_start || 0) - (b.page_start || 0),
    };
    return leaves.filter((l) => (!kind || l.kind === kind)
      && (!group || (kind === "spec_section" ? (l.number || "").startsWith(group) : discipline(l.number || "") === group))
      && (!amendedOnly || !!l.amended_by?.length)
      && (!ql || `${l.number || ""} ${l.title || ""} ${l.label}`.toLowerCase().includes(ql))).sort(cmp[sort] || cmp.number);
  }, [leaves, kind, group, amendedOnly, ql, sort, fileById]);
  const node = sel ? leaves.find((l) => l.id === sel) : null;
  useEffect(() => { if (sel) listRef.current?.querySelector(".cat-row.on")?.scrollIntoView({ block: "nearest" }); }, [sel]);
  const kindLabel: Record<string, string> = { spec_section: "Spec", drawing_sheet: "Sheet", amendment: "Amendment", document: "Document" };

  return (
    <div className={`cat-layout ${node ? "has-sel" : ""}`}>
      <section className="panel cat-browser">
        <div className="cat-filters">
          <div className="pills" role="radiogroup" aria-label="Show">
            {CAT_KINDS.map(([k, l]) => (
              <button key={k} role="radio" aria-checked={kind === k} className={`pill ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}
                disabled={!!k && !counts[k]}>{l}<span className="pill-n">{k ? counts[k] || 0 : leaves.length}</span></button>
            ))}
          </div>
          <div className="filterbar">
            <div className="search"><Icon name="search" size={15} /><input placeholder="Number, title or keyword" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter the catalog" />
              {q && <button className="icon-btn" aria-label="Clear" onClick={() => setQ("")}><Icon name="x" size={14} /></button>}</div>
            {groups.length > 1 && (
              <select className="input input-sm" value={group} onChange={(e) => setGroup(e.target.value)} aria-label={kind === "spec_section" ? "Division" : "Discipline"}>
                <option value="">{kind === "spec_section" ? "All divisions" : "All disciplines"}</option>
                {groups.map((g) => <option key={g} value={g}>{kind === "spec_section" ? `${g} ${DIVISION_NAMES[g] || ""}` : g}</option>)}
              </select>
            )}
            <label className="check small"><input type="checkbox" checked={amendedOnly} onChange={(e) => setAmendedOnly(e.target.checked)} /> Amended only</label>
            <SortSelect value={sort} onChange={setSort} options={CAT_SORTS} />
          </div>
          <p className="muted tiny">{shown.length} of {leaves.length} items</p>
        </div>
        <div className="cat-list" ref={listRef}>
          {shown.map((n) => {
            const f = fileById[n.file_id || ""];
            return (
              <button key={n.id} className={`cat-row ${sel === n.id ? "on" : ""}`} onClick={() => setSel(sel === n.id ? null : n.id)} aria-pressed={sel === n.id}>
                <span className="mono-num">{n.number || ""}</span>
                <span className="cat-t">{n.title || n.label}{!node && f && <span className="muted tiny cat-src">{f.filename}{n.page_start ? `, p.${n.page_start}` : ""}</span>}</span>
                {!kind && <span className="cat-kind">{kindLabel[n.kind]}</span>}
                {!!n.amended_by?.length && <Badge tone="warn">amended</Badge>}<span className="muted tiny cat-pp">{n.pages} pp</span>
              </button>
            );
          })}
          {!shown.length && <p className="empty">{leaves.length ? "Nothing matches these filters." : "Nothing catalogued yet. Process your project information first."}</p>}
        </div>
      </section>
      {node && (
        <section className="panel panel-flush cat-viewer">
          {node.file_id && node.page_start ? (
            <PageViewer fileId={node.file_id} start={node.page_start} end={node.page_end || node.page_start}
              title={<><span className="mono">{node.number}</span> {node.title || node.label}</>}
              sub={fileById[node.file_id]?.filename ? `${fileById[node.file_id].filename}, ${(node.page_end || node.page_start) > node.page_start ? `pages ${node.page_start}-${node.page_end}` : `page ${node.page_start}`}` : undefined}
              pdfUrl={`/api/projects/${project.id}/catalog/item.pdf?id=${encodeURIComponent(node.id)}`}
              fullUrl={`/api/library/${node.file_id}/file.pdf#page=${node.page_start}`} onClose={() => setSel(null)}>
              {item && (
                <>
                  {item.summary && <p className="small">{item.summary}</p>}
                  {!!item.keywords?.length && <div className="kw">{item.keywords.slice(0, 14).map((k: string) => <span key={k} className="kwc">{k}</span>)}</div>}
                  {!!item.amended_by?.length && <p className="t-warn small">Amended by {item.amended_by.join(", ").replace(/AM:/g, "")}</p>}
                  {item.text && <button className="link-btn small" onClick={() => setShowText(!showText)}>{showText ? "Hide the extracted text" : "Show the extracted text the reviewer reads"}</button>}
                  {showText && <pre className="txt small-pre">{item.text}</pre>}
                </>
              )}
            </PageViewer>
          ) : <p className="empty">This item has no pages to show.</p>}
        </section>
      )}
    </div>
  );
}
