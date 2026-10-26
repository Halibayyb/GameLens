// Homography helpers for the freeze-frame editor: the same least-squares fit as vision/homography.py
// (homography_with_lines), image pixels -> pitch metres, from landmark pairs and points on painted lines.

// Painted lines as a*x + b*y + c = 0 in metres (as vision/homography.PITCH_LINES).
export const PITCH_LINES = {
  far_touchline: ["Far touchline", [0, 1, -68]],
  near_touchline: ["Near touchline", [0, 1, 0]],
  halfway: ["Halfway line", [1, 0, -52.5]],
  left_goal_line: ["Left goal line", [1, 0, 0]],
  right_goal_line: ["Right goal line", [1, 0, -105]],
  left_box_edge: ["Left penalty box edge", [1, 0, -16.5]],
  right_box_edge: ["Right penalty box edge", [1, 0, -88.5]],
  left_six_yard_edge: ["Left 6-yard box edge", [1, 0, -5.5]],
  right_six_yard_edge: ["Right 6-yard box edge", [1, 0, -99.5]],
  box_side_far: ["Penalty box side, far", [0, 1, -54.16]],
  box_side_near: ["Penalty box side, near", [0, 1, -13.84]],
};

export const apply = (H, x, y) => {
  const w = H[2][0] * x + H[2][1] * y + H[2][2];
  return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w, w];
};

export function invert3(m) {
  const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (!det) return null;
  return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
    [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
    [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]];
}

const mul = (x, y) => x.map((row) => [0, 1, 2].map((j) => row.reduce((s, v, k) => s + v * y[k][j], 0)));

function normaliser(pts) {
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length, cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const d = pts.reduce((s, p) => s + Math.hypot(p[0] - cx, p[1] - cy), 0) / pts.length || 1;
  const k = Math.SQRT2 / d;
  return [[k, 0, -k * cx], [0, k, -k * cy], [0, 0, 1]];
}

// Image -> metres from [{image, world}] landmark pairs and [{image, line: [a, b, c]}] line points (h33 = 1,
// normal equations, Hartley-normalised). null when there are fewer than 8 constraints or they do not fix it.
export function solveImageToWorld(pairs, linePoints = []) {
  if (2 * pairs.length + linePoints.length < 8) return null;
  const ts = normaliser([...pairs.map((p) => p.image), ...linePoints.map((p) => p.image)]);
  const td = pairs.length >= 2 ? normaliser(pairs.map((p) => p.world)) : [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  const tdInv = invert3(td);
  const ap = (m, [x, y]) => [m[0][0] * x + m[0][2], m[1][1] * y + m[1][2]];
  const A = [], b = [];
  for (const p of pairs) {
    const [x, y] = ap(ts, p.image), [u, v] = ap(td, p.world);
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  for (const p of linePoints) {   // world line in normalised coordinates: td^-T l
    const [x, y] = ap(ts, p.image);
    const [l0, l1, l2] = p.line;
    const la = tdInv[0][0] * l0 + tdInv[1][0] * l1 + tdInv[2][0] * l2;
    const lb = tdInv[0][1] * l0 + tdInv[1][1] * l1 + tdInv[2][1] * l2;
    const lc = tdInv[0][2] * l0 + tdInv[1][2] * l1 + tdInv[2][2] * l2;
    A.push([la * x, la * y, la, lb * x, lb * y, lb, lc * x, lc * y]); b.push(-lc);
  }
  const M = Array.from({ length: 8 }, (_, r) => Array.from({ length: 9 }, (_, c) =>
    A.reduce((s, row, k) => s + row[r] * (c < 8 ? row[c] : b[k]), 0)));
  const scale = Math.max(...M.map((row) => Math.max(...row.slice(0, 8).map(Math.abs)))) || 1;
  for (let c = 0; c < 8; c++) {   // Gauss-Jordan with partial pivoting
    let piv = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-10 * scale) return null;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k < 9; k++) M[r][k] -= f * M[c][k];
    }
  }
  const h = M.map((row, i) => row[8] / row[i]);
  const Hn = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
  const H = mul(mul(tdInv, Hn), ts);
  return H[2][2] ? H.map((row) => row.map((v) => v / H[2][2])) : null;
}

// Mean metres the landmarks and line points miss by under H (image -> metres).
export function fitError(H, pairs, linePoints = []) {
  const e = [
    ...pairs.map((p) => { const [x, y] = apply(H, ...p.image); return Math.hypot(x - p.world[0], y - p.world[1]); }),
    ...linePoints.map((p) => { const [x, y] = apply(H, ...p.image); const [a, b, c] = p.line; return Math.abs(a * x + b * y + c) / Math.hypot(a, b); }),
  ];
  return e.length ? Math.max(...e) : null;
}
