import assert from "node:assert/strict";
import { test } from "node:test";

import { partStatus, playingParts, secondPictures } from "../src/lib/song-parts.mjs";

const choices = [{ start: 20.2 }, { start: 59.6 }, { start: 0.2 }];

test("a Song story plays the parts it says, else the one starting where it does", () => {
  assert.deepEqual(playingParts({ playing: [2, 0], start: 0, choices }), [2, 0]);
  assert.deepEqual(playingParts({ start: 59.3, choices }), [1]);
  assert.deepEqual(playingParts({ playing: [], start: 40, choices }), []);
});

test("another reel of the song says how far along it is", () => {
  const part = { status: "queued", message: "waiting for the part before it", error: null };
  assert.equal(partStatus({ ...part, render: null }), "waiting for the part before it");
  assert.equal(partStatus({ ...part, status: "painting", render: null }), "painting pictures");
  assert.equal(partStatus({ ...part, status: "ready", render: null }), "ready");
  assert.equal(
    partStatus({ ...part, status: "ready", render: { status: "running" } }),
    "rendering",
  );
  assert.equal(partStatus({ ...part, status: "ready", render: { status: "done" } }), "rendered");
  assert.equal(
    partStatus({ ...part, status: "failed", error: "YouTube found nothing", render: null }),
    "failed: YouTube found nothing",
  );
});

test("a line with two pictures has a second scene", () => {
  assert.deepEqual(secondPictures([{ pictures: 1 }, { pictures: 2 }, {}]), [
    false,
    false,
    true,
    false,
  ]);
});
