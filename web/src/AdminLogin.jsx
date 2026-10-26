import { useState } from "react";
import { adminChangePassword, adminLogin } from "./api.js";

// Sign-in for the admin workshop. The first sign-in uses the deployment's admin token as a one-time password and
// then must choose a new password; the session that comes back is what every admin call sends.
const MIN = 12;

export default function AdminLogin({ pending, notice, onSignedIn }) {
  const [username, setUsername] = useState(pending?.username || "admin");
  const [password, setPassword] = useState("");
  const [session, setSession] = useState(pending?.must_change ? pending : null);   // signed in, must change
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const signIn = (e) => {
    e.preventDefault();
    run(async () => {
      const s = await adminLogin(username.trim(), password);
      if (s.must_change) { setSession(s); setCurrent(password); } else onSignedIn(s);
      setPassword("");
    });
  };
  const change = (e) => {
    e.preventDefault();
    if (next !== again) { setError("The two new passwords are different."); return; }
    if (next.length < MIN) { setError(`Use at least ${MIN} characters.`); return; }
    run(async () => onSignedIn(await adminChangePassword(session.token, current, next)));
  };

  if (session) {
    return (
      <form className="admin-gate" onSubmit={change}>
        <h1 className="section-title">Choose your password</h1>
        <p className="muted">First sign-in for <strong>{session.username}</strong>. Replace the one-time password before using
          the workshop. After this, the one-time password stops working.</p>
        <label htmlFor="admin-current">One-time (current) password</label>
        <input id="admin-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password" required />
        <label htmlFor="admin-new">New password</label>
        <input id="admin-new" type="password" value={next} onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password" minLength={MIN} required aria-describedby="admin-new-rules" />
        <p id="admin-new-rules" className="muted small">At least {MIN} characters, not containing the username, not the
          one-time password. A short sentence works well.</p>
        <label htmlFor="admin-again">New password again</label>
        <input id="admin-again" type="password" value={again} onChange={(e) => setAgain(e.target.value)}
          autoComplete="new-password" required />
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save and continue"}</button>
        {error && <p role="alert">{error}</p>}
      </form>
    );
  }
  return (
    <form className="admin-gate" onSubmit={signIn}>
      <h1 className="section-title">Admin video</h1>
      <p className="muted">Testing only. This page is not linked from the fan view.</p>
      {notice && <p role="status">{notice}</p>}
      <label htmlFor="admin-user">Username</label>
      <input id="admin-user" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
      <label htmlFor="admin-pass">Password</label>
      <input id="admin-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password" required />
      <p className="muted small">First time here? Use the one-time password you were given (the deployment's admin token);
        you will choose your own next.</p>
      <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
