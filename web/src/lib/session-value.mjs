// Plain JavaScript so node --test can run it without a build step.

/** @param {Storage | undefined} storage */
function store(storage) {
  // Reading sessionStorage itself throws where site data is blocked.
  return storage ?? globalThis.sessionStorage;
}

/**
 * A value this tab keeps across pages and reloads. Private windows and blocked storage
 * just forget it.
 * @param {string} key
 * @param {Storage} [storage]
 * @returns {string | null}
 */
export function recall(key, storage) {
  try {
    return store(storage)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/**
 * @param {string} key
 * @param {string | null} value null forgets it
 * @param {Storage} [storage]
 */
export function remember(key, value, storage) {
  try {
    if (value === null) store(storage)?.removeItem(key);
    else store(storage)?.setItem(key, value);
  } catch {
    // Nothing to do: the value only saves a step later.
  }
}
