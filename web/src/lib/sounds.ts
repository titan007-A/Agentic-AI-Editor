/** Little wins, played on the page itself (Web Audio, no files): a bell when a reel is
 * ready, a jingle for a badge, a sparkle when the streak grows. Off when the person turns
 * them off (kept per browser) or the browser won't play before a tap. */
export const SOUND_KEY = "aiedit-sounds";

let context: AudioContext | null = null;

export function soundsOn(): boolean {
  try {
    return localStorage.getItem(SOUND_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSounds(on: boolean) {
  try {
    localStorage.setItem(SOUND_KEY, on ? "on" : "off");
  } catch {
    // private window
  }
}

function audio(): AudioContext | null {
  if (typeof window === "undefined" || !soundsOn()) return null;
  try {
    context ??= new AudioContext();
    if (context.state === "suspended") void context.resume();
    return context;
  } catch {
    return null;
  }
}

/** Notes as [frequency Hz, start s, length s], each a soft bell. */
function play(notes: [number, number, number][], wave: OscillatorType = "sine", volume = 0.12) {
  const ctx = audio();
  if (!ctx) return;
  const now = ctx.currentTime;
  for (const [freq, start, length] of notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = wave;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + start);
    gain.gain.linearRampToValueAtTime(volume, now + start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + start + length);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now + start);
    osc.stop(now + start + length + 0.05);
  }
}

export const sounds = {
  /** A reel is ready: two bell tones. */
  ready: () =>
    play([
      [1318.5, 0, 0.6],
      [1975.5, 0.12, 0.9],
    ]),
  /** A badge won: a rising jingle. */
  badge: () =>
    play(
      [
        [523.25, 0, 0.25],
        [659.25, 0.1, 0.25],
        [783.99, 0.2, 0.25],
        [1046.5, 0.3, 0.6],
      ],
      "triangle",
    ),
  /** The streak grew: a quick sparkle. */
  streak: () =>
    play(
      [
        [1567.98, 0, 0.18],
        [2093, 0.07, 0.18],
        [2637, 0.14, 0.35],
      ],
      "sine",
      0.08,
    ),
};
