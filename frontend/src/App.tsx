import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, Job, LibraryFile, Project, Settings, Sub, TreeNode, User } from "./api";
import { ProjectModal, SettingsModal } from "./components/Modals";
import { Icon, ToastProvider, useToast } from "./components/ui";
import { LibTab, Library } from "./pages/Library";
import { Login, Logo } from "./pages/Login";
import { NewReview, Prefill } from "./pages/NewReview";
import { ProjectHome, ProjectsPage, SubmittalsPage } from "./pages/ProjectPages";
import { RunView } from "./pages/RunView";

type View = "home" | "submittals" | "new" | "library" | "run";
interface Route { pid: string | null; view: View; arg: string }

const parse = (): Route => {
  const m = location.hash.replace(/^#\/?/, "").split("/");
  if (m[0] === "p" && m[1]) return { pid: m[1], view: (m[2] as View) || "home", arg: decodeURIComponent(m.slice(3).join("/")) };
  return { pid: null, view: "home", arg: "" };
};
const go = (pid: string | null, view: View = "home", arg = "") => { location.hash = pid ? `#/p/${pid}/${view}${arg ? `/${encodeURIComponent(arg)}` : ""}` : "#/projects"; };

const applyTheme = (t: string) => {
  document.documentElement.dataset.theme = t === "dark" ? "dark" : "light";
  try { localStorage.setItem("asm.theme", t); } catch { /* ignore */ }
};
try { applyTheme(localStorage.getItem("asm.theme") || "light"); } catch { /* ignore */ }

function Shell({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const toast = useToast();
  const [route, setRoute] = useState<Route>(parse());
  const [settings, setSettings] = useState<Settings | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [defaults, setDefaults] = useState<any>({});
  const [pending, setPending] = useState({ files: 0, pages: 0, est_ai_cost: 0 });
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [projModal, setProjModal] = useState<"new" | "edit" | null>(null);
  const [menu, setMenu] = useState(false);
  const [theme, setThemeState] = useState<string>(user.prefs?.theme || (document.documentElement.dataset.theme as string) || "light");
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [reviewKey, setReviewKey] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<string>(route.view);
  viewRef.current = route.view;

  useEffect(() => { const h = () => setRoute(parse()); window.addEventListener("hashchange", h); return () => window.removeEventListener("hashchange", h); }, []);
  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, [menu]);
  const setTheme = (t: string) => { setThemeState(t); api.saveSettings({ theme: t }).catch(() => {}); };

  const loadProjects = useCallback(async () => { const ps = await api.projects(); setProjects(ps); return ps; }, []);
  useEffect(() => {
    api.settings().then((s) => { setSettings(s); if (!s.api_key_set && !s.mock) setShowSettings(true); }).catch(() => {});
    loadProjects().catch(() => {});
  }, []);

  const pid = route.pid;
  const project = projects?.find((p) => p.id === pid) || null;

  const loadLibrary = useCallback(async () => {
    if (!pid) return null;
    const lib = await api.library(pid);
    setFiles(lib.files); setDefaults(lib.defaults || {}); setPending(lib.pending);
    setProjects((ps) => ps?.map((p) => (p.id === pid ? { ...p, library: lib.summary } : p)) || ps);
    return lib;
  }, [pid]);
  const loadSubs = useCallback(() => { if (pid) api.subs(pid).then(setSubs).catch(() => {}); }, [pid]);

  useEffect(() => {
    if (!pid) return;
    let alive = true;
    let lastReady = -1;
    loadSubs();
    const tick = async () => {
      const lib = await loadLibrary().catch(() => null);
      if (!alive) return;
      if (lib && lib.summary.ready !== lastReady) { lastReady = lib.summary.ready; api.tree(pid).then(setTree).catch(() => {}); }
      setTimeout(tick, lib?.summary.processing || viewRef.current === "library" ? 2500 : 10000);
    };
    tick();
    return () => { alive = false; };
  }, [pid]);

  const openRun = (id: string) => pid && go(pid, "run", id);
  const started = (j: Job) => { toast("Review started", "info"); go(j.project_id, "run", j.id); };
  const newReview = (p?: Prefill) => { setPrefill(p || null); setReviewKey((k) => k + 1); pid && go(pid, "new"); };
  const nav = (v: string, arg?: any) => pid && go(pid, v as View, arg || "");

  const navItems: [View, string, string][] = [["home", "Home", "home"], ["submittals", "Submittals", "list"], ["new", "New Submittal Review", "play"], ["library", "Project Library", "book"]];

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => go(null)} aria-label="All projects"><Logo size={28} /><span className="wordmark">AJAX</span><span className="product">Submittal Manager</span></button>
        {projects && projects.length > 0 && (
          <div className="proj-pill">
            <Icon name="building" size={16} />
            <select aria-label="Project" value={pid || ""} onChange={(e) => e.target.value ? go(e.target.value) : go(null)}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.number ? `${p.number}  ` : ""}{p.name}</option>)}
            </select>
          </div>
        )}
        <span className="grow" />
        <button className="top-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}>
          <Icon name={theme === "dark" ? "sun" : "moon"} size={17} /></button>
        {settings && !settings.api_key_set && !settings.mock && <button className="top-warn" onClick={() => setShowSettings(true)}>Add API key</button>}
        {settings?.mock && <button className="top-warn" onClick={() => setShowSettings(true)}>Offline demo</button>}
        <div className="usermenu" ref={menuRef}>
          <button className="avatar-btn" onClick={() => setMenu(!menu)} aria-expanded={menu} aria-label="Account menu">
            <span className="avatar">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</span><span className="uname">{user.display_name}</span><Icon name="down" size={14} />
          </button>
          {menu && (
            <div className="menu">
              <div className="menu-head"><b>{user.display_name}</b><span className="muted tiny">{user.username}</span></div>
              <button onClick={() => { setMenu(false); setShowSettings(true); }}><Icon name="gear" size={16} /> Settings</button>
              <button onClick={() => { setMenu(false); onSignOut(); }}><Icon name="logout" size={16} /> Sign out</button>
            </div>
          )}
        </div>
      </header>

      <div className="body">
        {project && (
          <nav className="sidebar" aria-label="Project">
            <button className="side-project" onClick={() => go(pid, "home")}>
              <span className="mono tiny side-num">{project.number || "Project"}</span><b>{project.name}</b>
            </button>
            <div className="side-nav">
              {navItems.map(([v, label, icon]) => (
                <button key={v} className={`nav-btn ${route.view === v ? "on" : ""}`} onClick={() => v === "new" ? newReview() : go(pid, v)} aria-current={route.view === v ? "page" : undefined}>
                  <Icon name={icon} size={17} /><span>{label}</span>
                  {v === "library" && project.library.processing > 0 && <span className="spinner spinner-brand" />}
                  {v === "library" && !project.library.processing && project.library.unprocessed > 0 && <span className="nav-dot" title="Files waiting to be processed" />}
                </button>
              ))}
              {route.view === "run" && <button className="nav-btn on"><Icon name="layers" size={17} /><span>Review</span></button>}
            </div>
            <button className="side-all" onClick={() => go(null)}><Icon name="chevron" size={14} className="flip" /> All projects</button>
          </nav>
        )}

        <main className="main">
          {!pid && projects && <ProjectsPage projects={projects} onOpen={(id) => go(id)} onNew={() => setProjModal("new")} />}
          {pid && projects && !project && <div className="page"><div className="panel empty">This project was not found. <button className="link-btn" onClick={() => go(null)}>Back to projects</button></div></div>}
          {project && (
            <>
              <div style={{ display: route.view === "new" ? "block" : "none" }}>
                <NewReview key={`${project.id}-${reviewKey}`} project={project} tree={tree} library={files} defaults={defaults} subs={subs} onSubsChanged={loadSubs}
                  onStarted={started} settingsMock={!!settings?.mock} apiKeySet={!!settings?.api_key_set} defaultMode={settings?.review_mode}
                  goLibrary={(t) => go(pid, "library", t)} prefill={prefill} />
              </div>
              {route.view === "home" && <ProjectHome project={project} onNav={nav} onOpenRun={openRun} onEdit={() => setProjModal("edit")} />}
              {route.view === "submittals" && <SubmittalsPage key={route.arg} project={project} initialFilter={route.arg} onOpenRun={openRun} onNew={newReview} />}
              {route.view === "library" && <Library project={project} files={files} tree={tree} defaults={defaults} pending={pending}
                onChanged={() => { loadLibrary(); loadSubs(); }} tab={(route.arg as LibTab) || "info"} setTab={(t) => go(pid, "library", t)} />}
              {route.view === "run" && route.arg && <RunView jobId={route.arg} onRerun={(j) => openRun(j.id)} onNew={() => newReview()} />}
            </>
          )}
        </main>
      </div>

      {showSettings && <SettingsModal theme={theme} setTheme={setThemeState} onClose={() => setShowSettings(false)} onSaved={(s) => setSettings(s)} />}
      {projModal && (
        <ProjectModal project={projModal === "edit" ? project || undefined : undefined} onClose={() => setProjModal(null)}
          onSaved={(p) => { loadProjects().then(() => go(p.id, projModal === "new" ? "library" : "home")); toast(projModal === "new" ? "Project created. Add its specs and drawings to the Project Library." : "Project saved", "ok"); }}
          onDelete={project ? async () => { await api.deleteProject(project.id); setProjModal(null); go(null); loadProjects(); } : undefined} />
      )}
    </div>
  );
}

function Gate() {
  const [state, setState] = useState<{ loading: boolean; user: User | null; hasUsers: boolean; version: string }>({ loading: true, user: null, hasUsers: true, version: "" });
  const check = () => api.status().then((s) => {
    setState({ loading: false, user: s.user, hasUsers: s.has_users, version: s.version });
    if (s.user?.prefs?.theme) applyTheme(s.user.prefs.theme);
  }).catch(() => setState((x) => ({ ...x, loading: false })));
  useEffect(() => { check(); const h = () => check(); window.addEventListener("asm:signed-out", h); return () => window.removeEventListener("asm:signed-out", h); }, []);
  if (state.loading) return <div className="boot"><span className="spinner spinner-brand" /></div>;
  if (!state.user) return <Login hasUsers={state.hasUsers} version={state.version} onSignedIn={(u) => { if (u.prefs?.theme) applyTheme(u.prefs.theme); setState((x) => ({ ...x, user: u })); }} />;
  return <Shell key={state.user.id} user={state.user} onSignOut={async () => { await api.logout().catch(() => {}); location.hash = ""; setState((x) => ({ ...x, user: null, hasUsers: true })); }} />;
}

export default function App() {
  return <ToastProvider><Gate /></ToastProvider>;
}
