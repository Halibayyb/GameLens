import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { cueIndexAt, cuesFor, withCaptions } from "../commentary.js";
import { getLanguages } from "../api.js";
import { setLang, useLang } from "../lang.js";
import { createSpeaker, speechSupported } from "../speech.js";
import "./commentary.css";

// The text-to-speech commentary, under the video and above the goal stepper: a sound button, then the closed-caption line
// the voice reads. All three captions (the commentator's line over the analytics clip, the odds and the closing word over
// the smart view) come from the API in the commentary language and are read by that language's Azure voice; the browser's
// own voice stands in only without Azure or before the captions arrive (see speech.js). The sound is on until the visitor turns it off,
// and the choice is remembered. The captions and the voice run only at a live goal stop (the page started the video itself),
// and only over the analytics clip and the smart view; a video the visitor plays by hand gets neither.
// Next to the CC badge, the commentary language picker (shown only when the API can translate): the captions and their
// voice follow the choice.
//   live   useLive(): its `state` says whether the live match is on and halted at a goal
//   assist the open goal's assist block (the captions are written from it)
//   clock  createClock() the player writes the video's time and play state to
//   momentId, captions   the open goal's id and its captions (the API's commentary.captions: line, odds, closing word, in the
//                        commentary language), when they have arrived; all three are spoken by the language's Azure voice
const KEY = "matchday.commentarySound";
const loadSound = () => { try { return localStorage.getItem(KEY) !== "0"; } catch { return true; } };   // on unless turned off

export default function CommentaryCC({ live, assist, clock, momentId, captions }) {
  const cues = useMemo(() => withCaptions(cuesFor(assist), captions), [assist, captions]);
  const lang = useLang();
  const atGoal = live.state === "goal";
  const idx = useSyncExternalStore(clock.subscribe, () => (atGoal ? cueIndexAt(cues, clock.get()) : -1));
  const playing = useSyncExternalStore(clock.subscribe, clock.isPlaying);
  const can = useMemo(speechSupported, []);
  const [sound, setSound] = useState(loadSound);
  const [languages, setLanguages] = useState(null);   // [{ code, name }] from the API, or null until loaded
  useEffect(() => {
    let alive = true;
    getLanguages().then((r) => { if (alive && r.translated) setLanguages(r.languages); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const speaker = useRef(null);
  speaker.current ||= createSpeaker();
  const on = live.state === "running" || live.state === "paused" || atGoal;
  const text = idx >= 0 ? cues[idx].text
    : atGoal ? "Commentary begins with the analytics."
    : on ? "Waiting for the next goal…"
    : "Commentary starts with Start live match.";

  // say the caption on show while the video plays; any other moment (paused, scrubbed away, muted, goal over) is silence
  useEffect(() => {
    if (!(can && sound && playing && idx >= 0)) { speaker.current.cancel(); return; }
    if (cues[idx].commentary && momentId) speaker.current.speakMoment(momentId, cues[idx].text, undefined, lang, cues[idx].part);   // the commentator's voice
    else speaker.current.speak(cues[idx].text);
  }, [can, sound, playing, idx, cues, momentId, lang]);
  useEffect(() => () => speaker.current.cancel(), []);

  const toggle = () => {
    const next = !sound;
    setSound(next);
    try { localStorage.setItem(KEY, next ? "1" : "0"); } catch { /* storage blocked: the choice just is not remembered */ }
  };
  const label = !can ? "Commentary sound is not supported in this browser" : sound ? "Commentary sound on: turn off" : "Commentary sound off: turn on";
  const speaking = idx >= 0;
  return (
    <div className={`cc ${speaking ? "speaking" : ""}`}>
      <button type="button" className="cc-sound" aria-pressed={can && sound} aria-label={label} title={label} disabled={!can} onClick={toggle}>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 8v4h3l4 3.5v-11L6 8z" />
          {can && sound ? <><path d="M13 7.5a3.6 3.6 0 0 1 0 5" /><path d="M15.5 5a7 7 0 0 1 0 10" /></> : <path d="M13.5 7.5l4 5M17.5 7.5l-4 5" />}
        </svg>
      </button>
      <span className="cc-tag">Commentary</span>
      <p className="cc-text" role="status" aria-live="polite" aria-label="Live commentary captions">{text}</p>
      {languages && (
        <select className="cc-lang" value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Commentary language">
          {languages.map((l) => <option key={l.code} value={l.code} lang={l.code}>{l.name}</option>)}
        </select>
      )}
      <span className="cc-badge" aria-hidden="true">CC</span>
    </div>
  );
}
