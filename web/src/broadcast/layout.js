// The decision overlay as pure data: which players get a glass sphere, the arrows from the passer, and where each label
// sits (placeLabel collision). Computed once per frozen frame from props, so the reveal only toggles visibility.
// Ported from good-assist-proto/src/main.js (addArrow, landingPath, reservePath, addLandingArrow, pitchText).
import { toProto, centre, project, FIG } from './projection.js';
import { fmtPct, placeLabel, figBoxes, standBox, trim } from './labels.js';
import { shirtNo } from './figures.js';
import { ballAt } from '../motion.js';

const LBL_CHAR = 10.2, LBL_H = 20;   // .bc-lbl is 17px monospace; `ls` scales both when the labels are enlarged on phones
export const SHOWN_OTHERS = 3;       // the played pass plus the top 3 other options by expected value, unless the data says which
const LETTERS = 'ABCDE';
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

// The played pass drawn as it goes: the ball's own path from the passer to where it lands, leaving a gap at the ball and at
// the landing mark. A cross ("air") flies on the ball's height curve and ends at head height, its shadow on the grass.
// Returns the screen geometry, or null when the ball has no path. ctx = { track, assist, shot } as for ballAt.
function landingPath(ctx, attackRight, flip, steps = 40) {
  const pass = ctx.assist.pass, from = pass.tau, to = pass.reception_tau;
  if (to == null || to <= from) return null;
  const samples = Array.from({ length: steps + 1 }, (_, i) => ballAt(from + (to - from) * i / steps, ctx)).filter(b => b.p);
  if (samples.length < 2) return null;
  const scr = b => project(...toProto(b.p, attackRight), flip);   // [x, y, scale] on the grass
  const onGrass = samples.map(b => scr(b).slice(0, 2));
  const [lx, ly, lsc] = scr(samples[samples.length - 1]);
  const k = lsc * FIG;
  let kept;
  const air = !!pass.aerial;
  if (air) {
    // heights scaled so the arrow's tip is at head height above the landing mark (where the ball meets the head)
    const head = 80 * k, zk = head / ((pass.arrive_z || 1) * lsc);
    const inAir = samples.map(b => { const [px, py, psc] = scr(b); return [px, py - b.z * zk * psc]; });
    kept = inAir.filter((p, i) => dist(p, inAir[0]) > 10 && dist(p, inAir[inAir.length - 1]) > 12);
  } else {
    kept = onGrass.filter(p => dist(p, onGrass[0]) > 10 && dist(p, [lx, ly]) > 30 * k + 4);
  }
  return kept.length > 1 ? { lx, ly, k, air, onGrass, kept } : null;
}

// The options the pitch draws and their letters: the played pass plus the data's `shown` ones (else the top 3 others by
// expected value), plus `include` (a picked player_id that is not among them). Letters rank them by expected value, A first.
// The pitch labels and the verdict panel's table both call this, so they always agree.
export function shownOptions(assist, include = null) {
  const opts = assist.options || [], pass = assist.pass;
  const playedOpt = opts.find(o => o.played) || opts.find(o => o.player_id === pass.receiver_id);
  const ranked = opts.filter(o => o !== playedOpt).sort((a, b) => b.ev - a.ev);
  const flagged = ranked.filter(o => o.shown);
  const others = (flagged.length ? flagged : ranked.slice(0, SHOWN_OTHERS)).slice();
  const picked = include && include !== playedOpt?.player_id && !others.some(o => o.player_id === include) && opts.find(o => o.player_id === include);
  if (picked) others.push(picked);
  const letters = new Map([...others, playedOpt].filter(Boolean).sort((a, b) => b.ev - a.ev).map((o, i) => [o.player_id, LETTERS[i]]));
  return { playedOpt, others, letters };
}

// posOf(playerId) -> proto position at the frozen frame (or null); everyone = all proto positions, for figure boxes
// include: a player_id (the picked option) that gets a sphere and arrow even when it is not among the top few
// ctx: what ballAt needs ({ track, assist, shot }); with pass.show_landing the played pass is drawn to where the ball lands
export function buildReveal({ assist, attackRight, flip = false, posOf, everyone, ls = 1, include = null, ctx = null }) {
  const pass = assist.pass;
  const { playedOpt, others, letters } = shownOptions(assist, include);
  const letter = letters;
  const at = o => posOf(o.player_id) || toProto(o.location, attackRight);
  const from = posOf(pass.passer_id) || toProto(pass.start, attackRight);
  const cFrom = centre(from, flip);
  const rings = [{ id: pass.passer_id, kind: 'passer', c: cFrom, opt: null }];
  [...others, playedOpt].filter(Boolean).forEach(o => rings.push({ id: o.player_id, kind: o.played || o === playedOpt ? 'played' : 'opt', c: centre(at(o), flip), opt: o }));

  const boxes = rings.map(r => [r.c.x - r.c.r, r.c.y - r.c.r, r.c.x + r.c.r, r.c.y + r.c.r]);
  const land = pass.show_landing && ctx ? landingPath(ctx, attackRight, flip) : null;
  if (land) {   // the other labels keep off the played arrow and its landing mark
    boxes.push([land.lx - 30 * land.k, land.ly - 9 * land.k, land.lx + 30 * land.k, land.ly + 9 * land.k]);
    land.kept.forEach(([x, y]) => boxes.push([x - 9, y - 9, x + 9, y + 9]));
  }
  const obstacles = [...figBoxes(everyone, flip), ...boxes, standBox()], placed = [];
  const place = (x, y, text, dir) => placeLabel(x, y, text.length * LBL_CHAR * ls + 6, LBL_H * ls, LBL_H * ls, dir, obstacles, placed);
  const textOf = r => `${letter.get(r.id)} · #${shirtNo(r.id)} · ${fmtPct(r.opt.arrives)}`;
  const arrows = [], labels = [];
  const optionRings = rings.filter(r => r.opt && r.kind !== 'played'), playedRing = rings.find(r => r.kind === 'played');
  for (const r of optionRings) {   // dashed arrow and label above the sphere, nudged clear of players, spheres and other labels
    const [a, b] = trim([cFrom.x, cFrom.y], [r.c.x, r.c.y], cFrom.r + 2, r.c.r + 5);
    arrows.push({ id: r.id, played: false, x1: a[0], y1: a[1], x2: b[0], y2: b[1] });
    const text = textOf(r);
    const [x, y] = place(r.c.x, r.c.y - r.c.r - 8, text, -1);
    labels.push({ id: r.id, played: false, x, y, text });
  }
  let landing = null;
  if (playedRing) {
    const text = textOf(playedRing);
    if (land) {   // the label sits below the landing mark; a dotted line shows the receiver running onto it
      landing = { ...land, from: { x: playedRing.c.x, y: playedRing.c.foot } };
      const [x, y] = place(land.lx, land.ly + 9 * land.k + 22, text, 1);
      labels.push({ id: playedRing.id, played: true, x, y, text });
      arrows.push({ id: playedRing.id, played: true, landing: true });
    } else {      // sphere to sphere, label below the receiver's feet
      const [a, b] = trim([cFrom.x, cFrom.y], [playedRing.c.x, playedRing.c.y], cFrom.r + 2, playedRing.c.r + 8);
      arrows.push({ id: playedRing.id, played: true, x1: a[0], y1: a[1], x2: b[0], y2: b[1] });
      const [x, y] = place(playedRing.c.x, playedRing.c.foot + 30 * playedRing.c.sc + 8, text, 1);
      labels.push({ id: playedRing.id, played: true, x, y, text });
    }
  }
  return { rings, arrows, labels, landing, letters: Object.fromEntries(letter) };
}
