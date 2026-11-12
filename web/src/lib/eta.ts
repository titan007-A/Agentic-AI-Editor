/** Seconds left, as a person would say it; null when the server can't tell. */
export function timeLeft(seconds: number | null | undefined, queued = false): string | null {
  if (seconds === null || seconds === undefined) return null;
  const prefix = queued ? "ready in " : "";
  if (seconds < 50) return queued ? "ready in under a minute" : "under a minute left";
  if (seconds < 90) return queued ? `${prefix}about a minute` : "about a minute left";
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60);
    return queued ? `${prefix}about ${minutes} min` : `about ${minutes} min left`;
  }
  const hours = (seconds / 3600).toFixed(1);
  return queued ? `${prefix}about ${hours} h` : `about ${hours} h left`;
}
