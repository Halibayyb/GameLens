// Motion helpers for the animated replay: pure functions, no DOM.
// Ported from good-assist-proto/src/motion.js (easings, passPoint). Its velocity-based player model is replaced
// by sampleTrack, because here the positions come from the tracking data. Time `tau` is seconds around the
// moment: tau = 0 is the frame the decisions are scored on.
// The ball is NOT read from the 5 fps tracking (kinked, no height): ballAt choreographs it from the pass and shot
// events the way the prototype's play() does, so both pitch views draw exactly the same ball.

import { toProto } from "./broadcast/projection.js";

export const easeOut = t => 1 - (1 - t) * (1 - t);
export const easeInOut = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
// a struck ball is fastest off the foot and slows a little in flight
export const easeBall = t => 0.5 * t + 0.5 * easeOut(t);

// Ball position at progress u (0..1) of a pass: one smooth quadratic curve, bulging `bend` pitch units to the
// right of the direction of travel (negative = left), plus a parabolic height that peaks `lift` above the straight
// line between the start height `z0` and the end height `z1` (a cross leaves at foot height, arrives at head height).
export function passPoint(start, end, bend, lift, u, z0 = 0, z1 = 0) {
  const dx = end[0] - start[0], dy = end[1] - start[1], len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len, ny = dx / len;                       // unit normal, to the right of travel
  const cx = (start[0] + end[0]) / 2 + nx * 2 * bend;        // a quadratic curve bulges half as far as its control point
  const cy = (start[1] + end[1]) / 2 + ny * 2 * bend;
  const a = (1 - u) * (1 - u), b = 2 * (1 - u) * u, c = u * u;
  return { p: [a * start[0] + b * cx + c * end[0], a * start[1] + b * cy + c * end[1]], z: z0 * (1 - u) + z1 * u + lift * 4 * u * (1 - u) };
}

// How high above the grass a player is `rel` seconds after the pass: a parabola over the jump's hang time. jump = { at, h, hang }.
export function liftAt(jump, rel) {
  if (!jump) return 0;
  const d = (rel - jump.at) / (jump.hang / 2);
  return Math.abs(d) >= 1 ? 0 : jump.h * (1 - d * d);
}

// A held pose `rel` seconds after the pass for a player going down: 'crouch' just before the slide, then 'slide'; null otherwise.
export function slidePoseAt(slide, rel) {
  if (!slide) return null;
  if (rel >= slide.at) return "slide";
  return rel >= slide.at - (slide.crouch ?? 0.12) ? "crouch" : null;
}

const JUMP = 15;   // pitch units between two 5 fps samples that means a restart or teleport, not movement

// Where object `id` is `tau` seconds from the moment, or null when it is not on the pitch there.
// Catmull-Rom through the tracked samples; falls back to a straight line next to gaps and jumps.
export function sampleTrack(track, id, tau) {
  const xy = track.objects[id]?.xy, t = track.t;
  if (!xy) return null;
  if (tau <= t[0]) return xy[0];
  const last = t.length - 1;
  if (tau >= t[last]) return xy[last];
  let i = 0;
  while (t[i + 1] < tau) i++;
  const p1 = xy[i], p2 = xy[i + 1];
  if (!p1 || !p2) return p1 && tau - t[i] < t[i + 1] - tau ? p1 : p2 || null;
  const u = (tau - t[i]) / (t[i + 1] - t[i]);
  const p0 = xy[i - 1] || p1, p3 = xy[i + 2] || p2;
  const near = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < JUMP;
  if (!near(p0, p1) || !near(p1, p2) || !near(p2, p3)) return [p1[0] + (p2[0] - p1[0]) * u, p1[1] + (p2[1] - p1[1]) * u];
  const u2 = u * u, u3 = u2 * u;
  const cr = k => 0.5 * (2 * p1[k] + (p2[k] - p0[k]) * u + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * u2 + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * u3);
  return [cr(0), cr(1)];
}

const PW = 105, PH = 68;   // world pitch, metres
// the inverse of toProto: proto units (120 x 80, attacking x = 120) back to world metres
export const fromProto = ([u, v], attackRight) => {
  const x = u * PW / 120, y = v * PH / 80;
  return attackRight ? [x, y] : [PW - x, PH - y];
};

const AHEAD_M = 0.7, LOOK_S = 0.2, RUN_MS = 1.5;   // the ball sits this far ahead of the feet; velocity window; speed at which "ahead" is the running direction
const SETTLE_S = 0.3;                              // after the goal the ball sinks to the ground over this long

// Where a player's feet are `tau` seconds from the moment, with the ball just ahead: in the running direction, or
// toward the goal when standing still. World metres, or null when the player is not on the pitch.
function feet(track, id, tau, attackRight) {
  const p = sampleTrack(track, id, tau);
  if (!p) return null;
  const a = sampleTrack(track, id, tau - LOOK_S) || p, b = sampleTrack(track, id, tau + LOOK_S) || p;
  const vx = (b[0] - a[0]) / (2 * LOOK_S), vy = (b[1] - a[1]) / (2 * LOOK_S), sp = Math.hypot(vx, vy);
  const k = Math.min(1, sp / RUN_MS);
  const dx = (k * vx / (sp || 1)) + (1 - k) * (attackRight ? 1 : -1), dy = k * vy / (sp || 1);
  const n = Math.hypot(dx, dy) || 1;
  return [p[0] + AHEAD_M * dx / n, p[1] + AHEAD_M * dy / n];
}

// The ball `tau` seconds from the moment: { p: [x, y] on the grass in world metres, z: height in proto units }.
//   before the pass   at the passer's feet (tracked passer, ball slightly ahead)
//   the pass          one curved path (bend) with a parabolic lift, easeBall, aimed at where the receiver will be at
//                     assist.pass.reception_tau; leaves at start_z and arrives at arrive_z (a cross arrives at head height)
//   the carry         at the receiver's feet until the shot (tau 0)
//   the shot          easeOut to shot.to with its lift, ending at height shot.z
//   after             in the net, sinking to the ground
// With no assist or shot (or without the new fields) it falls back to the tracked ball.
export function ballAt(tau, { track, assist, shot }) {
  const pass = assist?.pass;
  if (!pass || !shot || pass.reception_tau == null) return { p: sampleTrack(track, "ball", tau), z: 0 };
  const right = assist.attack_right ?? true;
  const proto = w => toProto(w, right);
  const tPass = pass.tau, tRecv = Math.max(pass.reception_tau, tPass + 0.2), tShot = Math.max(shot.tau ?? 0, tRecv);
  const fromPasser = feet(track, pass.passer_id, tPass, right) || pass.start;
  const atReceiver = t => feet(track, pass.receiver_id, t, right) || pass.end;

  if (tau <= tPass) return { p: feet(track, pass.passer_id, tau, right) || fromPasser, z: 0 };
  if (tau < tRecv) {
    const b = passPoint(proto(fromPasser), proto(atReceiver(tRecv)), pass.bend || 0, pass.lift || 0,
                        easeBall((tau - tPass) / (tRecv - tPass)), pass.start_z || 0, pass.arrive_z || 0);
    return { p: fromProto(b.p, right), z: b.z };
  }
  if (tau <= tShot) return { p: atReceiver(tau), z: tau === tRecv ? pass.arrive_z || 0 : 0 };
  const z0 = tShot > tRecv ? 0 : pass.arrive_z || 0;   // a shot straight off the pass (a header) starts at arrival height
  const u = Math.min(1, (tau - tShot) / (shot.s || 0.5));
  const b = passPoint(proto(atReceiver(tShot)), proto(shot.to), 0, shot.lift ?? 0.3, easeOut(u), z0, shot.z || 0);
  const settle = easeOut(Math.min(1, Math.max(0, (tau - tShot - (shot.s || 0.5)) / SETTLE_S)));
  return { p: fromProto(b.p, right), z: u < 1 ? b.z : (shot.z || 0) * (1 - settle) };
}

// The ball's path on the grass between two times as world points [x, y], sampled every `step` seconds and ending
// exactly at `toTau`. Both pitch views draw the ball's trail and the played pass from this, so they curve alike.
export function ballPath(ctx, fromTau, toTau, step = 0.04) {
  const out = [];
  for (let s = fromTau; s < toTau; s += step) {
    const b = ballAt(s, ctx);
    if (b.p) out.push(b.p);
  }
  const end = ballAt(toTau, ctx);
  if (end.p) out.push(end.p);
  return out;
}

// When the played pass arrives (seconds from the moment); 0 without an assist
export const receptionTau = assist => assist?.pass?.reception_tau ?? 0;

// The shot to finish the ball with: the API's `shot` (moment detail), or, when the page does not pass it, a stand-in made
// from where the tracked ball ends up (a goal-mouth point) so the views never lose the ball.
export function resolveShot(shot, track, assist) {
  if (shot) return shot;
  const last = track.t[track.t.length - 1], end = sampleTrack(track, "ball", last);
  const right = assist?.attack_right ?? (end ? end[0] > PW / 2 : true);
  const to = end && Math.abs(end[0] - (right ? PW : 0)) < 3 ? end : [right ? PW : 0, PH / 2];
  return { from: end, to, tau: 0, s: 0.45, z: 0.3, lift: 0.2 };
}
