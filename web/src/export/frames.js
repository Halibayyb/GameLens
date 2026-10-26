// Frame export for the Broadcast replay, ported from good-assist-proto/src/main.js (keyFrames, addBallArrow, exportFrames):
// the key moments of a goal as PNGs in one ZIP, plus frames.txt with the options at the pass. Nothing leaves the browser.
// Pure helpers here; BroadcastPitch hands over the two things that touch the page (show a frame, hand back its SVG).
import { fmtPct } from "../broadcast/labels.js";
import { shirtNo } from "../broadcast/figures.js";
import { pageCss, svgToPng } from "./snapshot.js";
import { zip } from "./zip.js";

export const FRAME = 0.04;                              // one broadcast frame (1/25 s)
const LOOK_S = 0.2, ARROW_PX = 64;                      // how far ahead the ball's direction is read; the arrow's length on the pitch (view units)
const SVG_NS = "http://www.w3.org/2000/svg";
const INK = "#1f2430";

export const frameOf = rel => Number((Math.round(rel / FRAME) * FRAME).toFixed(2));
export const fmtTau = rel => `${rel < 0 ? "−" : "+"}${Math.abs(rel).toFixed(2)} s`;

// The frames to save, as [{ rel (seconds from the pass), slug, text }], snapped to the frame grid and kept inside [min, max].
// g: { min, max, pre ({ pass, recv } relative to the pass, or null), flight, carry, tGoal } all relative to the pass.
export function keyFrames(g) {
  const at = (rel, up) => Math.max(g.min, Math.min(g.max, frameOf(up ? Math.ceil(rel / FRAME) * FRAME : rel)));
  const list = [{ rel: g.min, slug: "lead-in", text: "the replay starts" }];
  if (g.pre) list.push({ rel: g.pre.pass, slug: "ball-played-in", text: "the ball is played in to the passer" },
    { rel: g.pre.recv, slug: "first-touch", text: "the passer's first touch" });
  list.push({ rel: 0, slug: "the-pass", text: "the scored frame: the moment of the pass, with the analysis" },
    { rel: g.flight / 2, slug: "ball-in-flight", text: "the ball is on its way" });
  if (g.carry) list.push({ rel: g.flight, slug: "receives", text: "the receiver controls the ball" });
  list.push({ rel: g.flight + g.carry, slug: "shot", text: g.carry ? "the shot" : "the receiver finishes first time" },
    { rel: g.tGoal, slug: "goal", text: "the ball crosses the line", up: true });
  const seen = new Set();
  return list.map(f => ({ ...f, rel: at(f.rel, f.up) })).filter(f => !seen.has(f.rel) && seen.add(f.rel));
}

// A fixed-length arrow (direction, not speed) from the ball towards where it goes next, drawn into the clone only.
// from / to: the ball's screen points now and LOOK_S later. None when the ball is at rest.
export function addBallArrow(svg, from, to) {
  const len = Math.hypot(to[0] - from[0], to[1] - from[1]);
  if (len < 3) return;
  const ux = (to[0] - from[0]) / len, uy = (to[1] - from[1]) / len;
  const line = { x1: from[0] + ux * 10, y1: from[1] + uy * 10, x2: from[0] + ux * (10 + ARROW_PX), y2: from[1] + uy * (10 + ARROW_PX), "stroke-linecap": "round" };
  const g = document.createElementNS(SVG_NS, "g");
  const draw = attrs => { const l = document.createElementNS(SVG_NS, "line"); Object.entries({ ...line, ...attrs }).forEach(([k, v]) => l.setAttribute(k, v)); g.append(l); };
  draw({ stroke: "#f4f4ef", "stroke-opacity": 0.85, "stroke-width": 9 });   // halo, so it reads on stripes
  draw({ stroke: INK, "stroke-width": 4, "marker-end": "url(#bc-head-played)" });
  svg.append(g);
}
export { LOOK_S };

// frames.txt: the options at the pass, lettered as on the pitch (ranked by expected value, the shown ones only)
export function framesText({ title, lines, options, letters }) {
  const opts = options.filter(o => letters[o.player_id]).sort((a, b) => letters[a.player_id].localeCompare(letters[b.player_id])).map(o =>
    `${letters[o.player_id]}  ${o.player} (#${shirtNo(o.player_id)})${o.played ? " (the pass played)" : ""}  arrives ${fmtPct(o.arrives)}  goal ${fmtPct(o.goal)}  cut out ${o.cut_out}`);
  return [title, "Synthetic data (no real players, clubs or matches). Percentages are estimated.",
    "Times are seconds around the pass: +0.00 s is the pass, the frame that is scored.", "", ...lines, "",
    "Options at the pass (ranked by expected value):", ...opts, ""].join("\n");
}

function download(bytes, name) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// Renders each key frame and saves the ZIP.
//   frames       keyFrames(...) output
//   show(rel)    Promise: the page now shows that frame (relative to the pass)
//   snapshot()   the pitch <svg> as drawn right now (cloned and cleaned here), plus { ball: [from, to] } screen points or null
//   caption(f)   the strip's first line;  textFile  frames.txt content builder (given the listing lines);  zipName
//   onProgress(i, n)  progress for the button
export async function exportFrames({ frames, show, snapshot, caption, textFile, zipName, onProgress, isStale = () => false, width = 2000, height = 1034 }) {
  const css = pageCss(), sub = "Synthetic data · positions and percentages are estimated";
  const files = [], lines = [];
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    onProgress?.(i + 1, frames.length);
    await show(f.rel);
    if (isStale()) return;
    const { svg, ball } = snapshot();
    const clone = svg.cloneNode(true);
    clone.querySelector(".bc-flash")?.remove();                   // the GOAL! card would hide the action; the caption says it
    if (f.rel !== 0 && ball) addBallArrow(clone, ball[0], ball[1]);
    const name = `${String(i + 1).padStart(2, "0")}_${f.slug}_${f.rel < 0 ? "-" : "+"}${Math.abs(f.rel).toFixed(2)}s.png`;
    files.push({ name: `pitch/${name}`, data: await svgToPng(clone, { width, height, css, caption: caption(f), sub }) });
    lines.push(`${String(i + 1).padStart(2, "0")}  ${fmtTau(f.rel).padStart(8)}  ${f.slug.padEnd(15)} ${f.text}`);
  }
  files.push({ name: "frames.txt", data: new TextEncoder().encode(textFile(lines)) });
  download(zip(files), zipName);
}
