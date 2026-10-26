import "./livebar.css";

export const LIVE_LABEL = {
  idle: "Start live match", running: "Pause", paused: "Resume live match",
  goal: "Pause", full: "Restart match", reset: "Start live match",
};

// Compact live match controls for the page header: button, clock, restart, the latest event and a thin progress line.
// The full event list (newest first) is kept for screen readers only.
export default function LiveBar({ live, teams }) {
  const { state, minute, total, ticker, ready, source } = live;
  const pct = Math.min(100, (minute / total) * 100);
  const started = state !== "idle" && state !== "reset";
  const latest = ticker[0];
  return (
    <div className="livebar" role="group" aria-label="Live match">
      <div className="live-row">
        <button type="button" className={`live-btn${state === "running" || state === "goal" ? " ghost" : ""}`} disabled={!ready} onClick={live.toggle}>
          {ready ? LIVE_LABEL[state] : "Loading match feed…"}
        </button>
        {started && (
          <button type="button" className="live-restart" onClick={live.restart}>Restart</button>
        )}
        <span className="clock" aria-label={`Match clock, minute ${Math.floor(minute)}`}>{Math.floor(minute)}'</span>
        {started && latest && (
          <span className={`live-latest${latest.goal != null ? " goal" : ""}`} aria-hidden="true">
            <b>{Math.floor(latest.minute)}'</b> {latest.text}
          </span>
        )}
        <span className="live-status small" role="status">
          {state === "goal" ? "Replay on, live match resumes in a moment…" : state === "full" ? "Full time" : ""}
          {source === "moments" && state === "idle" ? "Using the key-moment list (live feed unavailable)" : ""}
        </span>
      </div>
      <div className="progress" role="progressbar" aria-label="Match progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <ol className="sr-only" aria-live="polite" aria-label="Match events, newest first">
        {ticker.map((e) => (
          <li key={e.id}>
            {Math.floor(e.minute)}' {e.text}
          </li>
        ))}
      </ol>
    </div>
  );
}
