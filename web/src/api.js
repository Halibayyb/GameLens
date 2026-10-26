import { textVersion } from "./speech.js";

const BASE = import.meta.env.VITE_API_BASE || "";

async function get(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} failed: ${r.status}`);
  return r.json();
}

export const getMatch = () => get("/api/match");
// A moment's detail, kept for the visit per moment and language (prefetch.js asks for the next goals ahead of time, so
// opening one is instant); a failed request is forgotten so it is tried again.
const moments = new Map();
export function getMoment(id, lang) {
  const key = `${id}|${lang || "en"}`;
  if (!moments.has(key)) {
    const p = get(`/api/moments/${id}${lang && lang !== "en" ? `?lang=${encodeURIComponent(lang)}` : ""}`);
    moments.set(key, p);
    p.catch(() => moments.delete(key));
  }
  return moments.get(key);
}
export const getLanguages = () => get("/api/languages");
export const getAiSentence = (id) => get(`/api/moments/${id}/ai-sentence`);

// The commentator's line spoken: { url } of an MP3 from Azure Speech, or { text } for the browser's own voice.
export async function getMomentSpeech(id, lang, part, text) {
  const q = new URLSearchParams();
  if (lang && lang !== "en") q.set("lang", lang);
  if (part && part !== "line") q.set("part", part);
  if (text) q.set("v", textVersion(text));
  const r = await fetch(`${BASE}/api/moments/${id}/speech${q.size ? `?${q}` : ""}`);
  if (!r.ok) throw new Error(`Speech failed: ${r.status}`);
  if ((r.headers.get("content-type") || "").startsWith("audio/")) return { url: URL.createObjectURL(await r.blob()) };
  return r.json();
}

function highlightOrigin() {
  return BASE ? BASE.replace(/^http/, "ws") : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
}

export const highlightVideoUrl = (id) => `${BASE}/api/highlights/jobs/${id}/video`;
export function highlightSocketUrl(id) {
  return `${highlightOrigin()}/api/highlights/jobs/${id}/live`;
}

// Admin calls send the signed-in session (Bearer); a bare admin token still works for scripts until a password is set.
function adminHeaders(token) {
  const headers = {};
  if (token?.startsWith("s1.")) headers.Authorization = `Bearer ${token}`;
  else if (token) headers["X-Admin-Token"] = token;
  return headers;
}

// Thrown by any admin call when the session has ended (expired, password changed): the page signs out.
export class SessionEnded extends Error {}

async function adminFetch(url, options) {
  const r = await fetch(url, options);
  if (r.status === 401) {
    let detail = "Your session has ended. Sign in again.";
    try { detail = (await r.clone().json()).detail || detail; } catch { /* keep */ }
    throw new SessionEnded(detail);
  }
  return r;
}

async function authJson(path, body, token) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { ...adminHeaders(token), "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(typeof detail === "string" ? detail : "Sign-in failed");
  }
  return r.json();
}

export const adminLogin = (username, password) => authJson("/api/admin/login", { username, password });
export const adminChangePassword = (token, current, next) => authJson("/api/admin/password", { current, new: next }, token);

export async function adminMe(token) {
  const r = await adminFetch(`${BASE}/api/admin/me`, { headers: adminHeaders(token) });
  if (!r.ok) throw new Error(`Session check failed: ${r.status}`);
  return r.json();
}

export const getAdminStatus = () => get("/api/admin/status");

export async function getAdminVideo(token) {
  const r = await adminFetch(`${BASE}/api/admin/video`, { headers: adminHeaders(token) });
  if (!r.ok) throw new Error(r.status === 403 ? "Change the first-time password first." : `Admin video failed: ${r.status}`);
  return r.json();
}

export async function createAdminVideoJob({ token, file, source = "demo" } = {}) {
  const body = new FormData();
  body.append("source", source);
  if (file) body.append("file", file);
  const r = await adminFetch(`${BASE}/api/admin/video/jobs`, { method: "POST", headers: adminHeaders(token), body });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(typeof detail === "string" ? detail : "Admin job failed");
  }
  return r.json();
}

export async function getAdminVideoJob(id, token) {
  const r = await adminFetch(`${BASE}/api/admin/video/jobs/${id}`, { headers: adminHeaders(token) });
  if (!r.ok) throw new Error(`Job failed: ${r.status}`);
  return r.json();
}

export async function getAdminVideoResult(id, token) {
  const r = await adminFetch(`${BASE}/api/admin/video/jobs/${id}/result`, { headers: adminHeaders(token) });
  if (!r.ok) throw new Error(`Result failed: ${r.status}`);
  return r.json();
}

export async function publishAdminVideo(id, token) {
  const r = await adminFetch(`${BASE}/api/admin/video/jobs/${id}/publish`, { method: "POST", headers: adminHeaders(token) });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(typeof detail === "string" ? detail : "Publish failed");
  }
  return r.json();
}



// Every event of the match, collected once from the live feed at top speed (it is replayed in order, then the
// socket closes). The paced clock lives in the browser, see live.js. Rejects on error or timeout.
export function getLiveEvents(timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const origin = BASE ? BASE.replace(/^http/, "ws") : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
    const events = [];
    let ws, done = false;
    const finish = (fn, v) => { if (done) return; done = true; clearTimeout(timer); try { ws?.close(); } catch { /* already closed */ } fn(v); };
    const timer = setTimeout(() => finish(reject, new Error("live feed timed out")), timeoutMs);
    try { ws = new WebSocket(`${origin}/api/live?channel=events&speed=100000`); } catch (e) { finish(reject, e); return; }
    ws.onmessage = (m) => { try { events.push(JSON.parse(m.data)); } catch { /* skip a bad frame */ } };
    ws.onerror = () => finish(reject, new Error("live feed failed"));
    ws.onclose = () => finish(events.length ? resolve : reject, events.length ? events : new Error("live feed was empty"));
  });
}

export async function verifyFreeze(id, momentId, body, token) {
  const r = await adminFetch(`${BASE}/api/admin/video/jobs/${id}/freeze/${momentId}`, {
    method: "POST", headers: { ...adminHeaders(token), "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(typeof detail === "string" ? detail : "Could not save the correction");
  }
  return r.json();
}
