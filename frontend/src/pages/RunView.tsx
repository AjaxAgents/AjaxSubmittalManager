import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtTime, Job } from "../api";
import { Badge, Button, Card, Icon, Modal, Skeleton, useToast } from "../components/ui";

const STAGES: [string, string, string][] = [
  ["prepare", "Prepare & combine", "Merge zone 1 files, OCR check, lock reference scope"],
  ["intake", "Intake", "Page-map the package: products, models, options"],
  ["planner", "Context Planner", "Read scoped specs & amendments; build requirement checklist"],
  ["advocate", "Compliance Advocate", "Find affirmative evidence for each requirement"],
  ["examiner", "Non-Compliance Examiner", "Hunt deviations, omissions, contradictions"],
  ["adjudicator", "Adjudicator", "Verify citations; settle disagreements or escalate"],
  ["assemble", "Markup & Assemble", "Bluebeam-editable markups, cover, TOC, bookmarks"],
  ["qa", "QA Check", "Naming, cover, markup placement, TXT"],
];
const STAGES_SINGLE: [string, string, string][] = [
  STAGES[0], STAGES[1],
  ["single", "Reviewer (single pass)", "Build the checklist, then prove and challenge each requirement"],
  ["adjudicator", "Verify citations", "Check every cited page and quote; unverified calls go to Human Review"],
  STAGES[6], STAGES[7],
];
type StageState = { status: string; msg?: string; elapsed?: number; started?: number };

export function RunView({ jobId, onRerun, onNew }: { jobId: string; onRerun: (j: Job) => void; onNew: () => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [stages, setStages] = useState<Record<string, StageState>>({});
  const [log, setLog] = useState<{ t: number; text: string; tone?: string }[]>([]);
  const [spent, setSpent] = useState(0);
  const [cap, setCap] = useState(0);
  const [status, setStatus] = useState("queued");
  const [mock, setMock] = useState(false);
  const [mode, setMode] = useState("cross");
  const [now, setNow] = useState(Date.now() / 1000);
  const toast = useToast();

  useEffect(() => {
    setStages({}); setLog([]); setSpent(0); setStatus("queued"); setJob(null);
    api.job(jobId).then((j) => { setJob(j); setStatus(j.status); if (j.params?.review_mode) setMode(j.params.review_mode); if (j.params?.usd_cap) setCap(j.params.usd_cap); }).catch(() => {});
    api.settings().then((s) => setCap((c) => c || s.usd_cap)).catch(() => {});
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.kind === "stage") {
        setStages((s) => ({ ...s, [e.stage]: { status: e.status, msg: e.msg, elapsed: e.elapsed, started: e.status === "running" ? e.t : s[e.stage]?.started } }));
        if (e.msg && e.status !== "running") setLog((l) => [...l, { t: e.t, text: `${e.stage}: ${e.msg}`, tone: e.status === "error" ? "bad" : undefined }]);
      } else if (e.kind === "tool") {
        setLog((l) => [...l.slice(-300), { t: e.t, text: `${e.stage} → ${e.tool} ${e.input}` }]);
      } else if (e.kind === "cost") {
        setSpent(e.spent); setCap(e.cap);
      } else if (e.kind === "log") {
        setLog((l) => [...l, { t: e.t, text: e.msg, tone: e.level === "error" ? "bad" : e.level === "warn" ? "warn" : undefined }]);
      } else if (e.kind === "job") {
        if (e.mock !== undefined) setMock(e.mock);
        if (e.mode) setMode(e.mode);
        setStatus(e.status);
        if (["done", "error", "cancelled"].includes(e.status)) {
          api.job(jobId).then(setJob);
          if (e.status === "done") toast("Review complete - outputs ready", "ok");
          if (e.status === "error") toast(`Run failed: ${e.error}`, "bad");
        }
      }
    };
    es.addEventListener("end", () => es.close());
    es.onerror = () => { /* browser retries automatically */ };
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => { es.close(); clearInterval(t); };
  }, [jobId]);

  if (status === "done" && job?.result?.pdf) return <Results job={job} onRerun={onRerun} onNew={onNew} />;

  const running = status === "running" || status === "queued";
  const pct = cap ? Math.min(100, (spent / cap) * 100) : 0;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{running ? "Reviewing…" : status === "error" ? "Run failed" : status === "cancelled" ? "Run cancelled" : "Review"}</h1>
          <p className="muted mono">{job?.params?.spec_section} {job?.params?.spec_title}, submittal {job?.params?.submittal_no || "-"} rev {job?.params?.revision}{mode === "single" ? ", single pass" : ", cross-examined"}{mock && ", offline demo mode"}</p>
        </div>
        <div className="row gap">
          {running && <Button variant="danger" icon="stop" onClick={() => api.cancel(jobId).then(() => toast("Cancelling after the current call…"))}>Cancel</Button>}
          {!running && <Button icon="plus" onClick={onNew}>New review</Button>}
        </div>
      </div>
      {status === "error" && <div className="banner banner-bad"><Icon name="alert" /> {job?.error || "The run failed."} Earlier stages' outputs are kept in the job folder.</div>}
      <div className="run-grid">
        <Card title="Pipeline" icon="layers">
          <ol className="stepper">
            {(mode === "single" ? STAGES_SINGLE : STAGES).map(([k, label, desc], i) => {
              const s = stages[k] || { status: "pending" };
              const el = s.status === "running" && s.started ? Math.max(0, now - s.started) : s.elapsed;
              return (
                <li key={k} className={`step step-${s.status}`}>
                  <span className="step-dot">{s.status === "done" ? <Icon name="check" size={14} /> : s.status === "error" ? <Icon name="x" size={14} /> : s.status === "running" ? <span className="pulse" /> : i + 1}</span>
                  <div className="step-body">
                    <div className="step-title">{label}{(k === "advocate" || k === "examiner") && <span className="tag-par">parallel</span>}
                      {el !== undefined && s.status !== "pending" && <span className="step-time mono">{fmtDur(el)}</span>}</div>
                    <div className="step-desc">{s.msg || desc}</div>
                  </div>
                </li>
              );
            })}
          </ol>
        </Card>
        <div className="col gap">
          <Card title="Cost" icon="spark">
            <div className="meter"><div className="meter-fill" style={{ width: `${pct}%` }} /></div>
            <div className="meter-legend"><b>${spent.toFixed(2)}</b><span className="muted">of ${cap ? cap.toFixed(2) : "—"} budget for this review</span></div>
          </Card>
          <Card title="Activity" icon="history" sub="Every retrieval is logged and listed in the TXT under Scope Checked.">
            <div className="log">
              {log.length === 0 && <><Skeleton /><Skeleton w="70%" /><Skeleton w="85%" /></>}
              {log.slice().reverse().map((l, i) => (
                <div key={i} className={`log-line ${l.tone ? `t-${l.tone}` : ""}`}><span className="mono muted">{new Date(l.t * 1000).toLocaleTimeString()}</span> {l.text}</div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

const fmtDur = (s: number) => (s < 60 ? `${s.toFixed(0)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);

// ===================================================================== results

const RES: Record<string, { label: string; tone: "ok" | "bad" | "warn" | "neutral" }> = {
  compliant: { label: "Compliant", tone: "ok" }, non_compliant: { label: "Non-compliant", tone: "bad" },
  human_review: { label: "Human review", tone: "warn" }, not_applicable: { label: "N/A", tone: "neutral" },
};

function Results({ job, onRerun, onNew }: { job: Job; onRerun: (j: Job) => void; onNew: () => void }) {
  const r = job.result;
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [internal, setInternal] = useState(false);
  const [tab, setTab] = useState<"matrix" | "human" | "txt" | "looked" | "markups">("matrix");
  const [filter, setFilter] = useState("all");
  const [txt, setTxt] = useState("");
  const [rerun, setRerun] = useState(false);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [imgLoading, setImgLoading] = useState(true);
  const reqs = useMemo(() => Object.fromEntries((r.requirements || []).map((q: any) => [q.id, q])), [r]);
  const verdicts: any[] = r.verdicts || [];
  const counts = r.counts || {};
  const s2o: Record<string, number> = r.src_to_out || {};
  const outPage = (src?: number) => (src ? s2o[String(src)] || src : undefined);
  const nHR = (counts.human_review || 0) + (r.extra_human_review || 0);
  useEffect(() => { api.txt(job.id).then(setTxt).catch(() => {}); }, [job.id]);
  useEffect(() => setImgLoading(true), [page, internal]);

  const shown = verdicts.filter((v) => filter === "all" || v.result === filter);
  const go = (p?: number) => { if (p) { setPage(p); } };

  return (
    <div className="page page-results">
      <div className="res-head">
        <div className="res-title">
          <span className="res-meta">Reviewed {fmtTime(job.updated_at)}, {r.review_mode === "single" ? "single pass" : "cross-examined"}{r.mock && ", offline demo mode"}</span>
          <h1 className="mono">{r.pdf_name}</h1>
          <div className="res-counts">
            <Badge tone="ok">{counts.compliant || 0} compliant</Badge>
            <Badge tone="bad">{counts.non_compliant || 0} non-compliant</Badge>
            <Badge tone="warn">{nHR} human review</Badge>
            {!!counts.not_applicable && <Badge>{counts.not_applicable} N/A</Badge>}
            <span className="muted small">{r.pages} pages, {r.markups?.length || 0} markups, ${Number(r.cost || 0).toFixed(2)}</span>
          </div>
        </div>
        <div className="res-actions">
          <a className="btn btn-primary btn-md" href={`/api/jobs/${job.id}/download/pdf`}><Icon name="download" size={17} /><span>PDF</span></a>
          <a className="btn btn-secondary btn-md" href={`/api/jobs/${job.id}/download/txt`}><Icon name="download" size={17} /><span>TXT</span></a>
          <Button icon="folder" onClick={() => api.openFolder(job.id).then((x) => toast(x.ok ? `Opened ${x.folder}` : `Output folder: ${x.folder}`, x.ok ? "ok" : "info"))}>Open folder</Button>
          <Button icon="refresh" onClick={() => setRerun(true)}>Re-run</Button>
          <Button variant="ghost" icon="plus" onClick={onNew}>New</Button>
        </div>
      </div>
      {r.budget_hit && <div className="banner banner-warn"><Icon name="alert" /> The job budget was reached; unreviewed requirements are listed under Human Review (budget).</div>}

      <div className="res-grid">
        <div className="viewer">
          <div className="thumbs" role="listbox" aria-label="Pages">
            {Array.from({ length: r.pages }, (_, i) => i + 1).map((p) => (
              <button key={p} className={`vthumb ${p === page ? "on" : ""}`} onClick={() => setPage(p)} aria-label={`Page ${p}`}>
                <img loading="lazy" src={`/api/jobs/${job.id}/pages/${p}.png?dpi=24`} alt="" />
                <span>{p}</span>
              </button>
            ))}
          </div>
          <div className="stage">
            <div className="stage-bar">
              <Button size="sm" variant="ghost" icon="chevron" className="flip" onClick={() => setPage(Math.max(1, page - 1))} aria-label="Previous page" />
              <span className="mono small">Page {page} / {r.pages}</span>
              <Button size="sm" variant="ghost" icon="chevron" onClick={() => setPage(Math.min(r.pages, page + 1))} aria-label="Next page" />
              <span className="grow" />
              <label className="toggle small"><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /><span className="toggle-ui" /> Internal layer</label>
              <a className="link-btn small" href={`/api/jobs/${job.id}/view.pdf`} target="_blank" rel="noreferrer">Open PDF ↗</a>
            </div>
            <div className="stage-canvas">
              {imgLoading && <div className="stage-loading"><span className="spinner spinner-brand" /></div>}
              <img key={`${page}-${internal}`} src={`/api/jobs/${job.id}/pages/${page}.png?dpi=110&internal=${internal ? 1 : 0}`} alt={`Output page ${page}`} onLoad={() => setImgLoading(false)} />
            </div>
          </div>
        </div>

        <div className="side">
          <div className="tabs" role="tablist">
            {([["matrix", `Requirements (${verdicts.length})`], ["human", `Human review (${nHR})`], ["txt", "Review notes"], ["looked", "Sources & cost"], ["markups", "Markups"]] as const).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          <div className="tab-body">
            {tab === "matrix" && (
              <>
                <div className="filters">
                  {["all", "non_compliant", "human_review", "compliant", "not_applicable"].map((f) => (
                    <button key={f} className={`fchip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
                      {f === "all" ? "All" : RES[f].label} <span>{f === "all" ? verdicts.length : verdicts.filter((v) => v.result === f).length}</span>
                    </button>
                  ))}
                </div>
                <table className="matrix">
                  <thead><tr><th>ID</th><th>Requirement</th><th>Verdict</th><th>Pg</th></tr></thead>
                  <tbody>
                    {shown.map((v) => {
                      const q = reqs[v.requirement_id] || {};
                      const p = outPage(v.pages?.[0]);
                      return (
                        <tr key={v.requirement_id} onClick={() => go(p)} className={p ? "clickable" : ""} title={p ? `Jump to page ${p}` : ""}>
                          <td className="mono nowrap">{v.txt_id}</td>
                          <td>
                            <div className="req-text">{q.text}</div>
                            <div className="req-src mono">{q.source?.section} {q.source?.article}{q.amended_by?.length ? `, ${q.amended_by.join(", ").replace(/AM:/g, "")}` : ""}</div>
                            <div className="req-sum">{v.summary}</div>
                          </td>
                          <td><Badge tone={RES[v.result].tone}>{RES[v.result].label}</Badge>{v.decided_by === "adjudicator" && <div className="muted tiny">adjudicated</div>}</td>
                          <td className="mono">{p || "-"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
            {tab === "human" && (
              <div className="hr-list">
                {[["questionable", "Questionable compliance / missing info"], ["rfi", "RFI candidates"], ["faulty_data", "Faulty or contradictory data"]].map(([k, label]) => {
                  const kinds = k === "questionable" ? ["questionable", "missing_info", "budget_hit", "scope", ""] : [k];
                  const items = [...verdicts.filter((v) => v.result === "human_review" && kinds.includes(v.human_review_kind || "")).map((v) => ({ id: v.txt_id, summary: v.summary, page: outPage(v.pages?.[0]) })),
                    ...(r.extra_hr || []).filter((x: any) => kinds.includes(x.kind)).map((x: any) => ({ id: x.txt_id, summary: x.summary, page: undefined }))];
                  if (!items.length) return null;
                  return (
                    <div key={k} className="hr-group">
                      <h4>{label} <span className="muted">({items.length})</span></h4>
                      {items.map((x) => (
                        <div key={x.id} className={`hr-item ${x.page ? "clickable" : ""}`} onClick={() => go(x.page)}>
                          <span className="mono hr-id">{x.id}</span><span>{x.summary}</span>{x.page && <span className="mono muted">p.{x.page}</span>}
                        </div>
                      ))}
                    </div>
                  );
                })}
                {nHR === 0 && <p className="muted">Nothing needs human review.</p>}
              </div>
            )}
            {tab === "txt" && <pre className="txt">{txt || "Loading…"}</pre>}
            {tab === "looked" && (
              <div>
                <h4 className="sub-h">Retrievals ({(r.retrievals || []).length})</h4>
                <table className="matrix compact">
                  <thead><tr><th>Stage</th><th>Item</th><th>Pages</th><th>Scope</th></tr></thead>
                  <tbody>
                    {(r.retrievals || []).map((x: any, i: number) => (
                      <tr key={i}><td>{x.stage}</td><td className="mono">{String(x.item).replace(/^[A-Z]+:/, "")}<div className="muted tiny">{x.reason}</div></td><td>{x.pages}</td>
                        <td>{!x.allowed ? <Badge tone="bad">refused</Badge> : x.in_scope ? <Badge tone="ok">in scope</Badge> : <Badge tone="warn">extra</Badge>}</td></tr>
                    ))}
                  </tbody>
                </table>
                <h4 className="sub-h">Cost by stage</h4>
                <table className="matrix compact">
                  <thead><tr><th>Stage</th><th>Calls</th><th>In</th><th>Cached</th><th>Out</th><th>Cost</th></tr></thead>
                  <tbody>
                    {Object.entries(r.cost_by_stage || {}).map(([k, v]: any) => (
                      <tr key={k}><td>{k}</td><td>{v.calls}</td><td>{v.input}</td><td>{v.cache_read}</td><td>{v.output}</td><td>${v.cost.toFixed(3)}</td></tr>
                    ))}
                  </tbody>
                </table>
                {!!(r.qa_lines || []).length && <><h4 className="sub-h">QA notes</h4><ul className="plain">{r.qa_lines.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></>}
              </div>
            )}
            {tab === "markups" && (
              <table className="matrix compact">
                <thead><tr><th>ID</th><th>Type</th><th>Layer</th><th>Pg</th></tr></thead>
                <tbody>
                  {(r.markups || []).map((m: any) => (
                    <tr key={m.id} className="clickable" onClick={() => go(m.page)}>
                      <td className="mono">{m.id}</td><td>{m.subject} ({m.type})<div className="muted tiny">{m.text}</div>{m.warning && <div className="t-warn tiny">{m.warning}</div>}</td>
                      <td>{m.layer === "ae" ? <Badge tone="info">A/E</Badge> : <Badge>internal</Badge>}</td><td className="mono">{m.page}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {rerun && (
        <Modal title="Re-run adjudication & assembly" onClose={() => setRerun(false)}
          footer={<><Button variant="ghost" onClick={() => setRerun(false)}>Cancel</Button>
            <Button variant="primary" icon="refresh" loading={busy} onClick={async () => {
              setBusy(true);
              try { const nj = await api.rerun(job.id, notes); setRerun(false); onRerun(nj); } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
            }}>Re-run</Button></>}>
          <p className="muted">Reuses the intake, requirements and both reviewers' findings. Only the Adjudicator, markups, package and notes are rebuilt, so it is quick and cheap. Refer to items by their IDs.</p>
          <textarea className="input textarea" rows={6} autoFocus value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder={'e.g. "N-02 is accepted per RFI 112 - finish 630 approved by Architect."\n"H-03: supplier confirmed kick plates are 10 in. high."'} />
        </Modal>
      )}
    </div>
  );
}
