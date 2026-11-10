// Plain JavaScript so node --test can run it without a build step.

// A preset's own words in braces, "Día {day} : Canción : {song_number}", which only the
// person can give; the song, artist and headline are found by the editor (names.py).
const FIELD = /\{(\w+)\}/g;
const FOUND = new Set(["song", "artist", "headline"]);

/**
 * The words a preset needs given, in the order its text has them. Blocks above or below
 * the video whose words `replaced` gives instead (a PDF's top and bottom text) don't count:
 * the edit drops them (names.with_texts), unless a block there writes {headline}.
 * @param {import("./api").Preset} preset
 * @param {string[]} [replaced] "top", "bottom"
 * @returns {string[]}
 */
export function presetFields(preset, replaced = []) {
  const found = [];
  const blocks = preset.text_blocks ?? [];
  // Where a block writes {headline}, the given words are that headline and every block
  // there stays.
  const headlined = new Set(
    blocks.filter((b) => String(b.text ?? "").includes("{headline}")).map((b) => b.anchor),
  );
  // A random field is picked by the editor for each reel (random_fields), so not asked.
  const random = new Set(Object.keys(preset.random_fields ?? {}));
  for (const block of blocks) {
    if (replaced.includes(block.anchor) && !headlined.has(block.anchor)) continue;
    for (const [, name] of String(block.text ?? "").matchAll(FIELD)) {
      if (!FOUND.has(name) && !random.has(name) && !found.includes(name)) found.push(name);
    }
  }
  return found;
}

/**
 * Every word in braces a preset's text writes that a random number can fill: not the song,
 * artist or headline, which the editor finds.
 * @param {import("./api").Preset} preset
 * @returns {string[]}
 */
export function textFields(preset) {
  const found = [];
  for (const block of preset.text_blocks ?? []) {
    for (const [, name] of String(block.text ?? "").matchAll(FIELD)) {
      if (!FOUND.has(name) && !found.includes(name)) found.push(name);
    }
  }
  return found;
}

/**
 * The words a preset needs given for one of a PDF's items: its top and bottom text stand
 * in for the preset's there, and a year the PDF writes beside the name is that item's
 * {year}, so it isn't asked for (the notes carry it).
 * @param {import("./api").Preset} preset
 * @param {{ top?: string, bottom?: string, year?: string } | undefined} entry
 * @returns {string[]}
 */
export function entryFields(preset, entry) {
  if (!entry) return presetFields(preset);
  const replaced = /** @type {const} */ (["top", "bottom"]).filter((a) => entry[a]);
  const fromPdf = /^\d{4}$/.test(String(entry.year ?? "").trim()) ? ["year"] : [];
  return presetFields(preset, [...replaced]).filter((name) => !fromPdf.includes(name));
}

/** "song_number" -> "Song number". @param {string} name */
export function fieldLabel(name) {
  const words = name.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Whether a field may be left empty: the editor finds the song's year by itself (from
 * the post's caption, else MusicBrainz). @param {string} name */
export function optional(name) {
  return name === "year";
}

/** Whether a field counts up from one video to the next: a number, "song_number". */
export function counts(/** @type {string} */ name) {
  return /(^|_)(number|no|num)$/.test(name);
}

/**
 * The notes lines that give one job its fields: a counting field is the number given plus
 * how many jobs before this one used it, so three videos are songs 34, 35 and 36.
 * @param {string[]} fields the job's preset's
 * @param {Record<string, string>} values as typed
 * @param {Record<string, number>} used per field, jobs given it so far; updated
 * @returns {string}
 */
export function fieldNotes(fields, values, used) {
  return fields
    .filter((name) => !optional(name) || (values[name] ?? "").trim())
    .map((name) => {
      const typed = (values[name] ?? "").trim();
      let value = typed;
      if (counts(name) && /^\d+$/.test(typed)) {
        value = String(Number(typed) + (used[name] ?? 0));
      }
      used[name] = (used[name] ?? 0) + 1;
      return `${name.replace(/_/g, " ")}: ${value}`;
    })
    .join("\n");
}

/**
 * What to show next time: the same day, the song after the last one given.
 * @param {Record<string, string>} values as typed
 * @param {Record<string, number>} used per field, after the jobs were made
 * @returns {Record<string, string>}
 */
export function nextValues(values, used) {
  /** @type {Record<string, string>} */
  const next = {};
  for (const [name, typed] of Object.entries(values)) {
    const value = typed.trim();
    next[name] =
      counts(name) && /^\d+$/.test(value) && used[name]
        ? String(Number(value) + used[name])
        : value;
  }
  return next;
}
