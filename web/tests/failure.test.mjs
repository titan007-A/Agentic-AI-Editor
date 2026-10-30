import assert from "node:assert/strict";
import { test } from "node:test";

import { failureText } from "../src/lib/failure.mjs";

test("the server's own words come first", () => {
  assert.equal(failureText(409, "the research is still running"), "the research is still running");
});

test("an offline server behind Cloudflare says so instead of nothing", () => {
  // Cloudflare's 530 page isn't JSON and HTTP/2 has no status text: detail is "".
  for (const status of [502, 524, 530]) {
    assert.match(failureText(status, ""), new RegExp(`couldn't be reached \\(error ${status}\\)`));
  }
  assert.match(failureText(530, null), /try again/);
});

test("any other failure without words names its status", () => {
  assert.equal(failureText(500, ""), "The request failed (error 500).");
  assert.equal(failureText(413, "  "), "The request failed (error 413).");
});
