// Plain JavaScript so node --test can run it without a build step.

/** What each status of a channel autopilot's video means, in a few words. */
export const STATUS = {
  queued: "waiting to be checked",
  checking: "being checked",
  skipped: "skipped",
  unused: "not needed",
  editing: "being edited",
  reviewing: "edit being checked",
  look: "needs a look",
  awaiting: "waiting for your approval",
  ready: "ready: posts at the next time",
  due: "posting now",
  posting: "posting now",
  posted: "posted",
  failed: "post failed",
};

/** Statuses that are a problem a person should see. */
export const TROUBLE = ["look", "failed"];

/**
 * What a person can do with a video: approve it (it posts at the next time), skip it, or
 * try a failed post again.
 * @param {string} status
 * @returns {{ approve: boolean, skip: boolean, retry: boolean }}
 */
export function actions(status) {
  return {
    approve: status === "awaiting" || status === "look",
    skip: ["queued", "editing", "reviewing", "look", "awaiting", "ready", "failed"].includes(
      status,
    ),
    retry: status === "failed",
  };
}

// A post's numbers, in the order shown; Meta gives views, reach and saves only with the
// insights permission.
const NUMBERS = [
  ["views", "Views"],
  ["reach", "Reach"],
  ["saved", "Saves"],
  ["shares", "Shares"],
  ["likes", "Likes"],
  ["comments", "Comments"],
];

/**
 * The numbers to show for a post, as [label, value] pairs: those Meta gave.
 * @param {Record<string, number> | null | undefined} insights
 * @returns {[string, number][]}
 */
export function numbers(insights) {
  if (!insights) return [];
  /** @type {[string, number][]} */
  const shown = [];
  for (const [key, label] of NUMBERS) {
    const value = insights[key];
    if (typeof value === "number") shown.push([label, value]);
  }
  return shown;
}

/**
 * A number the short way: 1234 is "1.2k".
 * @param {number} value
 */
export function short(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(value);
}

/**
 * The post times for `perDay` posts: the ones chosen, the earliest first; when more are
 * wanted, the defaults not chosen yet; when fewer, the `kept` ones (those saved) first,
 * then the earliest.
 * @param {number} perDay
 * @param {string[]} chosen
 * @param {string[]} defaults
 * @param {string[]} [kept]
 * @returns {string[]}
 */
export function timesFor(perDay, chosen, defaults, kept = []) {
  let times = [...new Set(chosen.filter(Boolean))].sort();
  if (times.length > perDay) {
    const saved = times.filter((t) => kept.includes(t));
    times = [...saved, ...times.filter((t) => !kept.includes(t))].slice(0, perDay);
  }
  for (const t of defaults) {
    if (times.length >= perDay) break;
    if (!times.includes(t)) times.push(t);
  }
  let hour = 0;
  while (times.length < perDay && hour < 24) {
    const t = `${String(hour).padStart(2, "0")}:00`;
    if (!times.includes(t)) times.push(t);
    hour += 1;
  }
  return times.sort().slice(0, perDay);
}

/**
 * The checks a video went through, in a line: "8 of 9 checks passed".
 * @param {{ ok: boolean }[]} checks
 */
export function passed(checks) {
  if (!checks?.length) return "not checked yet";
  const ok = checks.filter((c) => c.ok).length;
  return ok === checks.length
    ? `all ${ok} checks passed`
    : `${ok} of ${checks.length} checks passed`;
}

/**
 * The song a video is, as Shazam heard it, else as the research named it.
 * @param {{ heard_song: string, heard_artist: string, song: string, artist: string, code: string }} post
 */
export function songName(post) {
  const song = post.heard_song || post.song;
  const artist = post.heard_song ? post.heard_artist : post.artist;
  if (!song) return post.code;
  return artist ? `${song} by ${artist}` : song;
}
