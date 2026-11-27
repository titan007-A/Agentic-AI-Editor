"use client";

import { Ban, Music } from "lucide-react";
import Link from "next/link";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  api,
  errorText,
  type Blocked,
  type LabelGroup,
  type NewStrike,
  type SongNotice,
  type Strike,
} from "@/lib/api";
import { noticeText, noticeTone, songBadges } from "@/lib/song-notice.mjs";

export const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";
export const GROUPS: { value: LabelGroup; label: string }[] = [
  { value: "UMG", label: "Universal Music (UMG)" },
  { value: "Sony", label: "Sony Music" },
  { value: "Warner", label: "Warner Music" },
  { value: "other", label: "Another label" },
];
export const WHATS: { value: Blocked["what"]; label: string }[] = [
  { value: "blocked", label: "Blocked worldwide" },
  { value: "removed", label: "Post removed" },
  { value: "strike", label: "Copyright strike" },
];

/** The small badges for a song: blocked before, its label group, "2024 · new". */
export function SongBadges(props: Parameters<typeof songBadges>[0]) {
  const badges = songBadges(props);
  if (badges.length === 0) return null;
  return (
    <>
      {badges.map((badge) => (
        <Badge
          key={badge.label}
          variant={badge.tone === "blocked" ? "destructive" : "outline"}
          data-tone={badge.tone === "warn" ? "warn" : undefined}
          title={badge.title}
          data-testid="song-badge"
        >
          {badge.label}
        </Badge>
      ))}
    </>
  );
}

/** The job page's word on its song: blocked before, a major label's song, a new song, or
 * just what it is. "Block aaya" puts it on the strike list when a label acted on it. */
export function SongNoticeCard({
  notice,
  jobId,
  onAdded,
}: {
  notice: SongNotice;
  jobId: string;
  onAdded?: (strike: Strike) => void;
}) {
  const [adding, setAdding] = useState(false);
  const tone = noticeTone(notice);
  const { heading, lines } = noticeText(notice);
  return (
    <Card
      className="gap-2 p-4"
      data-tone={tone === "quiet" ? undefined : tone}
      data-testid="song-notice"
      role={tone === "quiet" ? undefined : "status"}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex min-w-0 items-center gap-2 font-semibold break-words">
          {tone === "blocked" ? (
            <Ban className="size-4 shrink-0 text-destructive" aria-hidden />
          ) : (
            <Music className="size-4 shrink-0" aria-hidden />
          )}
          {heading}
        </p>
        <div className="flex flex-wrap gap-1">
          <SongBadges
            year={notice.year}
            new={notice.new}
            group={notice.group}
            label={notice.label}
            blocked={notice.blocked}
          />
        </div>
      </div>
      {lines.map((line) => (
        <p
          key={line}
          className={tone === "quiet" ? "text-sm text-muted-foreground" : "text-sm break-words"}
        >
          {line}
        </p>
      ))}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {!notice.blocked && (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Ban /> Block aaya
          </Button>
        )}
        <Link href="/strikes/" className="text-xs underline">
          Strike list
        </Link>
      </div>
      <StrikeDialog
        open={adding}
        onOpenChange={setAdding}
        initial={{
          song: notice.song,
          artist: notice.artist,
          label_group: (notice.group || "other") as LabelGroup,
          job_id: jobId,
        }}
        onAdded={(strike) => {
          setAdding(false);
          onAdded?.(strike);
        }}
      />
    </Card>
  );
}

/** "Block aaya" for a reel whose song the edit didn't name: what its caption named. */
export function BlockButton({
  song,
  artist,
  jobId,
  onAdded,
}: {
  song: string;
  artist: string;
  jobId: string;
  onAdded?: (strike: Strike) => void;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
        <Ban /> Block aaya
      </Button>
      <StrikeDialog
        open={adding}
        onOpenChange={setAdding}
        initial={{ song, artist, job_id: jobId }}
        onAdded={(strike) => {
          setAdding(false);
          onAdded?.(strike);
        }}
      />
    </>
  );
}

/** The fields of a strike list entry, for the dialog and the list's own page. */
export function StrikeFields({
  value,
  onChange,
  id,
}: {
  value: NewStrike;
  onChange: (next: NewStrike) => void;
  id: string;
}) {
  const set = (part: Partial<NewStrike>) => onChange({ ...value, ...part });
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor={`${id}-song`}>Song</Label>
        <Input
          id={`${id}-song`}
          value={value.song}
          onChange={(e) => set({ song: e.target.value })}
          maxLength={200}
          required
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-artist`}>Artist</Label>
        <Input
          id={`${id}-artist`}
          value={value.artist}
          onChange={(e) => set({ artist: e.target.value })}
          maxLength={200}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-group`}>Label</Label>
        <select
          id={`${id}-group`}
          className={SELECT}
          value={value.label_group}
          onChange={(e) => set({ label_group: e.target.value as LabelGroup })}
        >
          {GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-what`}>What happened</Label>
        <select
          id={`${id}-what`}
          className={SELECT}
          value={value.what}
          onChange={(e) => set({ what: e.target.value as Blocked["what"] })}
        >
          {WHATS.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1 sm:col-span-2">
        <Label htmlFor={`${id}-note`}>Note</Label>
        <Input
          id={`${id}-note`}
          value={value.note}
          onChange={(e) => set({ note: e.target.value })}
          placeholder="228 countries"
          maxLength={200}
        />
      </div>
    </div>
  );
}

export const RUSSIA_HINT =
  "Only worldwide blocks, removed posts and strikes go on the list. Mutes or blocks in Russia " +
  "alone don't: every reel with a song gets them there, and they cost nothing elsewhere.";

function StrikeDialog({
  open,
  onOpenChange,
  initial,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: Partial<NewStrike>;
  onAdded: (strike: Strike) => void;
}) {
  const blank: NewStrike = {
    song: "",
    artist: "",
    label_group: "other",
    what: "blocked",
    note: "",
    ...initial,
  };
  const [value, setValue] = useState<NewStrike>(blank);
  const [busy, setBusy] = useState(false);
  const [was, setWas] = useState(open);
  // Opened again: from the job's song, not what was typed last time.
  if (open !== was) {
    setWas(open);
    if (open) setValue(blank);
  }

  async function add() {
    if (busy || !value.song.trim()) return;
    setBusy(true);
    try {
      const strike = await api.addStrike(value);
      toast.success(`“${strike.song}” is on the strike list`);
      onAdded(strike);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            add();
          }}
        >
          <DialogHeader>
            <DialogTitle>Put the song on the strike list</DialogTitle>
            <DialogDescription>
              Every account&apos;s jobs warn about it from now on, and the researcher never suggests
              it. {RUSSIA_HINT}
            </DialogDescription>
          </DialogHeader>
          <StrikeFields value={value} onChange={setValue} id="block" />
          <DialogFooter>
            <Button type="submit" disabled={busy || !value.song.trim()}>
              <Ban /> Add to the strike list
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
