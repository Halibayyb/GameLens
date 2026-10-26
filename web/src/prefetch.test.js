import { test } from "node:test";
import assert from "node:assert/strict";
import { prefetchGoals, upcoming } from "./prefetch.js";

const caps = (id) => ({ commentary: { captions: [{ part: "line", text: `${id} line` }, { part: "smart", text: "32%" }, { part: "finish", text: "Wow!" }] } });

test("the goals after the open one come first, then the ones before it", () => {
  assert.deepEqual(upcoming(["g1", "g2", "g3"], "g1"), ["g2", "g3"]);
  assert.deepEqual(upcoming(["g1", "g2", "g3"], "g2"), ["g3", "g1"]);
  assert.deepEqual(upcoming(["g1", "g2"], "x"), ["g1", "g2"]);
});

test("each upcoming goal is fetched in the language, one at a time, with its voice when the sound is on", async () => {
  const log = [];
  await prefetchGoals(["g1", "g2", "g3"], "g1", "th", {
    moment: async (id, lang) => { log.push(`moment ${id} ${lang}`); return caps(id); },
    speech: async (id, lang, part) => { log.push(`speech ${id} ${part}`); },
    sound: () => true,
  });
  assert.deepEqual(log, ["moment g2 th", "speech g2 line", "speech g2 smart", "speech g2 finish",
    "moment g3 th", "speech g3 line", "speech g3 smart", "speech g3 finish"]);
});

test("with the sound off only the captions are fetched, and a failed goal does not stop the rest", async () => {
  const log = [];
  await prefetchGoals(["g1", "g2", "g3"], "g1", "es", {
    moment: async (id) => { log.push(id); if (id === "g2") throw new Error("busy"); return caps(id); },
    speech: async () => { log.push("speech"); },
    sound: () => false,
  });
  assert.deepEqual(log, ["g2", "g3"]);
});

test("a newer prefetch (another goal or language) stops the older one", async () => {
  const log = [];
  let release;
  const slow = new Promise((r) => { release = r; });
  const first = prefetchGoals(["g1", "g2", "g3"], "g1", "fr", {
    moment: async (id) => { log.push(`fr ${id}`); await slow; return caps(id); }, speech: async () => {}, sound: () => false,
  });
  const second = prefetchGoals(["g1", "g2", "g3"], "g1", "lo", {
    moment: async (id) => { log.push(`lo ${id}`); return caps(id); }, speech: async () => {}, sound: () => false,
  });
  release();
  await Promise.all([first, second]);
  assert.deepEqual(log, ["fr g2", "lo g2", "lo g3"]);
});
