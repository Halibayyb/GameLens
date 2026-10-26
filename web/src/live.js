// Live match mode: a compressed match clock driving an event ticker. No DOM and no React, so it can be unit-tested
// with a fake timer. Ported from good-assist-proto/src/live.js: quiet early minutes fly by, the tense last 25
// (and the run-up to each goal) run slower, and the whole match, goal pauses included, fits about two minutes.
// When the clock reaches a stop (a moment in the current filter) it halts, awaits onStop (select the moment, let its
// replay finish), then runs on.

export const TARGET_S = 120;      // wanted length of a whole live match, goal pauses included
export const STOP_EST_MS = 20000; // typical clock pause at a stop with a replay (the 19 s goal video, longer than the ~10 s replay, + read hold), used to size the pace
export const SMALL_STOP_EST_MS = 2500; // the same for a stop without a replay (a card or a substitution)
export const MIN_CLOCK_S = 60;    // the running clock keeps at least this long, however many stops there are
export const READ_HOLD_MS = 1000; // how long a finished replay and video stay up before the clock resumes
export const NO_TRACK_HOLD_MS = 2000; // same for a moment with no tracking data (the map fallback has no replay)
export const REPLAY_TIMEOUT_MS = 30000; // no "replay and video finished" signals by now: resume anyway
const SLOW_LAST = 25;             // minutes at the end that run slowly
const SLOW_BEFORE_GOAL = 3;       // minutes before each goal that run slowly
const SLOW_MS = 1600;             // real ms per match minute in a slow stretch
const MIN_SLOW_MS = 500;          // slow stretches shrink down to this when many stops crowd the match
const MIN_FAST_MS = 250;
const RUSH_S = [3, 2, 1.5, 1.5];  // seconds the run into the first replay stop, each stop to the next, and the last stop to full time take
const TICK_MS = 100;

// Real ms per match minute at each minute of the match, as a function. Slow zones are the last 25 minutes and the
// 3 minutes leading up to each stop with a replay; the fast rate is whatever makes the whole match last TARGET_S.
// `stops` are minutes (all with a replay) or { minute, replay }. Many stops stretch the target so the running clock
// keeps MIN_CLOCK_S, and shrink the slow stretches toward MIN_SLOW_MS before the fast rate hits its floor.
// `rushes`: [{from, to, s}] stretches that fly by in s seconds in all (the clock races to the first replay stops).
export function buildPace(duration, stops = [], { targetS = TARGET_S, readMs = STOP_EST_MS, smallMs = SMALL_STOP_EST_MS, rushes = [] } = {}) {
  const list = stops.map(s => (typeof s === "number" ? { minute: s, replay: true } : { minute: s.minute, replay: s.replay !== false }));
  const zones = [[Math.max(0, duration - SLOW_LAST), duration]];
  for (const g of list.filter(s => s.replay).map(s => s.minute)) zones.push([Math.max(0, g - SLOW_BEFORE_GOAL), g]);
  zones.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const z of zones) {
    const last = merged[merged.length - 1];
    if (last && z[0] <= last[1]) last[1] = Math.max(last[1], z[1]); else merged.push([...z]);
  }
  const slowMin = merged.reduce((s, z) => s + (z[1] - z[0]), 0);
  const fastMin = Math.max(0, duration - slowMin);
  const pausesMs = list.reduce((s, x) => s + (x.replay ? readMs : smallMs), 0);
  const budgetMs = Math.max(MIN_CLOCK_S * 1000, targetS * 1000 - pausesMs);
  const slowMs = slowMin > 0 ? Math.min(SLOW_MS, Math.max(MIN_SLOW_MS, (budgetMs - fastMin * MIN_FAST_MS) / slowMin)) : SLOW_MS;
  const fastMs = fastMin > 0 ? Math.max(MIN_FAST_MS, (budgetMs - slowMin * slowMs) / fastMin) : MIN_FAST_MS;
  return minute => ((r => (r ? (r.s * 1000) / (r.to - r.from) : null))(rushes.find(r => minute >= r.from && minute < r.to)) ?? (merged.some(z => minute >= z[0] && minute < z[1]) ? slowMs : fastMs));
}

// Kick-off to the first replay stop, then each stop to the next, then the last stop to full time, each in a fixed few seconds (RUSH_S).
function rushStretches(stops, duration) {
  const marks = [0, ...stops.filter(s => s.replay).map(s => s.minute), duration];
  return RUSH_S.map((s, i) => ({ from: marks[i], to: marks[i + 1], s })).filter(r => r.to > r.from);
}

const num = v => (Number.isFinite(+v) ? +v : null);
const parseScore = s => {
  const m = /^(\d+)\s*-\s*(\d+)$/.exec(s || "");
  return m ? { home: +m[1], away: +m[2] } : null;
};
export { parseScore };

// What the ticker says about one raw match event, or null for the many events that are not worth a line.
export function eventText(e) {
  const who = e.player_name || "a player", team = e.team_name || "";
  switch (e.type) {
    case "goal": return `GOAL! ${team}: ${who}`;
    case "shot": return `Shot: ${who}${e.outcome ? ` (${String(e.outcome).replace(/_/g, " ")})` : ""}`;
    case "save": return `Save: ${who}`;
    case "yellow_card": return `Yellow card: ${who}`;
    case "red_card": return `Red card: ${who}`;
    case "substitution": return `Substitution, ${team}: ${e.player_on_name || "a player"} on for ${who}`;
    case "period_end": return e.period_name ? e.period_name.charAt(0).toUpperCase() + e.period_name.slice(1) : "Period ends";
    case "corner_awarded": return `Corner to ${team}`;
    default: return null;
  }
}

// Raw WS events (or null) plus the match's moments -> sorted live events [{minute, text, kind, score?, goal?}].
// `minute` is match minutes elapsed, the same scale as the moments' `elapsed`. A goal event carries `goal`: the id
// of the goal moment it belongs to, which is how the clock knows to stop. Without raw events the moments stand in.
export function buildEvents(raw, moments) {
  const goals = moments.filter(m => m.kind === "goal");
  const out = [];
  const used = new Set();
  for (const e of raw || []) {
    const text = eventText(e);
    const minute = num(e.match_seconds) != null ? e.match_seconds / 60 : null;
    if (!text || minute == null) continue;
    const ev = { minute, text, kind: e.type, score: parseScore(e.score) };
    if (e.type === "goal") {
      const m = goals.filter(g => !used.has(g.id)).sort((a, b) => Math.abs(a.elapsed - minute) - Math.abs(b.elapsed - minute))[0];
      if (m) { used.add(m.id); ev.goal = m.id; }
    }
    out.push(ev);
  }
  for (const g of goals) {       // a goal moment with no matching raw event still has to stop the clock
    if (!used.has(g.id)) out.push({ minute: g.elapsed, text: `GOAL! ${g.team_name}: ${g.player || ""}`.trim(), kind: "goal", goal: g.id, score: parseScore(g.score) });
  }
  if (!raw || !raw.length) {     // fallback: the moments are the only events there are
    for (const m of moments) {
      if (m.kind === "goal") continue;
      out.push({ minute: m.elapsed, text: `${m.title}${m.player ? `: ${m.player}` : ""}`, kind: m.kind, score: parseScore(m.score) });
    }
  }
  return out.sort((a, b) => a.minute - b.minute);
}

// events: from buildEvents; duration: match minutes; stops: [{id, minute}] the moments the clock halts at (default: the
// goal events); callbacks:
//   onEvent(e), onTick(minute), onStop(momentId, stale) -> Promise (onGoal is an alias), onState('idle'|'running'|'paused'|'goal'|'full'|'reset')
// State 'goal' means "halted at a stop, waiting for its replay". deps (for tests): now(), setTimer(fn, ms), clearTimer(id)
export function createLive({ events, duration, stops, onEvent, onTick, onStop, onGoal, onState, pace, readMs = STOP_EST_MS, deps = {} }) {
  const now = deps.now || (() => performance.now());
  const setTimer = deps.setTimer || ((f, ms) => setTimeout(f, ms));
  const clearTimer = deps.clearTimer || (id => clearTimeout(id));
  const handle = onStop || onGoal;
  const list = [...events].sort((a, b) => a.minute - b.minute);
  const goalAt = new Map(list.filter(e => e.goal != null).map(e => [e.goal, e.minute]));
  let msPerMinute, allStops, pending = [], fired = new Set();
  let minute = 0, next = 0, running = false, started = false, busy = false, last = 0, token = 0, timer = null;

  // a goal's stop sits at its goal event, so the ticker and score show the goal before the replay starts
  const place = (all) => all.map(s => ({ id: s.id, replay: s.replay !== false, minute: Math.max(s.minute, goalAt.get(s.id) ?? -Infinity) })).sort((a, b) => a.minute - b.minute);
  function setStops(all) {
    allStops = all;
    const placed = place(all);
    pending = placed.filter(s => !fired.has(s.id) && s.minute >= minute);
    msPerMinute = pace || buildPace(duration, placed, { readMs, rushes: rushStretches(placed, duration) });
  }
  setStops(stops || list.filter(e => e.goal != null).map(e => ({ id: e.goal, minute: e.minute })));

  function loop() {
    clearTimer(timer);
    if (!running || busy) return;
    const t = now();
    minute = Math.min(duration, minute + (t - last) / msPerMinute(minute)); last = t;
    for (;;) {
      const e = list[next], s = pending[0];
      const eReady = e && e.minute <= minute, sReady = s && s.minute <= minute;
      if (!eReady && !sReady) break;
      if (eReady && (!sReady || e.minute <= s.minute)) { next++; onEvent(e); continue; }
      pending.shift(); fired.add(s.id); minute = s.minute; onTick(minute); stopAt(s.id); return;
    }
    onTick(minute);
    if (minute >= duration) {
      while (next < list.length) onEvent(list[next++]);   // anything logged after the last tick still happened
      running = false; onState("full"); return;
    }
    timer = setTimer(loop, TICK_MS);
  }

  async function stopAt(id) {
    busy = true; onState(running ? "goal" : "paused");
    const t = token;
    await handle(id, () => t !== token);
    if (t !== token) return;                  // stopped (a moment was picked) while the replay was showing
    busy = false; last = now();
    onState(running ? "running" : "paused");
    loop();
  }

  return {
    get running() { return running; },
    get minute() { return minute; },
    setStops,
    start() {
      if (minute >= duration) this.restart();
      running = started = true; last = now();
      if (busy) onState("goal"); else { onState("running"); loop(); }
    },
    pause() { running = false; clearTimer(timer); if (busy) onState("paused"); else onState(started ? "paused" : "idle"); },
    toggle() { running ? this.pause() : this.start(); },
    // leave the current stop to the user (a moment was picked by hand); the clock stays where it is
    stop() { token++; busy = false; this.pause(); },
    restart() {
      token++; busy = false; running = false; clearTimer(timer); started = false; minute = 0; next = 0; fired = new Set();
      setStops(allStops);
      onState("reset"); onTick(0);
    },
  };
}

// The wait at one stop: begin(id) resolves after the replay reports done(id) plus a read hold, or after the safety
// timeout if it never does. replayStarted(id) (the Replay button) goes back to waiting; release() ends the wait now.
export function createStopGate({ readMs = READ_HOLD_MS, noTrackMs = NO_TRACK_HOLD_MS, timeoutMs = REPLAY_TIMEOUT_MS, deps = {} } = {}) {
  const setTimer = deps.setTimer || ((f, ms) => setTimeout(f, ms));
  const clearTimer = deps.clearTimer || (id => clearTimeout(id));
  let w = null;
  const arm = ms => { clearTimer(w.timer); w.timer = setTimer(release, ms); };
  function settle() { if (w.replay && w.video) arm(w.hold); }
  function release() {
    if (!w) return;
    clearTimer(w.timer);
    const { resolve } = w; w = null; resolve();
  }
  return {
    // the stop waits for two signals, the pitch replay (`done`) and the goal video (`videoDone`); the hold starts after the later one
    begin: id => new Promise(resolve => { w = { id, resolve, timer: null, replay: false, video: false, hold: readMs }; arm(timeoutMs); }),
    done(id, { noTrack = false } = {}) { if (w && w.id === id) { w.replay = true; w.hold = noTrack ? noTrackMs : readMs; settle(); } },
    videoDone(id) { if (w && w.id === id) { w.video = true; settle(); } },
    replayStarted(id) { if (w && w.id === id) { w.replay = false; arm(timeoutMs); } },
    release,
  };
}
