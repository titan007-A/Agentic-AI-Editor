// Plain JavaScript so node --test can run it without a build step.

/** The answers a proxy gives when it can't reach the server (Cloudflare's 52x among them). */
const UNREACHED = new Set([502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 530]);

/**
 * What a failed request says: the server's own words when it gave any, else what the status
 * means. Cloudflare's error pages aren't JSON and HTTP/2 has no status text, so without this
 * a server that is offline for a moment showed an empty error.
 * @param {number} status
 * @param {unknown} detail the server's `detail`, or the status text
 */
export function failureText(status, detail) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (UNREACHED.has(status)) {
    return `The editor's server couldn't be reached (error ${status}). It is usually back within a minute: try again.`;
  }
  return `The request failed (error ${status}).`;
}

/** What a request that got no answer at all (no connection) says. */
export const NO_CONNECTION =
  "No connection to the editor's server. Check your internet and try again.";
