"use client";

import { Copy, ExternalLink, Eye, Heart, ImageOff, Lightbulb, MessageCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ReelFeedback } from "@/components/reel-feedback";
import { type Preset, type Research, type ResearchItem } from "@/lib/api";
import { cn } from "@/lib/utils";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

const percent = (value: number) => `${Math.round(value * 100)}%`;

function ago(when: string): string {
  const days = Math.floor((Date.now() - new Date(when + "Z").getTime()) / 86_400_000);
  if (days < 1) return "today";
  if (days < 60) return `${days} day${days === 1 ? "" : "s"} ago`;
  if (days < 730) return `${Math.round(days / 30)} months ago`;
  return `${Math.round(days / 365)} years ago`;
}

/** A YouTube Short rather than an Instagram reel (see research.YOUTUBE). */
export const isShort = (item: ResearchItem) => item.code.startsWith("yt:");

/** "HD" or "SD" by the picture's short side (see research.Reel.quality); "" when not known. */
export function quality(item: ResearchItem): string {
  if (!item.width || !item.height) return "";
  return Math.min(item.width, item.height) >= 720 ? "HD" : "SD";
}

/** Seconds as 0:30. */
const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

/** The shortest reel its suggested preset makes, when the source is shorter than that. */
function tooShort(item: ResearchItem, presets: Preset[]): number | null {
  const shortest = presets.find((p) => p.name === item.preset)?.min_duration;
  return item.duration !== null && typeof shortest === "number" && item.duration < shortest
    ? shortest
    : null;
}

/** The model's words ended with one full stop, whether they came with one or not. */
const ended = (text: string) => text.replace(/[.\s]+$/, "") + ".";

/** Why the researcher chose this reel, step by step, from what it stored of its thinking. */
export function reasons(item: ResearchItem, research: Research, presets: Preset[]): string[] {
  const label = presets.find((p) => p.name === item.preset)?.label ?? item.preset;
  const lines = [`Found by the search “${item.query}”.`];
  lines.push(
    item.relevance === null
      ? "The model couldn't rate how well it fits, so it counted as half a fit."
      : ended(`Fit ${Math.round(item.relevance * 10)}/10: ${item.why || "no reason given"}`),
  );
  if (item.matches) {
    lines.push(
      ended(
        research.handle
          ? `Like @${research.handle}'s reels: ${item.matches}`
          : `Matches what you asked: ${item.matches}`,
      ),
    );
  }
  if (item.song) lines.push(ended(`The song: ${item.song}`));
  const grade = quality(item);
  lines.push(
    grade
      ? `Picture: ${item.width}×${item.height}${
          item.bitrate ? ` at ${(item.bitrate / 1000).toFixed(1)} Mbit/s` : ""
        } (${grade}${grade === "SD" ? ", so it ranks a little lower" : ""}).`
      : "The site didn't say how sharp its picture is.",
  );
  const shortest = tooShort(item, presets);
  if (shortest !== null && item.duration !== null) {
    lines.push(
      `It is ${clock(item.duration)} long, shorter than the ${shortest} s its preset makes at least.`,
    );
  }
  lines.push(
    item.preset
      ? ended(`Suits your preset “${label}”${item.preset_why ? `: ${item.preset_why}` : ""}`)
      : "None of your presets stood out for it.",
  );
  if (item.likes === null) {
    lines.push(
      `${isShort(item) ? "YouTube" : "Instagram"} didn't show its likes without signing in, so it counted as not viral.`,
    );
  } else {
    const reactions = `${compact.format(item.likes)} likes${
      item.comments !== null ? ` and ${compact.format(item.comments)} comments` : ""
    }`;
    const pace =
      item.per_day !== null && item.posted_at
        ? `, about ${compact.format(item.per_day)} a day since it was posted ${ago(item.posted_at)}`
        : "";
    lines.push(
      `${reactions}${pace}: viral ${percent(item.virality)} among the ${research.considered} reels rated.`,
    );
  }
  lines.push(
    research.handle
      ? `Not shown by an earlier research, not one of @${research.handle}'s own, and not edited before.`
      : "Not shown by an earlier research, and not edited before.",
  );
  const rank =
    item.rank === 0
      ? `the best of the ${research.considered} rated`
      : `#${item.rank + 1} of the ${research.items.length} given`;
  lines.push(
    `Score ${item.score.toFixed(2)} = fit × (0.5 + 0.5 × viral): ${rank}` +
      (research.cutoff !== null
        ? `; the best reel left out scored ${research.cutoff.toFixed(2)}.`
        : "."),
  );
  return lines;
}

/** One reel a research found: how well it fits and how far it spread, and ways to use it.
 * Chosen to edit, it carries a preset: the one the researcher thought it suits, when that
 * is still one of the person's, which they can change. */
export function ReelCard({
  item,
  presets,
  research,
  chosen,
  full,
  onChoose,
  onChanged,
}: {
  item: ResearchItem;
  presets: Preset[];
  research: Research;
  /** The preset it is to be edited with ("" until one is picked); null when not chosen. */
  chosen: string | null;
  /** As many reels are chosen as may be. */
  full: boolean;
  onChoose: (preset: string | null) => void;
  /** Feedback was given: the research as it is now. */
  onChanged: (next: Research) => void;
}) {
  const [broken, setBroken] = useState(!item.thumbnail);
  const [explaining, setExplaining] = useState(false);
  const suggested = presets.some((p) => p.name === item.preset) ? item.preset : "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(item.url);
      toast.success("Link copied");
    } catch {
      toast.error("Couldn't copy; open the reel and copy its link");
    }
  }

  return (
    <Card className={cn("gap-0 overflow-hidden p-0", chosen !== null && "ring-2 ring-primary")}>
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        className="relative block aspect-[4/5] bg-muted"
        aria-label={`Open the ${isShort(item) ? "Short" : "reel"} by ${item.account || "this account"} on ${isShort(item) ? "YouTube" : "Instagram"}`}
      >
        {broken ? (
          <span className="flex h-full items-center justify-center text-muted-foreground">
            <ImageOff className="size-8" />
          </span>
        ) : (
          // Instagram's own picture link: it expires after a few days, then the icon shows.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={item.thumbnail}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover"
            onError={() => setBroken(true)}
          />
        )}
        <Badge className="absolute top-2 left-2">#{item.rank + 1}</Badge>
      </a>
      <div className="space-y-2 p-3">
        <div className="flex items-baseline justify-between gap-2">
          <p className="min-w-0 truncate font-medium">
            {item.account || (isShort(item) ? "YouTube Short" : "Instagram reel")}
            {item.handle && (
              <span className="ml-1 text-xs font-normal text-muted-foreground">@{item.handle}</span>
            )}
          </p>
          <p className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
            {item.views !== null && (
              <span className="flex items-center gap-0.5" title="views">
                <Eye className="size-3" /> {compact.format(item.views)}
              </span>
            )}
            {item.likes !== null && (
              <span className="flex items-center gap-0.5">
                <Heart className="size-3" /> {compact.format(item.likes)}
              </span>
            )}
            {item.comments !== null && (
              <span className="flex items-center gap-0.5">
                <MessageCircle className="size-3" /> {compact.format(item.comments)}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge variant={(item.relevance ?? 0.5) >= 0.6 ? "default" : "outline"}>
            Fit {item.relevance === null ? "?" : percent(item.relevance)}
          </Badge>
          {isShort(item) && <Badge variant="outline">YouTube Short</Badge>}
          {quality(item) && (
            <Badge variant={quality(item) === "HD" ? "outline" : "secondary"}>
              {Math.min(item.width ?? 0, item.height ?? 0)}p {quality(item)}
            </Badge>
          )}
          {item.duration !== null && (
            <Badge variant={tooShort(item, presets) === null ? "outline" : "destructive"}>
              {clock(item.duration)}
              {tooShort(item, presets) !== null && ` (preset needs ${tooShort(item, presets)} s)`}
            </Badge>
          )}
          <Badge variant="secondary">Viral {percent(item.virality)}</Badge>
          {item.posted_at && <Badge variant="outline">{ago(item.posted_at)}</Badge>}
          {item.preset && (
            <Badge variant="outline">
              For {presets.find((p) => p.name === item.preset)?.label ?? item.preset}
            </Badge>
          )}
          {item.used && <Badge variant="outline">Edited</Badge>}
          {item.seen && !item.used && <Badge variant="outline">Shown before</Badge>}
        </div>
        {item.caption && <p className="line-clamp-3 text-sm break-words">{item.caption}</p>}
        {item.why && <p className="text-xs text-muted-foreground">Why: {item.why}</p>}
        <ReelFeedback item={item} onChanged={onChanged} />
        <div className="space-y-2 rounded-md border p-2">
          <label className="flex items-center gap-2 text-sm font-medium">
            <Checkbox
              checked={chosen !== null}
              disabled={chosen === null && full}
              onCheckedChange={(on) => onChoose(on ? suggested : null)}
            />
            {chosen === null && full ? "5 chosen already" : "Choose to edit"}
          </label>
          {chosen !== null && (
            <div className="space-y-1">
              <Label htmlFor={`reel-preset-${item.id}`}>Preset</Label>
              <select
                id={`reel-preset-${item.id}`}
                value={chosen}
                onChange={(e) => onChoose(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {!chosen && <option value="">Pick a preset</option>}
                {presets.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy /> Copy link
          </Button>
          <Button size="sm" variant="ghost" asChild>
            <a href={item.url} target="_blank" rel="noreferrer">
              <ExternalLink /> Open
            </a>
          </Button>
        </div>
        <Button
          size="sm"
          variant="secondary"
          className="w-full"
          aria-expanded={explaining}
          onClick={() => setExplaining(!explaining)}
        >
          <Lightbulb /> {explaining ? "Hide reasoning" : "Explain reasoning"}
        </Button>
        {explaining && (
          <ol className="list-inside list-decimal space-y-1 rounded-md border bg-muted/40 p-2 text-sm break-words">
            {reasons(item, research, presets).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}
