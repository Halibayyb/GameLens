// Player poses ported from good-assist-proto/index.html (<symbol id="fig-*">), one array of parts per pose.
// Figures are line-art, feet at (0,0), facing right, ~91 units tall; colours come from --kit/--short/--sock on each <use>.
//   ['limb', d, colour, outerWidth, innerWidth]  dark outline stroke under a coloured stroke
//   ['body', d, colour]                          filled shirt / shorts, outlined
//   ['head', cx, cy]  ['hair', d]
// run / run2 / stand make the stride; crouch, slide and head (jump) are ported but unused: tracking has no jump or slide data.
export const POSES = {
  run: [
    ['limb', 'M-7,-67 L-16,-57 L-11,-47', 'kit', 7.5, 4.2],
    ['limb', 'M-3,-40 L-12,-22 L-25,-9', 'sock', 9, 5.5],
    ['body', 'M-10,-73 Q0,-77 10,-73 L9,-47 L-9,-47 Z', 'kit'],
    ['body', 'M-9,-48 L9,-48 L11,-35 L-11,-35 Z', 'short'],
    ['limb', 'M3,-38 L12,-24 L13,-1', 'sock', 9, 5.5],
    ['limb', 'M7,-67 L17,-58 L25,-63', 'kit', 7.5, 4.2],
    ['head', 2, -83],
    ['hair', 'M-5.5,-84 Q-3,-92 3,-91 Q9,-90 9.5,-84 Q4,-87 -5.5,-84 Z'],
  ],
  run2: [
    ['limb', 'M7,-67 L14,-57 L9,-47', 'kit', 7.5, 4.2],
    ['limb', 'M-3,-40 L-7,-21 L-9,-1', 'sock', 9, 5.5],
    ['body', 'M-10,-73 Q0,-77 10,-73 L9,-47 L-9,-47 Z', 'kit'],
    ['body', 'M-9,-48 L9,-48 L11,-35 L-11,-35 Z', 'short'],
    ['limb', 'M3,-38 L14,-30 L22,-15', 'sock', 9, 5.5],
    ['limb', 'M-7,-67 L-1,-58 L8,-62', 'kit', 7.5, 4.2],
    ['head', 2, -83],
    ['hair', 'M-5.5,-84 Q-3,-92 3,-91 Q9,-90 9.5,-84 Q4,-87 -5.5,-84 Z'],
  ],
  stand: [
    ['limb', 'M-8,-67 L-12,-52 L-11,-42', 'kit', 7.5, 4.2],
    ['limb', 'M-3,-40 L-5,-20 L-6,-1', 'sock', 9, 5.5],
    ['limb', 'M3,-40 L5,-20 L6,-1', 'sock', 9, 5.5],
    ['body', 'M-10,-73 Q0,-77 10,-73 L9,-47 L-9,-47 Z', 'kit'],
    ['body', 'M-9,-48 L9,-48 L11,-35 L-11,-35 Z', 'short'],
    ['limb', 'M8,-67 L12,-52 L11,-42', 'kit', 7.5, 4.2],
    ['head', 1, -83],
    ['hair', 'M-5.5,-84 Q-3,-92 2,-91 Q8,-90 8.5,-84 Q3,-87 -5.5,-84 Z'],
  ],
  head: [
    ['limb', 'M-7,-67 L-19,-63 L-27,-71', 'kit', 7.5, 4.2],
    ['limb', 'M-3,-40 L-11,-27 L-21,-15', 'sock', 9, 5.5],
    ['body', 'M-12,-72 Q-2,-77 8,-72 L8,-47 L-10,-47 Z', 'kit'],
    ['body', 'M-10,-48 L8,-48 L11,-35 L-11,-35 Z', 'short'],
    ['limb', 'M3,-38 L10,-22 L3,-8', 'sock', 9, 5.5],
    ['limb', 'M7,-67 L19,-70 L27,-79', 'kit', 7.5, 4.2],
    ['head', 6, -85],
    ['hair', 'M-1.5,-86 Q1,-94 7,-93 Q13,-92 13.5,-86 Q8,-89 -1.5,-86 Z'],
  ],
  crouch: [
    ['limb', 'M-6,-56 L-13,-45 L-9,-36', 'kit', 7.5, 4.2],
    ['limb', 'M-3,-30 L-12,-17 L-7,-1', 'sock', 9, 5.5],
    ['body', 'M-8,-62 Q2,-67 12,-60 L8,-36 L-9,-36 Z', 'kit'],
    ['body', 'M-10,-37 L8,-37 L10,-25 L-11,-25 Z', 'short'],
    ['limb', 'M4,-28 L14,-16 L10,-1', 'sock', 9, 5.5],
    ['limb', 'M9,-58 L19,-50 L26,-54', 'kit', 7.5, 4.2],
    ['head', 9, -71],
    ['hair', 'M1.5,-72 Q4,-80 10,-79 Q16,-78 16.5,-72 Q11,-75 1.5,-72 Z'],
  ],
  slide: [
    ['limb', 'M-8,-38 L-17,-25 L-22,-11', 'kit', 7.5, 4.2],
    ['limb', 'M-1,-14 L9,-22 L19,-11', 'sock', 9, 5.5],
    ['body', 'M-16,-44 Q-8,-50 1,-42 L5,-20 L-9,-17 Z', 'kit'],
    ['body', 'M-10,-22 L5,-22 L8,-9 L-9,-8 Z', 'short'],
    ['limb', 'M1,-12 L17,-8 L32,-5', 'sock', 9, 5.5],
    ['limb', 'M-3,-41 L9,-49 L22,-53', 'kit', 7.5, 4.2],
    ['head', -11, -53],
    ['hair', 'M-18.5,-54 Q-16,-62 -10,-61 Q-4,-60 -3.5,-54 Q-9,-57 -18.5,-54 Z'],
  ],
};
export const OUTLINE = '#262b36';

// Stride: one stride = 4 poses, advanced every STEP_MS.
export const STRIDE = ['run', 'stand', 'run2', 'stand'];
export const STEP_MS = 90;
const MOVING = 1.0;   // m/s: below this a player stands (tracking jitter is well under it)

// Kits belong to teams (mono theme, from good-assist-proto): home dark navy-grey, away white; keepers a paler tone.
export const KITS = {
  home: { shirt: '#3d4760', shorts: '#2a3144', sock: '#3d4760' },
  away: { shirt: '#ffffff', shorts: '#e9ebef', sock: '#ffffff' },
  homeGk: { shirt: '#7a869d', shorts: '#4f596e', sock: '#7a869d' },
  awayGk: { shirt: '#b4bac6', shorts: '#8a91a0', sock: '#b4bac6' },
  ref: { shirt: '#c4a000', shorts: '#1a1a1a', sock: '#c4a000' },
};
// "H07" / "A01" -> kit; "01" is the goalkeeper. Referee uses the leftover kit.
export const kitOf = (id, team) => {
  if (team === "ref") return KITS.ref;
  return KITS[team + (/^[HA]0?1$/.test(id) ? 'Gk' : '')] || KITS.home;
};
export const shirtNo = id => String(+String(id).replace(/\D/g, '') || '');

// pose + facing sign for a player moving with velocity (vx, vy) in proto units/s; `ms` drives the stride, `seed` offsets it
export function poseFor(speed, ms, seed, frozen) {
  if (speed < MOVING) return { pose: 'stand', bob: 0 };
  if (frozen) return { pose: 'run', bob: 0 };
  const k = ms + seed;
  return { pose: STRIDE[Math.floor(k / STEP_MS) % STRIDE.length], bob: -2.2 * Math.abs(Math.sin(Math.PI * k / (2 * STEP_MS))) };
}
