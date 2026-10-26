import { useCallback, useEffect, useRef, useState } from "react";
import { getLiveEvents } from "./api.js";
import { READ_HOLD_MS, REPLAY_TIMEOUT_MS, buildEvents, createLive, createStopGate } from "./live.js";

const TICKER_MAX = 8;
let seq = 0;

// Live match mode for React: collects the match events once, runs the paced clock, and reports clock, score,
// ticker and state. The clock halts at every moment in `moments` (the filtered list) and calls `onStop(momentId)`;
// it waits for the replay (`notifyReplayDone(momentId)`) and the goal video (`notifyVideoDone(momentId)`) to report in, holds `readMs`, then runs on. If no
// signal comes within REPLAY_TIMEOUT_MS it runs on anyway. Call `stop()` when the user picks a moment by hand.
export function useLive({ match, moments, onStop, readMs = READ_HOLD_MS, timeoutMs = REPLAY_TIMEOUT_MS }) {
  const [feed, setFeed] = useState(null);                 // { events, source: "feed" | "moments" }
  const [state, setState] = useState("idle");
  const [minute, setMinute] = useState(0);
  const [score, setScore] = useState({ home: 0, away: 0 });
  const [ticker, setTicker] = useState([]);
  const [replayKey, setReplayKey] = useState(0);          // bumped at each stop so the same moment replays again
  const stopRef = useRef(onStop);
  const liveRef = useRef(null);
  const gateRef = useRef(null);                           // the wait at the current stop
  const stopsRef = useRef([]);
  useEffect(() => { stopRef.current = onStop; });

  const stopsKey = (moments || []).map((m) => `${m.id}@${m.elapsed}`).join("|");
  useEffect(() => {
    // shots have a tracked replay; cards, subs and VAR only hold briefly, so the pace only slows down before shots
    stopsRef.current = (moments || []).map((m) => ({ id: m.id, minute: m.elapsed, replay: m.kind === "goal" || m.kind === "chance" }));
    liveRef.current?.setStops(stopsRef.current);
  }, [stopsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!match) return undefined;
    let off = false;
    getLiveEvents()
      .then((raw) => !off && setFeed({ events: buildEvents(raw, match.moments), source: "feed" }))
      .catch(() => !off && setFeed({ events: buildEvents(null, match.moments), source: "moments" }));
    return () => { off = true; };
  }, [match]);

  useEffect(() => {
    if (!feed || !match) return undefined;
    const gate = createStopGate({ readMs, timeoutMs });
    gateRef.current = gate;
    const live = createLive({
      events: feed.events,
      duration: match.total_minutes,
      stops: stopsRef.current,
      onEvent: (e) => {
        if (e.score) setScore(e.score);
        setTicker((t) => [{ ...e, id: ++seq }, ...t].slice(0, TICKER_MAX));
      },
      onTick: (m) => setMinute(m),
      onStop: async (id) => {
        setReplayKey((k) => k + 1);
        const done = gate.begin(id);
        stopRef.current(id);
        await done;
      },
      onState: (st) => {
        setState(st);
        if (st === "reset") { setScore({ home: 0, away: 0 }); setTicker([]); }
      },
    });
    liveRef.current = live;
    return () => {
      live.stop();
      gate.release();
    };
  }, [feed, match, readMs, timeoutMs]);

  const stop = useCallback(() => {
    gateRef.current?.release();
    liveRef.current?.stop();
  }, []);

  // The replay of `id` has finished (or there is none to play): hold, then let the clock run on. Called again after a
  // Replay-button replay, it restarts the hold. `noTrack` moments only have the map, so they hold a little longer.
  const notifyReplayDone = useCallback((id, opts) => gateRef.current?.done(id, opts), []);
  // The user pressed Replay in the view: back to waiting for it to finish.
  const notifyReplayStart = useCallback((id) => gateRef.current?.replayStarted(id), []);
  // The goal video of `id` has ended (or there is none): the stop also waits for this, so the next goal never cuts it short.
  const notifyVideoDone = useCallback((id) => gateRef.current?.videoDone(id), []);

  const on = state === "running" || state === "paused" || state === "goal";
  return {
    ready: !!feed, source: feed?.source, state, minute, score, ticker, total: match?.total_minutes || 1,
    replayKey, liveVisibleUpTo: on ? minute : null,
    toggle: () => liveRef.current?.toggle(),
    restart: () => { gateRef.current?.release(); liveRef.current?.restart(); },
    stop, notifyReplayDone, notifyReplayStart, notifyVideoDone,
  };
}
