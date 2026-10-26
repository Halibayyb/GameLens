import { useEffect, useState } from "react";
import { getMoment } from "../api.js";
import { fmtPct } from "../broadcast/labels.js";
import { HISTORY } from "../matchHistory.js";
import { fmtRatio, vsNextBest } from "./VerdictPanel.jsx";
import "./matchsummary.css";

// The match summary under "Where it happened": the match's assists as three cards, then each team's last five matches as a bar
// chart of the average expected value (EV) of their assists, with this match as the last bar. The cards come from the pass
// analysis of each goal; the history is example data (matchHistory.js), labelled as such.
const LABELS = { line_breaker: ["line-breaker", "line-breakers"], brave_pass: ["brave pass", "brave passes"],
  best_option: ["best option", "best options"], smart_pass: ["smart pass", "smart passes"] };
const avg = xs => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const tally = list => {   // "2 line-breakers · 3 brave passes"
  const n = {};
  list.forEach(a => { n[a.v] = (n[a.v] || 0) + 1; });
  return Object.entries(LABELS).filter(([k]) => n[k]).map(([k, [one, many]]) => `${n[k]} ${n[k] === 1 ? one : many}`).join(" · ");
};

const W = 330, H = 128, TOP = 16, BASE = 100, SLOT = W / 6, BAR = 26, MAX = 0.16;   // the same 0-16% scale on both teams, so they compare
const y = v => BASE - (Math.min(v, MAX) / MAX) * (BASE - TOP);

function FormChart({ team, side, now }) {
  const past = HISTORY[side] || [];
  const evs = past.map(m => avg(m.assists.map(a => a.ev)));
  const mean = avg(evs.filter(v => v != null));
  const bars = [...past.map((m, i) => ({ label: m.opp.split(" ")[0], ev: evs[i], title: `vs ${m.opp}: average EV ${fmtPct(evs[i])}, ${m.assists.length} assist${m.assists.length === 1 ? "" : "s"}` })),
    ...(now ? [{ label: "This match", ev: now.ev, now: true, title: `This match: average EV ${fmtPct(now.ev)}, ${now.n} assist${now.n === 1 ? "" : "s"}` }] : [])];
  const allPast = past.flatMap(m => m.assists);
  return (
    <figure className="ms-form">
      <figcaption><b>{team}</b></figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${team}: average expected value of their assists in their last five matches${now ? `, and in this match ${fmtPct(now.ev)}` : ""}. Five-match average ${fmtPct(mean)}.`}>
        <defs>
          <pattern id={`ms-hatch-${side}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--surface-2)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--muted)" strokeWidth="2.2" />
          </pattern>
        </defs>
        <line x1="0" x2={W} y1={BASE} y2={BASE} stroke="var(--line)" />
        {now && <line x1={SLOT * past.length} x2={SLOT * past.length} y1={TOP - 4} y2={BASE + 18} stroke="var(--line)" strokeDasharray="2 3" />}
        {mean != null && (
          <line x1="0" x2={W} y1={y(mean)} y2={y(mean)} stroke="var(--muted)" strokeWidth="1" strokeDasharray="3 4" />
        )}
        {bars.map((b, i) => {
          const cx = SLOT * (i + 0.5), top = y(b.ev);
          return (
            <g key={b.label + i}>
              <title>{b.title}</title>
              <rect x={SLOT * i} y={TOP} width={SLOT} height={BASE - TOP + 30} fill="transparent" />
              <path d={`M${cx - BAR / 2},${BASE} V${top + 4} Q${cx - BAR / 2},${top} ${cx - BAR / 2 + 4},${top} H${cx + BAR / 2 - 4} Q${cx + BAR / 2},${top} ${cx + BAR / 2},${top + 4} V${BASE} Z`}
                    fill={b.now ? "var(--ink)" : `url(#ms-hatch-${side})`} stroke={b.now ? "none" : "var(--muted)"} strokeWidth="1" />
              {b.now && <text x={cx} y={top - 5} textAnchor="middle" className="ms-val">{fmtPct(b.ev)}</text>}
              <text x={cx} y={BASE + 14} textAnchor="middle" className={`ms-axis${b.now ? " now" : ""}`}>{b.label}</text>
            </g>
          );
        })}
      </svg>
      <p className="ms-stats">
        Average EV <b>{fmtPct(avg(allPast.map(a => a.ev)))}</b> <span className="muted">(dashed line)</span> · average xBest <b>{fmtRatio(avg(allPast.map(a => a.x)))}</b>
        <br /><span className="muted">{tally(allPast)}</span>
      </p>
    </figure>
  );
}

// Props: match (/api/match: teams, moments)
export default function MatchSummary({ match }) {
  const [rows, setRows] = useState(null);   // one per goal: { moment, assist }
  const goals = match.moments.filter(m => m.kind === "goal");
  const key = goals.map(g => g.id).join(",");
  useEffect(() => {
    let live = true;
    setRows(null);
    Promise.all(goals.map(g => getMoment(g.id).then(d => ({ moment: d.moment, assist: d.assist })))).then(r => { if (live) setRows(r); }).catch(() => { if (live) setRows([]); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!goals.length) return null;
  const cards = (rows || []).filter(r => r.assist).map(({ moment: m, assist: a }) => {
    const p = a.options.find(o => o.played);
    return { id: m.id, minute: m.minute, side: m.team, team: m.team_name, passer: a.pass.passer, receiver: a.pass.receiver,
      label: a.verdict.label, v: a.payload.verdict, ev: p.ev, x: vsNextBest(p, a.options), arrives: p.arrives, cut: p.cut_out };
  });
  const now = side => {
    const mine = cards.filter(c => c.side === side);
    return mine.length ? { ev: avg(mine.map(c => c.ev)), n: mine.length } : null;
  };
  return (
    <section className="ms" aria-labelledby="ms-title">
      <h2 id="ms-title" className="section-title">Match summary</h2>
      {rows === null && <p className="muted">Loading the summary…</p>}
      {cards.length > 0 && (
        <>
          <p className="ms-lead">{cards.length} assist{cards.length === 1 ? "" : "s"} in this match{tally(cards) ? ` · ${tally(cards)}` : ""}</p>
          <ul className="ms-cards">
            {cards.map(c => (
              <li key={c.id} className="ms-card">
                <div className="ms-card-top"><span className="ms-min">{c.minute}</span><span className="muted">{c.team}</span></div>
                <div className="ms-pass">{c.passer} → {c.receiver}</div>
                <span className="verdict-badge">{c.label}</span>
                <dl className="ms-nums">
                  <div><dt>EV</dt><dd>{fmtPct(c.ev)}</dd></div>
                  <div><dt>xBest</dt><dd>{c.x == null ? "–" : fmtRatio(c.x)}</dd></div>
                </dl>
              </li>
            ))}
          </ul>
          <p className="ms-note muted">EV = expected value of the pass (arrival chance × goal chance, est.). xBest = its EV as a multiple of the best other option.</p>
          <div className="ms-history">
            <div className="ms-history-head">
              <h3>Last 5 matches</h3>
              <span className="ms-chip" title="Made-up numbers for a synthetic match">History · example data</span>
            </div>
            <p className="muted small">Hatched bars are each team's earlier matches; the solid bar is this match. Same scale on both charts.</p>
            <div className="ms-forms">
              <FormChart team={match.teams.home} side="home" now={now("home")} />
              <FormChart team={match.teams.away} side="away" now={now("away")} />
            </div>
          </div>
        </>
      )}
    </section>
  );
}
