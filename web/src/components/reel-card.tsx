"use client";

import { Copy, ExternalLink, Heart, ImageOff, MessageCircle, Scissors } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, type Preset, type ResearchItem } from "@/lib/api";
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

/** One reel a research found: how well it fits and how far it spread, and ways to use it. */
export function ReelCard({ item, presets }: { item: ResearchItem; presets: Preset[] }) {
  const [broken, setBroken] = useState(!item.thumbnail);
  const [editing, setEditing] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(item.url);
      toast.success("Link copied");
    } catch {
      toast.error("Couldn't copy; open the reel and copy its link");
    }
  }

  return (
    <Card className="gap-0 overflow-hidden p-0">
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        className="relative block aspect-[4/5] bg-muted"
        aria-label={`Open the reel by ${item.account || "this account"} on Instagram`}
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
          <p className="min-w-0 truncate font-medium">{item.account || "Instagram reel"}</p>
          <p className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
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
          <Badge variant="secondary">Viral {percent(item.virality)}</Badge>
          {item.posted_at && <Badge variant="outline">{ago(item.posted_at)}</Badge>}
          {item.used && <Badge variant="outline">Edited</Badge>}
          {item.seen && !item.used && <Badge variant="outline">Shown before</Badge>}
        </div>
        {item.caption && <p className="line-clamp-3 text-sm break-words">{item.caption}</p>}
        {item.why && <p className="text-xs text-muted-foreground">Why: {item.why}</p>}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button size="sm" onClick={() => setEditing(true)}>
            <Scissors /> Edit
          </Button>
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy /> Copy link
          </Button>
          <Button size="sm" variant="ghost" asChild>
            <a href={item.url} target="_blank" rel="noreferrer">
              <ExternalLink /> Open
            </a>
          </Button>
        </div>
      </div>
      <EditDialog item={item} presets={presets} open={editing} onOpenChange={setEditing} />
    </Card>
  );
}

function EditDialog({
  item,
  presets,
  open,
  onOpenChange,
}: {
  item: ResearchItem;
  presets: Preset[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [chosen, setChosen] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  async function start() {
    if (!chosen || busy) return;
    setBusy(true);
    try {
      const [job] = await api.createJobs([item.url], [chosen], notes);
      toast.success("Job queued");
      router.push(`/job/?id=${job.id}`);
    } catch (err) {
      toast.error(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit this reel</DialogTitle>
          <DialogDescription className="break-all">{item.url}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium">Preset</legend>
            <div className="grid max-h-64 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
              {presets.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  aria-pressed={chosen === p.name}
                  onClick={() => setChosen(p.name)}
                  className={cn(
                    "sketch-tile p-2 text-left text-sm hover:bg-[#fdf3dc]",
                    chosen === p.name && "bg-[#fdf3dc] font-medium ring-2 ring-primary",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </fieldset>
          <div className="space-y-1">
            <Label htmlFor={`reel-notes-${item.id}`}>Notes for the editor (optional)</Label>
            <Textarea
              id={`reel-notes-${item.id}`}
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={"e.g. start on the chorus\nsong: Summer of '69\nartist: Bryan Adams"}
            />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={!chosen || busy} onClick={start}>
            <Scissors /> Start edit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
