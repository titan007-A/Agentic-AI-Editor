import assert from "node:assert/strict";
import { test } from "node:test";

import { folders, pileHref } from "../src/lib/pile-folders.mjs";

const job = (id, preset) => ({ id, outputs: [{ preset }] });

test("videos go into one folder per preset, newest preset first", () => {
  const found = folders([job(1, "b"), job(2, "a"), job(3, "b")]);
  assert.deepEqual(
    found.map((f) => [f.preset, f.jobs.map((j) => j.id)]),
    [
      ["b", [1, 3]],
      ["a", [2]],
    ],
  );
});

test("a job without an output still lands somewhere", () => {
  assert.equal(folders([{ id: 1, outputs: [] }])[0].preset, "");
});

test("the address names the tab and the open folder only when needed", () => {
  assert.equal(pileHref("ready", null), "/autopilot/");
  assert.equal(pileHref("look", null), "/autopilot/?tab=look");
  assert.equal(pileHref("ready", "gym fails"), "/autopilot/?folder=gym+fails");
  assert.equal(pileHref("look", "x"), "/autopilot/?tab=look&folder=x");
});
