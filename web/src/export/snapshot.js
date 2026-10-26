// Ported from good-assist-proto/src/snapshot.js.
// Turns one of the page's SVGs into a PNG (with a caption strip under it) for the frame export.
// DOM only, no app state: main.js hands over a clone of the SVG, already showing the frame it wants.

// the page's own CSS, so the SVG looks the same on its own (colours, fonts, label strokes)
export function pageCss() {
  return [...document.styleSheets].map(sh => { try { return [...sh.cssRules].map(r => r.cssText).join('\n'); } catch { return ''; } }).join('\n');
}

const loadImage = url => new Promise((resolve, reject) => {
  const img = new Image();
  img.onload = () => resolve(img); img.onerror = () => reject(new Error('could not draw the frame'));
  img.src = url;
});

// svg: an SVG element (a clone is fine); width/height: pixels of the picture, the strip is added below it.
// caption / sub: the strip's two lines. Resolves to the PNG bytes.
export async function svgToPng(svg, { width, height, css, caption, sub }) {
  const node = svg.cloneNode(true);
  node.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  node.setAttribute('width', width); node.setAttribute('height', height);
  // HTML tolerates `--` inside comments (the figure notes in index.html have it), XML does not: drop the comments
  const walk = document.createTreeWalker(node, NodeFilter.SHOW_COMMENT);
  [...(function* () { while (walk.nextNode()) yield walk.currentNode; })()].forEach(c => c.remove());
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
  // the page's animations would freeze at their first frame inside an image: show everything, and fix the size
  style.textContent = `${css}\n*{transition:none!important}.fade,.bc-fade{animation:none!important;opacity:1!important}svg{width:${width}px!important;height:${height}px!important}`;
  node.prepend(style);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(node)], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = await loadImage(url);
    const fs = Math.round(width / 62), strip = Math.round(fs * 3.4);
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height + strip;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fbfbf8'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, width, height);
    ctx.fillStyle = '#d6d7cf'; ctx.fillRect(0, height, width, 2);
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#1f2430'; ctx.font = `700 ${fs}px ui-monospace, Menlo, monospace`;
    ctx.fillText(caption, fs, height + fs * 1.5);
    ctx.fillStyle = '#6a7080'; ctx.font = `${Math.round(fs * 0.72)}px ui-monospace, Menlo, monospace`;
    ctx.fillText(sub, fs, height + fs * 2.6);
    const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
    return new Uint8Array(await blob.arrayBuffer());
  } finally { URL.revokeObjectURL(url); }
}
