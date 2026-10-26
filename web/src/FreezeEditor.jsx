import { useEffect, useMemo, useRef, useState } from "react";
import { verifyFreeze } from "./api.js";
import { PITCH_LINES, apply, fitError, invert3, solveImageToWorld } from "./homography.js";

// Check & correct one pass frame: drag pitch landmarks onto the painted pitch (exact mapping), fix teams, pick
// passer and receiver, set the pass flags. The API recomputes positions and the formulas from it (verified).
// Coordinates are in the tracking frame's pixels (freeze.frame_size); the canvas shows the full-size video frame.

const L = 105, W = 68;
const TEAM_CYCLE = ["home", "away", "ref", "ignore"];
const TEAM_COLOR = { home: "#4dabf7", away: "#f5a524", ref: "#e9ecef", ignore: "rgba(255,255,255,0.25)" };

// Painted lines in metres (as vision/pitch_keypoints.LINES), plus the centre circle.
const LINES = (() => {
  const pen = [16.5, 40.32], box = [5.5, 18.32];
  const seg = [[[0, 0], [L, 0]], [[0, W], [L, W]], [[0, 0], [0, W]], [[L, 0], [L, W]], [[L / 2, 0], [L / 2, W]]];
  for (const [d, w] of [pen, box]) {
    const lo = (W - w) / 2, hi = (W + w) / 2;
    seg.push([[d, lo], [d, hi]], [[0, lo], [d, lo]], [[0, hi], [d, hi]],
      [[L - d, lo], [L - d, hi]], [[L, lo], [L - d, lo]], [[L, hi], [L - d, hi]]);
  }
  for (let i = 0; i < 48; i++) {
    const a = (i / 48) * 2 * Math.PI, b = ((i + 1) / 48) * 2 * Math.PI;
    seg.push([[L / 2 + 9.15 * Math.cos(a), W / 2 + 9.15 * Math.sin(a)], [L / 2 + 9.15 * Math.cos(b), W / 2 + 9.15 * Math.sin(b)]]);
  }
  return seg;
})();

export default function FreezeEditor({ jobId, momentId, freeze, src, token, onSaved, onClose }) {
  const canvas = useRef(null);
  const video = useRef(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState("pitch");
  const [marks, setMarks] = useState(() => (freeze.landmarks || []).filter((l) => l.image).map((l) => ({ ...l })));
  const [placing, setPlacing] = useState("");
  const [onLines, setOnLines] = useState([]);   // [{line, image}] points clicked on a painted line
  const [lining, setLining] = useState("");
  const [teams, setTeams] = useState({});
  const [added, setAdded] = useState([]);
  const [pick, setPick] = useState(null);   // "passer" | "receiver" | "add"
  const [passer, setPasser] = useState(freeze.passer_id);
  const [receiver, setReceiver] = useState(freeze.receiver_id);
  const [lofted, setLofted] = useState(!!freeze.lofted);
  const [intoSpace, setIntoSpace] = useState(!!freeze.into_space);
  const [attackRight, setAttackRight] = useState(freeze.attack_right ?? true);
  const [addTeam, setAddTeam] = useState("home");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const drag = useRef(null);
  const [, redraw] = useState(0);
  const [fw, fh] = freeze.frame_size || [960, 540];

  const linePts = useMemo(() => onLines.map((p) => ({ image: p.image, line: PITCH_LINES[p.line][1] })), [onLines]);
  const i2w = useMemo(() => solveImageToWorld(marks, linePts), [marks, linePts]);
  const w2i = useMemo(() => (i2w ? invert3(i2w) : null), [i2w]);
  const fit = useMemo(() => (i2w ? fitError(i2w, marks, linePts) : null), [i2w, marks, linePts]);
  const boxes = useMemo(() => [...(freeze.boxes || []), ...added], [freeze.boxes, added]);

  useEffect(() => {   // the full-size frame at the pass, kept in a hidden video element
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.src = src;
    v.addEventListener("loadedmetadata", () => { v.currentTime = Math.max(0, freeze.t); }, { once: true });
    v.addEventListener("seeked", () => setReady(true), { once: true });
    video.current = v;
    return () => { v.removeAttribute("src"); v.load(); };
  }, [src, freeze.t]);

  useEffect(() => {
    const c = canvas.current, v = video.current;
    if (!c || !v || !ready) return;
    c.width = v.videoWidth; c.height = v.videoHeight;
    const s = c.width / fw, ctx = c.getContext("2d"), lw = Math.max(2, c.width / 600);
    ctx.drawImage(v, 0, 0, c.width, c.height);
    if (w2i) {   // the pitch as the current landmarks map it
      ctx.strokeStyle = "rgba(255, 60, 60, 0.85)"; ctx.lineWidth = lw;
      for (const [[ax, ay], [bx, by]] of LINES) {
        const a = apply(w2i, ax, ay), b = apply(w2i, bx, by);
        if (a[2] <= 0 || b[2] <= 0) continue;
        ctx.beginPath(); ctx.moveTo(a[0] * s, a[1] * s); ctx.lineTo(b[0] * s, b[1] * s); ctx.stroke();
      }
    }
    ctx.font = `600 ${Math.round(c.width / 80)}px system-ui, sans-serif`;
    for (const b of boxes) {
      const team = teams[b.player_id] || b.team;
      const [x1, y1, x2, y2] = b.xyxy.map((v2) => v2 * s);
      ctx.strokeStyle = b.player_id === passer ? "#e5484d" : b.player_id === receiver ? "#30a46c" : TEAM_COLOR[team] || "#fff";
      ctx.lineWidth = b.player_id === passer || b.player_id === receiver ? lw * 1.8 : lw;
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      if (tab === "players") {
        const label = b.player_id === passer ? "passer" : b.player_id === receiver ? "receiver" : team;
        ctx.fillStyle = "rgba(0,0,0,0.65)"; ctx.fillRect(x1, y1 - c.width / 70, ctx.measureText(label).width + 6, c.width / 75);
        ctx.fillStyle = ctx.strokeStyle; ctx.fillText(label, x1 + 3, y1 - 4);
      }
    }
    if (tab === "pitch") {
      for (const m of marks) {
        const [x, y] = m.image.map((v2) => v2 * s);
        ctx.fillStyle = "#ffe066"; ctx.strokeStyle = "#000"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, c.width / 160, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        ctx.fillStyle = "#000"; ctx.fillText(String(m.id), x + c.width / 140 + 1, y + 1);
        ctx.fillStyle = "#ffe066"; ctx.fillText(String(m.id), x + c.width / 140, y);
      }
      for (const p of onLines) {
        const [x, y] = p.image.map((v2) => v2 * s);
        ctx.fillStyle = "#3bc9db"; ctx.strokeStyle = "#000"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, c.width / 200, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        ctx.fillText(PITCH_LINES[p.line][0], x + c.width / 160, y - 4);
      }
    }
    if (drag.current?.rect) {
      const { x0, y0, x1, y1 } = drag.current.rect;
      ctx.setLineDash([6, 4]); ctx.strokeStyle = TEAM_COLOR[addTeam]; ctx.strokeRect(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s); ctx.setLineDash([]);
    }
  });

  const toFrame = (e) => {   // pointer -> tracking-frame pixels
    const r = canvas.current.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * fw, ((e.clientY - r.top) / r.height) * fh];
  };
  const boxAt = ([x, y]) => boxes.find((b) => x >= b.xyxy[0] && x <= b.xyxy[2] && y >= b.xyxy[1] && y <= b.xyxy[3]);
  const onDown = (e) => {
    const p = toFrame(e);
    if (tab === "pitch") {
      if (lining) {
        setOnLines((ls) => [...ls, { line: lining, image: p }]);
        return;   // keep clicking along the same line; pick "done" to stop
      }
      if (placing) {
        const lm = freeze.landmarks.find((l) => String(l.id) === placing);
        setMarks((ms) => [...ms.filter((m) => m.id !== lm.id), { ...lm, image: p }]);
        setPlacing("");
        return;
      }
      const near = marks.reduce((best, m) => {
        const d = Math.hypot(m.image[0] - p[0], m.image[1] - p[1]);
        return d < (best?.d ?? fw / 60) ? { m, d } : best;
      }, null);
      if (near) drag.current = { id: near.m.id };
      return;
    }
    if (tab === "players") {
      if (pick === "add") { drag.current = { rect: { x0: p[0], y0: p[1], x1: p[0], y1: p[1] } }; return; }
      const b = boxAt(p);
      if (!b) return;
      if (pick === "passer") { setPasser(b.player_id); setPick(null); return; }
      if (pick === "receiver") { setReceiver(b.player_id); setPick(null); return; }
      const now = teams[b.player_id] || b.team;
      setTeams((t) => ({ ...t, [b.player_id]: TEAM_CYCLE[(TEAM_CYCLE.indexOf(now) + 1) % TEAM_CYCLE.length] }));
    }
  };
  const onMove = (e) => {
    if (!drag.current) return;
    const p = toFrame(e);
    if (drag.current.id != null) setMarks((ms) => ms.map((m) => (m.id === drag.current.id ? { ...m, image: p } : m)));
    else if (drag.current.rect) { drag.current.rect.x1 = p[0]; drag.current.rect.y1 = p[1]; redraw((n) => n + 1); }
  };
  const onUp = () => {
    const r = drag.current?.rect;
    if (r && Math.abs(r.x1 - r.x0) > 4 && Math.abs(r.y1 - r.y0) > 8) {
      const n = added.length + 1;
      setAdded((a) => [...a, { player_id: `new${n}`, team: addTeam, role: "OUT", manual: true,
        xyxy: [Math.min(r.x0, r.x1), Math.min(r.y0, r.y1), Math.max(r.x0, r.x1), Math.max(r.y0, r.y1)] }]);
      setPick(null);
    }
    drag.current = null;
  };

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const body = {
        landmarks: marks.map((m) => ({ id: m.id, image: m.image })),
        line_points: onLines,
        players: Object.entries(teams).filter(([id]) => !id.startsWith("new")).map(([player_id, team]) => ({ player_id, team })),
        add: added.map((a) => ({ xyxy: a.xyxy, team: teams[a.player_id] || a.team, role: a.role })),
        passer_id: passer, receiver_id: receiver, lofted, into_space: intoSpace, attack_right: attackRight,
      };
      onSaved(await verifyFreeze(jobId, momentId, body, token));
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const missing = (freeze.landmarks || []).filter((l) => !marks.some((m) => m.id === l.id));
  return (
    <div className="xp-editor">
      <div className="xp-tabs" role="tablist">
        {[["pitch", "1 · Pitch"], ["players", "2 · Players"], ["pass", "3 · Pass"]].map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <canvas ref={canvas} className={`xp-edit-canvas ${tab}`} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp}
        aria-label="Pass frame: drag the yellow landmarks onto the painted pitch, or click players to fix their team" />
      {tab === "pitch" && (
        <div className="small">
          <p>Drag the yellow points onto where those markings really are; the red lines show the pitch they imply. Remove
            points you cannot see. Midfield frames rarely show four markings: add points on a painted line (the touchline,
            the halfway line) instead. Worst miss: <strong>{fit == null ? "needs 4 points, or fewer plus points on lines" : `${fit.toFixed(2)} m`}</strong></p>
          <label>Points on a line <select value={lining} onChange={(e) => setLining(e.target.value)}>
            <option value="">choose a line, then click along it…</option>
            {Object.entries(PITCH_LINES).map(([k, [name]]) => <option key={k} value={k}>{name}</option>)}
          </select></label>
          {lining && <button type="button" onClick={() => setLining("")}>Done with this line</button>}
          {onLines.length > 0 && (
            <ul className="xp-marks">
              {onLines.map((p, i) => (
                <li key={i}>{PITCH_LINES[p.line][0]} <button type="button" aria-label={`Remove line point ${i + 1}`} onClick={() => setOnLines((ls) => ls.filter((_, j) => j !== i))}>×</button></li>
              ))}
            </ul>
          )}
          <label>Add a landmark <select value={placing} onChange={(e) => setPlacing(e.target.value)}>
            <option value="">choose, then click it on the frame…</option>
            {missing.map((l) => <option key={l.id} value={l.id}>{l.id} · {l.name}</option>)}
          </select></label>
          <ul className="xp-marks">
            {marks.map((m) => (
              <li key={m.id}>{m.id} · {m.name} <button type="button" aria-label={`Remove landmark ${m.id}`} onClick={() => setMarks((ms) => ms.filter((x) => x.id !== m.id))}>×</button></li>
            ))}
          </ul>
        </div>
      )}
      {tab === "players" && (
        <div className="small">
          <p>Click a player to cycle home → away → referee → ignore. Passer <strong>{passer}</strong>, receiver <strong>{receiver}</strong>.</p>
          <button type="button" onClick={() => setPick("passer")} aria-pressed={pick === "passer"}>Set passer</button>{" "}
          <button type="button" onClick={() => setPick("receiver")} aria-pressed={pick === "receiver"}>Set receiver</button>{" "}
          <button type="button" onClick={() => setPick("add")} aria-pressed={pick === "add"}>Draw a missed player</button>{" "}
          <select value={addTeam} onChange={(e) => setAddTeam(e.target.value)} aria-label="Team of the drawn player">
            <option value="home">home</option><option value="away">away</option><option value="ref">referee</option>
          </select>
          {pick && <p className="muted">{pick === "add" ? "Drag a box around the player." : `Click the ${pick}.`}</p>}
        </div>
      )}
      {tab === "pass" && (
        <div className="small xp-pass-flags">
          <label><input type="checkbox" checked={lofted} onChange={(e) => setLofted(e.target.checked)} /> Lofted (in the air: lane defenders ignored, aerial penalty)</label>
          <label><input type="checkbox" checked={intoSpace} onChange={(e) => setIntoSpace(e.target.checked)} /> Into space (scored where the ball arrives)</label>
          <label><input type="checkbox" checked={attackRight} onChange={(e) => setAttackRight(e.target.checked)} /> Passer's team attacks the x = 105 goal</label>
        </div>
      )}
      {error && <p className="xp-error" role="alert">{error}</p>}
      <div>
        <button type="button" onClick={save} disabled={saving || !i2w}>{saving ? "Saving…" : "Save verified frame"}</button>{" "}
        <button type="button" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}
