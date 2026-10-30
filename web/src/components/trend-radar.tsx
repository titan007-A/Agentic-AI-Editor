"use client";

import { ExternalLink, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { api, type Trends } from "@/lib/api";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

/** What the person's researches of the last two weeks found spreading: songs several
 * accounts posted reels of, and the reels gaining likes fastest. Nothing shows until
 * researches have found reels posted lately. */
export function TrendRadar({ version }: { version: number }) {
  const [trends, setTrends] = useState<Trends | null>(null);

  useEffect(() => {
    api
      .researchTrends()
      .then(setTrends)
      .catch(() => {});
  }, [version]);

  if (!trends || (trends.reels.length === 0 && trends.songs.length === 0)) return null;
  return (
    <Card className="gap-3 p-4">
      <div>
        <p className="flex items-center gap-1 font-medium">
          <TrendingUp className="size-4" /> Trend radar
        </p>
        <p className="text-sm text-muted-foreground">
          From your researches of the last two weeks: what is spreading now.
        </p>
      </div>
      {trends.songs.length > 0 && (
        <div className="space-y-1">
          <p className="text-sm font-medium">Songs going round</p>
          <ul className="space-y-1 text-sm">
            {trends.songs.map((s) => (
              <li key={s.song} className="break-words">
                {s.song}{" "}
                <span className="text-muted-foreground">
                  · {s.accounts} accounts, {s.reels} reels, {compact.format(s.per_day)} likes a day
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {trends.reels.length > 0 && (
        <div className="space-y-1">
          <p className="text-sm font-medium">Reels spreading fastest</p>
          <ol className="list-inside list-decimal space-y-1 text-sm">
            {trends.reels.map((r) => (
              <li key={r.id} className="break-words">
                <a href={r.url} target="_blank" rel="noreferrer" className="underline">
                  {r.song || r.account || (r.handle ? `@${r.handle}` : "a reel")}
                </a>
                <ExternalLink className="ml-1 inline size-3" />{" "}
                <span className="text-muted-foreground">
                  {r.handle && `@${r.handle} · `}
                  {compact.format(r.per_day ?? 0)} a day
                  {r.used && " · edited"}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}
