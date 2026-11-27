// Plain JavaScript so node --test can run it without a build step.

/**
 * Where a research can search (see research.SITES), in the order shown and sent.
 * @type {{ site: "instagram" | "shorts" | "x" | "tiktok" | "songs", label: string }[]}
 */
export const SITES = [
  { site: "instagram", label: "Instagram reels" },
  { site: "shorts", label: "YouTube Shorts" },
  { site: "x", label: "X (posts with a video)" },
  { site: "tiktok", label: "TikTok (through the TikTok gateway)" },
  { site: "songs", label: "YouTube full songs (artist / label channel)" },
];

/**
 * The sites offered to the person signed in: whole songs with the youtube_songs feature,
 * TikTok while the server has the TikTok gateway.
 * @param {{ features?: string[], tiktok?: boolean } | null} me
 */
export function offeredSites(me) {
  const songs = Boolean(me?.features?.includes("youtube_songs"));
  return SITES.filter(
    ({ site }) => (site !== "songs" || songs) && (site !== "tiktok" || Boolean(me?.tiktok)),
  );
}

/**
 * Where a reel a research gave is, by its code and link (see research.code_of): the site's
 * name, what it calls the video, and both together.
 * @param {{ code: string, url: string }} item
 */
export function siteOf(item) {
  if (item.code.startsWith("yt:"))
    return item.url.includes("/shorts/")
      ? { site: "YouTube", what: "Short", label: "YouTube Short" }
      : { site: "YouTube", what: "song", label: "YouTube song" };
  if (item.code.startsWith("x:")) return { site: "X", what: "post", label: "X post" };
  if (item.code.startsWith("tt:")) return { site: "TikTok", what: "video", label: "TikTok video" };
  return { site: "Instagram", what: "reel", label: "Instagram reel" };
}
