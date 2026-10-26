import { polygonHull } from "d3";
import "./livepitch.css";

const PW = 105, PH = 68;

/** Top-down pitch for one tracking frame. Same drawing as the live pitch. */
export default function PitchFrame({ frame, teams, label }) {
  const home = hull(frame, "home");
  const away = hull(frame, "away");
  const zone = frame?.counter_zone;
  const lines = frame?.defensive_line || {};
  const ball = frame?.ball;
  const caption = label || (frame
    ? `${frame.players?.length || 0} players${frame.possession ? `, ${teams?.[frame.possession] || frame.possession} in possession` : ""}`
    : "Waiting for a tracking frame.");

  return (
    <svg viewBox="-2 -2 109 72" className="pitch" role="img" aria-label={caption}>
      <g transform={`translate(0,${PH}) scale(1,-1)`}>
        <rect x="0" y="0" width={PW} height={PH} className="grass" />
        <g className="lines">
          <rect x="0" y="0" width={PW} height={PH} />
          <line x1="52.5" y1="0" x2="52.5" y2={PH} />
          <circle cx="52.5" cy="34" r="9.15" />
          <rect x="0" y="13.84" width="16.5" height="40.32" />
          <rect x="88.5" y="13.84" width="16.5" height="40.32" />
          <rect x="0" y="24.84" width="5.5" height="18.32" />
          <rect x="99.5" y="24.84" width="5.5" height="18.32" />
        </g>
        {zone && <rect className={`zone ${zone.team}`} x={zone.x0} y={zone.y0} width={Math.max(0.4, zone.x1 - zone.x0)} height={zone.y1 - zone.y0} />}
        {home && <polygon className="shape home" points={home.map(([x, y]) => `${x},${y}`).join(" ")} />}
        {away && <polygon className="shape away" points={away.map(([x, y]) => `${x},${y}`).join(" ")} />}
        {lines.home != null && <line className="dline home" x1={lines.home} y1="0" x2={lines.home} y2={PH} />}
        {lines.away != null && <line className="dline away" x1={lines.away} y1="0" x2={lines.away} y2={PH} />}
        {(frame?.players || []).map((player) => (
          <circle key={player.player_id} cx={player.x} cy={player.y} r={player.role === "GK" ? 1.15 : 1.35} className={`player ${player.team || ""}`} />
        ))}
        {ball && <circle cx={ball.x} cy={ball.y} r="1.15" className="puck" />}
      </g>
      {(frame?.players || []).map((player) => (
        <text key={`${player.player_id}-n`} x={player.x} y={PH - player.y + 0.45} textAnchor="middle" className="shirt">
          {player.number ?? ""}
        </text>
      ))}
      {(frame?.players || []).filter((player) => player.highlight_role).map((player) => (
        <text key={`${player.player_id}-role`} x={player.x} y={PH - player.y + 4.2} textAnchor="middle" className="shirt">
          {player.name || player.highlight_role}
        </text>
      ))}
    </svg>
  );
}

function hull(frame, team) {
  const points = (frame?.players || []).filter((player) => player.team === team).map((player) => [player.x, player.y]);
  if (points.length < 3) return null;
  return polygonHull(points);
}

export function frameAt(frames, t) {
  if (!frames?.length) return null;
  let best = frames[0];
  for (const frame of frames) {
    if (frame.match_seconds <= t + 1e-6) best = frame;
    else break;
  }
  return best;
}
