import assert from "node:assert/strict";
import { test } from "node:test";

import { createPoller, keepIfSame } from "../src/lib/poller.mjs";

/** Timers that only fire when the test says so. */
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    setTimeout(fn, ms) {
      pending.set(next, { fn, ms });
      return next++;
    },
    clearTimeout(id) {
      pending.delete(id);
    },
    pending,
    delays: () => [...pending.values()].map((t) => t.ms),
    async fire() {
      const [[id, { fn }]] = pending;
      pending.delete(id);
      await fn();
    },
  };
}

function fakeDocument(state = "visible") {
  const listeners = new Set();
  return {
    visibilityState: state,
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
    listeners,
    async become(next) {
      this.visibilityState = next;
      for (const fn of listeners) fn();
      await settle();
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("runs at once, then again a delay after each call ends", async () => {
  const timers = fakeTimers();
  let calls = 0;
  const poller = createPoller(() => calls++, 3000, { document: fakeDocument(), timers });
  poller.start();
  await settle();
  assert.equal(calls, 1);
  assert.deepEqual(timers.delays(), [3000]);
  await timers.fire();
  await settle();
  assert.equal(calls, 2);
  assert.deepEqual(timers.delays(), [3000]);
});

test("a slow call is never overlapped by the next one", async () => {
  const timers = fakeTimers();
  let finish;
  let calls = 0;
  const run = () => {
    calls++;
    return new Promise((resolve) => (finish = resolve));
  };
  const poller = createPoller(run, 2000, { document: fakeDocument(), timers });
  poller.start();
  poller.now();
  poller.now();
  assert.equal(calls, 1);
  assert.equal(timers.pending.size, 0, "nothing is timed while a call is running");
  finish();
  await settle();
  assert.equal(calls, 2, "asking during a call runs once more after it");
});

test("a hidden page stops polling and coming back runs at once", async () => {
  const timers = fakeTimers();
  const doc = fakeDocument();
  let calls = 0;
  const poller = createPoller(() => calls++, 15000, { document: doc, timers });
  poller.start();
  await settle();
  await doc.become("hidden");
  assert.equal(timers.pending.size, 0);
  await doc.become("visible");
  assert.equal(calls, 2);
  assert.deepEqual(timers.delays(), [15000]);
  poller.stop();
  assert.equal(doc.listeners.size, 0);
  assert.equal(timers.pending.size, 0);
});

test("a page that opens hidden waits until it is shown", async () => {
  const timers = fakeTimers();
  const doc = fakeDocument("hidden");
  let calls = 0;
  createPoller(() => calls++, 1000, { document: doc, timers }).start();
  await settle();
  assert.equal(calls, 0);
  await doc.become("visible");
  assert.equal(calls, 1);
});

test("a new delay applies to the next wait", async () => {
  const timers = fakeTimers();
  const poller = createPoller(() => {}, 15000, { document: fakeDocument(), timers });
  poller.start();
  await settle();
  poller.setDelay(3000);
  assert.deepEqual(timers.delays(), [3000]);
});

test("a failed call doesn't stop the polling", async () => {
  const timers = fakeTimers();
  const poller = createPoller(() => Promise.reject(new Error("offline")), 1000, {
    document: fakeDocument(),
    timers,
  });
  poller.start();
  await settle();
  assert.deepEqual(timers.delays(), [1000]);
});

test("unchanged data keeps the old value so nothing re-renders", () => {
  const old = [{ id: 1, status: "done" }];
  assert.equal(keepIfSame(old, [{ id: 1, status: "done" }]), old);
  const changed = [{ id: 1, status: "failed" }];
  assert.equal(keepIfSame(old, changed), changed);
  assert.equal(keepIfSame(null, null), null);
});
