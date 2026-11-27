// Plain JavaScript so node --test can run it without a build step.

/**
 * @typedef {{ song: string, artist: string, label_group: string, what: string,
 *   what_label: string, note: string }} Blocked
 * @typedef {{ song: string, artist: string, year: string | null, label: string,
 *   group: string, group_name: string, blocked: Blocked | null, notified: boolean,
 *   new: boolean }} Notice
 * @typedef {{ label: string, tone: "blocked" | "warn" | "quiet", title: string }} SongBadge
 */

/** How a label group is named on a badge: "UMG", "Sony", "Warner". */
const SHORT = { UMG: "UMG", Sony: "Sony", Warner: "Warner" };

/**
 * What the song's notice asks for: "blocked" (on the strike list), "warn" (a major's song or
 * a new one), "quiet" (only named), or null without a notice.
 * @param {Notice | null | undefined} notice
 */
export function noticeTone(notice) {
  if (!notice) return null;
  if (notice.blocked) return "blocked";
  if (notice.notified || notice.new) return "warn";
  return "quiet";
}

/** @param {Blocked} blocked */
function blockedWhy(blocked) {
  return [blocked.label_group !== "other" ? blocked.label_group : "", blocked.note]
    .filter(Boolean)
    .join(", ");
}

/**
 * The little badges a job, a research reel or a channel reel shows for its song.
 * @param {{ year?: string | null, new?: boolean, group?: string, label?: string,
 *   blocked?: Blocked | null }} found
 * @returns {SongBadge[]}
 */
export function songBadges(found) {
  /** @type {SongBadge[]} */
  const badges = [];
  if (found.blocked) {
    const why = blockedWhy(found.blocked);
    badges.push({
      label: "blocked before",
      tone: "blocked",
      title: `On the strike list: ${found.blocked.what_label}${why ? ` (${why})` : ""}`,
    });
  }
  const group = found.group ?? "";
  if (group in SHORT) {
    badges.push({
      label: SHORT[/** @type {keyof typeof SHORT} */ (group)],
      tone: "warn",
      title: `A ${group} label's song${found.label ? ` (${found.label})` : ""}`,
    });
  }
  if (found.new && found.year) {
    badges.push({ label: `${found.year} · new`, tone: "warn", title: "Out in the last 3 years" });
  }
  return badges;
}

/**
 * The job page's banner: a heading and the lines under it.
 * @param {Notice} notice
 * @returns {{ heading: string, lines: string[] }}
 */
export function noticeText(notice) {
  const name = `“${notice.song}”${notice.artist ? ` by ${notice.artist}` : ""}`;
  const lines = [];
  let heading = `${name}: not on the strike list`;
  if (notice.blocked) {
    const why = blockedWhy(notice.blocked);
    heading = `Blocked before: ${name}`;
    lines.push(
      `It was ${notice.blocked.what_label}${why ? ` (${why})` : ""}. It will be blocked again: pick another song.`,
    );
  }
  if (notice.notified) {
    if (!notice.blocked) heading = `This song is ${notice.group_name}'s`;
    lines.push(
      `${name} is a ${notice.group_name} song${notice.label ? ` (${notice.label})` : ""}. ` +
        "Its label blocks or mutes reels of its songs.",
    );
  }
  if (notice.new && notice.year) {
    if (!notice.blocked && !notice.notified) heading = `A new song: ${notice.year}`;
    lines.push(
      `It came out in ${notice.year}, and labels block new songs worldwide more often. ` +
        "Pick an older song by this artist. If you keep it, keep the reel short, look at " +
        "Copyright alerts a day after posting, and put the song on the strike list if it gets blocked.",
    );
  }
  if (!notice.year) lines.push("Couldn't tell when it came out.");
  else if (!notice.new && (notice.blocked || notice.notified)) {
    lines.push(`It came out in ${notice.year}.`);
  }
  if (!notice.label) lines.push("No label found for it.");
  else if (!notice.notified && !notice.blocked) {
    lines.push(`Label: ${notice.label}${notice.year ? `, ${notice.year}` : ""}.`);
  }
  return { heading, lines };
}

/**
 * A toast's few words for a notice that needs one, null for one that doesn't.
 * @param {Notice | null | undefined} notice
 */
export function toastText(notice) {
  if (!notice) return null;
  const name = `“${notice.song}”`;
  if (notice.blocked) return `${name} was blocked before: pick another song`;
  if (notice.notified && notice.new) {
    return `${name} is ${notice.group_name}'s, and new (${notice.year})`;
  }
  if (notice.notified) return `${name} is ${notice.group_name}'s song`;
  if (notice.new) return `${name} is a new song (${notice.year})`;
  return null;
}

/**
 * What a toast is about, so the same notice isn't toasted twice.
 * @param {string} id the job's
 * @param {Notice | null | undefined} notice
 */
export function toastKey(id, notice) {
  return toastText(notice) ? `${id}:${toastText(notice)}` : null;
}
