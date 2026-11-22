// Plain JavaScript so node --test can run it without a build step.

/**
 * A time in a stream: "1:02:03", or "2:03" under an hour.
 * @param {number} seconds
 */
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

const LOOKING = ["queued", "reading", "listening", "cutting"];

/**
 * Whether a hunt is still at work: looking for its moments, or its clips being made.
 * @param {{ status: string, moments: { job: { status: string } | null }[] }} hunt
 */
export function working(hunt) {
  if (LOOKING.includes(hunt.status)) return true;
  return hunt.moments.some((m) => m.job && ["queued", "running"].includes(m.job.status));
}

/**
 * What a moment's clip is doing, in a few words.
 * @param {{ job: { status: string, progress: number, error: string | null } | null, error?: string }} moment
 */
export function clipState(moment) {
  if (!moment.job) return moment.error ? "couldn't be cut" : "not made";
  const { status, progress } = moment.job;
  if (status === "queued") return "waiting for its turn";
  if (status === "running") return `making the clip · ${Math.round((progress || 0) * 100)}%`;
  if (status === "failed") return "the clip failed";
  return "ready";
}

/**
 * A stream's length for a list: "2 h 14 min", "38 min", or "live".
 * @param {{ duration: number, live: boolean }} stream
 */
export function streamLength(stream) {
  if (stream.live) return "live now";
  const minutes = Math.round((stream.duration || 0) / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
