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
 * The Autopilot address for a tab and an open folder (none: the folders themselves).
 * @param {string} tab
 * @param {string | null} folder
 */
export function pileHref(tab, folder) {
  const params = new URLSearchParams();
  if (tab !== "ready") params.set("tab", tab);
  if (folder !== null) params.set("folder", folder);
  const query = params.toString();
  return `/autopilot/${query ? `?${query}` : ""}`;
}
