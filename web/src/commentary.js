import { fmtPct } from "./broadcast/labels.js";

// The text-to-speech commentary: the captions the voice reads (speech.js), written from the goal's own
// analysis and timed to the goal video. Each caption is short enough to be said in its slot at speech.js's RATE.
//
// A goal's video is its three clips joined (scripts/merge_goal_videos.py): the match clip, the analytics clip, the smart view.
// The commentary covers the last two only, so the times below must match the lengths of those clips.
export const CLIP_S = { broadcast: 8, analytics: 6, smart: 5 };
const A = CLIP_S.broadcast, S = A + CLIP_S.analytics;   // where the analytics clip and the smart view start, in video seconds

// The closing exclamation, by the pass's verdict (the API's verdict.key); it is the goal's last caption and is spoken like the rest.
const FINISH = { line: "Incredible vision!", brave: "Sensational!", best: "Beautiful pass!", smart: "What a ball!" };
export const finishWord = verdict => FINISH[verdict?.key] || "Incredible vision!";

// The captions for one goal: [{ from, to, text }] in video seconds, or [] when the goal has no pass analysis.
export function cuesFor(assist) {
  const pass = assist?.pass, played = assist?.options?.find(o => o.played);
  if (!pass || !played) return [];
  const label = assist.verdict?.label;
  return [
    { from: A, to: A + 2, text: `${label ? `${label}: ` : ""}${pass.passer} finds ${pass.receiver}.` },
    { from: A + 2, to: S, text: assist.sentence || `${played.cut_out} defenders cut out.` },
    { from: S, to: S + 3.5, text: `${fmtPct(played.arrives)} arrived, ${fmtPct(played.goal)} score.` },
    { from: S + 3.5, to: Infinity, text: finishWord(assist.verdict) },
  ];
};

// The cues with the commentator's line (the API's text.commentary: situation, momentum and pass, from the data) over the
// whole analytics clip in place of its two short captions; `commentary: true` marks the cue the Azure voice reads.
// Without a line the cues are unchanged.
export function withCommentary(cues, line) {
  if (!line || cues.length < 2) return cues;
  return [{ from: cues[0].from, to: cues[1].to, text: line, commentary: true }, ...cues.slice(2)];
}

// The cues with the server's captions (the API's commentary.captions: [{ part, text }], line / smart / finish, already in
// the commentary language): the line over the analytics clip, the odds and the closing word over the smart view, at the
// times cuesFor() gives them. Every one is `commentary: true` with its `part`, so the Azure voice of that language reads it.
// Without the three captions this is withCommentary() with the first one's text.
export function withCaptions(cues, captions) {
  const by = Object.fromEntries((captions || []).map(c => [c.part, c.text]));
  if (!by.line || !by.smart || !by.finish || cues.length < 4) return withCommentary(cues, by.line || null);
  const voiced = (cue, part) => ({ ...cue, text: by[part], commentary: true, part });
  return [{ ...voiced(cues[0], "line"), to: cues[1].to }, voiced(cues[2], "smart"), voiced(cues[3], "finish")];
}

// Index of the caption on show at video second `t`, or -1 (the match clip, or nothing to say).
export const cueIndexAt = (cues, t) => cues.findIndex(c => t >= c.from && t < c.to);

// The video's current time and whether it is playing, shared without re-rendering the page four times a second: the player
// calls set() / setPlaying(), and the caption bar subscribes through useSyncExternalStore to the small values it needs.
export function createClock() {
  let t = 0, playing = false;
  const subs = new Set();
  const emit = () => subs.forEach(f => f());
  return {
    get: () => t,
    set(v) { t = v || 0; emit(); },
    isPlaying: () => playing,
    setPlaying(on) { if (playing !== !!on) { playing = !!on; emit(); } },
    subscribe(f) { subs.add(f); return () => subs.delete(f); },
  };
}
