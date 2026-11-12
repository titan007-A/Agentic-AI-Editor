/** What just happened that the fun extras react to (the penguin, mostly). */
export type FunMood = "done" | "failed" | "badge" | "secret";

const EVENT = "aiedit:fun";

export function cheer(mood: FunMood) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<FunMood>(EVENT, { detail: mood }));
}

export function onCheer(listen: (mood: FunMood) => void): () => void {
  const handler = (e: Event) => listen((e as CustomEvent<FunMood>).detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
