import assert from "node:assert/strict";
import { test } from "node:test";

import { clipState, clock, streamLength, working } from "../src/lib/kick-clips.mjs";

test("a time in a stream reads as a clock", () => {
  assert.equal(clock(3723), "1:02:03");
  assert.equal(clock(75.9), "1:15");
  assert.equal(clock(0), "0:00");
  assert.equal(clock(-4), "0:00");
});

test("a hunt is at work while it looks or its clips are being made", () => {
  assert.equal(working({ status: "reading", moments: [] }), true);
  const done = { status: "done", moments: [{ job: { status: "done" } }, { job: null }] };
  assert.equal(working(done), false);
  assert.equal(working({ status: "done", moments: [{ job: { status: "running" } }] }), true);
  assert.equal(working({ status: "failed", moments: [] }), false);
});

test("a moment's clip says where it is", () => {
  assert.equal(
    clipState({ job: { status: "running", progress: 0.42, error: null } }),
    "making the clip · 42%",
  );
  assert.equal(
    clipState({ job: { status: "queued", progress: 0, error: null } }),
    "waiting for its turn",
  );
  assert.equal(clipState({ job: null, error: "403" }), "couldn't be cut");
  assert.equal(clipState({ job: { status: "done", progress: 1, error: null } }), "ready");
});

test("a stream's length is in hours and minutes", () => {
  assert.equal(streamLength({ duration: 48979, live: false }), "13 h 36 min");
  assert.equal(streamLength({ duration: 2280, live: false }), "38 min");
  assert.equal(streamLength({ duration: 0, live: true }), "live now");
});
