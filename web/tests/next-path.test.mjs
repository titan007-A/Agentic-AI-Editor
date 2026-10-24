import assert from "node:assert/strict";
import { test } from "node:test";

import { loginPath, safeNext } from "../src/lib/next-path.mjs";

const origin = "http://192.168.1.2:8000";

test("logging in returns to the page that asked", () => {
  assert.equal(safeNext("/job/?id=abc123", origin), "/job/?id=abc123");
  assert.equal(safeNext("/presets/", origin), "/presets/");
});

test("without a page to return to, it goes to the jobs page", () => {
  assert.equal(safeNext(null, origin), "/");
  assert.equal(safeNext("", origin), "/");
});

test("another site is never a place to return to", () => {
  for (const next of [
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example/",
    "/\t/evil.example/",
    "javascript:alert(1)",
    "job/?id=1",
  ]) {
    assert.equal(safeNext(next, origin), "/", next);
  }
});

test("it never returns to the login page, so it can't loop", () => {
  assert.equal(safeNext("/login/?next=/login/", origin), "/");
});

test("the login link carries the page being left", () => {
  assert.equal(loginPath("/job/?id=abc123"), "/login/?next=%2Fjob%2F%3Fid%3Dabc123");
  assert.equal(
    safeNext(new URLSearchParams(loginPath("/job/?id=abc123").split("?")[1]).get("next"), origin),
    "/job/?id=abc123",
  );
  assert.equal(loginPath("/"), "/login/");
  assert.equal(loginPath("/login/"), "/login/");
});
