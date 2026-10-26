// Shared wording helpers, ported from good-assist-proto/src/scoring.js and main.js.
export function fmtPct(p) {
  return (p < 0.1 ? (p * 100).toFixed(1) : Math.round(p * 100)) + "%";
}
// the prototype's order, with the left/right centre-back and centre-mid roles this API uses slotted in
export const POS_ORDER = ["GK", "RB", "RCB", "CB", "LCB", "LB", "DM", "RM", "RCM", "CM", "LCM", "LM", "AM", "RW", "LW", "ST"];
