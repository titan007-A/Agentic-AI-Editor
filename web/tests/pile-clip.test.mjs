import assert from "node:assert/strict";
import { test } from "node:test";

import { clipSays, loadClip, pasteProblem, saveClip } from "../src/lib/pile-clip.mjs";

function memory() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

test("a clip is kept for the tab and cleared", () => {
  const store = memory();
  assert.equal(loadClip(store), null);
  saveClip({ preset: "sq", videos: [1, 2], folders: [], from: "Monday" }, store);
  assert.deepEqual(loadClip(store), { preset: "sq", videos: [1, 2], folders: [], from: "Monday" });
  saveClip(null, store);
  assert.equal(loadClip(store), null);
});

test("a broken or empty clip, or no storage, is nothing cut", () => {
  const store = memory();
  store.setItem("aiedit-pile-clip", "{not json");
  assert.equal(loadClip(store), null);
  store.setItem("aiedit-pile-clip", JSON.stringify({ preset: "sq", videos: [], folders: [] }));
  assert.equal(loadClip(store), null);
  const blocked = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
    removeItem: () => {},
  };
  assert.equal(loadClip(blocked), null);
  saveClip({ preset: "sq", videos: [1], folders: [], from: "" }, blocked);
  assert.equal(loadClip(undefined), null);
});

test("the clip says what it holds", () => {
  assert.equal(clipSays({ preset: "s", videos: [1], folders: [], from: "" }), "1 video");
  assert.equal(
    clipSays({ preset: "s", videos: [1, 2], folders: [3], from: "" }),
    "2 videos and 1 folder",
  );
  assert.equal(clipSays({ preset: "s", videos: [], folders: [3, 4], from: "" }), "2 folders");
});

test("a clip pastes only in its own preset, and never a folder inside itself", () => {
  // A > B > C, D
  const made = [
    { id: 1, parent_id: null },
    { id: 2, parent_id: 1 },
    { id: 3, parent_id: 2 },
    { id: 4, parent_id: null },
  ];
  const clip = { preset: "sq", videos: [9], folders: [2], from: "A" };
  assert.equal(pasteProblem(made, clip, "sq", 4, "Square"), null);
  assert.equal(pasteProblem(made, clip, "sq", null, "Square"), null);
  assert.equal(pasteProblem(made, clip, "sq", 1, "Square"), null);
  assert.match(pasteProblem(made, clip, "sq", 2, "Square"), /inside itself/);
  assert.match(pasteProblem(made, clip, "sq", 3, "Square"), /inside itself/);
  assert.match(pasteProblem(made, clip, "vocal", null, "Square"), /Cut from Square/);
  assert.match(pasteProblem(made, clip, null, null, "Square"), /Open a Square folder/);
});
