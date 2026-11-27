"use client";

import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { BadgeStrip } from "@/components/badge-strip";
import { JobList } from "@/components/job-list";
import { jobLabel } from "@/components/job-status";
import { NewJobForm } from "@/components/new-job-form";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMe } from "@/hooks/use-me";
import { usePolling } from "@/hooks/use-polling";
import { api, errorText, type Job } from "@/lib/api";
import { confetti } from "@/lib/confetti";
import { cheer } from "@/lib/fun-events";
import { keepIfSame } from "@/lib/poller.mjs";
import { toastKey, toastText } from "@/lib/song-notice.mjs";
import { sounds } from "@/lib/sounds";

// Quick while something is being edited; slower otherwise, so jobs the autopilot or
// another device starts still show up.
const BUSY_MS = 3000;
const IDLE_MS = 15000;

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [busyClearing, setBusyClearing] = useState(false);
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");
  const finished = jobs.filter((j) => j.status === "done" || j.status === "failed").length;
  const me = useMe();
  const router = useRouter();
  const fun = me?.features?.includes("fun") ?? false;
  // A job seen running that is now done: snowfall and a bell (once, however many).
  const seenRunning = useRef<Set<string>>(new Set());
  useEffect(() => {
    let finishedNow = false;
    let failedNow = false;
    for (const job of jobs) {
      if (job.status === "queued" || job.status === "running") seenRunning.current.add(job.id);
      else if (seenRunning.current.delete(job.id)) {
        if (job.status === "done") finishedNow = true;
        else if (job.status === "failed") failedNow = true;
      }
    }
    if (fun && finishedNow) {
      confetti(60, true);
      sounds.ready();
      cheer("done");
    } else if (fun && failedNow) cheer("failed");
  }, [jobs, fun]);

  // A song notice that turns up on a job after the page opened (the edit found its song,
  // or the song went on the strike list) is said once, as a toast.
  const toasted = useRef<Set<string> | null>(null);
  useEffect(() => {
    const keys = jobs
      .map((job) => [job, toastKey(job.id, job.song)] as const)
      .filter((pair): pair is readonly [Job, string] => pair[1] !== null);
    if (!loaded) return;
    if (toasted.current === null) {
      toasted.current = new Set(keys.map(([, key]) => key));
      return;
    }
    for (const [job, key] of keys) {
      if (toasted.current.has(key)) continue;
      toasted.current.add(key);
      const text = `${toastText(job.song)} (${jobLabel(job)})`;
      const open = { label: "Open", onClick: () => router.push(`/job/?id=${job.id}`) };
      if (job.song?.blocked) toast.error(text, { action: open });
      else toast.warning(text, { action: open });
    }
  }, [jobs, loaded, router]);

  const refresh = usePolling(
    () =>
      api
        .jobs()
        .then((next) => {
          setJobs((current) => keepIfSame(current, next));
          setLoaded(true);
        })
        .catch(() => {}),
    busy ? BUSY_MS : IDLE_MS,
  );

  async function clearAll() {
    setBusyClearing(true);
    try {
      const { deleted, freed_mb } = await api.clearJobs();
      toast.success(`${deleted} job${deleted === 1 ? "" : "s"} deleted, ${freed_mb} MB freed`);
      setClearing(false);
      refresh();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusyClearing(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {fun && <BadgeStrip tick={finished} />}
      <NewJobForm onCreated={refresh} />
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="font-hand text-3xl font-bold">Recent jobs</h2>
          {finished > 0 && (
            <Button variant="outline" size="sm" onClick={() => setClearing(true)}>
              <Trash2 /> Clear all
            </Button>
          )}
        </div>
        <JobList jobs={jobs} onChange={refresh} />
      </section>

      <Dialog open={clearing} onOpenChange={setClearing}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete all finished jobs?</DialogTitle>
            <DialogDescription>
              {finished} finished job{finished === 1 ? "" : "s"} and their videos are deleted from
              the server to free its disk. Jobs still being edited stay. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="destructive" disabled={busyClearing} onClick={clearAll}>
              {busyClearing ? "Deleting..." : "Delete all"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
