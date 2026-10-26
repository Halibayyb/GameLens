import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ballAt, ballPath, easeInOut, liftAt, receptionTau, resolveShot, sampleTrack, slidePoseAt } from "../motion.js";
import { FIG, PXU, RING_Y, VIEW, arc, inferAttackRight, project, pts, toProto, up } from "../broadcast/projection.js";
import { OUTLINE, POSES, kitOf, poseFor } from "../broadcast/figures.js";
import { clamp, fmtPct, trim } from "../broadcast/labels.js";
import { buildReveal } from "../broadcast/layout.js";
import SeekBar from "./SeekBar.jsx";
import { FRAME, LOOK_S, exportFrames, fmtTau, frameOf, framesText, keyFrames } from "../export/frames.js";
import "./broadcast.css";

// Broadcast (perspective) replay of a moment, ported from good-assist-proto: the build-up to the pass, the pass, the
// shot (GOAL flash), a rewind to the pass, then a frozen frame where glass spheres and arrows reveal the passer's options.
// Data is in world metres; `toProto` turns it into the proto's 120 x 80 frame attacking toward x = 120 before projecting.
//
// The players come from the 5 fps tracking; the BALL does not: motion.js ballAt choreographs it from the pass and shot
// (curve, lift, easing, at the feet in between) and this view draws its height with a shadow left on the grass.
//
// Props
//   moment, track, assist, teams   the moment summary, its replay window, the assist block, { home, away } names
//   shot         the API's moment.shot { from, to, tau, s, z, lift } (optional: derived from the tracking without it)
//   selected     a player_id: that pass option gets a focus ring and its arrow drawn solid, even if it is not among
//                the arrows shown by default (set it from a graph or the roster)
//   onSelect(id) called with a player_id when a sphere is clicked / activated
//   onDone, onReplay               the reveal finished / Replay pressed
// The option that was played is always the strongest thing on the pitch (a thick ink arrow, to where the ball lands when
// the pass says `show_landing`); the rest are quieter dashed suggestions. Every shown option is labelled "A · #10 · 82%"
// (letters ranked by expected value). The SeekBar under the pitch scrubs, steps and exports the frames.

const HOLD_S = 0.5, REWIND_S = 0.75, PAUSE_S = 0.5, STEP_S = 0.5;   // flash hold, run back, beat before the reveal, gap between reveal steps
const AFTER_SHOT_S = 0.8;       // how long after the shot the tracking is played
const ZK = 2.4;                 // height exaggeration for the ball (matches the exaggerated figure size)
const DT = 0.1;                 // seconds either side of tau used to read a player's velocity
const INK = "#1f2430";
const pointList = list => list.map(q => q.map(n => n.toFixed(1)).join(",")).join(" ");
const GOLD = "#1f2430", SEL = "#2563eb";   // the played pass (ink), the picked option (focus ring)
const CROP_TOP = 83;   // viewBox top: just under the stand strip (it ends at y = 82), so no grandstand shows; world-to-screen maths is untouched
const RINGS = {                 // glass-sphere looks (proto RINGS)
  passer: { fill: "#44516e", op: 0.28, w: 2.4 },
  opt: { fill: "#8fa0c4", op: 0.2, w: 1.4 },
  played: { fill: "#5f78b0", op: 0.42, w: 3.2 },
};

const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// shared <defs>: arrow heads and the six poses (ids prefixed bc- so they never clash with other SVGs on the page)
const Defs = memo(function Defs() {
  return (
    <defs>
      <marker id="bc-head-opt" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#4b5563" /></marker>
      <marker id="bc-head-played" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="3.6" markerHeight="3.6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill={GOLD} stroke={INK} strokeWidth="2" strokeLinejoin="round" /></marker>
      <marker id="bc-head-sel" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill={SEL} /></marker>
      {Object.entries(POSES).map(([name, parts]) => (
        <symbol key={name} id={`bc-fig-${name}`} overflow="visible">
          <g strokeLinecap="round" strokeLinejoin="round" fill="none">
            {parts.map(([t, a, b, c, d], i) => t === "limb" ? (
              <g key={i}><path d={a} stroke={OUTLINE} strokeWidth={c} /><path d={a} style={{ stroke: `var(--${b})` }} strokeWidth={d} /></g>
            ) : t === "body" ? <path key={i} d={a} style={{ fill: `var(--${b})` }} stroke={OUTLINE} strokeWidth="2" />
              : t === "head" ? <circle key={i} cx={a} cy={b} r="7.5" fill="#f3efe7" stroke={OUTLINE} strokeWidth="2" />
              : <path key={i} d={a} fill={OUTLINE} stroke="none" />)}
          </g>
        </symbol>
      ))}
    </defs>
  );
});

// stripes, markings, goals and the stand, in proto pitch coordinates (static, drawn once)
const Turf = memo(function Turf({ flip }) {
  const stand = project(60, 0)[1];
  const line = (list, close, key) => {
    const P = close ? "polygon" : "polyline";
    return <P key={key} points={pts(list, flip)} fill="none" stroke="#3b4250" strokeWidth="1.6" strokeLinejoin="round" strokeOpacity=".85" />;
  };
  const poly = (list, w, c, key) => <polyline key={key} points={list.map(p => p.map(n => n.toFixed(1)).join(",")).join(" ")} fill="none" stroke={c} strokeWidth={w} strokeLinejoin="round" />;
  const a = Math.acos(6 / 9.15), H = 2.8, D = 3;
  return (
    <g>
      {Array.from({ length: 14 }, (_, i) => (i - 1) * 10).map(x => <polygon key={x} points={pts([[x, 0], [x + 10, 0], [x + 10, 86], [x, 86]], flip)} fill={(x / 10) % 2 ? "#f7f7f2" : "#eeeee6"} />)}
      <polygon points={pts([[-10, 0], [60, 0], [60, 86], [-10, 86]], flip)} fill="#f4f4ef" fillOpacity=".55" />   {/* defending half faded */}
      <rect x="0" y="0" width={VIEW.w} height={stand - 2} fill="#d5d8de" />
      <rect x="0" y={stand - 18} width={VIEW.w} height="16" fill="#c3c7cf" />
      <line x1="0" y1={stand - 2} x2={VIEW.w} y2={stand - 2} stroke={INK} strokeWidth="1.6" />
      {line([[0, 0], [120, 0], [120, 80], [0, 80]], true, "o")}
      {line([[60, 0], [60, 80]], false, "h")}
      {line(arc(60, 40, 9.15, 0, Math.PI * 2, 48), true, "c")}
      {[[120, -1], [0, 1]].map(([gx, d]) => {   // both ends; d points into the pitch
        const a0 = d < 0 ? Math.PI : 0, [sx, sy, sc] = project(gx + 12 * d, 40, flip);
        const front = [[gx, 36, 0], [gx, 36, H], [gx, 44, H], [gx, 44, 0]].map(p => up(...p, flip));
        const back = [[gx - D * d, 36, 0], [gx - D * d, 36, H * 0.9], [gx - D * d, 44, H * 0.9], [gx - D * d, 44, 0]].map(p => up(...p, flip));
        return (
          <g key={gx}>
            {line([[gx, 18], [gx + 18 * d, 18], [gx + 18 * d, 62], [gx, 62]], false, "p")}
            {line([[gx, 30], [gx + 6 * d, 30], [gx + 6 * d, 50], [gx, 50]], false, "s")}
            {line(arc(gx + 12 * d, 40, 9.15, a0 - a, a0 + a, 16), false, "a")}
            <ellipse cx={sx} cy={sy} rx={4 * sc} ry={2 * sc} fill="none" stroke={INK} strokeWidth="1.4" />
            {poly(back, 1.2, "#8b92a0", "b")}
            {[[1, 1], [2, 2], [0, 0], [3, 3]].map(([i, j]) => poly([front[i], back[j]], 1.2, "#8b92a0", `n${i}`))}
            {poly(front, 3, INK, "f")}
          </g>
        );
      })}
    </g>
  );
});

// One frame of the timeline for a clock reading: which tau to show, which phase we are in, how far the reveal has got.
function frameAt(clock, T) {
  const { t0, tEnd, freezeTau, playS, hasReveal } = T;
  const rewindAt = playS + HOLD_S, frozenAt = rewindAt + REWIND_S, revealAt = frozenAt + PAUSE_S;
  if (clock < playS) return { phase: "play", tau: t0 + clock, tdir: 1, step: 0 };
  if (clock < rewindAt) return { phase: "hold", tau: tEnd, tdir: 1, step: 0 };
  if (clock < frozenAt) return { phase: "rewind", tau: tEnd + (freezeTau - tEnd) * easeInOut((clock - rewindAt) / REWIND_S), tdir: -1, step: 0 };
  const step = hasReveal && clock >= revealAt ? Math.min(4, 1 + Math.floor((clock - revealAt) / STEP_S)) : 0;   // 1 passer, 2 options, 3 option arrows, 4 played pass
  return { phase: "frozen", tau: freezeTau, tdir: 1, step };
}

// mode "frame": no intro and no replay controls; the scene sits on the frozen pass and the only control is Export frames.
// mode "still": the same frozen pass with no controls at all (the explainer uses it as a picture).
export default function BroadcastPitch({ moment, track, assist, shot: shotProp, teams, selected, onSelect, onDone, onReplay, mode = "replay" }) {
  const [clock, setClock] = useState(0);          // seconds since the replay started
  const [paused, setPaused] = useState(false);
  const [run, setRun] = useState(0);              // bumped by the Replay button
  const [tipId, setTipId] = useState(null);
  const [narrow, setNarrow] = useState(false);
  // the replay seekbar: null while the intro runs; once touched, { rel, playing } with rel = seconds from the pass (0 = the scored frame)
  const [seek, setSeek] = useState(null);
  const [rate, setRate] = useState(1);          // replay speed: 1, 0.5 or 0.25
  const [exporting, setExporting] = useState(null);   // { i, n } while Export frames runs
  const seekRef = useRef(null), exportRun = useRef(0), svgRef = useRef(null);
  const ref = useRef({ clock: 0, last: 0, paused: false, stopped: false, doneCalled: false });
  const wrap = useRef(null);
  const cb = useRef({});
  cb.current = { onDone, onReplay };   // latest callbacks without restarting the timeline

  const attackRight = assist?.attack_right ?? inferAttackRight(moment);
  const flip = moment.team === "away";   // an Away moment is mirrored: Away attacks the left-hand goal, as in the proto
  const shot = useMemo(() => resolveShot(shotProp, track, assist), [shotProp, track, assist]);
  const ctx = useMemo(() => ({ track, assist, shot }), [track, assist, shot]);   // what ballAt needs
  const hasReveal = !!assist?.pass && !!assist.options?.length;
  const t0 = track.t[0], tEnd = Math.min(track.t[track.t.length - 1], AFTER_SHOT_S);
  const freezeTau = hasReveal ? assist.pass.tau : 0;
  const playS = tEnd - t0;
  const T = { t0, tEnd, freezeTau, playS, hasReveal };
  const end = playS + HOLD_S + REWIND_S + (hasReveal ? PAUSE_S + 3 * STEP_S + 0.4 : 0);
  const ls = narrow ? 1.7 : 1;                    // labels are enlarged on phones, where the 1000 px wide scene is shown ~375 px wide

  useEffect(() => {
    ref.current = { clock: 0, last: performance.now(), paused: ref.current.paused, stopped: false, doneCalled: false };
    seekRef.current = null; setSeek(null); exportRun.current++;   // a new replay or moment: back to the intro
    setTipId(null);
    if (reducedMotion()) { setClock(end); cb.current.onDone?.(); return undefined; }   // jump to the revealed freeze frame
    setClock(0);
    let raf;
    const frame = now => {
      const r = ref.current;
      if (r.stopped) return;   // the seekbar took over
      if (!r.paused) r.clock = Math.min(end, r.clock + (now - r.last) / 1000);
      r.last = now;
      setClock(r.clock);
      if (r.clock < end) raf = requestAnimationFrame(frame); else { r.doneCalled = true; cb.current.onDone?.(); }   // reveal complete
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [moment.id, track, run, end]);

  useEffect(() => {   // labels need to be bigger when the scene is shown small
    const el = wrap.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 560));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // initial value set synchronously for the reduced-motion / server render: start on the final frame
  const shown = clock === 0 && reducedMotion() ? end : clock;
  const seeking = seek !== null;
  const { phase, tau: frameTau, tdir, step } = seeking
    ? { phase: "seek", tau: freezeTau + seek.rel, tdir: 1, step: Math.abs(seek.rel) < 0.001 ? 4 : 0 }   // the analysis shows on the pass only
    : frameAt(shown, T);
  const frozen = phase === "frozen" || (seeking && Math.abs(seek.rel) < 0.001);
  const tau = frameTau;
  const still = frozen;                                                  // figures hold their pose on the scored frame
  const done = shown >= end || seeking;
  const nowMs = seeking ? (tau - t0) * 1000 : shown * 1000;   // the stride clock: follows tau while scrubbing, so a frame always looks the same

  const ids = useMemo(() => Object.keys(track.objects).filter(id => id !== "ball"), [track]);
  const proto = (id, at) => { const p = sampleTrack(track, id, at); return p && toProto(p, attackRight); };

  // the decision overlay, laid out once on the frozen frame
  const reveal = useMemo(() => {
    if (!hasReveal) return null;
    const pos = Object.fromEntries(ids.map(id => [id, proto(id, freezeTau)]));
    return buildReveal({ assist, attackRight, flip, posOf: id => pos[id], everyone: Object.values(pos).filter(Boolean), ls, include: selected, ctx });
  }, [assist, track, ctx, attackRight, flip, ls, freezeTau, hasReveal, selected]); // eslint-disable-line react-hooks/exhaustive-deps

  // players at this tau: position, facing and stride pose from the tracked velocity
  const figs = ids.map((id, i) => {
    const p = proto(id, tau);
    if (!p) return null;
    const a = sampleTrack(track, id, tau - DT), b = sampleTrack(track, id, tau + DT);
    const vx = a && b ? (b[0] - a[0]) / (2 * DT) * (attackRight ? 1 : -1) * (flip ? -1 : 1) * tdir : 0;
    const speed = a && b ? Math.hypot(b[0] - a[0], b[1] - a[1]) / (2 * DT) : 0;
    const team = track.objects[id].team;
    const { pose, bob } = poseFor(speed, nowMs, (i * 53) % 360, still);
    const rel = tau - freezeTau, air = liftAt(assist?.pass?.jumps?.[id], rel), held = slidePoseAt(assist?.pass?.slides?.[id], rel);
    return { id, p, pose: held || (air > 0.05 ? "head" : pose), bob: held ? 0 : bob, air, kit: kitOf(id, team), face: Math.abs(vx) > 0.3 ? Math.sign(vx) : (team === moment.team ? 1 : -1) * (flip ? -1 : 1) };
  }).filter(Boolean).sort((a, b) => a.p[1] - b.p[1]);   // far players first so nearer ones overlap them

  // the ball (choreographed, with height) and its trail along the grass, from the pass until the rewind starts
  const spot = w => w && project(...toProto(w, attackRight), flip);
  const b = ballAt(tau, ctx), ball = spot(b.p);
  const trailFrom = hasReveal ? assist.pass.tau : -1;
  const trail = [];
  if ((phase === "play" || phase === "hold" || phase === "seek") && tau > trailFrom) {
    for (const w of ballPath(ctx, trailFrom, tau)) { const [x, y, sc] = spot(w); trail.push(`${x.toFixed(1)},${(y - 5 * sc).toFixed(1)}`); }
  }

  const activate = r => { onSelect?.(r.id); setTipId(r.id); };
  const tipRing = reveal && step >= 2 ? reveal.rings.find(r => r.id === tipId && r.opt) : null;
  const colorOf = (k, sel) => (sel ? SEL : k === "played" ? INK : "#4b5563");
  const flash = moment.kind === "goal" && tau >= 0 && (phase === "play" || phase === "hold" || phase === "seek");
  const nameOf = id => assist?.options?.find(o => o.player_id === id)?.player;

  // ---- the replay seekbar (ported from the proto's seekSetup / seekTo / seekPlay) ----
  // rel is seconds from the pass; every frame is a pure function of tau, so any instant draws exactly.
  const sMin = Math.ceil((t0 - freezeTau) / FRAME - 1e-6) * FRAME, sMax = Math.floor((tEnd - freezeTau) / FRAME + 1e-6) * FRAME;
  const goalRel = moment.kind === "goal" ? -freezeTau + (shot.s || 0.45) : null;
  const marks = useMemo(() => {
    const list = [{ rel: 0, text: hasReveal ? "◆ pass" : "◆ shot", side: "c", title: hasReveal ? "The scored frame: the moment of the pass" : "The shot" }];
    if (hasReveal && assist.pass.carry > 0.01) list.push({ rel: assist.pass.flight, title: "The receiver controls the ball" });
    if (hasReveal) list.push({ rel: -freezeTau, text: "shot", side: "l", title: "The shot" });
    if (goalRel != null) list.push({ rel: goalRel, text: "goal", side: "r", title: "The ball crosses the line" });
    return list.filter(k => k.rel >= sMin - 1e-6 && k.rel <= sMax + 1e-6).sort((a, b) => a.rel - b.rel);
  }, [assist, hasReveal, freezeTau, goalRel, sMin, sMax]);
  const marksRel = marks.map(k => (k.rel === 0 ? 0 : Math.min(sMax, frameOf(Math.ceil(k.rel / FRAME) * FRAME))));
  const putSeek = next => { seekRef.current = next; setSeek(next); };
  const clampRel = r => Math.max(sMin, Math.min(sMax, r));
  const takeOver = () => {   // first touch during the intro: skip it and show the frozen pass with the full analysis
    const r = ref.current;
    r.stopped = true;
    if (!r.doneCalled) { r.doneCalled = true; cb.current.onDone?.(); }
    return seekRef.current || { rel: 0, playing: false };
  };
  const goTo = (rel, snap = true) => { takeOver(); setTipId(null); putSeek({ rel: clampRel(snap ? frameOf(rel) : rel), playing: false }); };
  const onPlay = () => {
    const cur = takeOver();
    if (cur.playing) return putSeek({ rel: clampRel(frameOf(cur.rel)), playing: false });   // pausing snaps to the nearest frame
    putSeek({ rel: cur.rel >= sMax - 0.001 ? sMin : cur.rel, playing: true });
  };
  const onJump = dir => {
    const cur = takeOver();
    const to = dir > 0 ? marksRel.find(t => t > cur.rel + 1e-6) : [...marksRel].reverse().find(t => t < cur.rel - 1e-6);
    goTo(to ?? (dir > 0 ? sMax : sMin));
  };
  const playing = !!seek?.playing;
  useEffect(() => {   // the frame tab starts on the scored frame instead of the intro
    if (mode !== "frame" && mode !== "still") return;
    takeOver(); putSeek({ rel: 0, playing: false });
  }, [mode, moment.id, track, run]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {   // play: advance rel by real time x the chosen speed
    if (!playing) return undefined;
    let raf, last = performance.now();
    const tick = now => {
      const cur = seekRef.current;
      if (!cur?.playing) return;
      const rel = cur.rel + (now - last) / 1000 * rate; last = now;
      if (rel >= sMax) { putSeek({ rel: sMax, playing: false }); return; }
      putSeek({ rel, playing: true });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, rate, sMax]); // eslint-disable-line react-hooks/exhaustive-deps

  // Export frames: each key frame is shown through the same seek state, then the SVG is turned into a PNG
  const ballXY = rel => {   // the ball on screen at rel (height included), or null
    const b = ballAt(freezeTau + rel, ctx);
    if (!b.p) return null;
    const [x, y, sc] = project(...toProto(b.p, attackRight), flip);
    return [x, y - 5 * sc - b.z * ZK * PXU * sc];
  };
  const exportList = useMemo(   // the frames Export saves, also the chips of the Frame tab
    () => (hasReveal ? keyFrames({ min: sMin, max: sMax, pre: null, flight: assist.pass.flight, carry: assist.pass.carry || 0, tGoal: goalRel ?? sMax }) : []),
    [hasReveal, assist, sMin, sMax, goalRel]);
  const runExport = async () => {
    if (exporting || !hasReveal || !reveal) return;
    const cur = takeOver(), token = ++exportRun.current;
    const goalN = moment.kind === "goal" ? String(moment.score || "").split("-").reduce((n, v) => n + (+v || 0), 0) : 0;
    const head = `${moment.kind === "goal" ? `Goal ${goalN}` : moment.title} · ${moment.minute} · ${moment.team === "home" ? "Home" : "Away"}`;
    setExporting({ i: 0, n: 0 });
    try {
      await exportFrames({
        frames: exportList, width: 2000, height: Math.round(2000 * (VIEW.h - CROP_TOP) / VIEW.w),
        isStale: () => exportRun.current !== token,
        show: rel => new Promise(res => { putSeek({ rel, playing: false }); requestAnimationFrame(() => requestAnimationFrame(res)); }),
        snapshot: () => {
          const rel = seekRef.current.rel, ahead = Math.min(rel + LOOK_S, goalRel ?? sMax);
          const a = ballXY(rel), b = ballXY(ahead);
          return { svg: svgRef.current, ball: rel === 0 || (goalRel != null && rel >= goalRel) || !a || !b ? null : [a, b] };
        },
        caption: f => `${head} · ${fmtTau(f.rel)} · ${f.text}`,
        textFile: lines => framesText({ title: head, lines, options: assist.options, letters: reveal.letters }),
        zipName: `${moment.kind === "goal" ? `goal${goalN}` : "moment"}-${parseInt(moment.minute, 10) || 0}min-frames.zip`,
        onProgress: (i, n) => setExporting({ i, n }),
      });
    } catch (e) {
      console.error(e);
    } finally {
      if (exportRun.current === token) putSeek({ rel: cur.rel, playing: false });
      setExporting(null);
    }
  };

  // text alternative: the scene in words, with every number from the props
  const description = [
    `${moment.title} by ${moment.player}${moment.team_name ? ` for ${moment.team_name}` : ""}, minute ${moment.minute}.`,
    "Replay of the build-up and the shot, then frozen at the moment of the pass.",
    hasReveal && `${assist.pass.passer} passes to ${assist.pass.receiver}.`,
    hasReveal && `Suggested options: ${[...assist.options].sort((a, b) => b.ev - a.ev).map(o => `${o.player}${o.played ? " (played)" : ""} arrives ${fmtPct(o.arrives)} estimated, goal ${fmtPct(o.goal)} estimated, ${o.cut_out} defenders cut out`).join("; ")}.`,
    hasReveal && assist.verdict?.label && `Verdict: ${assist.verdict.label}.`,
    hasReveal && assist.sentence,
  ].filter(Boolean).join(" ");

  const lblStyle = { fontSize: 17 * ls, strokeWidth: 5 * ls };
  const status = !frozen ? (phase === "rewind" ? "Rewinding to the pass" : seeking ? `Replay ${fmtTau(seek.rel)}` : tau < 0 ? `Replay, ${Math.abs(tau).toFixed(1)} s before the shot` : "Replay at the shot")
    : hasReveal ? (step < 4 ? "Frozen at the pass: what was behind it?" : "Frozen at the pass") : "Frozen at the moment of the shot";

  return (
    <div className="bc" ref={wrap} onKeyDown={e => e.key === "Escape" && setTipId(null)}>
      <div className="bc-stage">
        <svg ref={svgRef} viewBox={`0 ${CROP_TOP} ${VIEW.w} ${VIEW.h - CROP_TOP}`} className="bc-svg" role="group" aria-label="Broadcast view of the attacking end" aria-describedby={mode === "still" ? "bc-desc-still" : "bc-desc"}
             onClick={() => setTipId(null)}>
          <Defs />
          <Turf flip={flip} />
          <g>{/* ellipses on the grass under the ringed players */}
            {reveal && step >= 1 && reveal.rings.filter(r => r.kind === "passer" || step >= 2).map(r => (
              <ellipse key={r.id} className="bc-fade" cx={r.c.x} cy={r.c.foot} rx={30 * r.c.sc} ry={9 * r.c.sc} fill={RINGS[r.kind].fill} fillOpacity=".55" stroke={INK} strokeWidth="1.4" />
            ))}
          </g>
          <g>
            {figs.map(f => {
              const [sx, sy, sc] = project(f.p[0], f.p[1], flip), k = sc * FIG;
              return (
                <g key={f.id} transform={`translate(${sx.toFixed(1)} ${sy.toFixed(1)}) scale(${(k * f.face).toFixed(3)} ${k.toFixed(3)})`}>
                  <ellipse cx="0" cy="0" rx="24" ry="7" fill={INK} fillOpacity=".1" />
                  <use href={`#bc-fig-${f.pose}`} transform={`translate(0 ${(f.bob - f.air * PXU * ZK / FIG).toFixed(1)})`}
                       style={{ "--kit": f.kit.shirt, "--short": f.kit.shorts, "--sock": f.kit.sock }} />
                </g>
              );
            })}
          </g>
          {reveal && (
            <g>
              {reveal.rings.filter(r => r.kind === "passer" ? step >= 1 : step >= 2).map(r => {
                const st = RINGS[r.kind], sel = r.id === selected;
                const sphere = (
                  <>
                    <circle className="bc-sph" cx={r.c.x} cy={r.c.y} r={r.c.r} fill={st.fill} fillOpacity={st.op} stroke={INK} strokeWidth={st.w} />
                    {r.kind === "played" && <circle cx={r.c.x} cy={r.c.y} r={r.c.r - st.w / 2 - 1.5} fill="none" stroke={GOLD} strokeWidth={st.w - 1.5} />}
                    {sel && <circle className="bc-focus" cx={r.c.x} cy={r.c.y} r={r.c.r + 9} fill="none" stroke="#fff" strokeWidth="9" />}
                    {sel && <circle className="bc-focus" cx={r.c.x} cy={r.c.y} r={r.c.r + 9} fill="none" stroke={SEL} strokeWidth="5" />}
                  </>
                );
                return r.opt ? (
                  <g key={r.id} className={`bc-ring bc-fade ${sel ? "on" : ""}`} tabIndex={0} role="button"
                     aria-label={`Pass to ${r.opt.player}${r.opt.played ? " (played)" : " (suggested)"}: arrives ${fmtPct(r.opt.arrives)} estimated, goal ${fmtPct(r.opt.goal)} estimated, ${r.opt.cut_out} defenders cut out`}
                     aria-pressed={sel}
                     onClick={e => { e.stopPropagation(); activate(r); }}
                     onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); activate(r); } }}>
                    {sphere}
                  </g>
                ) : <g key={r.id} className="bc-fade" aria-hidden="true">{sphere}</g>;
              })}
              {step >= 3 && reveal.arrows.filter(a => !a.played || step >= 4).map(a => {
                const sel = a.id === selected;
                if (a.played && a.landing) {   // the played pass, to where the ball lands: a dashed mark on the grass, the receiver's run onto it
                  const l = reveal.landing;
                  return (
                    <g key={a.id} className="bc-fade">
                      <line x1={l.from.x} y1={l.from.y} x2={l.lx} y2={l.ly} stroke={INK} strokeWidth="1.6" strokeOpacity=".55" strokeDasharray="2 5" strokeLinecap="round" />
                      <ellipse cx={l.lx} cy={l.ly} rx={30 * l.k} ry={9 * l.k} fill={INK} fillOpacity=".1" stroke={INK} strokeWidth="1.6" strokeDasharray="5 4" />
                      {l.air && <polyline points={pointList(l.onGrass)} fill="none" stroke={INK} strokeWidth="1.4" strokeOpacity=".35" strokeDasharray="4 4" />}
                      <polyline points={pointList(l.kept)} fill="none" stroke={INK} strokeWidth="4.5" strokeLinejoin="round" markerEnd="url(#bc-head-played)" />
                    </g>
                  );
                }
                if (a.played) return (
                  <line key={a.id} className="bc-fade" x1={a.x1} y1={a.y1} x2={a.x2} y2={a.y2} stroke={INK} strokeWidth="4.5" markerEnd="url(#bc-head-played)" />
                );
                return sel ? (   // the picked suggestion: solid in the focus colour
                  <line key={a.id} className="bc-fade" x1={a.x1} y1={a.y1} x2={a.x2} y2={a.y2} stroke={SEL} strokeWidth={3.6} strokeLinecap="round" markerEnd="url(#bc-head-sel)" />
                ) : (
                  <line key={a.id} className="bc-fade" x1={a.x1} y1={a.y1} x2={a.x2} y2={a.y2} stroke="#4b5563" strokeWidth="1.8" strokeDasharray="7 5" markerEnd="url(#bc-head-opt)" />
                );
              })}
              {step >= 3 && reveal.labels.filter(l => !l.played || step >= 4).map(l => (
                <text key={l.id} className="bc-lbl bc-fade" x={l.x} y={l.y} textAnchor="middle" fill={l.played ? INK : colorOf("opt", l.id === selected)} style={lblStyle}>{l.text}</text>
              ))}
            </g>
          )}
          <g>{/* ball: trail along the grass, its shadow there, then the ball lifted by its height */}
            <polyline points={trail.join(" ")} fill="none" stroke={INK} strokeWidth="1.6" strokeOpacity=".4" strokeDasharray="5 5" strokeLinejoin="round" />
            {ball && <ellipse cx={ball[0]} cy={ball[1]} rx={6 * ball[2] * (1 + 0.12 * b.z)} ry={2.2 * ball[2] * (1 + 0.12 * b.z)} fill={INK} fillOpacity={(0.26 / (1 + 0.5 * b.z)).toFixed(3)} />}
            {ball && <circle cx={ball[0]} cy={ball[1] - 5 * ball[2] - b.z * ZK * PXU * ball[2]} r={6.5 * ball[2]} fill="#fff" stroke={INK} strokeWidth="1.8" />}
          </g>
          <g className="bc-flash" opacity={flash ? 1 : 0} pointerEvents="none">
            <rect x="360" y="230" width="280" height="120" rx="10" fill={INK} fillOpacity=".88" />
            <text x="500" y="312" textAnchor="middle" fontSize="76" fontWeight="800" fill="#f4f4ef" fontFamily="ui-monospace, Menlo, monospace">GOAL!</text>
          </g>
        </svg>
        {tipRing && (
          <div className="bc-tip" role="status" style={{ left: `${clamp(tipRing.c.x / VIEW.w * 100, 20, 80)}%`, top: `${Math.max(14, (tipRing.c.y - tipRing.c.r - CROP_TOP) / (VIEW.h - CROP_TOP) * 100)}%` }}>
            <b>{tipRing.opt.player}{tipRing.opt.played ? " (played)" : ""}</b>
            Arrives: {fmtPct(tipRing.opt.arrives)} (estimated)<br />
            Goal: {fmtPct(tipRing.opt.goal)} (estimated)<br />
            Expected value: {fmtPct(tipRing.opt.ev)} (estimated)<br />
            Defenders cut out: {tipRing.opt.cut_out}
          </div>
        )}
      </div>
      <p id={mode === "still" ? "bc-desc-still" : "bc-desc"} className="bc-sr">{description}</p>
      {mode === "still" ? null : mode === "frame" ? (
        <>
        {exportList.length > 0 && (
          <ol className="bc-frames" aria-label="Frames that Export saves">
            {exportList.map((f, i) => (
              <li key={f.slug}>
                <button type="button" aria-pressed={Math.abs((seek?.rel ?? 0) - f.rel) < 0.001} disabled={!!exporting} onClick={() => goTo(f.rel)} title={f.text}>
                  <span className="bc-fn">{i + 1}</span><span className="bc-ft">{fmtTau(f.rel)}</span><span className="bc-fl">{f.text}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
        <div className="bc-bar">
          <button type="button" className="bc-export" disabled={!!exporting || !hasReveal} onClick={runExport}
                  title="Save the key frames of this moment as PNGs in one ZIP" aria-label="Export the key frames of this moment as PNG images in a ZIP">
            {exporting ? `Exporting ${exporting.i}/${exporting.n}…` : "⤓ Export frames"}
          </button>
          <span className="bc-status">{hasReveal ? `Previewing one of ${exportList.length} frames · Export saves all ${exportList.length} as PNGs in one ZIP` : "No pass to export in this moment"}</span>
        </div>
        </>
      ) : (
        <>
      <div className="bc-bar">
        <button type="button" onClick={() => { ref.current.paused = false; setPaused(false); setRun(n => n + 1); onReplay?.(); }}>Replay</button>
        <button type="button" aria-pressed={paused} disabled={done} onClick={() => { ref.current.paused = !paused; setPaused(!paused); }}>{paused ? "Resume" : "Pause"}</button>
        <span className="bc-sr" role="status">{done ? "Replay finished, frozen at the moment of the pass" : ""}</span>
        <span className="bc-status" aria-hidden="true">{status}</span>
        {teams && (
          <span className="bc-legend" aria-hidden="true">
            <i className="bc-dot home" />{teams.home} <i className="bc-dot away" />{teams.away}
          </span>
        )}
      </div>
      <SeekBar min={sMin} max={sMax} rel={seeking ? seek.rel : hasReveal && frozen ? 0 : clampRel(frameOf(frameTau - freezeTau))} marks={marks}
               playing={playing} speed={rate} scrubbed={seeking && Math.abs(seek.rel) > 0.001 && hasReveal} hasPass={hasReveal}
               onStart={() => goTo(sMin)} onPrev={() => goTo((seekRef.current?.rel ?? 0) - FRAME)} onNext={() => goTo((seekRef.current?.rel ?? 0) + FRAME)}
               onPass={() => goTo(0)} onPlay={onPlay} onSpeed={setRate} onJump={onJump}
               onScrub={(v, dragging) => goTo(dragging && Math.abs(v) <= FRAME + 1e-6 ? 0 : v)} />
        </>
      )}
    </div>
  );
}
