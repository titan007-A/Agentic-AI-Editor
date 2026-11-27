import assert from "node:assert/strict";
import { test } from "node:test";

import {
  noticeText,
  noticeTone,
  songBadges,
  toastKey,
  toastText,
} from "../src/lib/song-notice.mjs";

const flowers = {
  song: "Flowers",
  artist: "Miley Cyrus",
  year: "2025",
  label: "Columbia",
  group: "Sony",
  group_name: "Sony Music",
  blocked: null,
  notified: true,
  new: true,
};
const luna = {
  song: "Luna",
  artist: "Feid",
  year: "2023",
  label: "Universal Music Latin Entertainment",
  group: "UMG",
  group_name: "Universal Music",
  notified: true,
  new: false,
  blocked: {
    song: "Luna",
    artist: "Feid & ATL Jacob",
    label_group: "UMG",
    what: "blocked",
    what_label: "blocked worldwide",
    note: "228 countries",
  },
};
const tumHiHo = {
  song: "Tum Hi Ho",
  artist: "Arijit Singh",
  year: "2013",
  label: "T-Series",
  group: "other",
  group_name: "",
  blocked: null,
  notified: false,
  new: false,
};

test("a song on the strike list says so first and asks for another song", () => {
  assert.equal(noticeTone(luna), "blocked");
  const { heading, lines } = noticeText(luna);
  assert.equal(heading, "Blocked before: “Luna” by Feid");
  assert.equal(
    lines[0],
    "It was blocked worldwide (UMG, 228 countries). It will be blocked again: pick another song.",
  );
  assert.deepEqual(
    songBadges(luna).map((b) => b.label),
    ["blocked before", "UMG"],
  );
  assert.equal(toastText(luna), "“Luna” was blocked before: pick another song");
});

test("a major's new song says whose it is and what to do", () => {
  assert.equal(noticeTone(flowers), "warn");
  const { heading, lines } = noticeText(flowers);
  assert.equal(heading, "This song is Sony Music's");
  assert.match(lines[0], /is a Sony Music song \(Columbia\)/);
  assert.match(lines[1], /came out in 2025.*Pick an older song by this artist/);
  assert.deepEqual(
    songBadges(flowers).map((b) => [b.label, b.tone]),
    [
      ["Sony", "warn"],
      ["2025 · new", "warn"],
    ],
  );
  assert.equal(toastText(flowers), "“Flowers” is Sony Music's, and new (2025)");
});

test("an independent old song is only named, quietly", () => {
  assert.equal(noticeTone(tumHiHo), "quiet");
  assert.deepEqual(noticeText(tumHiHo), {
    heading: "“Tum Hi Ho” by Arijit Singh: not on the strike list",
    lines: ["Label: T-Series, 2013."],
  });
  assert.deepEqual(songBadges(tumHiHo), []);
  assert.equal(toastText(tumHiHo), null);
  assert.equal(toastKey("j1", tumHiHo), null);
});

test("what isn't known is said, without a badge", () => {
  const unknown = { ...tumHiHo, year: null, label: "", group: "" };
  assert.deepEqual(noticeText(unknown).lines, [
    "Couldn't tell when it came out.",
    "No label found for it.",
  ]);
  const newOnly = { ...tumHiHo, year: "2026", new: true };
  assert.equal(noticeText(newOnly).heading, "A new song: 2026");
  assert.equal(toastText(newOnly), "“Tum Hi Ho” is a new song (2026)");
});

test("no notice, nothing to show", () => {
  assert.equal(noticeTone(null), null);
  assert.equal(toastText(undefined), null);
});

test("a toast is told once per job and notice", () => {
  assert.equal(toastKey("j1", flowers), "j1:“Flowers” is Sony Music's, and new (2025)");
  assert.notEqual(toastKey("j1", flowers), toastKey("j1", luna));
});

test("a research reel's badges come from its own fields", () => {
  const badges = songBadges({ year: "2024", new: true, group: "Warner", label: "Atlantic" });
  assert.deepEqual(
    badges.map((b) => b.label),
    ["Warner", "2024 · new"],
  );
  assert.deepEqual(songBadges({ year: "", new: false, group: "" }), []);
});
