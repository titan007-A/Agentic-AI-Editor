// Plain JavaScript so node --test can run it without a build step.

const BUSY_WORDS = {
  drafting: "writing",
  changing: "changing",
  painting: "painting pictures",
  voicing: "recording voice",
};

/**
 * The parts (song.choices) a Song story plays: as it says, or for a reel made before it
 * said, the part starting where it does.
 * @param {{ playing?: number[], start: number, choices: { start: number }[] }} song
 * @returns {number[]}
 */
export function playingParts(song) {
  if (song.playing?.length) return song.playing;
  const found = song.choices.findIndex((c) => Math.abs(c.start - song.start) < 1.5);
  return found >= 0 ? [found] : [];
}

/**
 * How far along another reel of the song is, in a few words: waiting its turn, being
 * made, ready, rendering, rendered, or what went wrong.
 * @param {{ status: string, message: string, error: string | null,
 *   render: { status: string } | null }} part
 * @returns {string}
 */
export function partStatus(part) {
  if (part.status === "failed") return `failed: ${part.error ?? "something went wrong"}`;
  if (part.status === "queued") return part.message;
  if (part.status !== "ready") return BUSY_WORDS[part.status] ?? part.status;
  const render = part.render?.status;
  if (render === "done") return "rendered";
  if (render === "failed") return "ready, the render failed";
  if (render === "queued" || render === "running") return "rendering";
  return "ready";
}

/**
 * For each of a Song story's scenes, whether it is its line's second picture (a line on
 * screen a long time has two, a scene each).
 * @param {{ pictures?: number }[]} lines
 * @returns {boolean[]}
 */
export function secondPictures(lines) {
  return lines.flatMap((line) => ((line.pictures ?? 1) >= 2 ? [false, true] : [false]));
}
