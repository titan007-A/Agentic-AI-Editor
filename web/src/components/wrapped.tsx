"use client";

import { ChevronLeft, ChevronRight, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, errorText, type FunWrapped } from "@/lib/api";
import { confetti } from "@/lib/confetti";

function hour(h: number): string {
  const am = h < 12;
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve} ${am ? "am" : "pm"}`;
}

function slides(w: FunWrapped): { big: string; small: string[] }[] {
  if (w.reels === 0) {
    return [
      { big: "Nothing yet", small: [`No reels in ${w.label} so far. The month isn't over!`] },
    ];
  }
  const out = [
    {
      big: `${w.reels} reel${w.reels === 1 ? "" : "s"}`,
      small: [`That's your ${w.label}.`, "Let's look back."],
    },
    {
      big: `${w.active_days} day${w.active_days === 1 ? "" : "s"}`,
      small: [
        "with at least one reel.",
        w.best_run > 1 ? `Longest run: ${w.best_run} days in a row.` : "",
        w.best_day
          ? `Busiest day: ${new Date(w.best_day.date).toLocaleDateString(undefined, { day: "numeric", month: "long" })}, ${w.best_day.reels} reels.`
          : "",
      ],
    },
  ];
  if (w.top_preset) {
    out.push({
      big: w.top_preset.name,
      small: [
        `your go-to preset, ${w.top_preset.reels} reel${w.top_preset.reels === 1 ? "" : "s"}.`,
        w.top_song ? `Song of the month: ${w.top_song.name}.` : "",
        w.top_artist ? `Most edited artist: ${w.top_artist.name}.` : "",
      ],
    });
  }
  if (w.busiest_hour !== null) {
    out.push({
      big: hour(w.busiest_hour),
      small: [
        "your power hour.",
        w.night_reels
          ? `${w.night_reels} reel${w.night_reels === 1 ? "" : "s"} after midnight. Night owl!`
          : "",
      ],
    });
  }
  out.push({
    big: w.team_place ? `#${w.team_place}` : "On the team",
    small: [
      w.team_place ? `of ${w.team_size} on the team this month.` : "",
      `${w.reels} of the team's ${w.team_reels} reels.`,
      w.captions || w.posts ? `${w.captions} captions written, ${w.posts} sent to Buffer.` : "",
    ],
  });
  return out.map((s) => ({ ...s, small: s.small.filter(Boolean) }));
}

/** The month in slides, like a music app's year in review: reels, days, the go-to preset,
 * song and artist, the power hour and the place on the team. */
export function Wrapped({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [data, setData] = useState<FunWrapped | null>(null);
  const [month, setMonth] = useState<string | undefined>(undefined);
  const [at, setAt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    api
      .wrapped(month)
      .then((next) => {
        if (!live) return;
        setData(next);
        setAt(0);
        setError(null);
      })
      .catch((err) => live && setError(errorText(err)));
    return () => {
      live = false;
    };
  }, [open, month]);

  const all = data ? slides(data) : [];
  const last = at === all.length - 1;
  useEffect(() => {
    if (open && data && last && all.length > 1) confetti(70);
  }, [open, data, last, all.length]);
  const slide = all[at];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 sm:max-w-md" data-testid="wrapped">
        <DialogHeader className="px-5 pt-5">
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-[var(--tangerine)]" /> {data?.label ?? "Your month"}{" "}
            Wrapped
          </DialogTitle>
          <DialogDescription>
            {all.length > 0 ? `${at + 1} of ${all.length}` : (error ?? "Counting your reels...")}
          </DialogDescription>
        </DialogHeader>
        {slide && (
          <div
            className="wrapped-slide mx-5 mb-4 flex min-h-64 flex-col justify-center gap-2 p-6 text-center"
            key={`${data?.month}-${at}`}
          >
            <p
              className="font-hand text-6xl leading-none font-bold break-words"
              data-testid="wrapped-big"
            >
              {slide.big}
            </p>
            {slide.small.map((line) => (
              <p key={line} className="text-base">
                {line}
              </p>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between gap-2 px-5 pb-5">
          <Button
            variant="outline"
            size="icon"
            disabled={at === 0}
            onClick={() => setAt(at - 1)}
            aria-label="Back"
          >
            <ChevronLeft />
          </Button>
          {data && data.months.length > 1 ? (
            <select
              aria-label="Month"
              className="min-h-11 rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
              value={data.month}
              onChange={(e) => setMonth(e.target.value)}
            >
              {data.months.map((m) => (
                <option key={m} value={m}>
                  {new Date(`${m}-01T12:00:00`).toLocaleDateString(undefined, {
                    month: "long",
                    year: "numeric",
                  })}
                </option>
              ))}
            </select>
          ) : (
            <span />
          )}
          <Button
            variant={last ? "outline" : "default"}
            size="icon"
            disabled={last}
            onClick={() => setAt(at + 1)}
            aria-label="Next"
          >
            <ChevronRight />
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
