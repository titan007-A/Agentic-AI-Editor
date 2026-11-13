"use client";

import { Share2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { api, errorText, type IgChannel, type Output } from "@/lib/api";

export function InstagramButton({ output, title }: { output: Output; title: string }) {
  const [open, setOpen] = useState(false);
  const [channels, setChannels] = useState<IgChannel[] | null>(null);
  const [channelId, setChannelId] = useState("");
  const [caption, setCaption] = useState("");
  const [shareToFeed, setShareToFeed] = useState(true);
  const [busy, setBusy] = useState(false);

  async function show() {
    setOpen(true);
    setCaption(output.caption || title);
    setChannels(null);
    try {
      const rows = await api.igChannels();
      setChannels(rows);
      if (rows.length === 1) setChannelId(rows[0].id);
      else setChannelId("");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function send() {
    if (!channelId) return;
    setBusy(true);
    try {
      const { channel } = await api.postToInstagram(output.id, {
        channel_id: channelId,
        caption,
        share_to_feed: shareToFeed,
      });
      toast.success(`Posted to @${channel} on Instagram`);
      setOpen(false);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const selected = channels?.find((c) => c.id === channelId) ?? null;

  return (
    <>
      <Button variant="outline" onClick={show}>
        <Share2 /> Instagram
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Post to Instagram</DialogTitle>
            <DialogDescription>
              The video is posted as a Reel directly via the Meta API — no Buffer needed.
            </DialogDescription>
          </DialogHeader>

          {channels === null && <p className="text-sm text-muted-foreground">Loading...</p>}

          {channels !== null && channels.length === 0 && (
            <p className="text-sm">
              No Instagram accounts connected yet.{" "}
              <a href="/settings" className="underline">
                Connect one in Settings.
              </a>
            </p>
          )}

          {channels !== null && channels.length > 0 && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Account</Label>
                <div className="space-y-2">
                  {channels.map((ch) => (
                    <label
                      key={ch.id}
                      htmlFor={`ig-ch-${ch.id}-${output.id}`}
                      className="sketch-tile flex min-h-11 cursor-pointer items-center gap-3 p-2"
                    >
                      <input
                        type="radio"
                        id={`ig-ch-${ch.id}-${output.id}`}
                        name={`ig-channel-${output.id}`}
                        value={ch.id}
                        checked={channelId === ch.id}
                        onChange={() => setChannelId(ch.id)}
                        className="accent-primary"
                      />
                      {ch.avatar && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={ch.avatar} alt="" className="size-7 shrink-0 rounded-full" />
                      )}
                      <span className="text-sm">
                        @{ch.username}
                        {ch.name && ch.name !== ch.username && (
                          <span className="text-muted-foreground"> · {ch.name}</span>
                        )}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-1">
                <Label htmlFor={`ig-caption-${output.id}`}>Caption</Label>
                <Textarea
                  id={`ig-caption-${output.id}`}
                  rows={5}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                />
              </div>

              <label
                htmlFor={`ig-feed-${output.id}`}
                className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"
              >
                <Checkbox
                  id={`ig-feed-${output.id}`}
                  checked={shareToFeed}
                  onCheckedChange={(on) => setShareToFeed(on === true)}
                />
                Share to feed (not Reels-only)
              </label>

              {selected && (
                <p className="text-xs text-muted-foreground">
                  Posting may take up to 5 minutes while Instagram processes the video.
                </p>
              )}
            </div>
          )}

          {channels !== null && channels.length > 0 && (
            <DialogFooter>
              <Button disabled={busy || !channelId} onClick={send}>
                {busy ? "Posting..." : "Post Reel"}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
