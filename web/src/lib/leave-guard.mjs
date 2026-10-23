// Plain JavaScript so node --test can run it without a build step.

/** @type {string[]} */
const reasons = [];

/**
 * Asks before in-app navigation leaves a page with unsaved work. Returns the function
 * that lifts the guard again.
 * @param {string} message the question to ask, such as "Discard unsaved changes?"
 */
export function guardLeaving(message) {
  const reason = String(message);
  reasons.push(reason);
  return () => {
    const at = reasons.lastIndexOf(reason);
    if (at !== -1) reasons.splice(at, 1);
  };
}

/**
 * Whether navigation may go ahead: nothing is unsaved, or the user agrees to lose it.
 * @param {(message: string) => boolean} [ask]
 */
export function mayLeave(ask = (message) => globalThis.confirm(message)) {
  const reason = reasons.at(-1);
  return reason === undefined || ask(reason);
}
