"use client";

import { Award, Flame, Sparkles, Target, Volume2, VolumeX } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Wrapped } from "@/components/wrapped";
import { useFunStats } from "@/hooks/use-fun-stats";
import { ICONS } from "@/lib/badge-icons";
import { setSounds, sounds, soundsOn } from "@/lib/sounds";
import { cn } from "@/lib/utils";

type Span = "today" | "week" | "month";

/** The team board (its own tab), for accounts trying the fun feature: the person's
 * streak and counts, who made most reels today, this week and this month, and badges.
 * A badge newly won gets confetti once. `tick` changes when jobs finish, to count again. */
export function FunPanel({ tick }: { tick: number }) {
  const stats = useFunStats(tick);
  const [span, setSpan] = useState<Span>("week");
  const [loud, setLoud] = useState(soundsOn);
  const [wrapped, setWrapped] = useState(false);

  if (!stats) return null;
  const { me, leaderboard, badges, quest } = stats;
  const share = Math.min(1, quest.progress / Math.max(1, quest.goal));
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
          <Button variant="outline" size="sm" onClick={() => setWrapped(true)}>
            <Sparkles /> Wrapped
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-4">
          <div className="sketch-tile flex items-center gap-3 p-3" data-testid="quest">
            <svg viewBox="0 0 40 40" className="size-14 shrink-0 -rotate-90" aria-hidden>
              <circle cx="20" cy="20" r="16" fill="none" stroke="var(--muted)" strokeWidth="5" />
              <circle
                cx="20"
                cy="20"
                r="16"
                fill="none"
                stroke={quest.done ? "var(--olive)" : "var(--teal)"}
                strokeWidth="5"
                strokeLinecap="round"
                strokeDasharray={`${share * 100.5} 100.5`}
              />
            </svg>
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase">
                <Target className="size-3.5" /> Daily quest
              </p>
              <p className="font-semibold">{quest.text}</p>
              <p className="text-sm text-muted-foreground" role="status">
                {quest.done ? "Done! See you tomorrow." : `${quest.progress} of ${quest.goal}`}
              </p>
            </div>
          </div>
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
      <Wrapped open={wrapped} onOpenChange={setWrapped} />
    </Card>
  );
}
