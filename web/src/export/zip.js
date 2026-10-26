// Ported unchanged from good-assist-proto/src/zip.js.
// Minimal ZIP writer for the frame export: store-only (the PNGs are already compressed), no DOM, so scripts/scores.js can test it.
// Dates are fixed at 1980-01-01 (the DOS epoch), so a ZIP carries no real timestamps.
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const DOS_DATE = (0 << 9) | (1 << 5) | 1;                  // 1980-01-01
const UTF8 = 0x0800;                                       // general-purpose flag: names are UTF-8

// files: [{ name, data: Uint8Array }] -> Uint8Array (the .zip)
export function zip(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  let offset = 0;
  files.forEach(({ name, data }) => {
    const nm = enc.encode(name), crc = crc32(data), size = data.length;
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, UTF8, true);
    local.setUint16(12, DOS_DATE, true); local.setUint32(14, crc, true);
    local.setUint32(18, size, true); local.setUint32(22, size, true); local.setUint16(26, nm.length, true);
    parts.push(new Uint8Array(local.buffer), nm, data);
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, UTF8, true);
    cen.setUint16(14, DOS_DATE, true); cen.setUint32(16, crc, true);
    cen.setUint32(20, size, true); cen.setUint32(24, size, true); cen.setUint16(28, nm.length, true);
    cen.setUint32(42, offset, true);
    central.push(new Uint8Array(cen.buffer), nm);
    offset += 30 + nm.length + size;
  });
  const cdSize = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  all.forEach(p => { out.set(p, at); at += p.length; });
  return out;
}
