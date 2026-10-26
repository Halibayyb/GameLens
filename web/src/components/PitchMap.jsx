// Where the moment happened, for a moment that has no tracking to replay.
export default function PitchMap({ moment }) {
  return (
    <svg viewBox="-2 -2 109 72" className="pitch" role="img"
         aria-label={`Pitch map: ${moment.title} at ${moment.x.toFixed(0)}, ${moment.y.toFixed(0)} metres`}>
      <rect x="-2" y="-2" width="109" height="72" rx="2" className="grass" />
      <g className="lines">
        <rect x="0" y="0" width="105" height="68" />
        <line x1="52.5" y1="0" x2="52.5" y2="68" />
        <circle cx="52.5" cy="34" r="9.15" />
        <rect x="0" y="13.84" width="16.5" height="40.32" />
        <rect x="88.5" y="13.84" width="16.5" height="40.32" />
        <rect x="0" y="24.84" width="5.5" height="18.32" />
        <rect x="99.5" y="24.84" width="5.5" height="18.32" />
      </g>
      <circle cx={moment.x} cy={moment.y} r="2.2" className={`ball ${moment.team}`} />
    </svg>
  );
}
