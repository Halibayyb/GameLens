import { useSyncExternalStore } from "react";

// The commentary language: the visitor's choice, else their browser's language when the API supports it, else English.
// The API translates the commentator's line and speaks it in that language's voice; the page only asks with ?lang=.
const KEY = "gamelens.lang";
export const CODES = ["en", "es", "fr", "lo", "th", "pt", "de", "it", "ja", "ko", "zh-Hans", "vi", "id", "ar"];

// "fr-CA" -> "fr", "zh-TW" -> "zh-Hans", anything unknown -> null
export function supported(tag) {
  if (!tag) return null;
  if (CODES.includes(tag)) return tag;
  const base = tag.split("-")[0].toLowerCase();
  return CODES.find((c) => c.split("-")[0].toLowerCase() === base) || null;
}

const browserLang = () => {
  const tags = typeof navigator !== "undefined" ? [...(navigator.languages || []), navigator.language] : [];
  for (const t of tags) { const c = supported(t); if (c) return c; }
  return "en";
};

let current = (() => {
  try { return supported(localStorage.getItem(KEY)) || browserLang(); } catch { return browserLang(); }
})();
const subs = new Set();

export const getLang = () => current;
export function setLang(code) {
  const next = supported(code) || "en";
  if (next === current) return;
  current = next;
  try { localStorage.setItem(KEY, next); } catch { /* storage blocked: the choice lasts until the page closes */ }
  subs.forEach((f) => f());
}
const subscribe = (f) => { subs.add(f); return () => subs.delete(f); };
export const useLang = () => useSyncExternalStore(subscribe, getLang, () => "en");
