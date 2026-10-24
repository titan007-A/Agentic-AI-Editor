import assert from "node:assert/strict";
import { test } from "node:test";

import { wholeNumber } from "../src/lib/form-values.mjs";

test("a whole number in range is taken as it is", () => {
  assert.equal(wholeNumber("5", 1, 20), 5);
  assert.equal(wholeNumber(" 20 ", 1, 20), 20);
});

test("a cleared field stays empty instead of becoming 1", () => {
  assert.equal(wholeNumber("", 1, 20), null);
});

test("out of range, fractions and stray text are refused", () => {
  assert.equal(wholeNumber("0", 1, 20), null);
  assert.equal(wholeNumber("21", 1, 20), null);
  assert.equal(wholeNumber("2.5", 1, 20), null);
  assert.equal(wholeNumber("-3", 1, 20), null);
  assert.equal(wholeNumber("3a", 1, 20), null);
});
