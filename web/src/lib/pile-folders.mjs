// Plain JavaScript so node --test can run it without a build step.

/**
 * One folder per preset, in the order of each preset's newest video (the pile comes
 * newest first), each holding its videos in the pile's order.
 * @template {{ outputs: { preset: string }[] }} J
 * @param {J[]} jobs
 * @returns {{ preset: string, jobs: J[] }[]}
 */
export function folders(jobs) {
  /** @type {Map<string, J[]>} */
  const groups = new Map();
  for (const job of jobs) {
    const preset = job.outputs[0]?.preset ?? "";
    groups.set(preset, [...(groups.get(preset) ?? []), job]);
  }
  return [...groups].map(([preset, inFolder]) => ({ preset, jobs: inFolder }));
}

/**
 * The Autopilot address for a tab, an open preset folder (none: the folders themselves)
 * and a folder made inside it (none: the preset's folder itself).
 * @param {string} tab
 * @param {string | null} folder
 * @param {number | null} [inside]
 */
export function pileHref(tab, folder, inside = null) {
  const params = new URLSearchParams();
  if (tab !== "ready") params.set("tab", tab);
  if (folder !== null) params.set("folder", folder);
  if (folder !== null && inside !== null) params.set("in", String(inside));
  const query = params.toString();
  return `/autopilot/${query ? `?${query}` : ""}`;
}

/** @typedef {{ id: number, preset: string, parent_id: number | null, name: string }} Made */

/**
 * The folder a made folder sits in: null (straight in the preset's folder) also when that
 * one isn't among the viewer's.
 * @param {Made} made
 * @param {Map<number, Made>} known
 */
function parentOf(made, known) {
  return made.parent_id !== null && known.has(made.parent_id) ? made.parent_id : null;
}

/**
 * The folder a video was put in, or null: straight in its preset's folder (also when its
 * folder is gone or isn't the viewer's).
 * @param {{ outputs: { folder_id?: number | null }[] }} job
 * @param {Map<number, Made>} known
 */
export function placeOf(job, known) {
  const id = job.outputs[0]?.folder_id ?? null;
  return id !== null && known.has(id) ? id : null;
}

/**
 * The folders made straight inside `parent` (null: inside the preset's folder), by name.
 * @param {Made[]} made
 * @param {string} preset
 * @param {number | null} parent
 */
export function subfolders(made, preset, parent) {
  const known = new Map(made.map((f) => [f.id, f]));
  return made
    .filter((f) => f.preset === preset && parentOf(f, known) === parent)
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

/**
 * A folder and every folder inside it, however deep.
 * @param {Made[]} made
 * @param {number} id
 */
export function within(made, id) {
  const known = new Map(made.map((f) => [f.id, f]));
  const found = new Set([id]);
  for (let grew = true; grew;) {
    grew = false;
    for (const f of made) {
      const parent = parentOf(f, known);
      if (parent !== null && found.has(parent) && !found.has(f.id)) {
        found.add(f.id);
        grew = true;
      }
    }
  }
  return found;
}

/**
 * The folders from the preset's folder down to `id`, outermost first; empty for none.
 * @param {Made[]} made
 * @param {number | null} id
 */
export function trail(made, id) {
  const known = new Map(made.map((f) => [f.id, f]));
  const out = [];
  for (let at = id === null ? undefined : known.get(id); at && out.length < 50;) {
    out.unshift(at);
    const parent = parentOf(at, known);
    at = parent === null ? undefined : known.get(parent);
  }
  return out;
}

/**
 * Every folder made inside a preset's folder, each named by its trail ("Monday / Funny"),
 * in the order they nest, for a "move to" list.
 * @param {Made[]} made
 * @param {string} preset
 */
export function folderPaths(made, preset) {
  /** @type {{ id: number, path: string }[]} */
  const out = [];
  /** @param {number | null} parent @param {string} before */
  const walk = (parent, before) => {
    for (const f of subfolders(made, preset, parent)) {
      const path = before ? `${before} / ${f.name}` : f.name;
      out.push({ id: f.id, path });
      walk(f.id, path);
    }
  };
  walk(null, "");
  return out;
}
