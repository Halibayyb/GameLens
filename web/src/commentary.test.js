import { test } from "node:test";
import assert from "node:assert/strict";
import { CLIP_S, createClock, cueIndexAt, cuesFor, withCaptions } from "./commentary.js";
import { LANG, RATE, createSpeaker } from "./speech.js";

const assist = {
  pass: { passer: "Jack", receiver: "Paul" },
  options: [{ player: "Luke", played: false, ev: 0.17, arrives: 0.79, goal: 0.22, cut_out: 4 }, { player: "Paul", played: true, ev: 0.0505, arrives: 0.3183, goal: 0.1586, cut_out: 5 }],
  verdict: { key: "line", label: "Line-breaker" }, sentence: "2/3 never make it. This one sliced through 5 defenders.",
};
const textAt = (cues, t) => cues[cueIndexAt(cues, t)]?.text;
const total = CLIP_S.broadcast + CLIP_S.analytics + CLIP_S.smart;
const words = s => s.split(/\s+/).length;

test("no captions over the match clip", () => {
  const cues = cuesFor(assist);
  assert.equal(textAt(cues, 0), undefined);
  assert.equal(textAt(cues, CLIP_S.broadcast - 0.01), undefined);
});

test("the analytics clip is captioned from its analysis, in order", () => {
  const cues = cuesFor(assist), a = CLIP_S.broadcast;
  assert.equal(textAt(cues, a), "Line-breaker: Jack finds Paul.");
  assert.equal(textAt(cues, a + 3), assist.sentence);
});

test("the smart view is captioned to the end of the video", () => {
  const cues = cuesFor(assist), s = CLIP_S.broadcast + CLIP_S.analytics;
  assert.equal(textAt(cues, s), "32% arrived, 16% score.");
  assert.equal(textAt(cues, s + 4), "Incredible vision!");
  assert.equal(textAt(cues, total - 0.1), "Incredible vision!");
});

test("the closing word follows the verdict", () => {
  const last = key => { const c = cuesFor({ ...assist, verdict: { key } }); return c[c.length - 1].text; };
  assert.deepEqual(["line", "brave", "best", "smart", undefined].map(last), ["Incredible vision!", "Sensational!", "Beautiful pass!", "What a ball!", "Incredible vision!"]);
});

test("captions are contiguous from the analytics clip on", () => {
  const cues = cuesFor(assist);
  cues.slice(1).forEach((c, i) => assert.equal(c.from, cues[i].to));
});

test("every caption can be spoken inside its slot at about 2.5 words a second", () => {
  for (const c of cuesFor(assist).filter(c => c.to !== Infinity)) assert.ok(words(c.text) / (c.to - c.from) <= 2.6, `${c.text} (${words(c.text)} words in ${c.to - c.from} s)`);
});

test("a goal with no pass analysis has no captions", () => {
  assert.deepEqual(cuesFor(null), []);
  assert.deepEqual(cuesFor({ pass: null, options: [] }), []);
});

test("the clock notifies subscribers of the time and the play state, and stops after unsubscribe", () => {
  const c = createClock(), seen = [];
  const off = c.subscribe(() => seen.push([c.get(), c.isPlaying()]));
  c.set(3.5); c.setPlaying(true); c.setPlaying(true); off(); c.set(4);
  assert.deepEqual(seen, [[3.5, false], [3.5, true]]);
  assert.equal(c.get(), 4);
});

function fakeSynth() {
  const log = [];
  class Utterance { constructor(text) { this.text = text; } }
  return { log, synth: { cancel: () => log.push("cancel"), speak: u => log.push(["speak", u.text, u.lang, u.rate]) }, Utterance };
}

test("speak drops what is still being said, then says the new text in English at the set rate", () => {
  const { log, synth, Utterance } = fakeSynth();
  createSpeaker(synth, Utterance).speak("Paul scores.");
  assert.deepEqual(log, ["cancel", ["speak", "Paul scores.", LANG, RATE]]);
});

test("cancel stops the voice, and with no speech support nothing throws", () => {
  const { log, synth, Utterance } = fakeSynth();
  createSpeaker(synth, Utterance).cancel();
  assert.deepEqual(log, ["cancel"]);
  const none = createSpeaker(null, null);
  none.speak("hello"); none.cancel();
});

test("the commentator's line takes the analytics clip, the smart view keeps its captions", async () => {
  const { withCommentary } = await import("./commentary.js");
  const cues = withCommentary(cuesFor(assist), "The deadlock is broken! Jack slices 5 defenders open, and Paul finishes.");
  const a = CLIP_S.broadcast, s = a + CLIP_S.analytics;
  assert.equal(cues[0].from, a); assert.equal(cues[0].to, s); assert.equal(cues[0].commentary, true);
  assert.equal(textAt(cues, a + 4), cues[0].text);
  assert.equal(textAt(cues, s), "32% arrived, 16% score.");
  assert.deepEqual(withCommentary(cuesFor(assist), null), cuesFor(assist));
});

test("speakMoment plays the Azure audio, once fetched per moment, and falls back to the browser voice", async () => {
  const said = [], played = [];
  const synth = { cancel() {}, speak(u) { said.push(u.text); u.onend?.(); } };
  function Utterance(text) { this.text = text; }
  function Audio(url) { this.url = url; this.play = async () => { played.push(url); this.onended?.(); }; this.pause = () => {}; }
  let fetches = 0;
  const speaker = createSpeaker(synth, Utterance, async (id) => { fetches += 1; return id === "e11" ? { url: "blob:e11" } : { text: "From the API." } }, Audio);
  let ended = 0;
  await speaker.speakMoment("e11", "fallback", () => { ended += 1; });
  await speaker.speakMoment("e11", "fallback");
  assert.deepEqual(played, ["blob:e11", "blob:e11"]); assert.equal(fetches, 1); assert.equal(ended, 1);
  await speaker.speakMoment("e17", "fallback");
  assert.deepEqual(said, ["From the API."]);
});

test("a line cancelled while its audio loads is never played", async () => {
  const played = [];
  function Audio(url) { this.play = async () => { played.push(url); }; this.pause = () => {}; }
  let release;
  const speaker = createSpeaker({ cancel() {}, speak() {} }, function U() {}, () => new Promise((r) => { release = r; }), Audio);
  const pending = speaker.speakMoment("e22", "x");
  speaker.cancel();
  release({ url: "blob:e22" });
  await pending;
  assert.deepEqual(played, []);
});

test("each language is fetched and spoken on its own, the browser voice with that language's accent", async () => {
  const fetched = [], spoken = [];
  const synth = { cancel() {}, speak(u) { spoken.push([u.text, u.lang]); } };
  function Utterance(text) { this.text = text; }
  const speaker = createSpeaker(synth, Utterance, async (id, lang) => { fetched.push(`${id}|${lang}`); return { text: `${lang}:line`, lang }; }, null);
  await speaker.speakMoment("e11", "x", undefined, "th");
  await speaker.speakMoment("e11", "x", undefined, "th");
  await speaker.speakMoment("e11", "x", undefined, "fr");
  assert.deepEqual(fetched, ["e11|th", "e11|fr"]);
  assert.deepEqual(spoken, [["th:line", "th-TH"], ["th:line", "th-TH"], ["fr:line", "fr-FR"]]);
});

test("the server's three captions take the analytics clip and the smart view, each voiced by its part", () => {
  const caps = [{ part: "line", text: "Rompe líneas de Jack" }, { part: "smart", text: "32% llegan, 16% gol." }, { part: "finish", text: "¡Qué visión!" }];
  const cues = withCaptions(cuesFor(assist), caps), a = CLIP_S.broadcast, s = a + CLIP_S.analytics;
  assert.deepEqual(cues.map(c => [c.from, c.to, c.text, c.part, c.commentary]),
    [[a, s, "Rompe líneas de Jack", "line", true], [s, s + 3.5, "32% llegan, 16% gol.", "smart", true], [s + 3.5, Infinity, "¡Qué visión!", "finish", true]]);
  assert.equal(withCaptions(cuesFor(assist), [caps[0]])[1].text, "32% arrived, 16% score.");   // only the line: the page writes the rest
  assert.deepEqual(withCaptions(cuesFor(assist), null), cuesFor(assist));
});

test("each caption part is fetched once per language", async () => {
  const fetched = [];
  const speaker = createSpeaker({ cancel() {}, speak() {} }, function U(t) { this.text = t; },
    async (id, lang, part) => { fetched.push([id, lang, part]); return { text: "x", lang }; }, null);
  await speaker.speakMoment("e11", "x", undefined, "th", "smart");
  await speaker.speakMoment("e11", "x", undefined, "th", "smart");
  await speaker.speakMoment("e11", "x", undefined, "th", "finish");
  await speaker.speakMoment("e11", "x", undefined, "th");
  assert.deepEqual(fetched, [["e11", "th", "smart"], ["e11", "th", "finish"], ["e11", "th", undefined]]);
});

test("the speech address changes with the caption, so the browser never replays an older script", async () => {
  const { textVersion } = await import("./speech.js");
  assert.equal(textVersion("Incredible vision!"), textVersion("Incredible vision!"));
  assert.notEqual(textVersion("Incredible vision!"), textVersion("Riverside Rovers strike first!"));
  const fetched = [];
  const speaker = createSpeaker({ cancel() {}, speak() {} }, function U(t) { this.text = t; },
    async (id, lang, part, text) => { fetched.push(text); return { text, lang }; }, null);
  await speaker.speakMoment("e11", "old line", undefined, "en");
  await speaker.speakMoment("e11", "new line", undefined, "en");
  assert.deepEqual(fetched, ["old line", "new line"]);
});
