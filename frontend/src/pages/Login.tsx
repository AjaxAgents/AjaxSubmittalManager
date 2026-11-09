import React, { useState } from "react";
import { api, User } from "../api";
import { Button, Field } from "../components/ui";

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true">
      <rect width="32" height="32" rx="6" fill="#FF5200" />
      <path d="M9 24 16 7l7 17h-4l-1.4-3.6h-3.3L13 24zm5.4-6.5h2.3L15.6 14z" fill="#fff" />
    </svg>
  );
}

export function Login({ hasUsers, onSignedIn, version }: { hasUsers: boolean; onSignedIn: (u: User, claimed?: number) => void; version: string }) {
  const [mode, setMode] = useState<"in" | "up">(hasUsers ? "in" : "up");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [name, setName] = useState("");
  const [remember, setRemember] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (mode === "up" && password !== password2) { setErr("The passwords don't match."); return; }
    setBusy(true);
    try {
      if (mode === "in") { const r = await api.login({ username, password, remember }); onSignedIn(r.user); }
      else { const r = await api.register({ username, password, display_name: name }); onSignedIn(r.user, r.claimed_projects); }
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="login">
      <div className="login-side">
        <div className="login-brand"><Logo size={40} /><div><b>AJAX</b><span>Submittal Manager</span></div></div>
        <p className="login-line">Review submittals against the project's own specs and drawings, and send a marked-up package that's ready for the architect.</p>
        <span className="login-ver">Version {version}</span>
      </div>
      <form className="login-card" onSubmit={submit}>
        <h1>{mode === "in" ? "Sign in" : hasUsers ? "Create an account" : "Create your account"}</h1>
        {mode === "up" && !hasUsers && <p className="muted small">This is the first account on this computer. Projects already here will move into it.</p>}
        {mode === "up" && <Field label="Your name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Jack Lund" /></Field>}
        <Field label="Username"><input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required /></Field>
        <Field label="Password"><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "in" ? "current-password" : "new-password"} required minLength={mode === "up" ? 8 : undefined} /></Field>
        {mode === "up" && <Field label="Confirm password" hint="At least 8 characters"><input className="input" type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" required /></Field>}
        {mode === "in" && <label className="check"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep me signed in for 2 weeks</label>}
        {err && <p className="form-err" role="alert">{err}</p>}
        <Button variant="primary" size="lg" loading={busy} type="submit">{mode === "in" ? "Sign in" : "Create account"}</Button>
        <p className="login-switch">
          {mode === "in" ? <>New here? <button type="button" className="link-btn" onClick={() => setMode("up")}>Create an account</button></>
            : hasUsers ? <>Already have an account? <button type="button" className="link-btn" onClick={() => setMode("in")}>Sign in</button></> : null}
        </p>
      </form>
    </div>
  );
}
