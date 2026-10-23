import assert from "node:assert/strict";
import { test } from "node:test";

import { isCancelled, sendFile } from "../src/lib/upload.mjs";

/** An XMLHttpRequest stand-in the test drives by hand. */
function fakeXhr() {
  const made = [];
  class FakeXhr {
    upload = {};
    constructor() {
      made.push(this);
    }
    open(method, url) {
      Object.assign(this, { method, url });
    }
    send(body) {
      this.body = body;
    }
    abort() {
      this.aborted = true;
      this.onabort?.();
    }
    progress(loaded, total) {
      this.upload.onprogress?.({ lengthComputable: true, loaded, total });
    }
    respond(status, text, statusText = "") {
      Object.assign(this, { status, statusText, responseText: text });
      this.onload?.();
    }
  }
  return { FakeXhr, made };
}

const file = new Blob(["video bytes"], { type: "video/mp4" });

test("sends the file as the form's file field and reports progress", async () => {
  const { FakeXhr, made } = fakeXhr();
  const seen = [];
  const pending = sendFile("/api/uploads", file, {
    XHR: FakeXhr,
    onProgress: (fraction) => seen.push(fraction),
  });
  const [xhr] = made;
  assert.equal(xhr.method, "POST");
  assert.equal(xhr.url, "/api/uploads");
  assert.equal(xhr.body.get("file").size, file.size);
  xhr.progress(25, 100);
  xhr.progress(100, 100);
  xhr.respond(200, JSON.stringify({ source: "upload:abc/clip.mp4", name: "clip.mp4" }));
  assert.deepEqual(await pending, {
    status: 200,
    statusText: "",
    body: { source: "upload:abc/clip.mp4", name: "clip.mp4" },
  });
  assert.deepEqual(seen, [0.25, 1]);
});

test("an error response still resolves, with its detail", async () => {
  const { FakeXhr, made } = fakeXhr();
  const pending = sendFile("/api/uploads", file, { XHR: FakeXhr });
  made[0].respond(400, JSON.stringify({ detail: "unsupported file type .3gp" }), "Bad Request");
  const result = await pending;
  assert.equal(result.status, 400);
  assert.equal(result.body.detail, "unsupported file type .3gp");
});

test("a body that isn't JSON comes back as null", async () => {
  const { FakeXhr, made } = fakeXhr();
  const pending = sendFile("/api/uploads", file, { XHR: FakeXhr });
  made[0].respond(502, "<html>Bad gateway</html>", "Bad Gateway");
  assert.equal((await pending).body, null);
});

test("cancelling aborts the request", async () => {
  const { FakeXhr, made } = fakeXhr();
  const controller = new AbortController();
  const pending = sendFile("/api/uploads", file, { XHR: FakeXhr, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error) => isCancelled(error));
  assert.equal(made[0].aborted, true);
});

test("an upload cancelled before it starts never opens a request", async () => {
  const { FakeXhr, made } = fakeXhr();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    sendFile("/api/uploads", file, { XHR: FakeXhr, signal: controller.signal }),
    isCancelled,
  );
  assert.equal(made.length, 0);
});

test("a dropped connection is a readable error, not a cancel", async () => {
  const { FakeXhr, made } = fakeXhr();
  const pending = sendFile("/api/uploads", file, { XHR: FakeXhr });
  made[0].onerror();
  await assert.rejects(pending, (error) => !isCancelled(error) && /connection/.test(error.message));
});
