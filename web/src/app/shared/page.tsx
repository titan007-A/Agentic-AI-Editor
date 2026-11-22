"use client";

import { Copy, Download } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { SharedFolder } from "@/lib/api";

function length(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A folder of videos shared with a client: watch, download, copy each post's caption.
 * No login: the link's token is the key. */
function Shared() {
  const token = useSearchParams().get("t") ?? "";
  const [folder, setFolder] = useState<SharedFolder | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    if (!token) return;
    fetch(`/api/shared/${encodeURIComponent(token)}`)
      .then(async (r) => {
        if (r.status === 404) throw new Error("This link isn't shared any more.");
        if (!r.ok) throw new Error("The clips couldn't be loaded. Try again in a minute.");
        setFolder((await r.json()) as SharedFolder);
      })
      .catch((err: Error) => setProblem(err.message));
  }, [token]);

  async function copy(caption: string) {
    try {
      await navigator.clipboard.writeText(caption);
      toast.success("Caption copied");
    } catch {
      toast.error("Couldn't copy: select the caption and copy it");
    }
  }

  const said = token ? problem : "This link is missing its key. Ask for the link again.";
  if (said) {
    return <p className="mx-auto max-w-xl p-6 text-center text-muted-foreground">{said}</p>;
  }
  if (!folder) {
    return <p className="mx-auto max-w-xl p-6 text-center text-muted-foreground">Loading...</p>;
  }
  const base = `/api/shared/${encodeURIComponent(token)}`;
  return (
    <main className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <header>
        <h1 className="font-hand text-4xl">{folder.name}</h1>
        <p className="text-sm text-muted-foreground">
          {folder.clips.length === 1 ? "1 clip" : `${folder.clips.length} clips`}, newest first
        </p>
      </header>
      {folder.clips.length === 0 && (
        <p className="text-muted-foreground">No clips here yet. New ones show up by themselves.</p>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {folder.clips.map((clip) => (
          <Card key={clip.id} data-testid="shared-clip">
            <CardContent className="space-y-2 p-3">
              <video
                className="aspect-[9/16] w-full rounded bg-black"
                controls
                playsInline
                preload="none"
                poster={`${base}/${clip.id}/thumbnail?width=480`}
                src={`${base}/${clip.id}/video`}
              />
              <p className="leading-tight font-semibold">{clip.title}</p>
              <p className="text-xs text-muted-foreground">
                {length(clip.duration)} · {new Date(clip.made).toLocaleDateString()}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button asChild size="sm">
                  <a href={`${base}/${clip.id}/video?download=1`} download>
                    <Download /> Download
                  </a>
                </Button>
                {clip.caption && (
                  <Button size="sm" variant="outline" onClick={() => copy(clip.caption)}>
                    <Copy /> Copy caption
                  </Button>
                )}
              </div>
              {clip.caption && (
                <p className="text-sm whitespace-pre-line text-muted-foreground">{clip.caption}</p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}

export default function SharedPage() {
  return (
    <Suspense>
      <Shared />
    </Suspense>
  );
}
