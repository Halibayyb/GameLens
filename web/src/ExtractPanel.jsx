import { useEffect, useMemo, useRef, useState } from "react";
import { highlightVideoUrl } from "./api.js";
import FreezeEditor from "./FreezeEditor.jsx";
import "./extractPanel.css";

// "Extracted data" for the admin video page: what vision (and the scene model) pulled out of the clip.
// Reads the job result as stored; every key is optional, so older jobs and jobs without a scene still render.
const NEAR_S = 1.5;
const SHOT_LABEL = { wide: "wide", close: "close-up", other: "other", replay: "replay" };

const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const num = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? "–" : Number(v).toFixed(d));

// Nearest row by time (rows sorted by `key`).
function nearest(rows, t, key) {
  if (!rows || !rows.length) return null;
  let lo = 0, hi = rows.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((rows[mid][key] ?? 0) < t) lo = mid + 1; else hi = mid;
  }
  const a = rows[Math.max(0, lo - 1)], b = rows[lo];
  return Math.abs((a[key] ?? 0) - t) <= Math.abs((b[key] ?? 0) - t) ? a : b;
}

function shotsOf(result) {
  if (result.debug?.shots?.length) return result.debug.shots;
  const out = new Map();
  for (const f of result.frames || []) {
    const s = f.shot;
    if (s?.id == null) continue;
    const seg = out.get(s.id) || { id: s.id, start: f.match_seconds, end: f.match_seconds, type: s.type };
    seg.end = f.match_seconds;
    out.set(s.id, seg);
  }
  return [...out.values()];
}

function assistsOf(result) {
  const raw = result.assist || result.assists;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.filter(Boolean);
  if (raw.options) return [raw];
  return Object.entries(raw).filter(([, v]) => v && typeof v === "object").map(([k, v]) => ({ ...v, moment_id: k }));
}

function summaryOf(result, shots) {
  const frames = result.frames || [];
  const debug = result.debug || {};
  const n = frames.length || 1;
  const mapped = frames.filter((f) => "mapped" in f);
  const methods = {};
  for (const k of debug.keypoints || []) methods[k.method || "none"] = (methods[k.method || "none"] || 0) + 1;
  const det = debug.detector || {};
  const scene = result.scene;
  const llm = !scene ? "not run" : scene.error ? `error: ${scene.error}` : scene.skipped ? `skipped (${scene.skipped})`
    : `${scene.model || "?"} · ${scene.prompt_version || "?"}`;
  return [
    ["Detector", [det.name || debug.model || "–", det.device || debug.device, det.ms_per_frame ? `${Math.round(det.ms_per_frame)} ms/frame` : null].filter(Boolean).join(" · ")],
    ["Pitch mapping", Object.entries(methods).map(([k, v]) => `${k} ${v}`).join(" · ") || "–"],
    ["Frames mapped", mapped.length ? `${Math.round((100 * mapped.filter((f) => f.mapped).length) / mapped.length)}%` : "not recorded"],
    ["Shots", shots.length ? `${shots.length} (${[...new Set(shots.map((s) => SHOT_LABEL[s.type] || s.type))].join(", ")})` : "not recorded"],
    ["Ball found", `${Math.round((100 * frames.filter((f) => f.ball).length) / n)}% of frames`],
    ["Players / frame", num(frames.reduce((s, f) => s + (f.players?.length || 0), 0) / n)],
    ["Scene model", llm],
  ];
}

export default function ExtractPanel({ result, time, onSeek, token, onAssist }) {
  const [editing, setEditing] = useState(null);
  const shots = useMemo(() => shotsOf(result), [result]);
  const summary = useMemo(() => summaryOf(result, shots), [result, shots]);
  const assists = useMemo(() => assistsOf(result), [result]);
  const frames = result.frames || [];
  const frame = nearest(frames, time, "match_seconds");
  const scene = result.scene && !result.scene.skipped && !result.scene.error ? result.scene : null;
  const near = (arr, key) => (arr || []).filter((e) => Math.abs((e[key] ?? -99) - time) <= NEAR_S);
  const sceneEvents = near(scene?.events, "t");
  const posture = near(scene?.posture, "t");
  const cvEvents = near(result.events, "match_seconds").filter((e) => !["kick_off", "period_end"].includes(e.type));

  const download = () => {
    const keep = ["job_id", "fps", "duration", "teams", "frames", "events", "scene", "scene_hints", "assist", "assists"];
    const body = Object.fromEntries(keep.filter((k) => k in result).map((k) => [k, result[k]]));
    const { detections, keypoints, shots: s, detector, frame_size, clusters, model, device } = result.debug || {};
    body.debug = { detections, keypoints, shots: s, detector, frame_size, clusters, model, device };
    const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${result.job_id || "clip"}-extracted.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <details className="xp">
      <summary className="xp-toggle"><span className="section-title">Extracted data</span>
        <span className="muted small">positions, scene facts and formula inputs for this clip</span></summary>

      <dl className="xp-summary">
        {summary.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>

      <Timeline result={result} frames={frames} shots={shots} events={scene?.events || []} time={time} onSeek={onSeek} />

      <div className="xp-grid">
        <section aria-label="Players in the frame">
          <h3 className="xp-h">Players at {num(frame?.match_seconds, 2)} s
            <span className="muted small"> · shot {frame?.shot?.id ?? "–"} {SHOT_LABEL[frame?.shot?.type] || ""}</span></h3>
          {frame?.mapped === false ? <p className="muted">Not mapped ({SHOT_LABEL[frame?.shot?.type] || "no trustworthy pitch mapping"}), so no positions.</p>
            : frame?.players?.length ? (
              <div className="xp-table-wrap">
                <table className="xp-table">
                  <thead><tr><th>ID</th><th>Team</th><th>Role</th><th>x (m)</th><th>y (m)</th><th>Conf.</th></tr></thead>
                  <tbody>
                    {frame.players.map((p) => (
                      <tr key={p.player_id}><td>{p.player_id}</td><td>{p.team || "–"}</td><td>{p.role || "–"}</td>
                        <td>{num(p.x)}</td><td>{num(p.y)}</td><td>{num(p.confidence, 2)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="muted">No players in this frame.</p>}
          <p className="muted small">Ball: {frame?.ball ? `${num(frame.ball.x)}, ${num(frame.ball.y)} m` : "not found"}</p>
        </section>

        <section aria-label="Scene facts">
          <h3 className="xp-h">Scene facts near {num(time, 1)} s <span className="muted small">± {NEAR_S} s</span></h3>
          {!result.scene && <p className="muted">The scene model has not run on this clip.</p>}
          {result.scene?.skipped && <p className="muted">Scene model skipped: {String(result.scene.skipped)}</p>}
          {result.scene?.error && <p role="alert">Scene model error: {String(result.scene.error)}</p>}
          {scene && (
            <ul className="xp-list">
              {sceneEvents.map((e, i) => (
                <li key={i}>
                  <strong>{e.type}</strong> at {num(e.t, 2)} s · {e.actor_id || "?"}{e.receiver_id ? ` → ${e.receiver_id}` : ""}
                  {e.body_part ? ` · ${e.body_part}` : ""}{e.lofted != null ? ` · ${e.lofted ? "lofted" : "ground"}` : ""}
                  {e.into_space ? " · into space" : ""} · confidence {pct(e.confidence)}
                  {e.evidence && <div className="muted small">“{e.evidence}”</div>}
                </li>
              ))}
              {posture.map((p, i) => (
                <li key={`p${i}`} className="small">Posture {p.player_id} at {num(p.t, 2)} s: {[p.body, p.facing].filter(Boolean).join(", ") || "–"}</li>
              ))}
              {!sceneEvents.length && !posture.length && <li className="muted">Nothing reported near this time.</li>}
            </ul>
          )}
          {!!cvEvents.length && (
            <ul className="xp-list small">
              {cvEvents.map((e, i) => (
                <li key={`c${i}`}>CV {e.type} at {num(e.match_seconds, 2)} s · {e.player_id || "?"}{e.receiver_id ? ` → ${e.receiver_id}` : ""}{e.outcome ? ` · ${e.outcome}` : ""}</li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section aria-label="Pass formulas">
        <h3 className="xp-h">Pass formulas</h3>
        <p className="muted small">PASS, goal chance and EV are estimates; CUT is a count of outfield defenders bypassed.</p>
        {!assists.length && <p className="muted">No pass scored for this clip yet (needs a passer, a receiver and a shot).</p>}
        {assists.map((a, i) => {
          const v = a.verified;
          const shown = v ? { ...a, options: v.options, verdict: v.verdict } : a;
          const frame = v && a.freeze ? { ...a.freeze, boxes: v.players, passer_id: v.passer_id, receiver_id: v.receiver_id } : a.freeze;
          const doubtful = a.freeze?.dense?.mapping === "doubtful";
          return (
          <div key={i} className="xp-assist">
            <p><strong>{shown.verdict?.label || "–"}</strong>{v ? ` · ${v.passer_id} → ${v.receiver_id}` : a.pass ? ` · ${a.pass.passer || a.pass.passer_id} → ${a.pass.receiver || a.pass.receiver_id}` : ""}{" "}
              <span className={v ? "xp-badge ok" : "xp-badge"}>{v ? `Verified · fit ${v.fit_m} m` : "Automatic, not checked"}</span>
              {!v && doubtful && <span className="xp-badge warn">Mapping doubtful: please check</span>}</p>
            {editing === a.moment_id && a.freeze?.landmarks?.length ? (
              <FreezeEditor jobId={result.job_id} momentId={a.moment_id} freeze={a.freeze} src={highlightVideoUrl(result.job_id)}
                token={token} onClose={() => setEditing(null)} onSaved={(next) => { setEditing(null); onAssist?.(next); }} />
            ) : (
              <>
                {frame && result.job_id && <FreezeFrame src={highlightVideoUrl(result.job_id)} freeze={frame} options={shown.options || []} />}
                {a.freeze?.landmarks?.length > 0 && token && (
                  <button type="button" onClick={() => setEditing(a.moment_id)}>{v ? "Correct again" : "Check & correct"}</button>
                )}
              </>
            )}
            <div className="xp-table-wrap">
              <table className="xp-table">
                <thead><tr><th>Option</th><th>PASS (arrives)</th><th>Goal chance</th><th>EV</th><th>CUT</th></tr></thead>
                <tbody>
                  {(shown.options || []).map((o) => (
                    <tr key={o.player_id} className={o.played ? "xp-played" : undefined}>
                      <td>{o.player || o.player_id}{o.played ? " (played)" : ""}</td>
                      <td>{pct(o.arrives)}</td><td>{pct(o.goal)}</td><td>{o.ev == null ? "–" : `${(o.ev * 100).toFixed(1)}%`}</td><td>{o.cut_out ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          );
        })}
        {(result.scene_hints?.passes || []).length > 0 && (
          <ul className="xp-list small">
            {result.scene_hints.passes.map((h, k) => (
              <li key={k}>Scene input, pass {h.actor_id || "?"} → {h.receiver_id}{h.receiver_inferred ? " (receiver inferred from the next shot)" : ""}:{" "}
                {h.aerial != null ? (h.aerial ? "lofted (lane defenders ignored, aerial penalty)" : "ground pass") : "lofted unknown"}
                {h.into_space ? " · into space (scored where the ball arrives)" : ""}{h.p_header != null ? ` · header likelihood ${pct(h.p_header)}` : ""}</li>
            ))}
          </ul>
        )}
      </section>

      <button type="button" onClick={download}>Download JSON</button>
    </details>
  );
}

// The real broadcast frame at the pass with each option's percentages on the player: no reconstruction, so
// nothing has to move. Boxes are in the tracking frame's pixels (freeze.frame_size) and scaled to the video.
function FreezeFrame({ src, freeze, options }) {
  const canvas = useRef(null);
  useEffect(() => {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.src = src;
    const ranked = [...options].sort((a, b) => (b.ev ?? 0) - (a.ev ?? 0));
    const letter = Object.fromEntries(ranked.map((o, i) => [o.player_id, String.fromCharCode(65 + i)]));
    const byId = Object.fromEntries(options.map((o) => [o.player_id, o]));
    const draw = () => {
      const c = canvas.current;
      if (!c || !v.videoWidth) return;
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      const ctx = c.getContext("2d");
      ctx.drawImage(v, 0, 0, c.width, c.height);
      const [fw, fh] = freeze.frame_size || [c.width, c.height];
      const sx = c.width / fw, sy = c.height / fh, lw = Math.max(2, c.width / 480);
      ctx.font = `600 ${Math.round(c.width / 70)}px system-ui, sans-serif`;
      for (const b of freeze.boxes || []) {
        const o = byId[b.player_id];
        const played = b.player_id === freeze.receiver_id;
        const color = b.player_id === freeze.passer_id ? "#e5484d" : played ? "#30a46c" : o ? "#f5a524" : "rgba(255,255,255,0.45)";
        const [x1, y1, x2, y2] = b.xyxy.map((v2, i) => v2 * (i % 2 ? sy : sx));
        ctx.lineWidth = o || played || b.player_id === freeze.passer_id ? lw : lw / 2;
        ctx.strokeStyle = color;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
        const label = b.player_id === freeze.passer_id ? "passer" : o ? `${letter[b.player_id]} · ${pct(o.arrives)} · goal ${pct(o.goal)}` : null;
        if (label) {
          const w = ctx.measureText(label).width + 8, h = Math.round(c.width / 55);
          ctx.fillStyle = "rgba(0,0,0,0.7)";
          ctx.fillRect(x1, y1 - h - 2, w, h);
          ctx.fillStyle = color;
          ctx.fillText(label, x1 + 4, y1 - 6);
        }
      }
      if (freeze.ball) {
        const [x1, y1, x2, y2] = freeze.ball.map((v2, i) => v2 * (i % 2 ? sy : sx));
        ctx.strokeStyle = "#ffe066";
        ctx.lineWidth = lw;
        ctx.beginPath();
        ctx.arc((x1 + x2) / 2, (y1 + y2) / 2, Math.max(6, x2 - x1), 0, 2 * Math.PI);
        ctx.stroke();
      }
    };
    const seek = () => { v.currentTime = Math.max(0, freeze.t); };
    v.addEventListener("loadedmetadata", seek, { once: true });
    v.addEventListener("seeked", draw);
    return () => { v.removeEventListener("seeked", draw); v.removeAttribute("src"); v.load(); };
  }, [src, freeze, options]);
  return (
    <figure className="xp-freeze">
      <canvas ref={canvas} aria-label={`Frame at the pass, ${freeze.t.toFixed(1)} s, with each option's pass and goal chance`} />
      <figcaption className="muted small">At the pass ({freeze.t.toFixed(1)} s): passer red, played option green, other options amber with PASS · goal chance (estimates).
        {freeze.dense?.added ? ` ${freeze.dense.added} more players found at full resolution.` : ""}
        {freeze.dense?.mapping === "doubtful" ? " Pitch mapping doubtful on this frame: positions, and so the percentages, may be off." : ""}</figcaption>
    </figure>
  );
}

function Timeline({ result, frames, shots, events, time, onSeek }) {
  const t0 = Math.min(0, frames[0]?.match_seconds ?? 0);
  const t1 = Math.max(result.duration || 0, frames[frames.length - 1]?.match_seconds || 0, 0.1);
  const x = (s) => `${(((s ?? 0) - t0) / (t1 - t0)) * 100}%`;
  const seek = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    onSeek?.(t0 + Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)) * (t1 - t0));
  };
  return (
    <div className="xp-timeline">
      <div className="xp-track" role="slider" tabIndex={0} aria-label="Clip time" aria-valuemin={t0} aria-valuemax={t1} aria-valuenow={time}
           onClick={seek} onKeyDown={(e) => {
             if (e.key === "ArrowLeft") onSeek?.(Math.max(t0, time - 0.2));
             if (e.key === "ArrowRight") onSeek?.(Math.min(t1, time + 0.2));
           }}>
        {shots.map((s) => (
          <span key={s.id} className={`xp-seg xp-shot-${s.type || "other"}`} title={`Shot ${s.id}: ${SHOT_LABEL[s.type] || s.type}`}
                style={{ left: x(s.start), width: `calc(${x(s.end)} - ${x(s.start)} + 3px)` }} />
        ))}
        {frames.map((f, i) => f.mapped === false ? <span key={i} className="xp-unmapped" style={{ left: x(f.match_seconds) }} /> : null)}
        {events.map((e, i) => <span key={i} className="xp-mark" style={{ left: x(e.t) }} title={`${e.type}${e.evidence ? `: ${e.evidence}` : ""}`} />)}
        <span className="xp-cursor" style={{ left: x(time) }} />
      </div>
      <p className="muted small xp-legend">
        <i className="xp-sw xp-shot-wide" /> wide <i className="xp-sw xp-shot-close" /> close-up <i className="xp-sw xp-shot-other" /> other
        <i className="xp-sw xp-unmapped-sw" /> not mapped <i className="xp-sw xp-mark-sw" /> scene event · click to seek
      </p>
    </div>
  );
}
