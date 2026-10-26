import { test } from "node:test";
import assert from "node:assert/strict";
import { createStopGate } from "./live.js";

// A fake clock: timers fire only when the test advances time.
function fake() {
  let t = 0, n = 0;
  const timers = new Map();
  return {
    deps: { setTimer: (f, ms) => { timers.set(++n, { f, at: t + ms }); return n; }, clearTimer: id => timers.delete(id) },
    advance(ms) {
      t += ms;
      for (const [id, x] of [...timers]) if (x.at <= t) { timers.delete(id); x.f(); }
    },
  };
}
const settled = p => { let s = false; p.then(() => { s = true; }); return () => new Promise(r => setImmediate(() => r(s)));
};

test("the stop waits for both the replay and the video, then holds", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.done("g1");                                   // the pitch replay is over at ~10 s ...
  c.advance(5000);
  assert.equal(await open(), false);                 // ... but the 19 s video still plays: the clock stays stopped
  gate.videoDone("g1");                              // the video ends: the read hold starts now
  c.advance(2499);
  assert.equal(await open(), false);
  c.advance(1);
  assert.equal(await open(), true);
});

test("the video finishing first does not release the stop before the replay", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.videoDone("g1");
  c.advance(10000);
  assert.equal(await open(), false);
  gate.done("g1");
  c.advance(2500);
  assert.equal(await open(), true);
});

test("no video to wait for: reporting it at once leaves the old replay-plus-hold behaviour", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.videoDone("g1");
  gate.done("g1");
  c.advance(2500);
  assert.equal(await open(), true);
});

test("a moment with no tracking holds for noTrackMs once the video is done", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, noTrackMs: 2000, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.done("g1", { noTrack: true });
  gate.videoDone("g1");
  c.advance(2000);
  assert.equal(await open(), true);
});

test("pressing Replay again waits for the replay again", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.videoDone("g1");
  gate.done("g1");
  c.advance(1000);
  gate.replayStarted("g1");
  c.advance(10000);
  assert.equal(await open(), false);
  gate.done("g1");
  c.advance(2500);
  assert.equal(await open(), true);
});

test("without the signals the stop still ends at the timeout", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  c.advance(29999);
  assert.equal(await open(), false);
  c.advance(1);
  assert.equal(await open(), true);
});

test("signals for another moment are ignored", async () => {
  const c = fake(), gate = createStopGate({ readMs: 2500, timeoutMs: 30000, deps: c.deps });
  const open = settled(gate.begin("g1"));
  gate.done("g0"); gate.videoDone("g0");
  c.advance(10000);
  assert.equal(await open(), false);
});
