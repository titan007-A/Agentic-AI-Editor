"use client";

import { Music } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type Job } from "@/lib/api";

/** Shown when a preset that names the song couldn't tell which it is (a link's name says
 * "Video by ...", and its sound wasn't recognised): the name goes to the notes as
 * "song:" and "artist:" lines and the job is made again from its cached download. */
export function NameSongForm({ job }: { job: Job }) {
  const router = useRouter();
  const [song, setSong] = useState("");
  const [artist, setArtist] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = song.trim() !== "" && artist.trim() !== "";

  async function carryOn() {
    if (busy || !ready) return;
    setBusy(true);
    try {
      const notes = `song: ${song.trim()}\nartist: ${artist.trim()}`;
      const next = await api.regenerate(job.id, job.unnamed[0], notes);
      // The stopped job made nothing: the new one takes its place in the list.
      if (job.outputs.length === 0) await api.deleteJob(job.id).catch(() => {});
      toast.success("Carrying on with that song");
      router.push(`/job/?id=${next.id}`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="gap-3 p-4">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          carryOn();
        }}
      >
        <div className="space-y-1">
          <p className="font-medium">Which song is it?</p>
          <p className="text-sm text-muted-foreground">
            The reel shows the song&apos;s name, and neither the video&apos;s name, its sound nor
            its post&apos;s caption said which song it is. Name it and the reel is made, from where
            the job stopped.
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor={`unnamed-song-${job.id}`}>Song</Label>
            <Input
              id={`unnamed-song-${job.id}`}
              value={song}
              onChange={(event) => setSong(event.target.value)}
              placeholder="Song name"
              maxLength={200}
              required
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`unnamed-artist-${job.id}`}>Artist</Label>
            <Input
              id={`unnamed-artist-${job.id}`}
              value={artist}
              onChange={(event) => setArtist(event.target.value)}
              placeholder="Artist name"
              maxLength={200}
              required
            />
          </div>
        </div>
        <Button type="submit" size="sm" disabled={busy || !ready}>
          <Music /> Make the reel with this song
        </Button>
      </form>
    </Card>
  );
}
