"use client";

import {
  Award,
  Clapperboard,
  Flame,
  Moon,
  PenLine,
  Send,
  Shapes,
  Sunrise,
  Trophy,
  Volume2,
  VolumeX,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type FunBadge, type FunStats } from "@/lib/api";
import { confetti } from "@/lib/confetti";
import { cheer } from "@/lib/fun-events";
import { setSounds, sounds, soundsOn } from "@/lib/sounds";
import { cn } from "@/lib/utils";

const SEEN_KEY = "aiedit-badges-seen";
// The streak last cheered, so a longer one gets a sparkle once.
const STREAK_KEY = "aiedit-streak-seen";
const ICONS: Record<string, typeof Award> = {
  first_reel: Clapperboard,
  reels_10: Clapperboard,
  reels_100: Clapperboard,
  reels_500: Clapperboard,
  reels_1000: Trophy,
  day_10: Zap,
  day_25: Zap,
  streak_3: Flame,
  streak_7: Flame,
  streak_30: Flame,
  night_owl: Moon,
  early_bird: Sunrise,
  captions_25: PenLine,
  posts_10: Send,
  variety_5: Shapes,
};
type Span = "today" | "week" | "month";

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

/** The team board on the jobs page, for accounts trying the fun feature: the person's
 * streak and counts, who made most reels today, this week and this month, and badges.
 * A badge newly won gets confetti once. `tick` changes when jobs finish, to count again. */
export function FunPanel({ tick, onBadge }: { tick: number; onBadge?: (b: FunBadge) => void }) {
  const [stats, setStats] = useState<FunStats | null>(null);
  const [span, setSpan] = useState<Span>("week");
  const [loud, setLoud] = useState(soundsOn);

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
      onBadge?.(badge);
    }
    if (fresh.length > 3) toast.success(`And ${fresh.length - 3} more badges`);
  }, [stats, onBadge]);

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

  if (!stats) return null;
  const { me, leaderboard, badges } = stats;
  const board = leaderboard[span];
  const top = Math.max(1, ...board.map((r) => r.reels));
  const won = badges.filter((b) => b.won_at).length;

  return (
    <Card className="tape tape-right lg:col-span-2" data-testid="fun-panel">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-baseline gap-x-3">
          Team board
          <span className="font-sans text-sm font-normal text-muted-foreground">
            {won} of {badges.length} badges
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto"
            onClick={() => {
              setSounds(!loud);
              setLoud(!loud);
              if (!loud) sounds.ready();
            }}
            aria-label={loud ? "Turn sounds off" : "Turn sounds on"}
            aria-pressed={loud}
            title={loud ? "Sounds on" : "Sounds off"}
          >
            {loud ? <Volume2 /> : <VolumeX />}
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "grid size-14 shrink-0 place-items-center rounded-[50%_45%_55%_40%] border-2 border-foreground",
                me.streak > 0 ? "bg-[var(--tangerine)] text-background" : "bg-muted",
              )}
            >
              <Flame className="size-7" />
            </span>
            <div>
              <p className="font-hand text-3xl leading-none font-bold">
                {me.streak} day{me.streak === 1 ? "" : "s"} in a row
              </p>
              <p className="text-sm text-muted-foreground">
                {me.made_today
                  ? `Best run: ${me.best_streak} days.`
                  : me.streak > 0
                    ? "Make a reel today to keep it going."
                    : "Make a reel today to start a run."}
              </p>
            </div>
          </div>
          <dl className="grid grid-cols-3 gap-2 text-center">
            {(
              [
                ["Today", me.today],
                ["This week", me.week],
                ["All time", me.total],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="sketch-tile p-2">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="font-hand text-3xl font-bold">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="space-y-2">
            <Tabs value={span} onValueChange={(v) => setSpan(v as Span)}>
              <TabsList>
                <TabsTrigger value="today">Today</TabsTrigger>
                <TabsTrigger value="week">This week</TabsTrigger>
                <TabsTrigger value="month">This month</TabsTrigger>
              </TabsList>
            </Tabs>
            {board.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No reels yet. First one takes the top.
              </p>
            ) : (
              <ol className="space-y-1.5" aria-label="Leaderboard">
                {board.map((row, i) => (
                  <li
                    key={row.user}
                    className={cn(
                      "flex items-center gap-2 text-sm",
                      row.user === me.user && "font-semibold",
                    )}
                  >
                    <span className="w-6 text-right font-hand text-xl">{i + 1}</span>
                    <span className="w-24 truncate">{row.user}</span>
                    <span className="h-3 flex-1 rounded-sm border border-foreground/40">
                      <span
                        className="block h-full rounded-sm bg-[var(--teal)]"
                        style={{ width: `${(row.reels / top) * 100}%` }}
                      />
                    </span>
                    <span className="w-8 text-right tabular-nums">{row.reels}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Badges">
          {badges.map((badge) => {
            const Icon = ICONS[badge.id] ?? Award;
            const done = Boolean(badge.won_at);
            return (
              <li
                key={badge.id}
                className={cn(
                  "sketch-tile flex flex-col gap-1 p-2 text-xs",
                  done ? "bg-[var(--cream-hover)]" : "border-dashed opacity-75",
                )}
                title={
                  done
                    ? `${badge.description}. Won ${new Date(badge.won_at!).toLocaleDateString()}`
                    : badge.description
                }
              >
                <span className="flex items-center gap-1.5 font-semibold">
                  <Icon
                    className={cn("size-4 shrink-0", done ? "text-[var(--tangerine)]" : "")}
                    aria-hidden
                  />
                  <span className="truncate">{badge.name}</span>
                </span>
                {done ? (
                  <span className="text-muted-foreground">Won</span>
                ) : (
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 flex-1 rounded-sm bg-muted">
                      <span
                        className="block h-full rounded-sm bg-[var(--teal)]"
                        style={{ width: `${(badge.progress / badge.goal) * 100}%` }}
                      />
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {badge.progress}/{badge.goal}
                    </span>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
