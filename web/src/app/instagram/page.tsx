"use client";

import { Share2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorText, type IgChannel } from "@/lib/api";

export default function InstagramPage() {
  const [channels, setChannels] = useState<IgChannel[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .igChannels()
      .then(setChannels)
      .catch((err) => toast.error(errorText(err)));
  }, []);

  async function disconnect(id: string) {
    setBusy(true);
    try {
      await api.removeIgChannel(id);
      setChannels((prev) => prev?.filter((c) => c.id !== id) ?? []);
      toast.success("Instagram account disconnected");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-6 p-6">
      <h1 className="text-2xl font-bold">Instagram</h1>
      <p className="text-sm text-muted-foreground">
        Connect your Instagram Business or Creator accounts to post Reels directly — no Buffer
        needed.
      </p>

      <Button asChild>
        <a href="/auth/instagram">
          <Share2 className="mr-2 size-4" />
          Connect Instagram account
        </a>
      </Button>

      {channels === null && <p className="text-sm text-muted-foreground">Loading...</p>}

      {channels !== null && channels.length === 0 && (
        <p className="text-sm text-muted-foreground">No accounts connected yet.</p>
      )}

      {channels !== null && channels.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Connected accounts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {channels.map((ch) => (
              <div key={ch.id} className="flex items-center gap-3">
                {ch.avatar && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={ch.avatar} alt="" className="size-9 shrink-0 rounded-full" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">@{ch.username}</p>
                  {ch.name && ch.name !== ch.username && (
                    <p className="text-xs text-muted-foreground">{ch.name}</p>
                  )}
                  {ch.token_expires_at && (
                    <p className="text-xs text-muted-foreground">
                      Token expires{" "}
                      {new Date(ch.token_expires_at).toLocaleDateString("en-IN", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}
                    </p>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy}
                  onClick={() => disconnect(ch.id)}
                  title="Disconnect"
                >
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
