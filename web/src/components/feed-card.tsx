"use client";

import { Folder, Link as LinkIcon, Pencil, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { FeedForm, hourLabel } from "@/components/feed-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Feed } from "@/lib/api";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  running: "default",
  queued: "secondary",
  done: "outline",
  failed: "destructive",
};

export function FeedCard({
  feed,
  onChange,
  onDelete,
}: {
  feed: Feed;
  onChange: (feed: Feed) => void;
  onDelete: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function act(work: () => Promise<Feed | void>) {
    setBusy(true);
    try {
      const updated = await work();
      if (updated) onChange(updated);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const hours =
    feed.active_from === null || feed.active_to === null
      ? "any time"
      : `${hourLabel(feed.active_from)}-${hourLabel(feed.active_to)}`;
  const counts = Object.entries(feed.counts).filter(([status]) => status !== "skipped");

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="truncate">{feed.name}</span>
          <Switch
            aria-label={feed.enabled ? "Pause this feed" : "Turn this feed on"}
            checked={feed.enabled}
            disabled={busy}
            onCheckedChange={(enabled) => act(() => api.updateFeed(feed.id, { ...feed, enabled }))}
          />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="flex items-center gap-2 break-all text-muted-foreground">
          {feed.kind === "folder" ? (
            <Folder className="size-4 shrink-0" />
          ) : (
            <LinkIcon className="size-4 shrink-0" />
          )}
          {feed.location}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {feed.presets.map((p) => (
            <Badge key={p} variant="secondary">
              {p}
            </Badge>
          ))}
          <Badge variant="outline">{hours}</Badge>
          <Badge variant="outline">{feed.batch} at a time</Badge>
        </div>
        {feed.notes && <p className="text-xs text-muted-foreground">“{feed.notes}”</p>}
        <p className="text-xs text-muted-foreground">
          {feed.last_checked
            ? `Checked ${new Date(feed.last_checked + "Z").toLocaleString()}: ${feed.last_result}`
            : "Not checked yet"}
        </p>
        {counts.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {counts.map(([status, n]) => (
              <Badge key={status} variant={STATUS_VARIANT[status] ?? "outline"}>
                {n} {status}
              </Badge>
            ))}
          </div>
        )}
        {feed.recent.length > 0 && (
          <ul className="space-y-1 text-xs">
            {feed.recent.map((item) => (
              <li key={`${item.source}-${item.created_at}`} className="flex justify-between gap-2">
                {item.job_id ? (
                  <Link href={`/job/?id=${item.job_id}`} className="truncate underline">
                    {item.title}
                  </Link>
                ) : (
                  <span className="truncate">{item.title}</span>
                )}
                <span className="shrink-0 text-muted-foreground">{item.status}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => act(() => api.checkFeed(feed.id))}
          >
            <RefreshCw /> Check now
          </Button>
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <Pencil /> Edit
          </Button>
          <Button variant="outline" size="sm" onClick={() => setDeleting(true)}>
            <Trash2 /> Remove
          </Button>
        </div>
      </CardContent>

      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit {feed.name}</DialogTitle>
          </DialogHeader>
          <FeedForm
            feed={feed}
            onSaved={(saved) => {
              setEditing(false);
              onChange(saved);
            }}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={deleting} onOpenChange={setDeleting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {feed.name}?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            The feed stops and forgets what it has seen. Videos it already edited stay.
          </p>
          <DialogFooter>
            <Button
              variant="destructive"
              onClick={() =>
                act(async () => {
                  await api.deleteFeed(feed.id);
                  setDeleting(false);
                  onDelete();
                })
              }
            >
              Remove feed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
