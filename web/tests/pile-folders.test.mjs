import assert from "node:assert/strict";
import { test } from "node:test";

import {
  folderPaths,
  folders,
  pileHref,
  placeOf,
  subfolders,
  trail,
  within,
} from "../src/lib/pile-folders.mjs";

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
  assert.equal(pileHref("ready", "x", 4), "/autopilot/?folder=x&in=4");
  assert.equal(pileHref("ready", null, 4), "/autopilot/");
});

// a: Monday (Funny, Sad > Old), Tuesday; b: Monday
const made = [
  { id: 1, preset: "a", parent_id: null, name: "Monday" },
  { id: 2, preset: "a", parent_id: 1, name: "Sad" },
  { id: 3, preset: "a", parent_id: 1, name: "Funny" },
  { id: 4, preset: "a", parent_id: 2, name: "Old" },
  { id: 5, preset: "a", parent_id: null, name: "Tuesday" },
  { id: 6, preset: "b", parent_id: null, name: "Monday" },
  // Made inside a folder the viewer can't see: shown straight in the preset's folder.
  { id: 7, preset: "a", parent_id: 99, name: "Adrift" },
];

test("folders made inside a preset's folder nest, each level by name", () => {
  assert.deepEqual(
    subfolders(made, "a", null).map((f) => f.name),
    ["Adrift", "Monday", "Tuesday"],
  );
  assert.deepEqual(
    subfolders(made, "a", 1).map((f) => f.name),
    ["Funny", "Sad"],
  );
  assert.deepEqual(
    subfolders(made, "b", null).map((f) => f.id),
    [6],
  );
  assert.deepEqual([...within(made, 1)].sort(), [1, 2, 3, 4]);
  assert.deepEqual([...within(made, 5)], [5]);
});

test("a folder's trail runs from the outermost down", () => {
  assert.deepEqual(
    trail(made, 4).map((f) => f.name),
    ["Monday", "Sad", "Old"],
  );
  assert.deepEqual(trail(made, null), []);
  assert.deepEqual(trail(made, 42), []);
});

test("the move-to list names each folder by its trail, nested in order", () => {
  assert.deepEqual(
    folderPaths(made, "a").map((f) => f.path),
    ["Adrift", "Monday", "Monday / Funny", "Monday / Sad", "Monday / Sad / Old", "Tuesday"],
  );
});

test("a video in a folder that is gone sits straight in its preset's folder", () => {
  const known = new Map(made.map((f) => [f.id, f]));
  const put = (folder_id) => ({ outputs: [{ preset: "a", folder_id }] });
  assert.equal(placeOf(put(3), known), 3);
  assert.equal(placeOf(put(null), known), null);
  assert.equal(placeOf(put(1234), known), null);
  assert.equal(placeOf({ outputs: [] }, known), null);
});
