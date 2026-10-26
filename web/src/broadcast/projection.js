// Perspective camera for the broadcast view, ported from good-assist-proto/src/main.js (project/projectWorld).
// Pure, no DOM. Data arrives in WORLD metres (105 x 68); everything is first mapped to the proto's frame: a 120 x 80
// pitch with the attacking team playing toward x = 120 (`toProto`), and only then projected. As in the proto, Home attacks
// the right-hand goal and Away the left-hand one: with `flip` (an Away moment) the scene is mirrored end to end, not
// rotated, so the action stays on the near side. Mirroring x -> 120 - x with a mirrored camera centre is the same as
// negating the screen-x offset from the middle, which is all `project` does.

export const VIEW = { w: 1000, h: 600, x0: 50, x1: 124, yTop: 84, yBot: 560, zFar: 1.4 };
export const FIG = 0.76;                           // player size relative to the pitch (exaggerated so figures stay readable)
export const PXU = VIEW.w / (VIEW.x1 - VIEW.x0);   // screen px per pitch unit at the near touchline
const PW = 105, PH = 68;                           // world pitch, metres

// world metres -> proto units, attacking toward x = 120 (a 180-degree turn when the team attacks left)
export const toProto = ([x, y], attackRight) => {
  const u = attackRight ? x : PW - x, v = attackRight ? y : PH - y;
  return [u * 120 / PW, v * 80 / PH];
};
// which way the team attacks when the assist block does not say: the half the moment happened in
export const inferAttackRight = moment => moment.x > PW / 2;

// proto pitch coordinates -> [screenX, screenY, scale]; scale is 1 on the near touchline (v = 80) and shrinks with distance
export function project(x, y, flip = false) {
  const cx = (VIEW.x0 + VIEW.x1) / 2;              // crops to the attacking end, so the own keeper is off screen
  const z = 1 + (1 - y / 80) * (VIEW.zFar - 1);
  const sc = 1 / z;
  const g = (sc - 1 / VIEW.zFar) / (1 - 1 / VIEW.zFar);
  return [VIEW.w / 2 + (flip ? -1 : 1) * (x - cx) * PXU * sc, VIEW.yTop + (VIEW.yBot - VIEW.yTop) * g, sc];
}
export const pts = (list, flip = false) => list.map(([x, y]) => project(x, y, flip).slice(0, 2).map(n => n.toFixed(1)).join(',')).join(' ');
export const arc = (cx, cy, r, a0, a1, n = 36) =>
  Array.from({ length: n + 1 }, (_, i) => { const a = a0 + (a1 - a0) * i / n; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; });
// a point `hu` pitch units above the grass at (x, y), on screen
export const up = (x, y, hu, flip = false) => { const [px, py, p] = project(x, y, flip); return [px, py - hu * PXU * p]; };

// ---- glass-sphere rings (proto RING_R / RING_Y): centre and size of a player's sphere, in screen px ----
export const RING_R = 56, RING_Y = -46;
export const centre = ([x, y], flip = false) => { const [sx, sy, sc] = project(x, y, flip); const k = sc * FIG; return { x: sx, y: sy + RING_Y * k, r: RING_R * k, foot: sy, sc: k }; };
