"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { sounds } from "@/lib/sounds";

const KEY = "aiedit-fortune-day";
const LINES = [
  "Aaj ka reel, kal ka viral.",
  "The best hook is the one you haven't cut yet.",
  "A quiet render today, a loud comment section tomorrow.",
  "Your next reel will make someone hit replay. Twice.",
  "Picture abhi baaki hai, mere dost.",
  "Today the algorithm smiles on vocals only.",
  "Three reels before lunch and the day is yours.",
  "Somewhere, a stranger is about to discover their new favourite singer. Because of you.",
  "Ek reel roz, viral har hafte.",
  "Good captions are short. Great ones are shorter.",
  "Trust the edit. Then trust it a little less and watch it once more.",
  "The GPU is warm, the queue is short: perfect day for a streak.",
  "Your watermark is showing. In the good way.",
  "Rule of the day: start on the note, end on the silence.",
  "A reel a day keeps the unfollow away.",
  "Kal se nahi, aaj se.",
  "That song you skipped? It's someone's favourite. Edit it anyway.",
  "Fortune favours the one who hits Regenerate.",
  "Today's lucky preset: the one you use least.",
  "Be the hook you want to see in the world.",
  "Even legends started with zero views.",
  "Zindagi mein teen cheezein: chai, reels aur streak.",
  "Your 100th reel is closer than you think.",
  "Ctrl+S your energy. Render your dreams.",
  "The comments love a good high note.",
  "Small edit, big feels.",
  "If it gives you goosebumps, post it.",
  "No one remembers the export settings. Everyone remembers the voice.",
  "Mood: one more reel.",
  "A penguin believes in you. That's a lot of belief for a penguin.",
];

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

function firstVisit(): boolean {
  try {
    return localStorage.getItem(KEY) !== today();
  } catch {
    return false;
  }
}

/** A fortune cookie on the first visit of each day (fun feature): tap to crack it. */
export function FortuneCookie() {
  const [open, setOpen] = useState(firstVisit);
  const [cracked, setCracked] = useState(false);
  const [line] = useState(() => LINES[Math.floor(Math.random() * LINES.length)]);

  function close() {
    setOpen(false);
    try {
      localStorage.setItem(KEY, today());
    } catch {
      // private window: it may come again
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : close())}>
      <DialogContent className="sm:max-w-sm" data-testid="fortune">
        <DialogHeader>
          <DialogTitle>Today&apos;s fortune cookie</DialogTitle>
          <DialogDescription>
            {cracked ? "Here's what it says:" : "Tap it to crack it open."}
          </DialogDescription>
        </DialogHeader>
        <button
          type="button"
          aria-label="Crack the fortune cookie"
          className={`fortune-cookie mx-auto ${cracked ? "fortune-cracked" : ""}`}
          onClick={() => {
            if (!cracked) sounds.streak();
            setCracked(true);
          }}
        >
          <svg viewBox="0 0 120 70" className="h-24 w-40" aria-hidden>
            <g className="fortune-left">
              <path
                d="M60 12 C 30 8, 8 30, 14 52 C 30 46, 46 40, 60 40 Z"
                fill="#e9b949"
                stroke="var(--ink)"
                strokeWidth="2"
              />
            </g>
            <g className="fortune-right">
              <path
                d="M60 12 C 90 8, 112 30, 106 52 C 90 46, 74 40, 60 40 Z"
                fill="#dda43a"
                stroke="var(--ink)"
                strokeWidth="2"
              />
            </g>
          </svg>
        </button>
        {cracked && (
          <p
            className="fortune-slip mx-auto font-hand text-2xl leading-tight"
            data-testid="fortune-line"
          >
            {line}
          </p>
        )}
        <Button variant="outline" onClick={close}>
          {cracked ? "Thanks!" : "Not today"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
