import React, { useCallback, useEffect, useState } from "react";
import { api, Health, Job, LibraryFile, Project, Settings, TreeNode } from "./api";
import { ProjectModal, SettingsModal } from "./components/Modals";
import { Badge, Button, Icon, ToastProvider, useToast } from "./components/ui";
import { History, Library } from "./pages/Library";
import { NewReview } from "./pages/NewReview";
import { RunView } from "./pages/RunView";

type View = "new" | "library" | "history" | "run";
const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

function Shell() {
  const toast = useToast();
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [pid, setPid] = useState<string | null>(store.get("asm.project"));
  const [view, setView] = useState<View>("new");
  const [jobId, setJobId] = useState<string | null>(null);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [projModal, setProjModal] = useState<"new" | "edit" | null>(null);
  const [reviewKey, setReviewKey] = useState(0);

  const project = projects?.find((p) => p.id === pid) || null;

  const loadProjects = useCallback(async () => {
    const ps = await api.projects();
    setProjects(ps);
    if (ps.length && (!pid || !ps.some((p) => p.id === pid))) setPid(ps[0].id);
    return ps;
  }, [pid]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => toast("Cannot reach the ASM backend. Is it running?", "bad"));
    api.settings().then(setSettings).catch(() => {});
    loadProjects().catch(() => {});
  }, []);
  useEffect(() => { if (pid) store.set("asm.project", pid); }, [pid]);

  const loadLibrary = useCallback(async () => {
    if (!pid) return;
    const lib = await api.library(pid);
    setFiles(lib.files);
    setProjects((ps) => ps?.map((p) => (p.id === pid ? { ...p, library: lib.summary } : p)) || ps);
    return lib;
  }, [pid]);

  // poll the library while cataloguing; refresh the tree when it settles
  useEffect(() => {
    if (!pid) return;
    let alive = true;
    let lastReady = -1;
    const tick = async () => {
      const lib = await loadLibrary().catch(() => null);
      if (!alive || !lib) return;
      if (lib.summary.ready !== lastReady) { lastReady = lib.summary.ready; api.tree(pid).then(setTree).catch(() => {}); }
      setTimeout(tick, lib.summary.processing ? 2000 : 8000);
    };
    tick();
    return () => { alive = false; };
  }, [pid]);

  const openJob = (id: string) => { setJobId(id); setView("run"); };
  const started = (j: Job) => { openJob(j.id); toast("Review started", "info"); };
  const apiReady = !!settings?.api_key_set || !!settings?.mock;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true"><svg viewBox="0 0 32 32" width="28" height="28"><rect width="32" height="32" rx="7" fill="var(--brand-orange)" /><path d="M9 24 16 7l7 17h-4l-1.4-3.6h-3.3L13 24zm5.4-6.5h2.3L15.6 14z" fill="#fff" /></svg></span>
          <span className="wordmark">AJAX</span><span className="product">Submittal Manager</span>
        </div>
        {project && (
          <div className="proj-pill">
            <select aria-label="Project" value={pid || ""} onChange={(e) => { setPid(e.target.value); setView("new"); setReviewKey((k) => k + 1); }}>
              {projects?.map((p) => <option key={p.id} value={p.id}>{p.number ? `${p.number} · ` : ""}{p.name}</option>)}
            </select>
          </div>
        )}
        <span className="grow" />
        <button className={`status-pill ${apiReady ? (settings?.mock ? "warn" : "ok") : "bad"}`} onClick={() => setShowSettings(true)}>
          <span className="dot" />{settings?.mock ? "Offline demo" : settings?.api_key_set ? "Claude API connected" : "API key needed"}
        </button>
        <button className="icon-btn" onClick={() => setShowSettings(true)} aria-label="Settings"><Icon name="gear" /></button>
      </header>

      <div className="body">
        <nav className="sidebar" aria-label="Main">
          <div className="side-section">
            <div className="side-label">Project</div>
            {project ? (
              <button className="side-project" onClick={() => setProjModal("edit")}>
                <b>{project.name}</b><span className="muted small">{project.number ? `#${project.number} · ` : ""}{project.ae_firm || "A/E not set"}</span>
              </button>
            ) : <p className="muted small">No project yet</p>}
          </div>
          <div className="side-nav">
            <NavBtn icon="play" label="New review" active={view === "new"} onClick={() => { setView("new"); }} disabled={!project} />
            <NavBtn icon="book" label="Project library" active={view === "library"} onClick={() => setView("library")} disabled={!project}
              badge={project?.library.processing ? <span className="spinner spinner-brand" /> : project ? <span className="nav-count">{project.library.sections + project.library.sheets}</span> : null} />
            <NavBtn icon="history" label="Review history" active={view === "history"} onClick={() => setView("history")} disabled={!project} />
            {jobId && <NavBtn icon="layers" label="Current run" active={view === "run"} onClick={() => setView("run")} />}
          </div>
          <div className="side-section">
            <div className="side-label">All projects</div>
            {projects?.map((p) => (
              <button key={p.id} className={`side-link ${p.id === pid ? "on" : ""}`} onClick={() => { setPid(p.id); setView("new"); setReviewKey((k) => k + 1); }}>
                <span className="mono small">{p.number || "—"}</span> {p.name}
              </button>
            ))}
            <Button variant="ghost" size="sm" icon="plus" onClick={() => setProjModal("new")}>New project</Button>
          </div>
          <div className="side-foot muted tiny">v{health?.version} · AI-assisted contractor review - a qualified person verifies before submission.</div>
        </nav>

        <main className="main">
          {projects && !projects.length && (
            <div className="page">
              <div className="welcome">
                <h1>Welcome to Ajax Submittal Manager</h1>
                <p className="muted">First-pass submittal review against your project's own specs and drawings, with Bluebeam-editable markups and a concise review memo.</p>
                <ol className="welcome-steps">
                  <li><b>Create a project</b> - name, number, A/E and contractor fill every cover sheet.</li>
                  <li><b>Load the library once</b> - drop the project manual, drawings and addenda; ASM catalogues them.</li>
                  <li><b>Run a review</b> - drop the submittal, confirm the reference scope, add instructions, run.</li>
                </ol>
                <Button variant="primary" size="lg" icon="plus" onClick={() => setProjModal("new")}>Create your first project</Button>
                {!apiReady && <p className="muted small mt">Then add your Anthropic API key in <button className="link-btn" onClick={() => setShowSettings(true)}>Settings</button> (or turn on offline demo mode to try it).</p>}
              </div>
            </div>
          )}
          {project && (
            <>
              <div style={{ display: view === "new" ? "block" : "none" }}>
                <NewReview key={`${project.id}-${reviewKey}`} project={project} tree={tree} library={files} onLibraryChanged={loadLibrary}
                  onStarted={started} settingsMock={!!settings?.mock} apiKeySet={!!settings?.api_key_set} />
              </div>
              {view === "library" && <Library project={project} files={files} tree={tree} onChanged={loadLibrary} />}
              {view === "history" && <History project={project} onOpen={openJob} />}
              {view === "run" && jobId && <RunView jobId={jobId} onRerun={(j) => openJob(j.id)} onNew={() => { setView("new"); setReviewKey((k) => k + 1); }} />}
            </>
          )}
        </main>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onSaved={(s) => { setSettings(s); api.health().then(setHealth); }} />}
      {projModal && (
        <ProjectModal project={projModal === "edit" ? project || undefined : undefined} onClose={() => setProjModal(null)}
          onSaved={(p) => { loadProjects().then(() => setPid(p.id)); toast(projModal === "new" ? "Project created - add the spec book and drawings to its library" : "Project saved", "ok"); if (projModal === "new") setView("new"); }}
          onDelete={project ? async () => { await api.deleteProject(project.id); setProjModal(null); setPid(null); loadProjects(); } : undefined} />
      )}
    </div>
  );
}

function NavBtn({ icon, label, active, onClick, disabled, badge }: { icon: string; label: string; active: boolean; onClick: () => void; disabled?: boolean; badge?: React.ReactNode }) {
  return (
    <button className={`nav-btn ${active ? "on" : ""}`} onClick={onClick} disabled={disabled} aria-current={active ? "page" : undefined}>
      <Icon name={icon} size={17} /><span>{label}</span>{badge}
    </button>
  );
}

export default function App() {
  return <ToastProvider><Shell /></ToastProvider>;
}
