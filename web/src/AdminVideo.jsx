import { useEffect, useMemo, useRef, useState } from "react";
import {
  createAdminVideoJob,
  getAdminStatus,
  getAdminVideo,
  getAdminVideoJob,
  getAdminVideoResult,
  highlightSocketUrl,
  highlightVideoUrl,
  publishAdminVideo,
  adminMe,
  SessionEnded,
} from "./api.js";
import AdminLogin from "./AdminLogin.jsx";
import BroadcastPitch from "./components/BroadcastPitch.jsx";
import ExtractPanel from "./ExtractPanel.jsx";
import PitchFrame, { frameAt } from "./components/PitchFrame.jsx";
import "./components/highlights.css";
import "./adminVideo.css";


// The signed-in session lives in this tab only. A ?token= link from the old shared-token gate is dropped from the
// address bar and not used: the page now signs in with a username and password.
const TOKEN_KEY = "gamelens.adminSession";
const loadToken = () => {
  const url = new URL(window.location.href);
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
  }
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
};
const storeToken = (token) => {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token); else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode: the session lasts until the page closes */ }
};

// What tracks an upload, as the API reports it (the vision service when VISION_URL is set).
function visionLine(info) {
  const v = info?.vision;
  if (!v) return `Device: ${info?.device || "cpu"} · model ${info?.cpu_model || "yolo11n"}.`;
  const where = v.where === "service" ? "Vision service" : "Local vision";
  if (v.status === "starting") return `${where}: waking up (it scales to zero), ready in about a minute.`;
  if (v.status !== "ok") return `${where}: ${v.status}.`;
  const detector = v.detector === "football"
    ? "football detector (players, goalkeepers, referees, ball)"
    : `COCO detector (${v.coco_model || "yolo11n.pt"})`;
  const pitch = v.pitch_model ? " + pitch keypoints" : "";
  const scene = v.scene?.model ? ` · scene model ${v.scene.model}` : " · scene model off";
  return `${where}: ${v.device || "cpu"} · ${detector}${pitch}${scene}.`;
}

export default function AdminVideo() {
  const [enabled, setEnabled] = useState(null);
  const [token, setToken] = useState(loadToken);
  const [pending, setPending] = useState(null);   // signed in with the one-time password: must change it
  const [notice, setNotice] = useState(null);
  const [info, setInfo] = useState(null);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState(null);
  const [overlay, setOverlay] = useState({ detections: true, keypoints: true, clusters: true });
  const [time, setTime] = useState(0);
  const fileRef = useRef(null);
  const canvasRef = useRef(null);
  const videoRef = useRef(null);
  // a call that finds the session over sends the person back to sign in; anything else is shown on the page
  const fail = (e) => (e instanceof SessionEnded ? signOut(e.message) : setError(e.message));

  useEffect(() => {
    if (!enabled || !token) return;
    adminMe(token).then((me) => {
      if (me.must_change) { setPending({ ...me, token }); storeToken(""); setToken(""); }
    }).catch(fail);
  }, [enabled, token]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    getAdminStatus().then((body) => setEnabled(!!body.enabled)).catch(() => setEnabled(false));
  }, []);

  useEffect(() => {
    if (!enabled || !token) return;
    getAdminVideo(token).then(setInfo).catch(fail);
  }, [enabled, token]);

  useEffect(() => {
    if (!job?.job_id || !token) return;
    if (job.status === "done") {
      getAdminVideoResult(job.job_id, token).then((body) => { setResult(body); setTime(0); }).catch(fail);
      return;
    }
    if (job.status === "error") return;
    let ws, done = false;
    try { ws = new WebSocket(highlightSocketUrl(job.job_id)); } catch { /* poll */ }
    if (ws) {
      ws.onmessage = (message) => {
        try { setJob((current) => ({ ...(current || {}), ...JSON.parse(message.data) })); } catch { /* skip */ }
      };
    }
    const poll = setInterval(() => {
      getAdminVideoJob(job.job_id, token).then((next) => {
        if (done) return;
        setJob(next);
        if (next.status === "done" || next.status === "error") clearInterval(poll);
      }).catch(() => {});
    }, 800);
    return () => { done = true; clearInterval(poll); try { ws?.close(); } catch { /* closed */ } };
  }, [job?.job_id, job?.status, token]);

  const signedIn = (session) => {
    storeToken(session.token);
    setPending(null);
    setNotice(null);
    setError(null);
    setToken(session.token);
  };
  const signOut = (message = null) => {
    storeToken("");
    setToken("");
    setInfo(null);
    setJob(null);
    setResult(null);
    setPending(null);
    setNotice(message);
  };

  const start = async (payload) => {
    setBusy(true);
    setError(null);
    setResult(null);
    setPublished(null);
    try {
      const created = await createAdminVideoJob({ token, ...payload });
      setJob(created);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!job?.job_id) return;
    setBusy(true);
    setError(null);
    try {
      const body = await publishAdminVideo(job.job_id, token);
      setPublished(body);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const view = result?.pitch_view?.views?.[0];
  const frames = result?.frames || [];
  const duration = result?.duration || 0;
  const frame = useMemo(() => frameAt(frames, time), [frames, time]);
  const debug = result?.debug || {};

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frame) return;
    drawDebug(canvas, frame, debug, overlay, time, result?.teams);
  }, [frame, debug, overlay, time, result?.teams]);

  if (enabled === null) return <main className="page admin-page"><p>Checking admin access…</p></main>;
  if (!enabled) {
    return (
      <main className="page admin-page">
        <p role="alert">Admin video is off on this instance. Set ADMIN_VIDEO=1 and ADMIN_TOKEN on a private machine.</p>
      </main>
    );
  }
  if (!token) {
    return (
      <main className="page admin-page">
        <AdminLogin key={pending?.token || "signin"} pending={pending} notice={notice} onSignedIn={signedIn} />
      </main>
    );
  }

  return (
    <main className="page admin-page">
      <header className="admin-top">
        <div>
          <p className="muted small">Admin / testing</p>
          <h1 className="section-title">Video → pitch view</h1>
        </div>
        <div className="admin-top-links">
          <a className="muted small" href="/">Back to match view</a>
          <button type="button" className="btn-link small" onClick={() => signOut("Signed out.")}>Sign out</button>
        </div>
      </header>
      <p className="muted small">
        Upload a short clip you have the rights to, or run the authored demo.
        The pipeline tracks players and the ball, maps them onto the pitch, and
        builds the same Broadcast / Analyst / Live replay the fan page already draws.
        Positions come from vision, never from the language model.
      </p>

      <form className="hl-form" onSubmit={(e) => { e.preventDefault(); const file = fileRef.current?.files?.[0]; start(file ? { file, source: "upload" } : { source: "demo" }); }}>
        <div className="hl-field">
          <label htmlFor="admin-file">Clip (mp4, webm, mov)</label>
          <input id="admin-file" ref={fileRef} type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov,.m4v" />
        </div>
        <div className="hl-actions">
          <button type="submit" className="btn-primary" disabled={busy}>Upload and process</button>
          <button type="button" disabled={busy} onClick={() => start({ source: "demo" })}>Use demo clip</button>
        </div>
      </form>
      <p className="muted small">{visionLine(info)} Demo clip is schematic and needs no vision service.</p>

      {error && <p role="alert">{error}</p>}

      {job && job.status !== "done" && (
        <div className="hl-progress" role="status">
          <div className="hl-bar" aria-hidden="true"><span style={{ width: `${job.progress || 0}%` }} /></div>
          <p className="muted small">{job.message || "Working…"} ({job.progress || 0}%) · {job.step}</p>
        </div>
      )}

      {result && (
        <div className="admin-grid">
          <section className="admin-debug" aria-label="Pipeline debug">
            <div className="section-head">
              <h2 className="section-title">Debug overlays</h2>
              <span className="badge">{result.badge}{result.confidence != null ? ` · ${Math.round(result.confidence * 100)}%` : ""}</span>
            </div>
            <div className="hl-stage">
              {result.has_video ? (
                <video ref={videoRef} src={highlightVideoUrl(result.job_id)} controls playsInline onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} />
              ) : (
                <canvas ref={canvasRef} width="960" height="540" aria-label="Detection and homography overlay" />
              )}
            </div>
            {!result.has_video && (
              <input type="range" min={0} max={duration || 0} step={0.05} value={Math.min(time, duration || 0)}
                     aria-label="Debug clock" onChange={(e) => setTime(Number(e.target.value))} />
            )}
            <div className="admin-toggles" role="group" aria-label="Overlays">
              {[["detections", "Detections"], ["keypoints", "Homography keypoints"], ["clusters", "Team clustering"]].map(([key, label]) => (
                <label key={key}>
                  <input type="checkbox" checked={overlay[key]} onChange={(e) => setOverlay((o) => ({ ...o, [key]: e.target.checked }))} />
                  {label}
                </label>
              ))}
            </div>
            <ol className="admin-steps">
              {(result.steps || []).map((step) => (
                <li key={step.name}><span className={step.ok ? "va-ok" : "va-err"}>{step.ok ? "✓" : "!"}</span> {step.name}</li>
              ))}
            </ol>
            {debug.clusters && (
              <p className="muted small">
                Clusters · home rgb({(debug.clusters.home || []).join(", ")}) ·
                away rgb({(debug.clusters.away || []).join(", ")}) ·
                referee rgb({(debug.clusters.referee || []).join(", ")})
              </p>
            )}
          </section>

          <section aria-label="Reconstructed pitch view">
            <div className="section-head">
              <h2 className="section-title">Where it happened</h2>
              <span className="badge">{view?.badge || result.badge}</span>
            </div>
            {view?.track ? (
              <BroadcastPitch
                moment={view.moment}
                track={view.track}
                assist={view.assist || null}
                shot={view.shot || null}
                teams={view.teams || result.teams}
              />
            ) : (
              <PitchFrame frame={frame} teams={result.teams} />
            )}
            <div className="hl-actions" style={{ marginTop: 16 }}>
              <button type="button" className="btn-primary" disabled={busy || !view} onClick={publish}>
                Publish to match view
              </button>
              {published && (
                <a href="/">Opened in the fan moment list ({published.published?.length || 0})</a>
              )}
            </div>
            {published && <p className="muted small">Published. Open the match view and pick the new moment in the strip.</p>}
          </section>
        </div>
      )}

      {result && (
        <ExtractPanel result={result} time={time} token={token} onAssist={(next) => setResult((r) => ({
          ...r, assist: (r.assist || []).map((a) => (a.moment_id === next.moment_id ? next : a)),
        }))} onSeek={(t) => {
          if (videoRef.current) videoRef.current.currentTime = t;
          setTime(t);
        }} />
      )}
    </main>
  );
}

function drawDebug(canvas, frame, debug, overlay, time, teams) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = "#f4f4ef";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#3b4250";
  ctx.strokeRect(40, 30, w - 80, h - 60);
  ctx.beginPath();
  ctx.moveTo(w / 2, 30);
  ctx.lineTo(w / 2, h - 30);
  ctx.stroke();
  const project = (x, y) => [40 + (x / 105) * (w - 80), 30 + (1 - y / 68) * (h - 60)];
  if (overlay.keypoints) {
    const row = (debug.keypoints || []).reduce((best, item) => (
      Math.abs(item.t - time) < Math.abs((best?.t ?? 1e9) - time) ? item : best
    ), null);
    if (row?.image_points?.length === 4) {
      ctx.strokeStyle = "#2563eb";
      ctx.beginPath();
      row.image_points.forEach(([x, y], i) => {
        const px = 40 + (x / 240) * (w - 80);
        const py = 30 + (y / 160) * (h - 60);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.closePath();
      ctx.stroke();
    }
  }
  for (const player of frame.players || []) {
    const [x, y] = project(player.x, player.y);
    ctx.fillStyle = player.team === "home" ? "#2b55d0" : player.team === "away" ? "#e07a1f" : "#c4a000";
    ctx.beginPath();
    ctx.arc(x, y, overlay.detections ? 8 : 6, 0, Math.PI * 2);
    ctx.fill();
    if (overlay.detections && player.confidence != null) {
      ctx.fillStyle = "#1f2430";
      ctx.font = "11px sans-serif";
      ctx.fillText(`${Math.round(player.confidence * 100)}%`, x + 8, y);
    }
  }
  if (frame.ball) {
    const [x, y] = project(frame.ball.x, frame.ball.y);
    ctx.fillStyle = "#111";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
  }
  if (overlay.clusters && teams) {
    ctx.font = "13px sans-serif";
    ctx.fillStyle = "#2b55d0";
    ctx.fillText(teams.home || "Home", 48, 24);
    ctx.fillStyle = "#e07a1f";
    ctx.fillText(teams.away || "Away", w - 200, 24);
  }
}
