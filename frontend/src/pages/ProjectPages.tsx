import React, { useEffect, useMemo, useState } from "react";
import { api, DIVISION_NAMES, fmtDate, fmtTime, Project, STATUS_LABEL, Submittal } from "../api";
import { Badge, Button, Icon, useToast } from "../components/ui";
import { SortSelect } from "../components/Pickers";
import type { Prefill } from "./NewReview";

// ===================================================================== all projects

export function ProjectsPage({ projects, onOpen, onNew }: { projects: Project[]; onOpen: (id: string) => void; onNew: () => void }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("recent");
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const f = ql ? projects.filter((p) => `${p.number || ""} ${p.name} ${p.ae_firm || ""} ${p.contractor || ""}`.toLowerCase().includes(ql)) : [...projects];
    if (sort === "name") f.sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === "number") f.sort((a, b) => (a.number || "").localeCompare(b.number || "", undefined, { numeric: true }));
    else f.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
    return f;
  }, [projects, q, sort]);
  return (
    <div className="page">
      <header className="page-head"><h1>Projects</h1><Button variant="primary" icon="plus" onClick={onNew}>New project</Button></header>
      {!projects.length ? (
        <div className="welcome">
          <h2>Start with a project</h2>
          <ol className="steps">
            <li><b>Create the project.</b> Name, number, architect and contractor fill every cover page.</li>
            <li><b>Load the Project Library.</b> Upload the spec book, drawings and addenda, then press Process.</li>
            <li><b>Review a submittal.</b> Drop the PDF, check the details, run.</li>
          </ol>
          <Button variant="primary" size="lg" icon="plus" onClick={onNew}>Create a project</Button>
        </div>
      ) : (
        <>
        <div className="filterbar gap-b">
          <div className="search"><Icon name="search" size={15} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a project by name, number or architect" aria-label="Find a project"
              onKeyDown={(e) => { if (e.key === "Enter" && shown.length === 1) onOpen(shown[0].id); }} />
            {q && <button className="icon-btn" aria-label="Clear" onClick={() => setQ("")}><Icon name="x" size={14} /></button>}
          </div>
          <SortSelect value={sort} onChange={setSort} options={[["recent", "Recently updated"], ["name", "Name"], ["number", "Project number"]]} />
        </div>
        {!shown.length && <div className="panel empty">No project matches "{q}".</div>}
        <div className="proj-grid">
          {shown.map((p) => (
            <button key={p.id} className="proj-card" onClick={() => onOpen(p.id)}>
              <span className="proj-num mono">{p.number || "No number"}</span>
              <b className="proj-name">{p.name}</b>
              <span className="muted small">{p.ae_firm || "Architect not set"}</span>
              <span className="proj-stats"><span><b>{p.submittals || 0}</b> submittals</span><span><b>{p.library.sections}</b> spec sections</span><span><b>{p.library.sheets}</b> sheets</span></span>
            </button>
          ))}
        </div>
        </>
      )}
    </div>
  );
}

// ===================================================================== project home

export function ProjectHome({ project, onNav, onOpenRun, onEdit }: { project: Project; onNav: (v: string, arg?: any) => void; onOpenRun: (id: string) => void; onEdit: () => void }) {
  const [h, setH] = useState<any>(null);
  useEffect(() => { api.home(project.id).then(setH).catch(() => {}); }, [project.id, project.updated_at]);
  const lib = project.library;
  const st = h?.by_status || {};
  return (
    <div className="page">
      <header className="proj-hero">
        <div>
          <span className="proj-num mono">{project.number || "No project number"}</span>
          <h1>{project.name}</h1>
          <p className="muted">{[project.ae_firm && `Architect: ${project.ae_firm}`, project.contractor && `Contractor: ${project.contractor}`, project.address].filter(Boolean).join("   |   ")}</p>
        </div>
        <div className="row gap"><Button variant="ghost" onClick={onEdit}>Project details</Button><Button variant="primary" icon="play" onClick={() => onNav("new")}>New Submittal Review</Button></div>
      </header>

      <div className="tiles">
        <button className="tile" onClick={() => onNav("submittals")}><b>{h?.submittals ?? "-"}</b><span>Submittals</span></button>
        <button className="tile tile-bad" onClick={() => onNav("submittals", "exceptions")}><b>{st.exceptions || 0}</b><span>With exceptions</span></button>
        <button className="tile tile-warn" onClick={() => onNav("submittals", "needs_review")}><b>{st.needs_review || 0}</b><span>Need review</span></button>
        <button className="tile tile-ok" onClick={() => onNav("submittals", "clean")}><b>{st.clean || 0}</b><span>No exceptions</span></button>
      </div>

      <div className="two-col">
        <section className="panel">
          <h3 className="panel-title">Recent reviews <button className="link-btn small" onClick={() => onNav("submittals")}>Submittal log</button></h3>
          <table className="matrix compact">
            <tbody>
              {(h?.recent || []).map((r: any) => (
                <tr key={r.id} className="clickable" onClick={() => onOpenRun(r.id)}>
                  <td><div className="mono small">{r.label}</div><div className="muted tiny">{r.title}{r.subcontractor && ` - ${r.subcontractor}`}</div></td>
                  <td className="nowrap small">{fmtTime(r.created_at)}</td>
                  <td>{r.status === "done" ? <Counts c={r.counts} x={r.extra_hr} /> : <Badge tone={r.status === "error" ? "bad" : "info"}>{r.status}</Badge>}</td>
                </tr>
              ))}
              {h && !h.recent.length && <tr><td className="empty">No reviews yet.</td></tr>}
            </tbody>
          </table>
        </section>
        <div className="col gap">
          <section className="panel">
            <h3 className="panel-title">Project Library <button className="link-btn small" onClick={() => onNav("library")}>Open</button></h3>
            <dl className="kv">
              <div><dt>Spec sections</dt><dd>{lib.sections}</dd></div><div><dt>Drawing sheets</dt><dd>{lib.sheets}</dd></div>
              <div><dt>Amendments</dt><dd>{lib.amendments}</dd></div><div><dt>Quality templates</dt><dd>{lib.examples}</dd></div>
              <div><dt>Division 01 files</dt><dd>{lib.div01}</dd></div><div><dt>Cover pages</dt><dd>{lib.covers}</dd></div>
            </dl>
            {lib.unprocessed > 0 && <p className="note-warn"><Icon name="alert" size={15} /> {lib.unprocessed} file{lib.unprocessed > 1 ? "s" : ""} waiting to be processed. <button className="link-btn" onClick={() => onNav("library")}>Process now</button></p>}
            {lib.processing > 0 && <p className="muted small"><span className="spinner spinner-brand" /> Processing {lib.processing} file{lib.processing > 1 ? "s" : ""}...</p>}
          </section>
          <section className="panel">
            <h3 className="panel-title">Submittals by division</h3>
            <ul className="divbars">
              {(h?.by_division || []).map((d: any) => (
                <li key={d.code}><button onClick={() => onNav("submittals", `div:${d.code}`)}><span className="mono">{d.code}</span><span className="grow">{d.name || DIVISION_NAMES[d.code]}</span><b>{d.count}</b></button></li>
              ))}
              {h && !h.by_division.length && <li className="empty">Divisions appear here as submittals are reviewed.</li>}
            </ul>
            {h && <p className="muted tiny">{h.subcontractors} subcontractors, ${Number(h.spend || 0).toFixed(2)} spent on reviews so far</p>}
          </section>
        </div>
      </div>
    </div>
  );
}

export function Counts({ c, x = 0 }: { c: Record<string, number>; x?: number }) {
  return (
    <span className="counts">
      {!!c.non_compliant && <Badge tone="bad">{c.non_compliant} non-compliant</Badge>}
      {!!((c.human_review || 0) + x) && <Badge tone="warn">{(c.human_review || 0) + x} to review</Badge>}
      {!c.non_compliant && !((c.human_review || 0) + x) && <Badge tone="ok">No exceptions</Badge>}
    </span>
  );
}

// ===================================================================== submittal log

export function SubmittalsPage({ project, initialFilter, onOpenRun, onNew }: { project: Project; initialFilter?: string; onOpenRun: (id: string) => void; onNew: (p?: Prefill) => void }) {
  const toast = useToast();
  const [rows, setRows] = useState<Submittal[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState(initialFilter && !initialFilter.startsWith("div:") ? initialFilter : "");
  const [div, setDiv] = useState(initialFilter?.startsWith("div:") ? initialFilter.slice(4) : "");
  const [sub, setSub] = useState("");
  const [sort, setSort] = useState("number");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const load = () => api.submittals(project.id).then(setRows).catch(() => setRows([]));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [project.id]);
  const ql = q.trim().toLowerCase();
  const shown = (rows || []).filter((s) => (!status || s.status === status) && (!div || s.division === div) && (!sub || s.subcontractor === sub)
    && (!ql || `${s.number} ${s.title} ${s.spec_title} ${s.subcontractor}`.toLowerCase().includes(ql)));
  const groups = useMemo(() => {
    const g: Record<string, Submittal[]> = {};
    const cmp: Record<string, (a: Submittal, b: Submittal) => number> = {
      number: (a, b) => a.number.localeCompare(b.number, undefined, { numeric: true }),
      updated: (a, b) => (b.updated_at || 0) - (a.updated_at || 0),
      created: (a, b) => (b.created_at || 0) - (a.created_at || 0),
      title: (a, b) => (a.title || a.spec_title).localeCompare(b.title || b.spec_title),
      sub: (a, b) => (a.subcontractor || "~").localeCompare(b.subcontractor || "~"),
      status: (a, b) => a.status.localeCompare(b.status),
    };
    [...shown].sort(cmp[sort] || cmp.number).forEach((s) => { (g[s.division] ||= []).push(s); });
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
  }, [shown, sort]);
  const divs = Array.from(new Set((rows || []).map((s) => s.division))).sort();
  const subsList = Array.from(new Set((rows || []).map((s) => s.subcontractor).filter(Boolean))).sort();

  return (
    <div className="page">
      <header className="page-head"><h1>Submittals</h1><Button variant="primary" icon="play" onClick={() => onNew()}>New Submittal Review</Button></header>
      <div className="filterbar">
        <div className="search"><Icon name="search" size={15} /><input placeholder="Search number, title or subcontractor" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <select className="input input-sm" value={div} onChange={(e) => setDiv(e.target.value)} aria-label="Division"><option value="">All divisions</option>{divs.map((d) => <option key={d} value={d}>{d} {DIVISION_NAMES[d] || ""}</option>)}</select>
        <select className="input input-sm" value={sub} onChange={(e) => setSub(e.target.value)} aria-label="Subcontractor"><option value="">All subcontractors</option>{subsList.map((s) => <option key={s}>{s}</option>)}</select>
        <select className="input input-sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">Any status</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
        <SortSelect value={sort} onChange={setSort} options={[["number", "Submittal number"], ["updated", "Last updated"], ["created", "Date created"], ["title", "Title"], ["sub", "Subcontractor"], ["status", "Status"]]} />
      </div>
      {rows && !rows.length && <div className="panel empty">No submittals yet. Each review you run is logged here by division.</div>}
      {groups.map(([d, list]) => {
        const isClosed = closed.has(d);
        return (
          <section key={d} className="panel panel-flush">
            <button className="div-head" onClick={() => setClosed((c) => { const n = new Set(c); n.has(d) ? n.delete(d) : n.add(d); return n; })} aria-expanded={!isClosed}>
              <Icon name="chevron" size={14} className={isClosed ? "" : "rot90"} /><span className="mono">Division {d}</span><span>{list[0].division_name || DIVISION_NAMES[d]}</span><span className="tab-count">{list.length}</span>
            </button>
            {!isClosed && (
              <table className="matrix">
                <thead><tr><th>Number</th><th>Title</th><th>Subcontractor</th><th>Type</th><th>Status</th><th>Last review</th><th /></tr></thead>
                <tbody>
                  {list.map((s) => {
                    const stl = STATUS_LABEL[s.status] || STATUS_LABEL.draft;
                    return (
                      <tr key={s.id} className={s.latest ? "clickable" : ""} onClick={() => s.latest && onOpenRun(s.latest.id)}>
                        <td className="mono nowrap">{s.number}</td>
                        <td><div>{s.title || s.spec_title}</div><div className="muted tiny">{s.spec_section} {s.spec_title}</div></td>
                        <td className="small">{s.subcontractor || "-"}</td>
                        <td className="small nowrap">{s.type.replace("Product Data and Shop Drawings", "PD + SD")}</td>
                        <td><Badge tone={stl.tone}>{stl.label}</Badge>{s.latest?.status === "done" && <div className="tiny muted">{s.latest.counts.non_compliant || 0} NC, {(s.latest.counts.human_review || 0) + (s.latest.extra_hr || 0)} to review</div>}</td>
                        <td className="small nowrap">{s.latest ? fmtDate(s.latest.updated_at) : "-"}</td>
                        <td className="w-act" onClick={(e) => e.stopPropagation()}>
                          <Button size="sm" variant="ghost" onClick={() => onNew({ spec_section: s.spec_section, spec_title: s.spec_title, seq: s.seq, revision: String((parseInt(s.revision, 10) || 0) + 1),
                            title: s.title, type: s.type, subcontractor_id: s.subcontractor_id, library_refs: s.options?.library_refs })}>New revision</Button>
                          <button className="icon-btn" aria-label="Remove from log" onClick={() => { if (confirm(`Remove ${s.number} from the log? Review files stay on disk.`)) api.deleteSubmittal(s.id).then(() => { toast("Removed", "ok"); load(); }); }}><Icon name="trash" size={15} /></button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
        );
      })}
    </div>
  );
}
