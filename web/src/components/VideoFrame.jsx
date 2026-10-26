import { useEffect, useRef, useState } from "react";
import "./videoframe.css";

// The video of the open goal, in a full-width 16:9 frame. With `moment.video` (a URL the API sets for the prototype
// match's goals) it is a plain <video> with the browser's controls: nothing starts by itself, the sound is on. If the file
// cannot be loaded (the clips are not in git, a fresh clone has none) or the moment has no video, the frame is a
// placeholder instead, never a broken player.
//   moment  the open moment (the timeline's summary); detail  /api/moments/{id} once it has arrived, else null
//   autoplay  0, or a number that changes each time the live clock stops at a goal: the clip then starts by itself
//   onTime    called with the video's current time in seconds (0 when the clip changes), for the commentary captions
//   onPlaying called with true / false as the video plays / pauses (or ends), for the commentary voice
//   onFinish  called when the clip ends, or at once when there is no clip to play (none, or the file cannot be loaded):
//             the live clock waits for it so the next goal does not cut the video short
export default function VideoFrame({ moment, detail, autoplay = 0, onFinish, onTime, onPlaying }) {
  const [failed, setFailed] = useState(null);   // the url that could not be loaded
  const ref = useRef(null), cb = useRef(onFinish), tick = useRef(onTime), play = useRef(onPlaying);
  cb.current = onFinish; tick.current = onTime; play.current = onPlaying;   // the latest callbacks without restarting the effects
  const url = moment?.video && failed !== moment.video ? moment.video : null;
  useEffect(() => { tick.current?.(0); play.current?.(false); }, [url]);   // a new clip starts at 0, not playing
  useEffect(() => { if (moment && !url) cb.current?.(); }, [moment?.id, url, autoplay]);   // nothing to wait for: report at once
  useEffect(() => {
    const v = ref.current;
    if (!autoplay || !v) return;
    v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });   // the browser may refuse sound without a click: play muted then
  }, [autoplay, moment?.id]);
  if (!moment) return null;
  const pass = detail?.moment?.id === moment.id ? detail.assist?.pass : null;   // ignore the previous moment's detail while loading
  const what = pass ? `${pass.passer} → ${pass.receiver}` : moment.player || moment.team_name || "";
  const head = `${moment.minute} ${moment.title}`;
  const spoken = `${head}${pass ? `, ${pass.passer} to ${pass.receiver}` : what ? `, ${what}` : ""}`;
  const src = url;
  if (src) {
    return (
      <figure className="video-frame">
        <video key={src} ref={ref} src={src} controls preload="metadata" playsInline aria-label={`Video: ${spoken}`}
               onError={() => setFailed(src)} onEnded={() => cb.current?.()}
               onTimeUpdate={e => tick.current?.(e.currentTarget.currentTime)} onSeeked={e => tick.current?.(e.currentTarget.currentTime)}
               onPlay={() => play.current?.(true)} onPause={() => play.current?.(false)} />
      </figure>
    );
  }
  return (
    <figure className="video-frame placeholder" role="img"
            aria-label={`Broadcast video placeholder for ${spoken}. ${moment.video ? "The video file was not found." : "No footage in this demo."}`}>
      <span className="video-tag">Placeholder</span>
      <span className="video-play" aria-hidden="true"><svg width="28" height="28" viewBox="0 0 28 28"><path d="M9 6 22 14 9 22z" fill="currentColor" /></svg></span>
      <span className="video-title">Broadcast video</span>
      <span className="video-moment">{head}{what ? ` · ${what}` : ""}</span>
      <span className="video-note">{moment.video ? "Video file not found: see the README to add it" : "No footage in this demo: the match is synthetic"}</span>
    </figure>
  );
}
