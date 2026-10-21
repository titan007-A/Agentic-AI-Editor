"use client";

import { Download, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, thumbnailUrl, videoUrl, type Job, type Output } from "@/lib/api";

function seconds(value: number) {
  const m = Math.floor(value / 60);
  const s = (value % 60).toFixed(1).padStart(4, "0");
  return m ? `${m}:${s}` : `${value.toFixed(1)}s`;
}

export function OutputCard({ job, output }: { job: Job; output: Output }) {
  const router = useRouter();
  const [notes, setNotes] = useState("");
  const [open, setOpen] = useState(false);

  async function regenerate() {
    try {
      const next = await api.regenerate(job.id, output.preset, notes);
      setOpen(false);
      toast.success("Regenerating with your notes");
      router.push(`/job/?id=${next.id}`);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <Card className="tape tape-right gap-4">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="truncate">{output.preset}</span>
          <span className="shrink-0 text-xl font-medium text-muted-foreground">
            {seconds(output.duration)}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="filmstrip">
          <video
            className="mx-auto max-h-[70vh] w-full rounded-sm bg-black object-contain"
            src={videoUrl(output)}
            poster={output.has_thumbnail ? thumbnailUrl(output) : undefined}
            controls
            playsInline
            preload="metadata"
          />
        </div>
        {output.title && <p className="text-sm font-medium">{output.title}</p>}
        {output.edl.hook && (
          <p className="text-sm text-muted-foreground">Hook: {output.edl.hook}</p>
        )}
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{output.planner}</Badge>
          <Badge variant="secondary">
            {output.renders} render{output.renders === 1 ? "" : "s"}
          </Badge>
          {output.issues.map((issue) => (
            <Badge key={issue} variant="destructive">
              {issue}
            </Badge>
          ))}
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Edit decisions</summary>
          <ul className="mt-2 space-y-2">
            {output.edl.clips.map((clip) => (
              <li key={clip.start} className="rounded-md bg-secondary/40 p-2">
                <span className="font-mono text-xs">
                  {seconds(clip.start)} - {seconds(clip.end)}
                </span>
                <p className="mt-1 text-xs text-muted-foreground">{clip.reason}</p>
              </li>
            ))}
          </ul>
        </details>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <a href={videoUrl(output, true)}>
              <Download /> Download
            </a>
          </Button>
          {output.has_thumbnail && (
            <Button variant="outline" asChild>
              <a href={thumbnailUrl(output)} download>
                Thumbnail
              </a>
            </Button>
          )}
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button variant="outline">
                <RefreshCw /> Regenerate
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Regenerate {output.preset}</DialogTitle>
              </DialogHeader>
              <div className="space-y-2">
                <Label htmlFor={`notes-${output.id}`}>What should change?</Label>
                <Textarea
                  id={`notes-${output.id}`}
                  rows={4}
                  placeholder="e.g. start where the singer first comes in, end on the high note"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </div>
              <DialogFooter>
                <Button onClick={regenerate}>Regenerate</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  );
}
