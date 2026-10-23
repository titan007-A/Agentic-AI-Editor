import assert from "node:assert/strict";
import { test } from "node:test";

import { blankPreset } from "../src/lib/preset-defaults.mjs";

const schema = {
  required: ["name", "label", "min_duration", "max_duration"],
  properties: {
    name: { type: "string" },
    label: { type: "string" },
    fps: { type: "integer", default: 30 },
    intro: { anyOf: [{ type: "string" }, { type: "null" }], default: null },
    captions: { $ref: "#/$defs/CaptionStyle", default: { enabled: true, size: 84 } },
    text_blocks: { type: "array", default: [] },
    min_duration: { type: "number" },
    max_duration: { type: "number" },
  },
};

test("a blank preset takes every default and the given name", () => {
  assert.deepEqual(blankPreset(schema, "my-reels"), {
    fps: 30,
    intro: null,
    captions: { enabled: true, size: 84 },
    text_blocks: [],
    name: "my-reels",
    label: "my-reels",
    min_duration: 15,
    max_duration: 60,
  });
});

test("editing a blank preset never changes the schema's defaults", () => {
  const preset = blankPreset(schema, "a");
  preset.captions.size = 40;
  assert.equal(schema.properties.captions.default.size, 84);
});
