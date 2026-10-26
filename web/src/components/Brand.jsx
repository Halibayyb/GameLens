import "./brand.css";

// The GameLens logo for the top bar: the lens ring with the pitch and the pass arrow inside, then the wordmark. The mark is
// inline SVG so it follows the theme (ring and lines in the ink colour, the pass in the selection blue, like the logo's
// light and dark versions); the wordmark is text. public/favicon.svg and apple-touch-icon.png are the same mark on a navy tile.
export default function Brand() {
  return (
    <div className="brand">
      <svg className="brand-mark" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
        <defs><clipPath id="brand-lens"><circle cx="46" cy="46" r="34" /></clipPath></defs>
        <g clipPath="url(#brand-lens)" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round">
          <path d="M33 24H59L76 70H16Z" /><path d="M24.5 47H67.5" /><ellipse cx="46" cy="47" rx="10" ry="4" /><rect x="40" y="19.5" width="12" height="4.5" />
        </g>
        <circle cx="46" cy="46" r="38" fill="none" stroke="currentColor" strokeWidth="6" />
        <path d="M76 76L83 83" stroke="currentColor" strokeWidth="8" />
        <path d="M33 68Q38 40 62 31" fill="none" stroke="var(--sel)" strokeWidth="4.5" strokeLinecap="round" />
        <polygon points="69,28.5 62.3,34.9 59.7,28.1" fill="var(--sel)" /><circle cx="73" cy="24" r="3.6" fill="var(--sel)" />
        <circle cx="28" cy="69" r="6.5" fill="var(--bg)" stroke="currentColor" strokeWidth="2" />
        <polygon points="28,66.4 30.5,68.2 29.6,71 26.4,71 25.5,68.2" fill="currentColor" />
      </svg>
      <span className="brand-word">Game<b>Lens</b></span>
    </div>
  );
}
