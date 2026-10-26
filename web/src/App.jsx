import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMatch, getMoment } from "./api.js";
import Timeline from "./components/Timeline.jsx";
import VerdictPanel from "./components/VerdictPanel.jsx";
import MomentStory from "./components/MomentStory.jsx";
import VideoFrame from "./components/VideoFrame.jsx";
import CommentaryCC from "./components/CommentaryCC.jsx";
import Brand from "./components/Brand.jsx";
import { createClock } from "./commentary.js";
import PitchMap from "./components/PitchMap.jsx";
import AnalystView from "./components/AnalystView.jsx";
import BroadcastPitch from "./components/BroadcastPitch.jsx";
import LiveBar from "./components/LiveBar.jsx";
import MatchSummary from "./components/MatchSummary.jsx";
import HowItWorks from "./components/HowItWorks.jsx";
import { useGoals } from "./goals.js";
import { useLive } from "./useLive.js";
import { useLang } from "./lang.js";
import { prefetchGoals } from "./prefetch.js";

const EMPTY = [];
const defaultMoment = (ms) => (ms.find((x) => x.important && x.kind === "goal") || ms.find((x) => x.kind === "goal") || ms[0]);
// The pitch views: the perspective replay ("analyst", BroadcastPitch), the top-down map ("node", AnalystView) and the frozen
// pass with Export frames ("frame", BroadcastPitch in frame mode).
const VIEWS = [["analyst", "Analyst"], ["node", "Node"], ["frame", "Frame"]];
const VIEW_KEY = "matchday.pitchView", OLD_VIEW_KEY = "matchday.view";   // the old key held "broadcast" / "analyst" / "live", where "analyst" meant the map
const API_HINT = import.meta.env.VITE_API_BASE || "the API";
const loadView = () => {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (VIEWS.some(([k]) => k === v)) return v;
    return localStorage.getItem(OLD_VIEW_KEY) === "analyst" ? "node" : "analyst";   // a saved top-down choice stays the map; anything else is the replay
  } catch { return "analyst"; }
};

// Previous / "Goal N of M" / next, driven by useGoals. It sits under the video and beside "Where it happened"; `where` keeps the
// two sets of buttons apart for screen readers.
function GoalStepper({ gl, where = "" }) {
  const noun = gl.noun.toLowerCase();
  return (
    <>
      <button type="button" className="step" aria-label={`Previous ${noun}${where}`} disabled={gl.prevIdx < 0} onClick={() => gl.go(gl.prevIdx)}>
        <svg width="10" height="16" viewBox="0 0 10 16" aria-hidden="true"><path d="M8 2 2 8l6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <span className="count">{gl.idx >= 0 ? `${gl.noun} ${gl.idx + 1} of ${gl.goals.length}` : `${gl.goals.length} ${noun}s`}</span>
      <button type="button" className="step" aria-label={`Next ${noun}${where}`} disabled={gl.nextIdx < 0} onClick={() => gl.go(gl.nextIdx)}>
        <svg width="10" height="16" viewBox="0 0 10 16" aria-hidden="true"><path d="m2 2 6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
    </>
  );
}

export default function App() {
  const [match, setMatch] = useState(null);
  const [selId, setSelId] = useState(null);             // the goal the analytics view shows (pitch, verdict)
  const [detail, setDetail] = useState(null);
  const [videoSelId, setVideoSelId] = useState(null);   // the goal the broadcast video shows: its own stepper moves it alone
  const [videoFetched, setVideoFetched] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState(loadView);
  const [selectedPlayer, setSelectedPlayer] = useState(null);   // one pass option, shared by the two pitch views
  const videoClock = useRef(null);   // the goal video's current time, for the commentary captions
  videoClock.current ||= createClock();

  // live mode selects goal moments itself (both views); every manual pick below stops it first, like the prototype's live.stop().
  // The timeline and live mode move both views; each stepper moves only its own.
  const showMoment = useCallback((id) => { setSelId(id); setVideoSelId(id); setSelectedPlayer(null); }, []);
  const pickers = useRef(null);
  const gl = useGoals(match?.moments ?? EMPTY, selId, (id) => pickers.current.analytics(id));   // the goals: the only moments that open
  const glVideo = useGoals(match?.moments ?? EMPTY, videoSelId, (id) => pickers.current.video(id));
  const live = useLive({ match, moments: gl.goals, onStop: showMoment });
  const pick = (id) => { live.stop(); showMoment(id); };
  pickers.current = {
    analytics: (id) => { live.stop(); setSelId(id); setSelectedPlayer(null); },
    video: (id) => { live.stop(); setVideoSelId(id); },
  };
  const notifyDone = live.notifyReplayDone;
  useEffect(() => {
    if (detail && !detail.track) notifyDone(detail.moment.id, { noTrack: true });
  }, [detail, live.replayKey, notifyDone]);
  const videoMoment = match?.moments.find((x) => x.id === videoSelId);
  const videoDetail = detail?.moment?.id === videoSelId ? detail : videoFetched?.moment?.id === videoSelId ? videoFetched : null;
  const chooseView = (v) => {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* storage blocked: the choice just is not remembered */ }
  };

  useEffect(() => {
    getMatch()
      .then((m) => {
        setMatch(m);
        setSelId(defaultMoment(m.moments)?.id);
        setVideoSelId(defaultMoment(m.moments)?.id);
      })
      .catch((e) => setError(e.message));
  }, []);

  const lang = useLang();
  useEffect(() => {   // fetched again when the commentary language changes (the line comes back translated)
    if (!selId) return;
    getMoment(selId, lang)
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [selId, lang]);

  useEffect(() => {   // the video's own goal, when it is not the analytics goal (then `detail` serves both)
    if (!videoSelId || videoSelId === selId) return;
    getMoment(videoSelId, lang).then(setVideoFetched).catch((e) => setError(e.message));
  }, [videoSelId, selId, lang]);

  // once the open goal's detail is in, get the next goals ready (translated captions and voice) so moving on is instant;
  // a new language does the same for every goal
  const videoReady = videoDetail?.moment?.id;
  const goalIds = useMemo(() => glVideo.goals.map((g) => g.id), [glVideo.goals]);
  useEffect(() => {
    if (videoReady) prefetchGoals(goalIds, videoReady, lang);
  }, [videoReady, goalIds, lang]);

  if (error) return <main className="page"><p role="alert">Could not load the match: {error}. Is {API_HINT} running?</p></main>;
  if (!match) return <main className="page"><p>Loading match…</p></main>;

  const m = detail?.moment;

  return (
    <main className="page">
      <header className="top">
        <div className="wrap top-inner">
          <Brand />
          <div className="scorebox">
            <h1 className="score">
              <span className="team home">{match.teams.home}<span className="dot home" aria-hidden="true" /></span>
              <span className="vs">vs</span>
              <span className="team away"><span className="dot away" aria-hidden="true" />{match.teams.away}</span>
            </h1>
            <span className="badge">{match.synthetic ? "Synthetic data" : match.source === "vision" ? "Vision tracking" : "Match data"}</span>
          </div>
          <LiveBar live={live} teams={match.teams} />
        </div>
      </header>

      <div className="wrap content">
        <div className="browse">
          <VideoFrame moment={videoMoment} detail={videoDetail} autoplay={live.state === "goal" ? live.replayKey : 0}
                      onFinish={() => videoMoment && live.notifyVideoDone(videoMoment.id)} onTime={videoClock.current.set} onPlaying={videoClock.current.setPlaying} />
          <CommentaryCC live={live} assist={videoDetail?.assist} clock={videoClock.current}
                        momentId={videoDetail?.moment?.id} captions={videoDetail?.commentary?.captions} />
          <div className="goalnav">
            <GoalStepper gl={glVideo} where=", video" />
            <MomentStory moment={videoMoment} detail={videoDetail} teams={match.teams} numbers={match.numbers} />
          </div>

          <Timeline match={match} selId={videoSelId} onSelect={pick} selectable={gl.goals} />
        </div>

        <section className="main" aria-label="Replay and decision">
          <article className="where">
            <div className="section-head">
              <div className="head-left">
                <h2 className="section-title">Where it happened</h2>
                <div className="goalnav"><GoalStepper gl={gl} where=", analytics" /></div>
              </div>
              {m?.badge && (
                <span className="verdict-badge" title="Player and ball positions from vision, not the language model">
                  {m.badge}
                  {m.confidence != null ? ` · ${Math.round(m.confidence * 100)}%` : ""}
                </span>
              )}
              <div className="segmented" role="group" aria-label="Pitch view">
                {VIEWS.map(([k, label]) => (
                  <button key={k} type="button" aria-pressed={view === k} onClick={() => chooseView(k)}>{label}</button>
                ))}
              </div>
            </div>
            {!detail && match.moments.length > 0 && <p className="muted">Loading the moment…</p>}
            {detail?.track && view === "analyst" && (
              <BroadcastPitch key={`${m.id}:${live.replayKey}`} moment={m} track={detail.track} assist={detail.assist || null} teams={match.teams}
                              selected={selectedPlayer} onSelect={setSelectedPlayer} shot={detail.shot || null}
                              onDone={() => notifyDone(m.id)} onReplay={() => live.notifyReplayStart(m.id)} />
            )}
            {detail?.track && view === "frame" && (
              <BroadcastPitch key={`${m.id}:frame`} mode="frame" moment={m} track={detail.track} assist={detail.assist || null} teams={match.teams}
                              selected={selectedPlayer} onSelect={setSelectedPlayer} shot={detail.shot || null} />
            )}
            {detail?.track && view === "node" && (
              <AnalystView key={`${m.id}:${live.replayKey}`} moment={m} track={detail.track} assist={detail.assist || null} roles={match.roles}
                           numbers={match.numbers} names={match.names} teams={match.teams} selected={selectedPlayer} onSelect={setSelectedPlayer} shot={detail.shot || null}
                           onDone={() => notifyDone(m.id)} onReplay={() => live.notifyReplayStart(m.id)} />
            )}
            {detail && !detail.track && <PitchMap moment={m} />}
            {!detail && match.moments.length === 0 && <p className="muted">No key moments in this match.</p>}
          </article>
          {detail && <VerdictPanel detail={detail} roles={match.roles} selected={selectedPlayer} onSelect={setSelectedPlayer} />}
        </section>

        <MatchSummary match={match} />

        <HowItWorks match={match} />

        <footer className="muted small">{match.disclaimer}</footer>
      </div>
    </main>
  );
}
