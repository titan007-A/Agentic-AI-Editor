/** The fun extras' settings, kept in this browser (they are about this screen). */
const KEY = "aiedit-fun-prefs";

export type FunPrefs = {
  city: string; // weather mood: empty for off
  festivals: boolean; // festival skins
  penguin: boolean;
};

const DEFAULTS: FunPrefs = { city: "", festivals: true, penguin: true };
const EVENT = "aiedit:fun-prefs";

export function readPrefs(): FunPrefs {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return DEFAULTS;
  }
}

export function writePrefs(next: Partial<FunPrefs>) {
  const all = { ...readPrefs(), ...next };
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // private window: holds until the page reloads
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: all }));
}

export function onPrefs(listen: (prefs: FunPrefs) => void): () => void {
  const handler = (e: Event) => listen((e as CustomEvent<FunPrefs>).detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
