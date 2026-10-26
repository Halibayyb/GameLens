// Gets the next goals ready while the current one plays: each goal's detail in the commentary language (the API translates
// and proofreads its captions then, which can take several seconds the first time) and, when the commentary sound is on,
// the voice for each caption. Moving to the next goal, or changing language, then starts at once.
// One goal at a time, the next goal first, so the API is never asked for everything at once; a newer call stops an older one.
import { prefetchSpeech } from "./speech.js";

const SOUND_KEY = "matchday.commentarySound";   // CommentaryCC's remembered sound choice
const soundOn = () => { try { return localStorage.getItem(SOUND_KEY) !== "0"; } catch { return true; } };   // on unless turned off

// The goals to get ready after `currentId`, in the order a visitor reaches them: the ones after it, then the ones before.
export function upcoming(goalIds, currentId) {
  const i = goalIds.indexOf(currentId);
  if (i < 0) return goalIds.slice();
  return [...goalIds.slice(i + 1), ...goalIds.slice(0, i)];
}

const getMoment = async (id, lang) => (await import("./api.js")).getMoment(id, lang);   // loaded when first used, like speech.js

let run = 0;
// deps are injectable for tests; returns a promise that settles when this run is done (or was overtaken)
export async function prefetchGoals(goalIds, currentId, lang, { moment = getMoment, speech = prefetchSpeech, sound = soundOn } = {}) {
  const mine = ++run;
  for (const id of upcoming(goalIds, currentId)) {
    if (mine !== run) return;
    let detail;
    try { detail = await moment(id, lang); } catch { continue; }   // a goal that fails now is simply fetched when opened
    if (mine !== run || !sound()) continue;
    for (const c of detail?.commentary?.captions || []) {
      if (mine !== run) return;
      await speech(id, lang, c.part, c.text);
    }
  }
}
