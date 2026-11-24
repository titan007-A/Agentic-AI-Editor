import assert from "node:assert/strict";
import { test } from "node:test";

import { actions, numbers, passed, short, songName, timesFor } from "../src/lib/channel-pilot.mjs";

test("a reel waiting for approval, or needing a look, can be approved or skipped", () => {
  assert.deepEqual(actions("awaiting"), { approve: true, skip: true, retry: false });
  assert.deepEqual(actions("look"), { approve: true, skip: true, retry: false });
});

test("a posted reel can't be touched, a failed one can be tried again", () => {
  assert.deepEqual(actions("posted"), { approve: false, skip: false, retry: false });
  assert.deepEqual(actions("posting"), { approve: false, skip: false, retry: false });
  assert.deepEqual(actions("failed"), { approve: false, skip: true, retry: true });
});

test("only the numbers Meta gave are shown, in a fixed order", () => {
  assert.deepEqual(numbers({ comments: 3, likes: 120, hours: 24 }), [
    ["Likes", 120],
    ["Comments", 3],
  ]);
  assert.deepEqual(numbers({ likes: 5, views: 900, saved: 7, reach: 800 }), [
    ["Views", 900],
    ["Reach", 800],
    ["Saves", 7],
    ["Likes", 5],
  ]);
  assert.deepEqual(numbers(null), []);
});

test("big numbers are short", () => {
  assert.equal(short(950), "950");
  assert.equal(short(1234), "1.2k");
  assert.equal(short(206363), "206.4k");
  assert.equal(short(2_000_000), "2M");
});

test("post times follow the posts a day: defaults fill, extra ones go", () => {
  const defaults = ["02:30", "04:00", "06:00", "18:30", "21:30"];
  assert.deepEqual(timesFor(5, [], defaults), defaults);
  assert.deepEqual(timesFor(2, ["21:30", "06:00", "04:00"], defaults), ["04:00", "06:00"]);
  assert.deepEqual(timesFor(3, ["19:00"], defaults), ["02:30", "04:00", "19:00"]);
  assert.deepEqual(timesFor(6, defaults, defaults), ["00:00", ...defaults]);
  // Back to fewer: the times saved stay.
  assert.deepEqual(timesFor(2, defaults, defaults, ["18:30", "21:30"]), ["18:30", "21:30"]);
});

test("checks are summed up in a line", () => {
  assert.equal(passed([]), "not checked yet");
  assert.equal(passed([{ ok: true }, { ok: true }]), "all 2 checks passed");
  assert.equal(passed([{ ok: true }, { ok: false }]), "1 of 2 checks passed");
});

test("a video is named by the song Shazam heard, else the research's", () => {
  const post = { heard_song: "", heard_artist: "", song: "Self Control", artist: "", code: "yt:x" };
  assert.equal(songName(post), "Self Control");
  assert.equal(
    songName({ ...post, heard_song: "Self Control", heard_artist: "Laura Branigan" }),
    "Self Control by Laura Branigan",
  );
  assert.equal(songName({ ...post, song: "" }), "yt:x");
});
