// Plain JavaScript so node --test can run it without a build step.

/**
 * Whether an output is ready to post and which doubts to list. The editor's "looks
 * fine" overrides the agent's doubts, as it does for the server's piles.
 * @param {{ issues?: string[], doubts?: string[], verdict?: string | null }} output
 */
export function readiness(output) {
  const issues = output.issues ?? [];
  // Issues already show as badges; older outputs have issues but no doubts.
  const doubts = (output.doubts ?? []).filter((d) => !issues.includes(d));
  const approved = output.verdict === "approved";
  return { ready: approved || (doubts.length === 0 && issues.length === 0), approved, doubts };
}
