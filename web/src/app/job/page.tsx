"use client";

import { ArrowLeft, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { JobStatus, jobLabel, needsName } from "@/components/job-status";
import { NameSongForm } from "@/components/name-song-form";
import { OutputCard } from "@/components/output-card";
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
import { Progress } from "@/components/ui/progress";
import { useJobEvents } from "@/hooks/use-job-events";
import { api, ApiError, errorText, type Job } from "@/lib/api";

function JobDetail() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [loaded, setLoaded] = useState<Job | null>(null);
  // Until the job asked for arrives, the one before (after Regenerate) isn't shown.
  const job = loaded?.id === id ? loaded : null;
  const wanted = useRef(id);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    wanted.current = id;
  });

  /** Fetches the job and says whether it is still running. With `quietOffline`, a
   * server that can't be reached counts as running, to be tried again without a word. */
  const load = useCallback(
    (quietOffline = false) =>
      api
        .job(id)
        .then((next) => {
          if (wanted.current !== id) return false;
          setLoaded(next);
          return next.status === "queued" || next.status === "running";
        })
        .catch((err) => {
          if (quietOffline && !(err instanceof ApiError)) return true;
          toast.error(errorText(err));
          return false;
        }),
    [id],
  );
  const follow = useCallback(() => load(true), [load]);

  useEffect(() => {
    if (id) load();
  }, [id, load]);

  const active = job?.status === "queued" || job?.status === "running";
  const events = useJobEvents(id, active, follow);
  const latest = events.at(-1);
  const progress = latest?.progress ?? job?.progress ?? 0;

  async function remove() {
    setBusy(true);
    try {
      await api.deleteJob(id);
      setDeleting(false);
      router.push("/");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!job) return <p className="text-sm text-muted-foreground">Loading...</p>;
  // The job is fetched once; while it runs, the latest event says where it is now.
  const now = active && latest ? { ...job, status: "running" as const, stage: latest.stage } : job;

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/" aria-label="Back to jobs">
            <ArrowLeft />
          </Link>
        </Button>
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="line-clamp-2 font-hand text-3xl leading-tight font-bold break-words sm:text-4xl">
            <span className="marker">{jobLabel(job)}</span>
          </h1>
          <p className="truncate text-xs text-muted-foreground">{job.source}</p>
          {job.notes && <p className="text-sm">Notes: {job.notes}</p>}
        </div>
        <JobStatus job={now} />
        {!active && (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDeleting(true)}
            aria-label="Delete job"
          >
            <Trash2 />
          </Button>
        )}
      </div>

      {active && (
        <Card className="gap-3 p-4">
          <Progress value={progress * 100} />
          <p className="text-sm">{latest?.message ?? "Waiting for a free slot..."}</p>
          <ul className="max-h-48 space-y-1 overflow-y-auto font-mono text-xs text-muted-foreground">
            {events.map((e, i) => (
              <li key={i}>
                {new Date(e.time + "Z").toLocaleTimeString()} {e.stage}: {e.message}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {needsName(job) ? (
        <NameSongForm job={job} />
      ) : (
        job.status === "failed" && (
          <Card className="border-destructive p-4">
            <p className="text-sm whitespace-pre-wrap text-destructive">{job.error}</p>
          </Card>
        )
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {job.outputs.map((output) => (
          <OutputCard key={output.id} job={job} output={output} />
        ))}
      </div>

      <Dialog open={deleting} onOpenChange={setDeleting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this job?</DialogTitle>
            <DialogDescription>
              {jobLabel(job)} and its {job.outputs.length === 1 ? "video" : "videos"} are removed
              from the app. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="destructive" disabled={busy} onClick={remove}>
              Delete job
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function JobPage() {
  return (
    <Suspense>
      <JobDetail />
    </Suspense>
  );
}
