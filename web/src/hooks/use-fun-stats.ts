"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { api, type FunStats } from "@/lib/api";
import { confetti } from "@/lib/confetti";
import { fireworks } from "@/lib/fireworks";
import { cheer } from "@/lib/fun-events";
import { sounds } from "@/lib/sounds";

const SEEN_KEY = "aiedit-badges-seen";
// The streak last cheered, so a longer one gets a sparkle once.
const STREAK_KEY = "aiedit-streak-seen";
// The day whose quest was last cheered as done.
const QUEST_KEY = "aiedit-quest-done";
// Round numbers of reels made that get fireworks, and the last one shown.
export const MILESTONES = [50, 100, 250, 500, 1000, 2500, 5000, 10000];
const MILESTONE_KEY = "aiedit-milestone-seen";

function seen(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function remember(ids: string[]) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(ids));
  } catch {
    // private window: a badge may be cheered again next time
  }
}

/** The fun stats (team board, streak, badges, quest), asked again every minute and when
 * `tick` changes (jobs finished), with the cheering for a new badge, a longer streak and
 * a quest done: each once (remembered in this browser), whichever page shows them. */
export function useFunStats(tick: number): FunStats | null {
  const [stats, setStats] = useState<FunStats | null>(null);
  useEffect(() => {
    let live = true;
    const load = () =>
      api
        .fun()
        .then((next) => live && setStats(next))
        .catch(() => {});
    load();
    const every = window.setInterval(load, 60000);
    return () => {
      live = false;
      window.clearInterval(every);
    };
  }, [tick]);

  useEffect(() => {
    if (!stats) return;
    const known = new Set(seen());
    const fresh = stats.badges.filter((b) => b.won_at && !known.has(b.id));
    if (!fresh.length) return;
    remember([...known, ...fresh.map((b) => b.id)]);
    confetti();
    sounds.badge();
    cheer("badge");
    for (const badge of fresh.slice(0, 3)) {
      toast.success(`Badge won: ${badge.name}`, { description: badge.description });
    }
    if (fresh.length > 3) toast.success(`And ${fresh.length - 3} more badges`);
  }, [stats]);

  useEffect(() => {
    if (!stats) return;
    let last: number | null = null;
    try {
      const kept = localStorage.getItem(STREAK_KEY);
      last = kept === null ? null : Number(kept);
      localStorage.setItem(STREAK_KEY, String(stats.me.streak));
    } catch {
      return;
    }
    // The first look only notes it; a longer run after that gets a sparkle.
    if (last !== null && stats.me.streak > last && stats.me.streak > 1) {
      sounds.streak();
      toast.success(`${stats.me.streak} days in a row!`, {
        description: "Keep it going tomorrow.",
      });
    }
  }, [stats]);

  useEffect(() => {
    if (!stats?.quest.done) return;
    try {
      if (localStorage.getItem(QUEST_KEY) === stats.quest.day) return;
      localStorage.setItem(QUEST_KEY, stats.quest.day);
    } catch {
      return;
    }
    confetti(90);
    sounds.badge();
    cheer("badge");
    toast.success("Daily quest done!", { description: stats.quest.text });
  }, [stats]);

  useEffect(() => {
    if (!stats) return;
    const reached = MILESTONES.filter((m) => stats.me.total >= m);
    if (!reached.length) return;
    let last = 0;
    try {
      last = Number(localStorage.getItem(MILESTONE_KEY) ?? "0");
      localStorage.setItem(MILESTONE_KEY, String(reached[reached.length - 1]));
    } catch {
      return;
    }
    const top = reached[reached.length - 1];
    if (top <= last) return;
    // The highest one reached, once: a whole history of them at first is one show.
    fireworks(`${top} reels!`);
    sounds.badge();
    cheer("badge");
  }, [stats]);

  return stats;
}
