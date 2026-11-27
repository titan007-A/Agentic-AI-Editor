import assert from "node:assert/strict";
import { test } from "node:test";

import { offeredSites, siteOf, SITES } from "../src/lib/research-sites.mjs";

const names = (sites) => sites.map((s) => s.site);

test("without the gateway or the songs feature, the sites everyone had before", () => {
  assert.deepEqual(names(offeredSites({ features: [] })), ["instagram", "shorts", "x"]);
  assert.deepEqual(names(offeredSites(null)), ["instagram", "shorts", "x"]);
});

test("TikTok is offered only while the server has the TikTok gateway", () => {
  assert.deepEqual(names(offeredSites({ features: [], tiktok: true })), [
    "instagram",
    "shorts",
    "x",
    "tiktok",
  ]);
  assert.ok(!names(offeredSites({ features: [], tiktok: false })).includes("tiktok"));
});

test("whole songs come with the youtube_songs feature, beside TikTok", () => {
  assert.deepEqual(names(offeredSites({ features: ["youtube_songs"], tiktok: true })), [
    ...names(SITES),
  ]);
});

test("a reel's site is told by its code", () => {
  const at = (code, url = "") => siteOf({ code, url }).label;
  assert.equal(at("tt:7234567890123456789", "https://www.tiktok.com/@a/video/1"), "TikTok video");
  assert.equal(at("yt:abcdefghijk", "https://www.youtube.com/shorts/abcdefghijk"), "YouTube Short");
  assert.equal(at("yt:abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"), "YouTube song");
  assert.equal(at("x:2118429544122307015"), "X post");
  assert.equal(at("DcafVd6BocI"), "Instagram reel");
});
