import assert from "node:assert/strict";
import { test } from "node:test";

import { recall, remember } from "../src/lib/session-value.mjs";

function memoryStorage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

test("a remembered value comes back until it is forgotten", () => {
  const storage = memoryStorage();
  assert.equal(recall("draft", storage), null);
  remember("draft", "abc123", storage);
  assert.equal(recall("draft", storage), "abc123");
  remember("draft", null, storage);
  assert.equal(recall("draft", storage), null);
});

test("blocked storage forgets quietly instead of throwing", () => {
  const blocked = {
    getItem() {
      throw new Error("SecurityError");
    },
    setItem() {
      throw new Error("QuotaExceededError");
    },
    removeItem() {
      throw new Error("SecurityError");
    },
  };
  assert.equal(recall("draft", blocked), null);
  assert.doesNotThrow(() => remember("draft", "abc", blocked));
  assert.doesNotThrow(() => remember("draft", null, blocked));
});

test("without any storage nothing is remembered", () => {
  assert.equal(recall("draft"), null);
  assert.doesNotThrow(() => remember("draft", "abc"));
});
