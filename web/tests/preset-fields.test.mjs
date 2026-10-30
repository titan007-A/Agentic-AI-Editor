import assert from "node:assert/strict";
import { test } from "node:test";

import {
  counts,
  fieldLabel,
  fieldNotes,
  nextValues,
  presetFields,
} from "../src/lib/preset-fields.mjs";

// rishab's IMG_5071 and IMG_5101, as the server sends them.
const series = {
  name: "img-5071-9x16",
  text_blocks: [
    { anchor: "top", text: "Si amas las canciones" },
    { anchor: "bottom", text: "Día {day} : Canción : {song_number}" },
  ],
};
const named = {
  name: "img-5101-9x16",
  text_blocks: [
    { anchor: "top", text: "Los temas" },
    { anchor: "bottom", text: "{song} ({year}) – {artist}" },
    { anchor: "bottom", text: "Sígueme para más" },
  ],
};

test("a preset needs its own words in braces, not the song, artist or headline", () => {
  assert.deepEqual(presetFields(series), ["day", "song_number"]);
  assert.deepEqual(presetFields(named), ["year"]);
  assert.deepEqual(
    presetFields({ name: "x", text_blocks: [{ anchor: "top", text: "{headline}" }] }),
    [],
  );
  assert.deepEqual(presetFields({ name: "x" }), []);
});

test("words a PDF gives above or below take the place of the blocks there", () => {
  assert.deepEqual(presetFields(series, ["top", "bottom"]), []);
  assert.deepEqual(presetFields(series, ["top"]), ["day", "song_number"]);
  assert.deepEqual(presetFields(named, ["bottom"]), []);
});

test("fields are labelled in words and only numbers count up", () => {
  assert.equal(fieldLabel("song_number"), "Song number");
  assert.equal(fieldLabel("day"), "Day");
  assert.ok(counts("song_number"));
  assert.ok(!counts("day"));
  assert.ok(!counts("year"));
});

test("three videos are songs 34, 35 and 36 of the same day", () => {
  const values = { day: "12", song_number: "34" };
  const used = {};
  const notes = [0, 1, 2].map(() => fieldNotes(["day", "song_number"], values, used));
  assert.deepEqual(notes, [
    "day: 12\nsong number: 34",
    "day: 12\nsong number: 35",
    "day: 12\nsong number: 36",
  ]);
  assert.deepEqual(nextValues(values, used), { day: "12", song_number: "37" });
});

test("a job whose preset doesn't use a field doesn't use up a number", () => {
  const values = { day: "3", song_number: "9", year: "1992" };
  const used = {};
  assert.equal(fieldNotes(["year"], values, used), "year: 1992");
  assert.equal(fieldNotes(["day", "song_number"], values, used), "day: 3\nsong number: 9");
  assert.deepEqual(nextValues(values, used), { day: "3", song_number: "10", year: "1992" });
});

test("a number that isn't one is given as typed", () => {
  const used = {};
  assert.equal(fieldNotes(["song_number"], { song_number: " 7a " }, used), "song number: 7a");
  assert.deepEqual(nextValues({ song_number: "7a" }, used), { song_number: "7a" });
});
