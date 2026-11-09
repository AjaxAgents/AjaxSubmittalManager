import React, { useEffect, useState } from "react";
import { api, Project, Settings } from "../api";
import { Button, Field, Icon, Modal, useToast } from "./ui";

const STAGE_LABELS: Record<string, string> = {
  catalog: "Library cataloguing", scope: "Scope suggestion", intake: "Intake", planner: "Context Planner",
  advocate: "Compliance Advocate", examiner: "Non-Compliance Examiner", adjudicator: "Adjudicator", qa: "QA Checker",
};
const MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"];

export function SettingsModal({ onClose, onSaved }: { onClose: () => void; onSaved: (s: Settings) => void }) {
  const toast = useToast();
  const [s, setS] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.settings().then(setS); }, []);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      const out = await api.saveSettings({ api_key: key || undefined, mock: s.mock, stage_models: s.stages, usd_cap: s.usd_cap,
        scope_mode: s.scope_mode, page_budget: s.page_budget, web_search: s.web_search, review_mode: s.review_mode } as any);
      toast("Settings saved", "ok"); onSaved(out); onClose();
    } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  return (
    <Modal title="Settings" onClose={onClose} wide footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save</Button></>}>
      <div className="settings-grid">
        <section>
          <h4><Icon name="key" size={16} /> Anthropic API key</h4>
          <p className="muted small">Stored only on this computer{s.key_location ? <> (<code>{s.key_location}</code>)</> : null}, outside OneDrive. Never sent to the browser or logged.</p>
          <Field label={s.api_key_set ? `Key set (…${s.api_key_hint}). Paste a new one to replace it.` : "No key set"}>
            <input className="input mono" type="password" placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          </Field>
          <label className="toggle"><input type="checkbox" checked={s.mock} onChange={(e) => setS({ ...s, mock: e.target.checked })} /><span className="toggle-ui" />
            Offline demo mode (no API calls; heuristic stand-ins - for trying the app, not real reviews)</label>
          <h4 className="mt"><Icon name="spark" size={16} /> Budget & scope defaults</h4>
          <div className="grid-2">
            <Field label="Job budget cap (USD)" hint="At 80% Opus stages fall back to Sonnet; at 100% the job stops."><input className="input" type="number" min={1} step={1} value={s.usd_cap} onChange={(e) => setS({ ...s, usd_cap: +e.target.value })} /></Field>
            <Field label="Library page budget per job"><input className="input" type="number" min={10} value={s.page_budget} onChange={(e) => setS({ ...s, page_budget: +e.target.value })} /></Field>
            <Field label="Default review mode" hint="Can be switched per run next to the Run button.">
              <select className="input" value={s.review_mode || "cross"} onChange={(e) => setS({ ...s, review_mode: e.target.value })}>
                <option value="cross">Cross-examined (2 Opus agents)</option><option value="single">Single agent (1 Opus pass)</option>
              </select>
            </Field>
            <Field label="Default scope mode">
              <select className="input" value={s.scope_mode} onChange={(e) => setS({ ...s, scope_mode: e.target.value })}><option value="flexible">Flexible</option><option value="strict">Strict</option></select>
            </Field>
            <Field label="Web lookups" hint="Manufacturer data / listings via Claude web search ($10 per 1,000 searches)">
              <label className="toggle"><input type="checkbox" checked={s.web_search} onChange={(e) => setS({ ...s, web_search: e.target.checked })} /><span className="toggle-ui" /> Enabled for reviewers</label>
            </Field>
          </div>
          <p className="muted small">Finished PDF + TXT are copied to <code>{s.output_dir}</code></p>
        </section>
        <section>
          <h4><Icon name="layers" size={16} /> Model per stage</h4>
          <table className="matrix compact">
            <tbody>
              {Object.entries(s.stages).map(([k, v]) => (
                <tr key={k}><td>{STAGE_LABELS[k] || k}</td><td>
                  <select className="input input-sm mono" value={v} onChange={(e) => setS({ ...s, stages: { ...s.stages, [k]: e.target.value } })}>
                    {Array.from(new Set([...MODELS, v])).map((m) => <option key={m}>{m}</option>)}
                  </select>
                </td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Defaults: Haiku for cataloguing/intake, Sonnet for planning/QA, Opus for the two reviewers and the Adjudicator.</p>
        </section>
      </div>
    </Modal>
  );
}

export function ProjectModal({ project, onClose, onSaved, onDelete }: { project?: Project; onClose: () => void; onSaved: (p: Project) => void; onDelete?: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ name: project?.name || "", number: project?.number || "", ae_firm: project?.ae_firm || "",
    contractor: project?.contractor || "Ajax Building Company", owner: project?.owner || "", address: project?.address || "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    if (!f.name.trim()) { toast("Project name is required", "bad"); return; }
    setBusy(true);
    try { const p = project ? await api.updateProject(project.id, f) : await api.createProject(f); onSaved(p); onClose(); }
    catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  return (
    <Modal title={project ? "Project details" : "New project"} onClose={onClose}
      footer={<>{project && onDelete && <Button variant="danger" icon="trash" onClick={() => { if (confirm(`Delete ${project.name}, its library and history? Files on disk under data/ are kept.`)) onDelete(); }}>Delete</Button>}<span className="grow" />
        <Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>{project ? "Save" : "Create project"}</Button></>}>
      <p className="muted small">These fill every cover sheet for this project.</p>
      <div className="grid-2">
        <Field label="Project name"><input className="input" value={f.name} onChange={set("name")} placeholder="Riverside Medical Office Building" /></Field>
        <Field label="Project number"><input className="input mono" value={f.number} onChange={set("number")} placeholder="2417" /></Field>
        <Field label="Architect / Engineer"><input className="input" value={f.ae_firm} onChange={set("ae_firm")} /></Field>
        <Field label="Contractor"><input className="input" value={f.contractor} onChange={set("contractor")} /></Field>
        <Field label="Owner"><input className="input" value={f.owner} onChange={set("owner")} /></Field>
        <Field label="Address"><input className="input" value={f.address} onChange={set("address")} /></Field>
      </div>
    </Modal>
  );
}
