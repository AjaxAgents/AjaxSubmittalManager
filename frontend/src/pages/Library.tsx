import React, { useEffect, useMemo, useState } from "react";
import { api, fmtBytes, fmtTime, Job, LibraryFile, Project, TreeNode } from "../api";
import { DropZone } from "../components/DropZone";
import { Badge, Button, Card, Icon, useToast } from "../components/ui";

const KINDS = ["spec", "drawing", "amendment", "document"];

export function Library({ project, files, tree, onChanged }: { project: Project; files: LibraryFile[]; tree: TreeNode[]; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [item, setItem] = useState<any>(null);
  const [q, setQ] = useState("");

  useEffect(() => { if (sel) api.catalogItem(project.id, sel).then(setItem).catch(() => setItem(null)); }, [sel]);

  const leaves = useMemo(() => {
    const out: { node: TreeNode; group: string }[] = [];
    const walk = (ns: TreeNode[], group: string) => ns.forEach((n) => {
      if (["spec_section", "drawing_sheet", "amendment", "document"].includes(n.kind)) out.push({ node: n, group });
      else walk(n.children || [], n.kind === "group" ? n.label : group);
    });
    walk(tree, "");
    return out;
  }, [tree]);
  const ql = q.toLowerCase();
  const shown = leaves.filter((l) => !ql || l.node.label.toLowerCase().includes(ql));

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Project library</h1><p className="muted">Specs, drawings and amendments are split, indexed and summarized once. Reviews read only the slices they need.</p></div>
      </div>
      <div className="lib-stats">
        <Stat n={project.library.sections} label="spec sections" /><Stat n={project.library.sheets} label="drawing sheets" />
        <Stat n={project.library.amendments} label="amendments" /><Stat n={project.library.files} label="files" />
      </div>
      <div className="two-col wide-left">
        <Card title="Files" icon="book">
          <DropZone index={2} title="Add project information" icon="upload" busy={busy} subtitle="Project manual, drawing set, addenda, ASIs, RFIs. Type is auto-detected - correct it below if needed."
            chips={[]} onFiles={async (f) => {
              setBusy(true);
              try { const r = await api.uploadLibrary(project.id, f); r.errors.forEach((e) => toast(`${e.filename}: ${e.error}`, "bad")); onChanged(); }
              catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
            }} />
          <table className="matrix compact files-table">
            <thead><tr><th>File</th><th>Type</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.id}>
                  <td><div className="mono small">{f.filename}</div><div className="muted tiny">{f.pages} pp · {fmtBytes(f.size)} · v{f.version} · {fmtTime(f.uploaded_at)}</div></td>
                  <td>
                    <select className="input input-sm" value={f.kind} onChange={(e) => api.setFileKind(f.id, e.target.value).then(onChanged)}>
                      {f.kind === "auto" && <option value="auto">detecting…</option>}
                      {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                  </td>
                  <td>
                    {f.status === "ready" ? <Badge tone="ok">ready · {f.notes}</Badge> : f.status === "error" ? <Badge tone="bad" title={f.error}>error</Badge> :
                      <span className="proc"><span className="spinner spinner-brand" /> {f.notes || "queued"} {Math.round(f.progress * 100)}%</span>}
                    {f.status === "error" && <div className="t-bad tiny">{f.error}</div>}
                  </td>
                  <td><button className="icon-btn" aria-label="Remove file" onClick={() => { if (confirm(`Remove ${f.filename} and its catalog entries?`)) api.deleteFile(f.id).then(onChanged); }}><Icon name="trash" size={15} /></button></td>
                </tr>
              ))}
              {!files.length && <tr><td colSpan={4} className="muted">No files yet.</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Catalog" icon="layers" sub="What agents see as the Tier-0 index. Click an item to inspect its extracted text.">
          <div className="scope-search boxed"><Icon name="search" size={15} /><input placeholder="Filter catalog" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div className="cat-list">
            {shown.map(({ node, group }) => (
              <button key={node.id} className={`cat-row ${sel === node.id ? "on" : ""}`} onClick={() => setSel(node.id)}>
                <span className="mono-num">{node.number || ""}</span><span className="cat-t">{node.title || node.label}</span>
                {!!node.amended_by?.length && <Badge tone="warn">amended</Badge>}
                <span className="muted tiny">{node.pages} pp</span>
              </button>
            ))}
            {!shown.length && <p className="muted pad">Nothing catalogued yet.</p>}
          </div>
          {item && (
            <div className="cat-detail">
              <div className="row gap"><b className="mono">{item.number}</b><span>{item.title}</span><span className="grow" /><button className="icon-btn" onClick={() => { setSel(null); setItem(null); }}><Icon name="x" size={15} /></button></div>
              {item.summary && <p className="small">{item.summary}</p>}
              {!!item.keywords?.length && <div className="kw">{item.keywords.slice(0, 16).map((k: string) => <span key={k} className="kwc">{k}</span>)}</div>}
              {!!item.amended_by?.length && <p className="t-warn small">Amended by {item.amended_by.join(", ").replace(/AM:/g, "")}</p>}
              <pre className="txt small-pre">{item.text}</pre>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return <div className="stat"><b>{n}</b><span>{label}</span></div>;
}

export function History({ project, onOpen }: { project: Project; onOpen: (id: string) => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  useEffect(() => { api.jobs(project.id).then(setJobs); const t = setInterval(() => api.jobs(project.id).then(setJobs), 5000); return () => clearInterval(t); }, [project.id]);
  return (
    <div className="page">
      <div className="page-head"><div><h1>Review history</h1><p className="muted">Every run keeps its inputs, retrievals, model calls, findings and outputs.</p></div></div>
      <Card>
        <table className="matrix">
          <thead><tr><th>Submittal</th><th>Started</th><th>Status</th><th>Result</th><th>Cost</th></tr></thead>
          <tbody>
            {(jobs || []).map((j) => (
              <tr key={j.id} className="clickable" onClick={() => onOpen(j.id)}>
                <td><div className="mono">{j.params.spec_section}-{(j.params.submittal_no || "").padStart(3, "0")}-{j.params.revision}</div><div className="muted tiny">{j.params.spec_title} · {j.params.submittal_type}{j.parent_job && " · re-run"}</div></td>
                <td className="small">{fmtTime(j.created_at)}</td>
                <td><Badge tone={j.status === "done" ? "ok" : j.status === "error" ? "bad" : j.status === "running" ? "info" : "neutral"}>{j.status}</Badge></td>
                <td className="small">{j.status === "done" && j.result?.counts ? `${j.result.counts.non_compliant} NC · ${j.result.counts.human_review + (j.result.extra_human_review || 0)} HR · ${j.result.counts.compliant} C` : j.error ? <span className="t-bad tiny">{j.error.slice(0, 80)}</span> : j.stage}</td>
                <td className="mono">${Number(j.cost || 0).toFixed(2)}</td>
              </tr>
            ))}
            {jobs && !jobs.length && <tr><td colSpan={5} className="muted">No reviews yet.</td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
