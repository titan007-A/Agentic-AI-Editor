"use client";

import { Ban, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { GROUPS, RUSSIA_HINT, StrikeFields } from "@/components/song-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, errorText, type NewStrike, type Strike } from "@/lib/api";

const BLANK: NewStrike = { song: "", artist: "", label_group: "other", what: "blocked", note: "" };

/** The strike list: songs a label blocked worldwide, took a post down for or struck a page
 * for. One list for every account; jobs warn about its songs and the researcher leaves them
 * out. Anyone adds; an admin or whoever added an entry takes it off. */
export default function StrikesPage() {
  const [entries, setEntries] = useState<Strike[] | null>(null);
  const [value, setValue] = useState<NewStrike>(BLANK);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Strike | null>(null);

  const load = useCallback(
    () =>
      api
        .strikes()
        .then((list) => setEntries(list.entries))
        .catch((err) => toast.error(errorText(err))),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !value.song.trim()) return;
    setBusy(true);
    try {
      const strike = await api.addStrike(value);
      toast.success(`“${strike.song}” is on the strike list`);
      setValue(BLANK);
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.removeStrike(removing.id);
      toast.success(`“${removing.song}” is off the strike list`);
      setRemoving(null);
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (entries === null) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="space-y-1">
        <h1 className="font-hand text-4xl font-bold">Strike list</h1>
        <p className="text-sm text-muted-foreground">
          Songs a label blocked worldwide, took a post down for, or gave a page a strike for. It is
          one list for every account: a label acts on its song wherever it is posted. A job with one
          of these songs says so on its page, and the researcher never suggests them.
        </p>
      </div>

      <Card className="tape">
        <CardHeader>
          <CardTitle>Add a song</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={add} className="space-y-3">
            <StrikeFields value={value} onChange={setValue} id="strike" />
            <p className="text-xs text-muted-foreground">
              {RUSSIA_HINT} A reel&apos;s own page has a <strong>Block aaya</strong> button that
              fills this in from its song.
            </p>
            <Button type="submit" disabled={busy || !value.song.trim()}>
              <Ban /> Add to the strike list
            </Button>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="font-hand text-2xl font-bold">
          {entries.length} song{entries.length === 1 ? "" : "s"}
        </h2>
        {entries.length === 0 && (
          <p className="text-sm text-muted-foreground">No songs on the list.</p>
        )}
        <ul className="space-y-2">
          {entries.map((entry) => (
            <li key={entry.id} className="sketch-tile space-y-1 p-3" data-testid="strike">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold break-words">
                    “{entry.song}”
                    {entry.artist && (
                      <span className="font-normal text-muted-foreground"> by {entry.artist}</span>
                    )}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Badge variant="destructive">{entry.what_label}</Badge>
                    <Badge
                      variant="outline"
                      data-tone={entry.label_group !== "other" ? "warn" : undefined}
                    >
                      {GROUPS.find((g) => g.value === entry.label_group)?.label ??
                        entry.label_group}
                    </Badge>
                    {entry.note && <Badge variant="outline">{entry.note}</Badge>}
                  </div>
                </div>
                {entry.can_remove && (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setRemoving(entry)}
                    aria-label={`Take “${entry.song}” off the list`}
                  >
                    <Trash2 />
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Added {new Date(entry.created_at + "Z").toLocaleDateString()}
                {entry.added_by ? ` by ${entry.added_by}` : ""}
                {entry.job_id && (
                  <>
                    {" "}
                    from{" "}
                    <Link href={`/job/?id=${entry.job_id}`} className="underline">
                      its reel
                    </Link>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <Dialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Take it off the strike list?</DialogTitle>
            <DialogDescription>
              Jobs stop warning about “{removing?.song}”, and the researcher may suggest it again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="destructive" disabled={busy} onClick={remove}>
              Take it off
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
