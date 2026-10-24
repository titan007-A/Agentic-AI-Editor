import assert from "node:assert/strict";
import { test } from "node:test";

import { readiness } from "../src/lib/output-status.mjs";

test("an output without doubts or issues is ready", () => {
  assert.deepEqual(readiness({ issues: [], doubts: [], verdict: null }), {
    ready: true,
    approved: false,
    doubts: [],
  });
});

test("doubts or review issues mean it needs a look", () => {
  assert.equal(readiness({ issues: [], doubts: ["8s without voice"] }).ready, false);
  assert.equal(readiness({ issues: ["loudness off"], doubts: [] }).ready, false);
});

test("issues aren't listed twice as doubts", () => {
  const { doubts } = readiness({ issues: ["loudness off"], doubts: ["loudness off", "short"] });
  assert.deepEqual(doubts, ["short"]);
});

test("an output the editor approved is ready despite its doubts", () => {
  const status = readiness({ issues: ["loudness off"], doubts: ["short"], verdict: "approved" });
  assert.equal(status.ready, true);
  assert.equal(status.approved, true);
});

test("older outputs without doubts still work", () => {
  assert.equal(readiness({ issues: [] }).ready, true);
});
