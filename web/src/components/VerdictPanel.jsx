import { useEffect, useState } from "react";
import { getAiSentence } from "../api.js";
import { shownOptions } from "../broadcast/layout.js";
import { fmtPct } from "../broadcast/labels.js";
import { shirtNo } from "../broadcast/figures.js";
import "./verdictpanel.css";

// The right column, "Assist analysis": why the pass was (or was not) the smart choice. The data comes first: the played pass
// as a hero block and the other options as compact rows (A, B, C ... by expected value), then the verdict keyword and one
// short line. The line is the template at once; the language model's wording replaces it
// when it arrives (and the template stays when it does not). The numbers come from the formula in docs/how-it-works.md
// (engine/assist.py); nothing here is computed.
const pctSmall = v => (v < 0.0105 ? "under 1%" : fmtPct(v));   // a goal chance or EV at or under 1.0% is not worth a decimal
// the score before the goal ("87' · 1–1"): the match is live, so the result is not given away
const goalContext = m => {
  if (m?.kind !== "goal" || !m.score) return null;
  let [h, a] = m.score.split("-").map(n => parseInt(n, 10) || 0);
  if (m.team === "home") h = Math.max(0, h - 1); else a = Math.max(0, a - 1);
  return `${m.minute} · ${h}–${a}`;
};
export const fmtRatio = r => (r >= 10 ? Math.round(r) : r.toFixed(1)) + "×";
// "xBest" (was "vs next best"): an option's expected value over the best of all the other options
export const vsNextBest = (o, all) => {
  const best = Math.max(...all.filter(x => x !== o).map(x => x.ev));
  return best > 0 ? o.ev / best : null;
};

// Props
//   detail      /api/moments/{id}: { moment, available, assist, text }
//   roles       { player_id: "CB" } (shown next to the shirt number)
//   selected    the picked player_id, onSelect(player_id): a table row picks an option on the pitch
export default function VerdictPanel({ detail, roles = {}, selected, onSelect }) {
  const { assist, moment: m, text } = detail;
  const [ai, setAi] = useState(null);   // the model's wording for this moment: { sentence } once it arrives, else null
  const id = m?.id, hasAssist = !!assist;
  useEffect(() => {   // ask for the model's wording on each goal; the template shows meanwhile and stays on failure or no model
    setAi(null);
    if (!id || !hasAssist) return undefined;
    let live = true;
    getAiSentence(id).then(r => { if (live && r.ai) setAi({ sentence: r.sentence }); }).catch(() => {});
    return () => { live = false; };
  }, [id, hasAssist]);
  if (!assist) {
    return (
      <section className="verdict" aria-label="Assist analysis">
        <h3 className="verdict-title">Assist analysis</h3>
        <p className="muted">No pass to judge in this moment.</p>
        {text?.why && <p className="verdict-why">{text.why}</p>}
      </section>
    );
  }
  const { letters } = shownOptions(assist);
  const rows = assist.options.filter(o => letters.has(o.player_id)).sort((a, b) => b.ev - a.ev);
  const played = rows.find(o => o.played) || rows[0];
  const others = rows.filter(o => o !== played);
  const pick = o => e => {
    if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    onSelect?.(selected === o.player_id ? null : o.player_id);
  };
  const who = o => (
    <>
      <span className="letter">{letters.get(o.player_id)}</span>
      <span className="who">{o.player} <span className="muted small">#{shirtNo(o.player_id)}{roles[o.player_id] ? ` · ${roles[o.player_id]}` : ""}</span></span>
    </>
  );
  const bar = (label, v) => (
    <span className="meter" title={`${label} ${fmtPct(v)} (est.)`}>
      <span className="meter-fill" style={{ width: `${Math.max(2, Math.min(100, v * 100))}%` }} />
    </span>
  );
  const pr = played && vsNextBest(played, assist.options);
  const context = goalContext(m);
  return (
    <section className="verdict" aria-label="Assist analysis">
      <h3 className="verdict-title">Assist analysis</h3>
      {context && <span className="verdict-chip" title="Minute and score before the goal">{context}</span>}
      <p className="verdict-note">Judged by the odds at the moment of the pass, not the result <span className="tag" title="estimated">est.</span></p>
      {played && (
        <div className={`hero opt-row played${selected === played.player_id ? " on" : ""}`} tabIndex={0} role="button"
             aria-pressed={selected === played.player_id} title="Show this option on the pitch" onClick={pick(played)} onKeyDown={pick(played)}>
          <div className="hero-who">{who(played)}<span className="hero-star" title="The pass played">★ played</span></div>
          <dl className="hero-stats">
            <div><dt>Arrives</dt><dd>{fmtPct(played.arrives)}</dd>{bar("Arrives", played.arrives)}</div>
            <div title={`Shot xG ${fmtPct(played.shot_xg ?? played.goal)}${played.xt ? ` · xT ${fmtPct(played.xt)}` : ""}`}><dt>Goal</dt><dd>{pctSmall(played.goal)}</dd>{bar("Goal", played.goal)}</div>
            <div title="Expected value: arrival chance times goal chance (est.)"><dt>EV</dt><dd>{pctSmall(played.ev)}</dd></div>
            <div><dt>Cut out</dt><dd>{played.cut_out}</dd></div>
            <div title={`Expected value ${fmtPct(played.ev)} (est.)`}><dt>xBest</dt><dd>{pr == null ? "–" : fmtRatio(pr)}</dd></div>
          </dl>
        </div>
      )}
      {others.length > 0 && (
        <ul className="opts" aria-label="Other options">
          <li className="opts-head" aria-hidden="true">
            <span>Option</span><span>Arrives</span><span>Goal</span><span>EV</span><span>Cut out</span><span>xBest</span>
          </li>
          {others.map(o => {
            const r = vsNextBest(o, assist.options);
            return (
              <li key={o.player_id} className={`opt-row${selected === o.player_id ? " on" : ""}`} tabIndex={0} role="button"
                  aria-pressed={selected === o.player_id} title="Show this option on the pitch" onClick={pick(o)} onKeyDown={pick(o)}>
                <span className="opt-who">{who(o)}</span>
                <span className="opt-num" title="Arrives">{fmtPct(o.arrives)}{bar("Arrives", o.arrives)}</span>
                <span className="opt-num" title="Goal">{pctSmall(o.goal)}{bar("Goal", o.goal)}</span>
                <span className="opt-num" title="Expected value (est.)">{pctSmall(o.ev)}</span>
                <span className="opt-num" title="Defenders cut out">{o.cut_out}</span>
                <span className="opt-num" title="xBest: expected value as a multiple of the best other option">{r == null ? "–" : fmtRatio(r)}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="verdict-caption">EV = expected value of the pass; Cut out = defenders the pass goes past; xBest = the option's expected value as a multiple of the best other option. Tap a row or a sphere for all numbers.</p>
      <span className="verdict-badge" id="verdict-label">{assist.verdict.label}</span>
      <p className="verdict-sentence" aria-live="polite">{ai ? ai.sentence : assist.sentence}</p>
      <p className="verdict-note">
        <span className="tag src">{ai ? "AI" : "Template"}</span> {ai ? "Worded by AI from the numbers" : "Worded from the numbers"}
      </p>
    </section>
  );
}
