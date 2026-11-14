// Plain JavaScript so node --test can run it without a build step.
// What was cut on the Autopilot piles, waiting to be pasted into another folder of the
// same preset. Kept for the browser tab (sessionStorage), so it survives switching between
// Ready and Needs a look, opening folders and reloading.

const KEY = "aiedit-pile-clip";

/** @typedef {{ preset: string, videos: number[], folders: number[], from: string }} Clip */

/** @param {Storage | null | undefined} [store] @returns {Clip | null} */
export function loadClip(store = globalThis.sessionStorage) {
  try {
    const clip = JSON.parse(store?.getItem(KEY) ?? "null");
    if (
      clip &&
      typeof clip.preset === "string" &&
      Array.isArray(clip.videos) &&
      Array.isArray(clip.folders) &&
      clip.videos.length + clip.folders.length > 0
    ) {
      return { from: "", ...clip };
    }
  } catch {
    // unreadable or blocked storage: nothing cut
  }
  return null;
}

/** @param {Clip | null} clip @param {Storage | null | undefined} [store] */
export function saveClip(clip, store = globalThis.sessionStorage) {
  try {
    if (clip) store?.setItem(KEY, JSON.stringify(clip));
    else store?.removeItem(KEY);
  } catch {
    // blocked storage: the clip lives only in the page
  }
}

/** "2 videos and 1 folder" @param {Clip} clip */
export function clipSays(clip) {
  const parts = [];
  if (clip.videos.length)
    parts.push(`${clip.videos.length} ${clip.videos.length === 1 ? "video" : "videos"}`);
  if (clip.folders.length)
    parts.push(`${clip.folders.length} ${clip.folders.length === 1 ? "folder" : "folders"}`);
  return parts.join(" and ");
}

/**
 * Why the clip can't be pasted here, or null when it can.
 * @param {{ id: number, parent_id: number | null }[]} made the folders made in presets' folders
 * @param {Clip} clip
 * @param {string | null} preset the open preset's folder (null: none open)
 * @param {number | null} here the made folder open in it (null: the preset's folder itself)
 * @param {string} presetLabel how the clip's preset reads
 */
export function pasteProblem(made, clip, preset, here, presetLabel) {
  if (preset === null) return `Open a ${presetLabel} folder to paste`;
  if (preset !== clip.preset) return `Cut from ${presetLabel}: paste it in one of its folders`;
  const known = new Map(made.map((f) => [f.id, f]));
  for (let at = here, steps = 0; at !== null && steps < 100; steps++) {
    if (clip.folders.includes(at)) return "A folder can't go inside itself";
    at = known.get(at)?.parent_id ?? null;
  }
  return null;
}
