// Plain JavaScript so node --test can run it without a build step.

/**
 * @typedef {object} Poller
 * @property {(immediate?: boolean) => void} start begin polling, at once by default
 * @property {() => void} stop stop polling and forget the page's visibility
 * @property {(ms: number) => void} setDelay change the pause between calls
 * @property {() => void} now call again now (or as soon as the running call ends)
 */

/**
 * Calls `run` again `delay` ms after each call has finished, so slow answers never pile
 * up. Nothing runs while the page is hidden; coming back to it runs at once.
 * @param {() => unknown} run
 * @param {number} delay
 * @param {{ document?: any, timers?: { setTimeout: Function, clearTimeout: Function } }} [env]
 * @returns {Poller}
 */
export function createPoller(run, delay, env = {}) {
  const doc = env.document ?? globalThis.document;
  const timers = env.timers ?? globalThis;
  /** @type {unknown} */
  let timer = null;
  let stopped = true;
  let running = false;
  let again = false;

  const hidden = () => doc?.visibilityState === "hidden";

  function clear() {
    if (timer !== null) timers.clearTimeout(timer);
    timer = null;
  }

  function schedule() {
    clear();
    if (!stopped && !hidden()) timer = timers.setTimeout(tick, delay);
  }

  async function tick() {
    timer = null;
    if (stopped || hidden()) return;
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      await run();
    } catch {
      // The next call tries again.
    }
    running = false;
    if (again) {
      again = false;
      tick();
      return;
    }
    schedule();
  }

  function onVisibility() {
    if (hidden()) clear();
    else if (!stopped) {
      clear();
      tick();
    }
  }

  return {
    start(immediate = true) {
      if (!stopped) return;
      stopped = false;
      doc?.addEventListener?.("visibilitychange", onVisibility);
      if (immediate) tick();
      else schedule();
    },
    stop() {
      stopped = true;
      again = false;
      clear();
      doc?.removeEventListener?.("visibilitychange", onVisibility);
    },
    setDelay(ms) {
      if (ms === delay) return;
      delay = ms;
      if (!running) schedule();
    },
    now() {
      clear();
      tick();
    },
  };
}

/**
 * The previous value when the new one holds the same data, so a poll that brings
 * nothing new doesn't re-render the list it feeds.
 * @template T
 * @param {T} previous
 * @param {T} next
 * @returns {T}
 */
export function keepIfSame(previous, next) {
  if (previous === next) return previous;
  return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
}
