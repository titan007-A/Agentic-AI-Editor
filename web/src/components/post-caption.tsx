"use client";

import { Copy, Music, PenLine } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type Output } from "@/lib/api";

const POLL_MS = 4000;

type Caption = Pick<
  Output,
  | "caption"
  | "caption_status"
  | "caption_sources"
  | "caption_notes"
  | "caption_song"
  | "caption_artist"
  | "caption_topic"
>;

function captionOf(output: Output): Caption {
  return {
    caption: output.caption ?? "",
    caption_status: output.caption_status ?? null,
    caption_sources: output.caption_sources ?? [],
    caption_notes: output.caption_notes ?? [],
    caption_song: output.caption_song ?? "",
    caption_artist: output.caption_artist ?? "",
    caption_topic: output.caption_topic ?? "",
  };
}

/** The post's caption under a video: written on request (or by the preset after every
 * render) from Wikipedia and Wikidata, with what to check before posting it. */
export function PostCaption({ output }: { output: Output }) {
  // What this card fetched itself, until the page hands it a newer output.
  const [fetched, setFetched] = useState<{ from: Output; value: Caption } | null>(null);
  const [asking, setAsking] = useState(false);
  // Naming the song, or another topic, by hand: open by itself when no caption could be
  // written.
  const [naming, setNaming] = useState(false);
  const [kind, setKind] = useState<"song" | "topic">("song");
  const [song, setSong] = useState("");
  const [artist, setArtist] = useState("");
  const [topic, setTopic] = useState("");
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

  const failed = state.caption_status === "failed";
  const showNaming = !busy && (naming || failed);

  function openNaming() {
    setKind(state.caption_topic ? "topic" : "song");
    setSong(state.caption_song);
    setArtist(state.caption_artist);
    setTopic(state.caption_topic);
    setNaming(true);
  }

  const ready = kind === "topic" ? topic.trim() !== "" : song.trim() !== "" && artist.trim() !== "";

  async function write(named?: { song: string; artist: string } | { topic: string }) {
    setAsking(true);
    try {
      const job = await api.writeCaption(output.id, named);
      const latest = job.outputs.find((o) => o.id === output.id);
      if (latest) setState(captionOf(latest));
      if (named) setNaming(false);
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
          <Button variant="outline" size="sm" disabled={busy || asking} onClick={() => write()}>
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
          Reading Wikipedia about {state.caption_topic || "the song"} and writing it; about a
          minute.
        </p>
      )}
      {failed && (
        <p className="text-xs text-destructive">
          No caption: {state.caption_notes[0] ?? "something went wrong"}
        </p>
      )}
      {state.caption_status === "done" && !showNaming && (
        <p className="text-xs text-muted-foreground">
          {/* Captions written before the song was kept don't know it. */}
          {state.caption_topic
            ? `About ${state.caption_topic}. `
            : state.caption_song &&
              `About “${state.caption_song}”${state.caption_artist && ` by ${state.caption_artist}`}. `}
          <button type="button" className="underline" onClick={openNaming}>
            Wrong song or topic?
          </button>
        </p>
      )}
      {showNaming && (
        <form
          className="space-y-2 rounded-md border p-2"
          onSubmit={(event) => {
            event.preventDefault();
            write(
              kind === "topic"
                ? { topic: topic.trim() }
                : { song: song.trim(), artist: artist.trim() },
            );
          }}
        >
          <div className="flex flex-wrap gap-2" role="group" aria-label="What the post is about">
            {(["song", "topic"] as const).map((choice) => (
              <Button
                key={choice}
                type="button"
                size="sm"
                variant={kind === choice ? "default" : "outline"}
                aria-pressed={kind === choice}
                onClick={() => setKind(choice)}
              >
                {choice === "song" ? "A song" : "Another topic"}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {kind === "song"
              ? "Name the song and it is looked up on Wikipedia and written from there."
              : "Name what the post is about (a person, place, event, film...) as Wikipedia would; the article it used is linked under the caption."}
          </p>
          {kind === "topic" ? (
            <div className="space-y-1">
              <Label htmlFor={`topic-${output.id}`}>Topic</Label>
              <Input
                id={`topic-${output.id}`}
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="Topic"
                maxLength={200}
                required
              />
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor={`song-${output.id}`}>Song</Label>
                <Input
                  id={`song-${output.id}`}
                  value={song}
                  onChange={(event) => setSong(event.target.value)}
                  placeholder="Song name"
                  maxLength={200}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor={`artist-${output.id}`}>Artist</Label>
                <Input
                  id={`artist-${output.id}`}
                  value={artist}
                  onChange={(event) => setArtist(event.target.value)}
                  placeholder="Artist name"
                  maxLength={200}
                  required
                />
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={asking || !ready}>
              <Music />{" "}
              {kind === "topic" ? "Write caption on this topic" : "Write caption for this song"}
            </Button>
            {naming && !failed && (
              <Button type="button" variant="ghost" size="sm" onClick={() => setNaming(false)}>
                Cancel
              </Button>
            )}
          </div>
        </form>
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
