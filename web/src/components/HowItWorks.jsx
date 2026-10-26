import { useEffect, useState } from "react";
import { getMoment } from "../api.js";
import BroadcastPitch from "./BroadcastPitch.jsx";
import "./howitworks.css";

// "How GameLens works": an explainer in plain words. The picture is the real frozen frame of the last goal of the match (the
// pass the example is about), drawn by the same pitch view as "Where it happened". The numbers in the example are fixed text,
// copied from goal 3 of the demo match: Joe 40% x 36% = 14%, Jack 50% x 12% = 6%.
const LABELS = ["Best option on the pitch", "The brave pass", "Line-breaker", "Smart pass"];

export default function HowItWorks({ match }) {
  const [frame, setFrame] = useState(null);   // { moment, track, assist, shot } of the last goal
  const goals = match.moments.filter(m => m.kind === "goal");
  const last = goals[goals.length - 1]?.id;
  useEffect(() => {
    if (!last) return undefined;
    let live = true;
    getMoment(last).then(d => { if (live && d.track && d.assist) setFrame({ moment: d.moment, track: d.track, assist: d.assist, shot: d.shot || null }); }).catch(() => {});
    return () => { live = false; };
  }, [last]);
  return (
    <details className="how" open>
      <summary>
        <span className="how-eyebrow">Explainer</span>
        <h2 id="how-title" className="how-title">How GameLens works</h2>
        <span className="how-by">A 2-minute read · plain words, no maths degree</span>
      </summary>
      <div className="how-body">
        <p className="how-lead">When a goal is scored, GameLens looks at the pass that set it up and asks one question: <b>was that the smartest pass available?</b></p>

        <h3>Every teammate is an option</h3>
        <p>GameLens pauses the moment of the pass and rates every teammate who could have received it. Here is goal 3, frozen at the pass. The thick arrow is the pass that was played; the dashed ones are the other options.</p>
        {frame && (
          <figure className="how-fig">
            <BroadcastPitch mode="still" moment={frame.moment} track={frame.track} assist={frame.assist} shot={frame.shot} teams={match.teams} />
            <figcaption>Goal 3, frozen at the pass. Each option shows its chance to arrive.</figcaption>
          </figure>
        )}

        <h3>Three questions for each option</h3>
        <ol className="how-steps">
          <li><span className="how-n">1</span><div><b>Will the pass arrive?</b> <span className="muted">(Arrives)</span><br />A short pass with no defender in the way is safe. A long pass, or one with a defender on its path, is risky.</div></li>
          <li><span className="how-n">2</span><div><b>If it arrives, will it score?</b> <span className="muted">(Goal)</span><br />A player near goal with a clear shot is worth more than one far out.</div></li>
          <li><span className="how-n">3</span><div><b>How many defenders does it skip?</b> <span className="muted">(Cut out)</span><br />The defenders the pass goes past.</div></li>
        </ol>
        <p>Multiply the first two and you get <b>EV</b>, short for expected value: one score for how good the option is.</p>

        <h3>A simple example</h3>
        <figure className="how-calc" aria-label="Worked example for goal 3">
          <div className="how-eq played">
            <span className="how-who">Joe <small>the pass played</small></span>
            <span className="how-chip"><small>Arrives</small>40%</span><span className="how-op">×</span>
            <span className="how-chip"><small>Goal</small>36%</span><span className="how-op">=</span>
            <span className="how-chip ev"><small>EV</small>14%</span>
          </div>
          <div className="how-eq">
            <span className="how-who">Jack <small>the next best option</small></span>
            <span className="how-chip"><small>Arrives</small>50%</span><span className="how-op">×</span>
            <span className="how-chip"><small>Goal</small>12%</span><span className="how-op">=</span>
            <span className="how-chip ev"><small>EV</small>6%</span>
          </div>
          <div className="how-result"><span className="how-chip x"><small>xBest</small>2.4×</span> 14 ÷ 6 ≈ 2.4. Joe's pass was worth 2.4 times the next best.</div>
        </figure>
        <blockquote className="how-quote">Joe's pass has the highest EV, so it is labelled <b>Best option on the pitch</b>. Only 2 in 5 passes like this get through, and this one did.</blockquote>

        <h3>How the label is chosen</h3>
        <p>GameLens checks in this order and stops at the first one that fits:</p>
        <ol className="how-flow" aria-label="Label order">
          {LABELS.map((l, i) => <li key={l}><span>{l}</span>{i < LABELS.length - 1 && <i aria-hidden="true">if not →</i>}</li>)}
        </ol>
        <p className="how-small">Highest EV, then highest goal chance, then most defenders skipped, then anything else.</p>

        <h3>Good to know</h3>
        <ul className="how-know">
          <li><b>Estimates.</b> They come from where players stood at the moment of the pass, not from what happened next.</li>
          <li><b>Numbers first.</b> The one-line wording is built only from these numbers. AI can reword it, but it can't add numbers of its own.</li>
          <li><b>Synthetic.</b> This match and its history are made up.</li>
        </ul>
      </div>
    </details>
  );
}
