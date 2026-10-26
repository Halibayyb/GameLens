import { useEffect, useRef, useState } from "react";
import { area, curveMonotoneX, scaleLinear } from "d3";
import "./timeline.css";

// Layout, in svg px. Top to bottom: the selected label, the home icons (goals, then everything else), the pressure chart
// (home above the baseline, away below), the away icons mirrored (everything else, then goals), the minute labels, the score bar.
// An icon sits on the side of the team that did it, the same rule as the pressure areas.
const ICON_Y = 30, GOAL_R = 6, OTHER_Y = ICON_Y + 14, ICON_GAP = 12;
const CHART_TOP = 54, CHART_BOTTOM = 146, MID = (CHART_TOP + CHART_BOTTOM) / 2, HALF = (CHART_BOTTOM - CHART_TOP) / 2;
const AWAY_OTHER_Y = CHART_BOTTOM + 16, AWAY_GOAL_Y = CHART_BOTTOM + 30;
const LABEL_Y = CHART_BOTTOM + 52;
const STRIP_Y = CHART_BOTTOM + 61, STRIP_H = 12, H = STRIP_Y + STRIP_H + 2;
// where a moment's icon is drawn: goals on the outer row, every other moment on the row next to the chart
const iconY = (m) => (m.team === "away" ? (m.kind === "goal" ? AWAY_GOAL_Y : AWAY_OTHER_Y) : (m.kind === "goal" ? ICON_Y : OTHER_Y));
const MIN_SCORE_PX = 44;
const PAD = 12;
const GRID_MINUTES = [15, 30, 60, 75, 90];

// What a marker says when you hover or focus it: "71' Goal · Paul (Home)".
const tipText = (m) => `${m.minute} ${m.title}${m.player ? ` · ${m.player}` : ""}${m.team_name ? ` (${m.team_name})` : ""}`;

// A small mono icon for each kind of moment, drawn at (cx, y). Goals are the circles you can click.
function Icon({ m, cx, y }) {
  if (m.kind === "goal") return <circle className="tl-mark tl-goal" cx={cx} cy={y} r={GOAL_R} />;
  if (m.kind === "card") return <rect className={`tl-glyph card ${/red/i.test(m.title) ? "red" : "yellow"}`} x={cx - 3.2} y={y - 4.8} width="6.4" height="9.6" rx="1.2" />;
  if (m.kind === "sub") {
    return (
      <g className="tl-glyph line" transform={`translate(${cx} ${y})`}>
        <path d="M-4 -1.5 H3 M1 -4 L4 -1.5 L1 1" /><path d="M4 2 H-3 M-1 -0.5 L-4 2 L-1 4.5" />
      </g>
    );
  }
  if (m.kind === "var") return <rect className="tl-glyph fill" x={cx - 3.5} y={y - 3.5} width="7" height="7" transform={`rotate(45 ${cx} ${y})`} />;
  if (m.kind === "penalty") return <g className="tl-glyph line"><circle cx={cx} cy={y} r="2.6" /><circle cx={cx} cy={y} r="5" /></g>;
  return <circle className="tl-glyph line" cx={cx} cy={y} r="3" />;   // a shot that did not go in
}

// The whole match on one chart: how much each team threatened, minute by minute (`match.pressure`, home above the
// baseline, away below), with every moment as an icon on top. Hover (or focus, or tap) any icon for its text; only the
// `selectable` moments (the goals) can be clicked to open them.
export default function Timeline({ match, selId, onSelect, selectable = match.moments }) {
  const svgRef = useRef(null);
  const [tip, setTip] = useState(null);   // the marker whose text is showing
  const [W, setW] = useState(1000);   // viewBox width follows the real width, so the chart fills its row and markers stay round
  const x = scaleLinear().domain([0, match.total_minutes]).range([PAD, W - PAD]);
  const sel = match.moments.find((m) => m.id === selId);
  const canOpen = new Set(selectable.map((m) => m.id));

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const measure = () => { const w = Math.round(el.clientWidth); if (w > 0) setW(w); };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const series = match.pressure?.values?.length ? match.pressure : null;
  const points = series ? series.values.map((v, i) => ({ t: Math.min(match.total_minutes, i * (series.step || 1)), v })) : [];
  const shape = area().x((p) => x(p.t)).curve(curveMonotoneX);
  const homePath = series ? shape.y0(MID).y1((p) => MID - Math.max(0, p.v) * HALF)(points) : null;
  const awayPath = series ? shape.y0(MID).y1((p) => MID - Math.min(0, p.v) * HALF)(points) : null;
  const ht = match.half_time_at;
  const gridMinutes = GRID_MINUTES.filter((g) => g < match.total_minutes - 2 && Math.abs(g - ht) > 3);
  const labelMinutes = [0, ...gridMinutes, ...(ht ? [Math.round(ht)] : [])];

  // icons of one team that fall on the same minute (three substitutions) fan out to the right instead of covering each other
  const iconX = {};
  const lastX = { home: -Infinity, away: -Infinity };
  [...match.moments].filter((m) => m.kind !== "goal").sort((a, b) => a.elapsed - b.elapsed).forEach((m) => {
    const side = m.team === "away" ? "away" : "home";
    const cx = Math.max(x(m.elapsed), lastX[side] + ICON_GAP);
    iconX[m.id] = cx;
    lastX[side] = cx;
  });
  const at = (m) => iconX[m.id] ?? x(m.elapsed);
  const selX = sel ? x(sel.elapsed) : 0;
  const anchor = selX < 70 ? "start" : selX > W - 70 ? "end" : "middle";

  return (
    <section className="timeline" aria-label={`Match timeline: ${match.teams.home} pressure above the line, ${match.teams.away} below`}>
      <div className="timeline-wrap" onMouseLeave={() => setTip(null)}>
      <svg ref={svgRef} className="timeline-bar" viewBox={`0 0 ${W} ${H}`} style={{ height: H }} role="group" aria-label="Moments and pressure through the match">
        {sel && <text x={selX} y="12" textAnchor={anchor} className="tl-text">{`${sel.minute} ${sel.title}`}</text>}
        <g aria-hidden="true">
          {gridMinutes.map((g) => <line key={g} x1={x(g)} x2={x(g)} y1={CHART_TOP} y2={CHART_BOTTOM} className="tl-grid" />)}
          {homePath && <path d={homePath} className="tl-area home" />}
          {awayPath && <path d={awayPath} className="tl-area away" />}
          <line x1={PAD} x2={W - PAD} y1={MID} y2={MID} className="tl-axis" />
          <line x1={x(ht)} x2={x(ht)} y1={CHART_TOP - 6} y2={CHART_BOTTOM} className="tl-ht" />
          <text x={PAD + 2} y={CHART_TOP + 10} className="tl-side">{match.teams.home}</text>
          <text x={PAD + 2} y={CHART_BOTTOM - 4} className="tl-side">{match.teams.away}</text>
          <text x={PAD + 2} y={MID + 12} className="tl-side">Balanced</text>
          {labelMinutes.map((g) => <text key={g} x={x(g)} y={LABEL_Y} textAnchor="middle" className="tl-min">{g}'</text>)}
        </g>
        {sel && (sel.team === "away"   // the open goal's line runs from its label, through the chart, to its icon on its own side
          ? <line x1={selX} x2={selX} y1={16} y2={AWAY_GOAL_Y - 8} className="tl-playhead" />
          : <line x1={selX} x2={selX} y1={ICON_Y + 8} y2={CHART_BOTTOM} className="tl-playhead" />)}
        {[...match.moments].sort((a, b) => canOpen.has(a.id) - canOpen.has(b.id)).map((m) => {   // the clickable ones last, so they sit on top
          const open = canOpen.has(m.id), on = m.id === selId;
          const cx = at(m), cy = iconY(m);
          const show = () => setTip(m.id), hide = () => setTip((t) => (t === m.id ? null : t));
          const common = { tabIndex: 0, "aria-label": tipText(m), onMouseEnter: show, onFocus: show, onBlur: hide,
                           className: `tl-marker ${m.team} ${m.kind} ${open ? "open" : "static"} ${on ? "on" : ""}` };
          const body = (
            <>
              <circle className="tl-hit" cx={cx} cy={cy} r={open ? 12 : 5} />
              <Icon m={m} cx={cx} y={cy} />
            </>
          );
          return open ? (
            <g key={m.id} role="button" aria-pressed={on} {...common}
               onClick={() => { onSelect(m.id); setTip(m.id); }}
               onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect(m.id))}>{body}</g>
          ) : (
            <g key={m.id} role="img" {...common}
               onClick={() => setTip((t) => (t === m.id ? null : m.id))}   // a tap on a touch screen, which has no hover
               onKeyDown={(e) => e.key === "Escape" && setTip(null)}>{body}</g>
          );
        })}
        <g aria-label="Score through the match">
          {match.score_segments.map((s) => {
            const x0 = x(s.from), w = Math.max(0, x(s.to) - x0 - 2);
            return (
              <g key={s.from}>
                <rect x={x0} y={STRIP_Y} width={w} height={STRIP_H} rx={STRIP_H / 2} className="tl-seg" />
                {w >= MIN_SCORE_PX && (
                  <text x={x0 + w / 2} y={STRIP_Y + STRIP_H / 2} textAnchor="middle" dominantBaseline="central" className="tl-score">{s.score}</text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      {tip && (() => {
        const m = match.moments.find((q) => q.id === tip);
        if (!m) return null;
        const cx = at(m), edge = cx < W * 0.3 ? "start" : cx > W * 0.7 ? "end" : "mid";   // keep the box inside the chart
        return <div className={`tl-tip ${edge}`} role="status" style={{ left: cx, top: iconY(m) - 14 }}>{tipText(m)}</div>;
      })()}
      </div>
    </section>
  );
}
