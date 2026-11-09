import React, { useEffect, useState } from "react";
import { api, Project, Settings } from "../api";
import { Button, Field, Icon, Modal, useToast } from "./ui";

const STAGE_LABELS: Record<string, string> = {
  catalog: "Library summaries", scope: "Reference suggestions", intake: "Reading the package", planner: "Requirements checklist",
  single: "Single-pass reviewer", advocate: "Compliance reviewer", examiner: "Deviation reviewer", adjudicator: "Settling disagreements", qa: "Package check",
};
const MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"];

export function SettingsModal({ onClose, onSaved, theme, setTheme }: { onClose: () => void; onSaved: (s: Settings) => void; theme: string; setTheme: (t: string) => void }) {
  const toast = useToast();
  const [s, setS] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState({ current: "", next: "" });
  const [busy, setBusy] = useState(false);
  const [adv, setAdv] = useState(false);
  useEffect(() => { api.settings().then((x) => { setS(x); setName(x.user.display_name); }); }, []);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      if (name !== s.user.display_name || pw.next) {
        await api.updateMe({ display_name: name, ...(pw.next ? { current_password: pw.current, new_password: pw.next } : {}) });
      }
      const out = await api.saveSettings({ api_key: key || undefined, mock: s.mock, stage_models: s.stages, usd_cap: s.usd_cap,
        scope_mode: s.scope_mode, page_budget: s.page_budget, web_search: s.web_search, review_mode: s.review_mode, theme });
      toast("Settings saved", "ok"); onSaved(out); onClose();
    } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  return (
    <Modal title="Settings" onClose={onClose} wide footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save</Button></>}>
      <div className="settings-grid">
        <section>
          <h4><Icon name="users" size={16} /> Your account</h4>
          <Field label="Display name" hint="Shown as 'Prepared by' on cover pages"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <div className="grid-2">
            <Field label="Current password"><input className="input" type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" /></Field>
            <Field label="New password"><input className="input" type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" /></Field>
          </div>
          <h4 className="mt"><Icon name="key" size={16} /> Anthropic API key</h4>
          <p className="muted small">Your own key, encrypted on this computer and never shown in the browser. Reviews on your projects are billed to it.</p>
          <Field label={s.api_key_set ? `Key ending in ${s.api_key_hint} is set${s.own_key ? "" : " (shared key)"}. Paste a new one to replace it.` : "No key set yet"}>
            <input className="input mono" type="password" placeholder="sk-ant-api..." value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          </Field>
          <h4 className="mt"><Icon name="sun" size={16} /> Appearance</h4>
          <div className="seg" role="radiogroup" aria-label="Theme">
            <button role="radio" aria-checked={theme === "light"} className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")}><Icon name="sun" size={15} /> Light</button>
            <button role="radio" aria-checked={theme === "dark"} className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")}><Icon name="moon" size={15} /> Dark</button>
          </div>
        </section>
        <section>
          <h4><Icon name="spark" size={16} /> Review defaults</h4>
          <div className="grid-2">
            <Field label="Review mode"><select className="input" value={s.review_mode || "cross"} onChange={(e) => setS({ ...s, review_mode: e.target.value })}>
              <option value="cross">Cross-examined (2 reviewers)</option><option value="single">Single pass</option></select></Field>
            <Field label="Budget per review (USD)" hint="Stops the review if it is reached"><input className="input" type="number" min={1} step={1} value={s.usd_cap} onChange={(e) => setS({ ...s, usd_cap: +e.target.value })} /></Field>
            <Field label="Reference scope"><select className="input" value={s.scope_mode} onChange={(e) => setS({ ...s, scope_mode: e.target.value })}>
              <option value="flexible">Flexible (may read a few extra pages)</option><option value="strict">Strict (selected references only)</option></select></Field>
            <Field label="Library pages per review"><input className="input" type="number" min={10} value={s.page_budget} onChange={(e) => setS({ ...s, page_budget: +e.target.value })} /></Field>
          </div>
          <p className="muted small">Finished files are copied to <code>{s.output_dir}</code></p>
          <button className="link-btn small" onClick={() => setAdv(!adv)}>{adv ? "Hide" : "Show"} advanced settings</button>
          {adv && (
            <>
              <label className="toggle small"><input type="checkbox" checked={s.web_search} onChange={(e) => setS({ ...s, web_search: e.target.checked })} /><span className="toggle-ui" /> Allow web lookups for manufacturer data ($10 per 1,000 lookups)</label>
              <label className="toggle small"><input type="checkbox" checked={s.mock} onChange={(e) => setS({ ...s, mock: e.target.checked })} /><span className="toggle-ui" /> Offline demo mode (no API calls; simple rules, not a real review)</label>
              <table className="matrix compact">
                <tbody>
                  {Object.entries(s.stages).map(([k, v]) => (
                    <tr key={k}><td className="small">{STAGE_LABELS[k] || k}</td><td>
                      <select className="input input-sm mono" value={v} onChange={(e) => setS({ ...s, stages: { ...s.stages, [k]: e.target.value } })}>
                        {Array.from(new Set([...MODELS, v])).map((m) => <option key={m}>{m}</option>)}
                      </select></td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
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
      footer={<>{project && onDelete && <Button variant="danger" icon="trash" onClick={() => { if (confirm(`Delete ${project.name}, its library and submittal log?`)) onDelete(); }}>Delete project</Button>}<span className="grow" />
        <Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>{project ? "Save" : "Create project"}</Button></>}>
      <p className="muted small">These fill every cover page for this project.</p>
      <div className="grid-2">
        <Field label="Project name"><input className="input" value={f.name} onChange={set("name")} placeholder="Microsoft CLT40 Data Center" /></Field>
        <Field label="Project number"><input className="input mono" value={f.number} onChange={set("number")} placeholder="50000394" /></Field>
        <Field label="Architect / engineer"><input className="input" value={f.ae_firm} onChange={set("ae_firm")} /></Field>
        <Field label="Contractor"><input className="input" value={f.contractor} onChange={set("contractor")} /></Field>
        <Field label="Owner"><input className="input" value={f.owner} onChange={set("owner")} /></Field>
        <Field label="Address"><input className="input" value={f.address} onChange={set("address")} /></Field>
      </div>
    </Modal>
  );
}
