"use client";

import { Copy, PenLine } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorText, type Output } from "@/lib/api";

const POLL_MS = 4000;

type Caption = Pick<Output, "caption" | "caption_status" | "caption_sources" | "caption_notes">;

function captionOf(output: Output): Caption {
  return {
    caption: output.caption ?? "",
    caption_status: output.caption_status ?? null,
    caption_sources: output.caption_sources ?? [],
    caption_notes: output.caption_notes ?? [],
  };
}

/** The post's caption under a video: written on request (or by the preset after every
 * render) from Wikipedia and Wikidata, with what to check before posting it. */
export function PostCaption({ output }: { output: Output }) {
  // What this card fetched itself, until the page hands it a newer output.
  const [fetched, setFetched] = useState<{ from: Output; value: Caption } | null>(null);
  const [asking, setAsking] = useState(false);
  const state = fetched?.from === output ? fetched.value : captionOf(output);
  const busy = state.caption_status === "queued" || state.caption_status === "writing";
  const setState = (value: Caption) => setFetched({ from: output, value });

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const job = await api.output(output.id);
        const latest = job.outputs.find((o) => o.id === output.id);
        if (latest) setFetched({ from: output, value: captionOf(latest) });
      } catch {
        // The next poll tries again.
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, output]);

  async function write() {
    setAsking(true);
    try {
      const job = await api.writeCaption(output.id);
      const latest = job.outputs.find((o) => o.id === output.id);
      if (latest) setState(captionOf(latest));
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setAsking(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(state.caption);
      toast.success("Caption copied");
    } catch {
      toast.error("Couldn't copy; select the text and copy it");
    }
  }

  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">Caption</p>
        <div className="flex flex-wrap gap-2">
          {state.caption_status === "done" && state.caption && (
            <Button variant="outline" size="sm" onClick={copy}>
              <Copy /> Copy
            </Button>
          )}
          <Button variant="outline" size="sm" disabled={busy || asking} onClick={write}>
            <PenLine />
            {busy
              ? "Writing..."
              : state.caption_status === "done" || state.caption_status === "failed"
                ? "Write again"
                : "Write caption"}
          </Button>
        </div>
      </div>
      {busy && (
        <p className="text-xs text-muted-foreground">
          Reading Wikipedia about the song and writing it; about a minute.
        </p>
      )}
      {state.caption_status === "failed" && (
        <p className="text-xs text-destructive">
          No caption: {state.caption_notes[0] ?? "something went wrong"}
        </p>
      )}
      {state.caption_status === "done" && state.caption && (
        <>
          <p className="rounded-md bg-secondary/40 p-3 text-sm whitespace-pre-wrap">
            {state.caption}
          </p>
          {state.caption_notes.length > 0 && (
            <div className="rounded-md border border-destructive/40 p-2 text-xs">
              <p className="font-medium text-destructive">Check before posting</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
                {state.caption_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          )}
          {state.caption_sources.length > 0 && (
            <p className="text-xs text-muted-foreground">
              From:{" "}
              {state.caption_sources.map((source, i) => (
                <span key={source.url}>
                  {i > 0 && " · "}
                  <a href={source.url} target="_blank" rel="noreferrer" className="underline">
                    {source.title}
                  </a>
                </span>
              ))}
            </p>
          )}
        </>
      )}
    </div>
  );
}
