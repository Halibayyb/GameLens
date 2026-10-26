import { useEffect, useMemo, useRef, useState } from "react";
import { ballAt, ballPath, easeBall, easeInOut, passPoint, receptionTau, resolveShot, sampleTrack } from "../motion.js";
import { inferAttackRight } from "../broadcast/projection.js";
import { POS_ORDER } from "../format.js";

const GOLD = "var(--pass)", CASING = "var(--pass-casing)", TINT = "#e3e7f0", SEL = "#2563eb", INK = "#1f2430";   // the played pass (ink on light, white on dark) and its casing, its tag, the picked option (focus ring)
const REVEAL_S = 0.6;     // option links grow out from the passer
const TRAIL_S = 1.5;      // how much of the ball's path trails behind it
const NODE_R = 1.9;
const PW = 105, PH = 68;   // world pitch, metres

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const rank = (role, num) => ((POS_ORDER.indexOf(role) + 1) || 99) * 100 + (Number(num) || 0);

// Top-down node map of all 22 players on the replay clock: play up to the pass, freeze, then draw the passer's
// options. Beside it, a roster of both teams by position. Options can be picked on the map or in the roster.
// The ball is the same choreographed ball as the broadcast view (motion.js ballAt: curve, lift, at the feet), drawn on
// its ground path and slightly larger while it is in the air.
//
// Props
//   moment, track, assist, roles, numbers, names, teams   as before (roles / numbers / names are maps by player_id)
//   shot         the API's moment.shot { from, to, tau, s, z, lift } (optional: derived from the tracking without it)
//   selected     a player_id: that option's node gets a focus ring and its link is drawn solid
//   onSelect(id) called with a player_id (or null to clear) when a node or roster row is picked
//   onDone, onReplay               the reveal finished / Replay pressed
// The played pass is the strongest thing on the map (thick, tagged "Played"); the other options are dashed suggestions.
export default function AnalystView({ moment, track, assist, shot: shotProp, roles = {}, numbers = {}, names = {}, teams = {}, selected, onSelect, onDone, onReplay }) {
  const pass = assist?.pass;
  const attackRight = assist?.attack_right ?? inferAttackRight(moment);
  // drawn like the broadcast: first the attack runs left to right (a team attacking left is turned 180 degrees), then an Away
  // moment is mirrored end to end, as in the proto, so Away attacks the left-hand goal
  const flip = moment.team === "away";
  const orient = (p) => { const q = attackRight ? p : [PW - p[0], PH - p[1]]; return flip ? [PW - q[0], q[1]] : q; };
  const t0 = track.t[0];
  const freezeTau = pass ? pass.tau : 0;
  const playS = Math.max(0.1, freezeTau - t0);
  const end = playS + REVEAL_S;

  const [clock, setClock] = useState(reducedMotion() ? end : 0);
  const [paused, setPaused] = useState(false);
  const [run, setRun] = useState(0);
  const [hover, setHover] = useState(null);
  const ref = useRef({ clock: 0, last: 0, paused: false });
  const cb = useRef({});
  cb.current = { onDone, onReplay };   // latest callbacks without restarting the timeline

  useEffect(() => {
    ref.current = { clock: 0, last: performance.now(), paused: ref.current.paused };
    if (reducedMotion()) { setClock(end); cb.current.onDone?.(); return undefined; }
    setClock(0);
    let raf;
    const frame = (now) => {
      const r = ref.current;
      if (!r.paused) r.clock = Math.min(end, r.clock + (now - r.last) / 1000);
      r.last = now;
      setClock(r.clock);
      if (r.clock < end) raf = requestAnimationFrame(frame); else cb.current.onDone?.();   // reveal complete
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [moment.id, run, end]);

  const frozen = clock >= playS;
  const tau = frozen ? freezeTau : Math.min(freezeTau, t0 + clock);
  const shot = useMemo(() => resolveShot(shotProp, track, assist), [shotProp, track, assist]);
  const ctx = useMemo(() => ({ track, assist, shot }), [track, assist, shot]);   // what ballAt needs
  const reveal = Math.max(0, Math.min(1, (clock - playS) / REVEAL_S));

  const optionIds = useMemo(() => new Set((assist?.options || []).map((o) => o.player_id)), [assist]);
  const players = Object.entries(track.objects).filter(([id]) => id !== "ball");
  const label = (id, o) => {
    const opt = assist?.options?.find((x) => x.player_id === id);
    const name = opt?.player || names[id] || (id === pass?.passer_id ? pass.passer : id === pass?.receiver_id ? pass.receiver : null);
    const side = teams[o.team] || o.team;
    return `${name ? `${name}, ` : ""}shirt ${numbers[id] ?? "?"}${roles[id] ? `, ${roles[id]}` : ""}, ${side}${opt ? (opt.played ? ", the pass played" : ", an option") : ""}`;
  };

  const ball = ballAt(tau, ctx);
  const trail = ballPath(ctx, Math.max(t0, tau - TRAIL_S), tau).map((p) => orient(p).map((n) => n.toFixed(2)).join(","));
  // the played pass follows the ball's path from the passer to where it was received, like the broadcast arrow
  const played = pass ? ballPath(ctx, pass.tau, receptionTau(assist)).map(orient) : [];
  const receiver = pass && sampleTrack(track, pass.receiver_id, freezeTau);

  const start = pass ? orient(pass.start) : null;
  const options = [...(assist?.options || [])].sort((a, b) => (a.player_id === selected) - (b.player_id === selected) || (a.played) - (b.played));

  const roster = ["home", "away"].map((side) => ({
    side,
    items: players.filter(([, o]) => o.team === side)
      .map(([id]) => ({ id, role: roles[id], num: numbers[id], opt: assist?.options?.find((x) => x.player_id === id) }))
      .sort((a, b) => rank(a.role, a.num) - rank(b.role, b.num)),
  }));

  if (!pass) {
    return <p className="muted">The analyst map needs a pass to show; this moment does not have one.</p>;
  }

  return (
    <div className="analyst">
      <svg viewBox="-2 -2 109 72" className="pitch" role="group"
           aria-label={`Analyst map of ${moment.title}: all players${frozen ? `, frozen at the pass with ${options.length} options drawn from ${pass.passer}` : " moving up to the pass"}`}>
        <rect x="-2" y="-2" width="109" height="72" rx="2" className="grass" />
        <g className="lines">
          <rect x="0" y="0" width="105" height="68" />
          <line x1="52.5" y1="0" x2="52.5" y2="68" />
          <circle cx="52.5" cy="34" r="9.15" />
          <rect x="0" y="13.84" width="16.5" height="40.32" />
          <rect x="88.5" y="13.84" width="16.5" height="40.32" />
          <rect x="0" y="24.84" width="5.5" height="18.32" />
          <rect x="99.5" y="24.84" width="5.5" height="18.32" />
        </g>
        {trail.length > 1 && <polyline points={trail.join(" ")} className="trail" />}
        {frozen && reveal > 0 && options.map((o) => {
          const e = easeBall(reveal);
          const cls = `link ${o.played ? "played" : ""} ${o.player_id === selected ? "on" : ""}`;
          if (o.played && played.length > 1) {   // the real ball path, grown with the reveal
            const part = played.slice(0, Math.max(2, Math.ceil(played.length * e)));
            const tip = part[part.length - 1], sel = o.player_id === selected;
            const pts = part.map((q) => q.join(",")).join(" ");
            return (   // the played pass: gold on a dark casing, thick
              <g key={o.player_id} className={cls} aria-hidden="true">
                <polyline points={pts} fill="none" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: CASING, strokeWidth: 1.7 }} />
                <polyline points={pts} fill="none" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: GOLD, strokeWidth: 1.1 }} />
                <circle cx={tip[0]} cy={tip[1]} r="1.1" style={{ fill: GOLD, stroke: CASING, strokeWidth: 0.3 }} />
              </g>
            );
          }
          const tip = passPoint(start, orient(o.location), 0, 0, e).p;
          const sel = o.player_id === selected;
          return (   // a suggestion: a quiet dashed link, solid in the focus colour when picked
            <g key={o.player_id} className={cls} aria-hidden="true">
              <line x1={start[0]} y1={start[1]} x2={tip[0]} y2={tip[1]}
                    style={sel ? { stroke: SEL, strokeWidth: 0.7, strokeDasharray: "none", strokeOpacity: 1 } : { strokeOpacity: 0.6 }} />
              <circle cx={tip[0]} cy={tip[1]} r={sel ? 0.9 : 0.7} style={sel ? { fill: SEL } : { fillOpacity: 0.6 }} />
            </g>
          );
        })}
        {players.map(([id, o]) => {
          const raw = sampleTrack(track, id, tau);
          if (!raw) return null;
          const p = orient(raw);
          const isOpt = optionIds.has(id);
          const cls = `node ${o.team} ${id === pass.passer_id ? "passer" : ""} ${id === selected ? "on" : ""} ${hover === id ? "hover" : ""}`;
          const isPlayed = id === pass.receiver_id && frozen;
          const body = (
            <>
              {id === selected && frozen && <circle r={NODE_R + 1.1} fill="none" style={{ stroke: SEL, strokeWidth: 0.6 }} />}
              <circle r={NODE_R} className="node-ring" style={isPlayed ? { stroke: GOLD, strokeWidth: 0.9 } : id === selected ? { stroke: SEL, strokeWidth: 0.5 } : undefined} />
              <text y="0.7" textAnchor="middle" className="node-num">{numbers[id] ?? ""}</text>
            </>
          );
          return isOpt && frozen ? (
            <g key={id} transform={`translate(${p[0]} ${p[1]})`} className={`${cls} pick`} role="button" tabIndex={0}
               aria-pressed={id === selected} aria-label={label(id, o)}
               onClick={() => onSelect?.(id === selected ? null : id)}
               onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect?.(id === selected ? null : id))}>
              {body}
            </g>
          ) : (
            <g key={id} transform={`translate(${p[0]} ${p[1]})`} className={cls} role="img" aria-label={label(id, o)}>{body}</g>
          );
        })}
        {frozen && receiver && reveal > 0.5 && (   // the "Played" tag over the receiver
          <g aria-hidden="true" pointerEvents="none" transform={`translate(${orient(receiver)[0]} ${orient(receiver)[1] - NODE_R - 2.2})`}>
            <rect x="-4.2" y="-1.7" width="8.4" height="3.2" rx="1.6" fill={TINT} stroke={INK} strokeWidth="0.3" />
            <text y="0.65" textAnchor="middle" style={{ font: "800 2px var(--font)", fill: INK }}>Played</text>
          </g>
        )}
        {ball.p && <circle cx={orient(ball.p)[0]} cy={orient(ball.p)[1]} r={0.9 * (1 + 0.12 * ball.z)} className="puck" />}
      </svg>

      <div className="replay-bar">
        <button type="button" onClick={() => { ref.current.paused = false; setPaused(false); setRun((n) => n + 1); onReplay?.(); }}>Replay</button>
        <button type="button" aria-pressed={paused} disabled={frozen} onClick={() => { ref.current.paused = !paused; setPaused(!paused); }}>
          {paused ? "Resume" : "Pause"}
        </button>
        <span className="sr-only" role="status">{frozen ? "Frozen at the pass" : ""}</span>
        <span className="muted small" aria-hidden="true">
          {frozen ? "Frozen at the pass · Attacking →" : `${Math.abs(tau).toFixed(1)} s before the shot`}
        </span>
      </div>

      <div className="roster" aria-label="Players by position">
        {roster.map(({ side, items }) => (
          <div key={side}>
            <h3><span className={`dot ${side}`} aria-hidden="true" /> {teams[side] || side}</h3>
            <ul>
              {items.map(({ id, role, num, opt }) => {
                const inner = (
                  <>
                    <span className={`chip ${side}`}>{num ?? "–"}</span>
                    <span>{opt?.player || names[id] || (id === pass.passer_id ? pass.passer : role || id)}</span>
                    {(opt?.player || names[id]) && role ? <span className="muted small">{role}</span> : null}
                    {id === pass.passer_id && <span className="role">pass</span>}
                    {opt?.played && <span className="role">played</span>}
                  </>
                );
                return (
                  <li key={id} className={`${id === selected ? "on" : ""} ${id === pass.passer_id ? "key" : ""}`}
                      onMouseEnter={() => setHover(id)} onMouseLeave={() => setHover(null)}>
                    {opt && frozen ? (
                      <button type="button" aria-pressed={id === selected} onFocus={() => setHover(id)} onBlur={() => setHover(null)}
                              onClick={() => onSelect?.(id === selected ? null : id)}>{inner}</button>
                    ) : <div className="rrow">{inner}</div>}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
