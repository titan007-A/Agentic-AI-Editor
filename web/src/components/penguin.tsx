"use client";

import { useEffect, useRef, useState } from "react";

import { confetti } from "@/lib/confetti";
import { useFunStats } from "@/hooks/use-fun-stats";
import { cheer, onCheer, type FunMood } from "@/lib/fun-events";
import { onPrefs, readPrefs } from "@/lib/fun-prefs";
import { sounds } from "@/lib/sounds";
import { cn } from "@/lib/utils";

const HIDE_KEY = "aiedit-penguin-hidden";
const LINES = [
  "Ek aur reel? Chalo!",
  "Stay frosty.",
  "I only edit in slow motion: waddling.",
  "That last reel? Chef's kiss.",
  "Hydrate. Then render.",
  "Fun fact: penguins can't fly. Reels can, though.",
  "Brrr. Is the GPU cold or is it just me?",
  "Instagram won't know what hit it.",
  "Ready-to-post pile is looking chunky today.",
  "Psst: try typing s-n-o-w somewhere.",
];
// Typed anywhere outside a text box: a snowstorm.
const SNOW_WORD = "snow";
const KONAMI = [
  "ArrowUp",
  "ArrowUp",
  "ArrowDown",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "ArrowLeft",
  "ArrowRight",
  "b",
  "a",
];

type Mood = "idle" | FunMood | "sleep";

// What the penguin has earned by reels made: a hat, then sunglasses, then a crown.
export const LEVELS = [
  { reels: 100, wears: "a hat" },
  { reels: 500, wears: "sunglasses" },
  { reels: 1000, wears: "a crown" },
] as const;

export function wearing(reels: number) {
  return {
    hat: reels >= 100 && reels < 1000,
    shades: reels >= 500,
    crown: reels >= 1000,
    next: LEVELS.find((l) => reels < l.reels) ?? null,
  };
}

function asleep(): boolean {
  const hour = new Date().getHours();
  return hour < 5;
}

function hiddenNow(): boolean {
  try {
    return sessionStorage.getItem(HIDE_KEY) === "1";
  } catch {
    return false;
  }
}

/** A small penguin in the corner for accounts trying the fun feature: it dances when a
 * reel is done or a badge is won, droops when a job fails, sleeps after midnight, and
 * says something when tapped. Also the hidden extras: typing "snow" (or the Konami code)
 * outside a text box brings a snowstorm, and five quick taps on the logo (within 3 s) a secret. */
export function Penguin() {
  const [mood, setMood] = useState<Mood>(() => (asleep() ? "sleep" : "idle"));
  // Each thing said is new, even the same words again, so it stays its full time.
  const [said, setSaid] = useState<{ text: string; at: number } | null>(null);
  const line = said?.text ?? null;
  const setLine = (text: string | null) => setSaid(text === null ? null : { text, at: Date.now() });
  const [hidden, setHidden] = useState(hiddenNow);
  const [shown, setShown] = useState(() => readPrefs().penguin);
  const stats = useFunStats(0);
  const reels = stats?.me.total ?? 0;
  const dressed = wearing(reels);
  useEffect(() => onPrefs((p) => setShown(p.penguin)), []);
  const back = useRef<number | undefined>(undefined);

  useEffect(() => {
    const settle = (next: Mood, ms: number) => {
      setMood(next);
      window.clearTimeout(back.current);
      back.current = window.setTimeout(() => setMood(asleep() ? "sleep" : "idle"), ms);
    };
    const off = onCheer((m) => {
      if (m === "failed") {
        settle("failed", 5000);
        setLine("Oh no, that one failed. Open it to see why.");
      } else {
        settle(m, 3500);
        if (m === "secret") setLine("You found it! Nobody knows about this. Except me.");
      }
    });
    const tick = window.setInterval(() => {
      setMood((m) =>
        m === "idle" && asleep() ? "sleep" : m === "sleep" && !asleep() ? "idle" : m,
      );
    }, 60000);
    return () => {
      off();
      window.clearInterval(tick);
      window.clearTimeout(back.current);
    };
  }, []);

  useEffect(() => {
    if (!said) return;
    const t = window.setTimeout(() => setSaid(null), 4500);
    return () => window.clearTimeout(t);
  }, [said]);

  // The hidden extras.
  useEffect(() => {
    let typed = "";
    let keys: string[] = [];
    const storm = () => {
      confetti(220, true);
      sounds.streak();
      cheer("secret");
    };
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable=true]")) return;
      keys = [...keys, e.key].slice(-KONAMI.length);
      if (keys.join() === KONAMI.join()) {
        keys = [];
        storm();
      }
      if (e.key.length === 1) {
        typed = (typed + e.key.toLowerCase()).slice(-SNOW_WORD.length);
        if (typed === SNOW_WORD) {
          typed = "";
          storm();
        }
      }
    };
    let taps: number[] = [];
    const onTap = (e: MouseEvent) => {
      const logo = (e.target as HTMLElement | null)?.closest("[data-logo]");
      if (!logo) return;
      const now = Date.now();
      taps = [...taps.filter((t) => now - t < 3000), now];
      if (taps.length >= 5) {
        taps = [];
        e.preventDefault();
        confetti(120);
        sounds.badge();
        cheer("secret");
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onTap, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onTap, true);
    };
  }, []);

  if (hidden || !shown) return null;
  const happy = mood === "done" || mood === "badge" || mood === "secret";
  return (
    <div className="pointer-events-none fixed right-3 bottom-3 z-40 flex flex-col items-end gap-1 sm:right-5 sm:bottom-5">
      {line && (
        <div
          role="status"
          className="pointer-events-auto max-w-56 rounded-[var(--wobble-lg)] border-2 border-foreground bg-card px-3 py-2 text-sm shadow-[3px_4px_0_var(--hard-shadow)]"
        >
          {line}
          <button
            type="button"
            className="mt-1 block text-xs text-muted-foreground underline"
            onClick={() => {
              setHidden(true);
              try {
                sessionStorage.setItem(HIDE_KEY, "1");
              } catch {
                // private window: hidden until the page reloads
              }
            }}
          >
            Hide the penguin for now
          </button>
        </div>
      )}
      <button
        type="button"
        aria-label="The penguin"
        data-testid="penguin"
        data-mood={mood}
        className={cn(
          "pointer-events-auto size-14 cursor-pointer select-none sm:size-20",
          happy && "penguin-dance",
          mood === "failed" && "penguin-sad",
          mood === "idle" && "penguin-idle",
        )}
        onClick={() => {
          if (mood === "sleep") {
            setMood("idle");
            setLine("Huh? Oh, hi. I was resting my eyes.");
            return;
          }
          const roll = Math.random();
          if (dressed.next && roll < 0.3) {
            const more = dressed.next.reels - reels;
            setLine(`${more} more reel${more === 1 ? "" : "s"} and I get ${dressed.next.wears}!`);
          } else setLine(LINES[Math.floor(roll * LINES.length)]);
        }}
      >
        <svg viewBox="0 0 64 64" aria-hidden className="size-full drop-shadow-sm">
          <ellipse
            cx="32"
            cy="38"
            rx="17"
            ry="22"
            fill="#1c232b"
            stroke="var(--ink)"
            strokeWidth="1.5"
          />
          <ellipse cx="32" cy="42" rx="11" ry="16" fill="#f6f4ef" />
          <circle cx="32" cy="20" r="12" fill="#1c232b" stroke="var(--ink)" strokeWidth="1.5" />
          <ellipse cx="32" cy="23" rx="8" ry="7" fill="#f6f4ef" />
          {mood === "sleep" || mood === "failed" ? (
            <g stroke="#1c232b" strokeWidth="1.6" strokeLinecap="round" fill="none">
              <path d={mood === "sleep" ? "M26 21 q2 2 4 0" : "M26 23 q2 -2 4 0"} />
              <path d={mood === "sleep" ? "M34 21 q2 2 4 0" : "M34 23 q2 -2 4 0"} />
            </g>
          ) : (
            <g fill="#1c232b">
              <circle cx="28.5" cy="21" r="1.8" className="penguin-eye" />
              <circle cx="35.5" cy="21" r="1.8" className="penguin-eye" />
            </g>
          )}
          <path d="M29 25 L35 25 L32 29 Z" fill="var(--tangerine)" />
          <path d="M16 36 q-5 8 -2 16 q4 -4 5 -10 Z" fill="#1c232b" />
          <path d="M48 36 q5 8 2 16 q-4 -4 -5 -10 Z" fill="#1c232b" />
          <ellipse cx="25" cy="60" rx="5" ry="2.2" fill="var(--tangerine)" />
          <ellipse cx="39" cy="60" rx="5" ry="2.2" fill="var(--tangerine)" />
          <path d="M20 31 q12 6 24 0 l0 4 q-12 6 -24 0 Z" fill="var(--brick)" />
          {dressed.hat && (
            <g data-testid="penguin-hat">
              <path
                d="M21 13 q11 -12 22 0 Z"
                fill="var(--teal)"
                stroke="var(--ink)"
                strokeWidth="1"
              />
              <rect x="20" y="12" width="24" height="3.5" rx="1.5" fill="var(--mustard)" />
              <circle cx="32" cy="3.5" r="2.6" fill="var(--mustard)" />
            </g>
          )}
          {dressed.crown && (
            <g data-testid="penguin-crown">
              <path
                d="M22 12 L24 3 L28 8 L32 1 L36 8 L40 3 L42 12 Z"
                fill="#f4c542"
                stroke="#8a6512"
                strokeWidth="1"
              />
              <circle cx="32" cy="7" r="1.3" fill="#e0473a" />
            </g>
          )}
          {dressed.shades && mood !== "sleep" && mood !== "failed" && (
            <g data-testid="penguin-shades" fill="#0b0b0b">
              <rect x="25" y="18.5" width="6.5" height="4.5" rx="1.6" />
              <rect x="32.5" y="18.5" width="6.5" height="4.5" rx="1.6" />
              <rect x="31" y="19.5" width="2" height="1" />
            </g>
          )}
          {mood === "sleep" && (
            <text x="44" y="12" fontSize="9" fontFamily="var(--font-hand)" fill="var(--ink-soft)">
              z z
            </text>
          )}
        </svg>
      </button>
    </div>
  );
}
