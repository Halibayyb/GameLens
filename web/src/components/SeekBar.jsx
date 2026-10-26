import { useRef } from "react";
import { FRAME, fmtTau } from "../export/frames.js";
import "./seekbar.css";

// Replay controls under the Broadcast pitch, ported from good-assist-proto (seekbar): start, previous / next frame
// (1/25 s), play / pause, the pass, speeds, and a slider with marks. All times are seconds relative to
// the pass (the scored frame is 0). The page decides what each control does; this only draws them.
//
// Props
//   min, max, rel        the range and the current time, relative to the pass
//   marks                [{ rel, text, side: "c" | "l" | "r", title }] drawn under the slider
//   playing, speed       state of the play button and the speed group
//   scrubbed             the analysis is hidden because the replay is away from the pass: shows the note
//   onStart, onPrev, onNext, onPass, onPlay, onSpeed(s), onScrub(rel, dragging), onJump(+1 | -1)
export default function SeekBar({ min, max, rel, marks, playing, speed, scrubbed, hasPass, onStart, onPrev, onNext, onPass, onPlay, onSpeed, onScrub, onJump }) {
  const dragging = useRef(false);
  const span = max - min || 1;
  return (
    <div className="seek" role="group" aria-label="Replay controls">
      <div className="seek-row">
        <button type="button" className="seek-btn" aria-label="Back to the start of the replay" title="Start" onClick={onStart}>⏮</button>
        <button type="button" className="seek-btn" aria-label="Previous frame" title="Previous frame" onClick={onPrev}>◀|</button>
        <button type="button" className="seek-btn" aria-label={playing ? "Pause the replay" : "Play the replay"} title="Play / pause" onClick={onPlay}>{playing ? "❚❚" : "▶"}</button>
        <button type="button" className="seek-btn" aria-label="Next frame" title="Next frame" onClick={onNext}>|▶</button>
        <button type="button" className="seek-btn" aria-label={hasPass ? "Jump to the scored frame, the moment of the pass" : "Jump to the moment of the shot"} title={hasPass ? "The scored frame" : "The shot"} onClick={onPass}>◆</button>
        <div className="seek-speed" role="group" aria-label="Replay speed">
          {[1, 0.5, 0.25].map(s => <button key={s} type="button" aria-pressed={speed === s} onClick={() => onSpeed(s)}>{s}×</button>)}
        </div>
        <output className="seek-time" htmlFor="seek-range">{fmtTau(rel)}</output>
      </div>
      <div className="seek-track">
        <input id="seek-range" type="range" aria-label="Replay position, seconds around the pass" min={min.toFixed(2)} max={max.toFixed(2)} step={FRAME} value={rel.toFixed(2)}
               aria-valuetext={Math.abs(rel) < 0.005 ? (hasPass ? "the moment of the pass" : "the moment of the shot") : `${Math.abs(rel).toFixed(2)} seconds ${rel < 0 ? "before" : "after"} the ${hasPass ? "pass" : "shot"}`}
               onPointerDown={() => { dragging.current = true; }}
               onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}
               onChange={e => onScrub(+e.target.value, dragging.current)}
               onKeyDown={e => {
                 if (e.key === " ") { e.preventDefault(); onPlay(); }
                 else if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); onJump(e.key === "ArrowRight" ? 1 : -1); }
               }} />
        <div className="seek-marks" aria-hidden="true">
          {marks.map(k => (
            <span key={k.text || k.rel} className={`seek-mark ${k.side || "c"}`} title={k.title} style={{ left: `${(k.rel - min) / span * 100}%` }}><i />{k.text || ""}</span>
          ))}
        </div>
      </div>
      <p className="seek-note" style={{ visibility: scrubbed ? "visible" : "hidden" }}>Analysis is frozen at the pass ◆. Go back to ◆ to see it.</p>
    </div>
  );
}
