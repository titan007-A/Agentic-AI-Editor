import assert from "node:assert/strict";
import { test } from "node:test";

import { guardLeaving, mayLeave } from "../src/lib/leave-guard.mjs";

test("leaving is free while nothing is unsaved", () => {
  assert.equal(
    mayLeave(() => assert.fail("must not ask")),
    true,
  );
});

test("unsaved work asks first, and the answer decides", () => {
  const release = guardLeaving("Discard unsaved changes?");
  const asked = [];
  assert.equal(
    mayLeave((message) => (asked.push(message), false)),
    false,
  );
  assert.equal(
    mayLeave(() => true),
    true,
  );
  assert.deepEqual(asked, ["Discard unsaved changes?"]);
  release();
  assert.equal(
    mayLeave(() => assert.fail("must not ask")),
    true,
  );
});

test("lifting a guard twice leaves other guards alone", () => {
  const first = guardLeaving("first");
  const second = guardLeaving("second");
  second();
  second();
  let asked = "";
  mayLeave((message) => ((asked = message), true));
  assert.equal(asked, "first");
  first();
});
