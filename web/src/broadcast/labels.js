// Label placement and small formatters, ported from good-assist-proto/src/main.js (placeLabel, trim, pitchLabel) and scoring.js (fmtPct).
import { VIEW, FIG, project } from './projection.js';

// the proto's percentage format: one decimal under 10 %, whole numbers above
export const fmtPct = p => (p < 0.1 ? (p * 100).toFixed(1) : Math.round(p * 100)) + '%';
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

// Boxes are [x0, y0, x1, y1] (an optional 5th entry weighs that obstacle). Tries the preferred spot, then steps in `dir`
// (-1 up, 1 down), then the other way, then the same heights shifted sideways, and keeps the first clear spot (or the
// least covered one). Overlap with another label weighs 3x. Returns [x, y] of the text anchor (middle, baseline).
const overlapArea = (a, b) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
export function placeLabel(x, y, w, hgt, step, dir, obstacles, placed) {
  const ks = [0, dir, -dir, 2 * dir];                     // at most two steps away, so a label stays next to its player
  const spots = [0, w * .55, -w * .55].flatMap(dx => ks.map(k => [x + dx, y + k * step]));
  let best = null;
  for (const [lx, ly] of spots) {
    const box = [lx - w / 2, ly - hgt * .78, lx + w / 2, ly + hgt * .22];
    const cost = obstacles.reduce((c, o) => c + overlapArea(box, o) * (o[4] ?? 1), 0) + 3 * placed.reduce((c, o) => c + overlapArea(box, o), 0);
    if (!best || cost < best.cost) best = { at: [lx, ly], box, cost };
    if (cost === 0) break;
  }
  placed.push(best.box);
  return best.at;
}

// every figure's screen box at its frozen position; weighted low, since the label's halo keeps it readable over a figure
export const figBoxes = (positions, flip = false) => positions.map(([x, y]) => { const [sx, sy, sc] = project(x, y, flip), k = sc * FIG; return [sx - 16 * k, sy - 92 * k, sx + 16 * k, sy + 4 * k, .25]; });
// keep labels off the stand behind the far touchline
export const standBox = () => [-VIEW.w, -VIEW.w, 2 * VIEW.w, project(60, 0)[1]];

// shorten a segment at both ends so arrows stop at the spheres
export function trim(a, b, startGap, endGap) {
  const d = dist(a, b) || 1, ux = (b[0] - a[0]) / d, uy = (b[1] - a[1]) / d;
  return [[a[0] + ux * startGap, a[1] + uy * startGap], [b[0] - ux * endGap, b[1] - uy * endGap]];
}
