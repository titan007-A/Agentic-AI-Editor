import assert from "node:assert/strict";
import { test } from "node:test";

import { canShareFiles, shareVideoFile, videoFileName } from "../src/lib/file-share.mjs";

test("sharing synchronously hands only the MP4 file to the OS", async () => {
  const file = new File(["test bytes"], "reel.mp4", { type: "video/mp4" });
  let called = false;
  const pending = shareVideoFile(
    {
      canShare: (data) => data.files[0] === file,
      share: (data) => {
        called = true;
        assert.deepEqual(Object.keys(data), ["files"]);
        return Promise.resolve();
      },
    },
    file,
  );
  assert.equal(called, true, "must not await anything before invoking native sharing");
  await pending;
});

test("unsupported file sharing never falls back to sharing a URL", () => {
  assert.throws(() => shareVideoFile({ canShare: () => false, share: () => assert.fail() }, {}), /Download/);
  assert.throws(() => shareVideoFile({}, {}), /Download/);
});

test("a cancelled share comes back as AbortError", async () => {
  const browser = {
    canShare: () => true,
    share: () => Promise.reject(new DOMException("Cancelled", "AbortError")),
  };
  await assert.rejects(shareVideoFile(browser, {}), { name: "AbortError" });
});

test("only phones count as able to share files", () => {
  const api = { share() {}, canShare() {} };
  assert.equal(canShareFiles({ ...api, userAgentData: { mobile: true } }), true);
  assert.equal(canShareFiles({ ...api, userAgentData: { mobile: false } }), false);
  assert.equal(canShareFiles({ ...api, userAgent: "Mozilla/5.0 (Linux; Android 15)" }), true);
  assert.equal(canShareFiles({ ...api, userAgent: "Mozilla/5.0 (Windows NT 10.0)" }), false);
  assert.equal(canShareFiles({ userAgent: "Android" }), false);
});

test("video file names are safe, bounded and end in one .mp4", () => {
  assert.equal(videoFileName("reel.MP4"), "reel.mp4");
  assert.equal(videoFileName("a/b:c"), "a_b_c.mp4");
  assert.equal(videoFileName("   "), "edited-video.mp4");
  assert.equal(videoFileName("x".repeat(200)).length, 104);
});
