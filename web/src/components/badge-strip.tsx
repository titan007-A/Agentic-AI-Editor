"use client";

import { ChevronRight, Flame } from "lucide-react";
import Link from "next/link";

import { useFunStats } from "@/hooks/use-fun-stats";
import { Award, ICONS } from "@/lib/badge-icons";

const SHOWN = 6; // badges drawn; the rest are counted

/** A small strip on the jobs page for accounts trying the fun feature: the run of days,
 * today's reels, the daily quest and the badges won, with the way to the full board. */
export function BadgeStrip({ tick }: { tick: number }) {
  const stats = useFunStats(tick);
  if (!stats) return null;
  const { me, quest, badges } = stats;
  const won = badges.filter((b) => b.won_at).sort((a, b) => (b.won_at! > a.won_at! ? 1 : -1));
  return (
    <Link
      href="/team/"
      className="sketch-tile flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2 text-sm hover:bg-[var(--cream-hover)] lg:col-span-2"
      data-testid="badge-strip"
    >
      <span className="flex items-center gap-1 font-semibold" title="Days in a row with a reel">
        <Flame className="size-4 text-[var(--tangerine)]" aria-hidden />
        {me.streak} day{me.streak === 1 ? "" : "s"}
      </span>
      <span title="Reels today">
        <span className="font-semibold">{me.today}</span> today
      </span>
      <span className="min-w-0 truncate text-muted-foreground" title={quest.text}>
        Quest: {quest.done ? "done" : `${quest.progress}/${quest.goal}`}
      </span>
      <span className="flex items-center gap-1" aria-label={`${won.length} badges won`}>
        {won.slice(0, SHOWN).map((b) => {
          const Icon = ICONS[b.id] ?? Award;
          return (
            <span
              key={b.id}
              title={b.name}
              className="grid size-7 place-items-center rounded-full border-[1.5px] border-foreground bg-[var(--mustard)]"
            >
              <Icon className="size-3.5" aria-hidden />
            </span>
          );
        })}
        {won.length > SHOWN && (
          <span className="text-xs text-muted-foreground">+{won.length - SHOWN}</span>
        )}
      </span>
      <span className="ml-auto flex items-center font-semibold">
        Team board <ChevronRight className="size-4" aria-hidden />
      </span>
    </Link>
  );
}
