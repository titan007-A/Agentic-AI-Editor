"use client";

import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { JobList } from "@/components/job-list";
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
import { usePolling } from "@/hooks/use-polling";
import { api, errorText, type Job } from "@/lib/api";
import { keepIfSame } from "@/lib/poller.mjs";

// Quick while something is being edited; slower otherwise, so jobs the autopilot or
// another device starts still show up.
const BUSY_MS = 3000;
const IDLE_MS = 15000;

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [clearing, setClearing] = useState(false);
  const [busyClearing, setBusyClearing] = useState(false);
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");
  const finished = jobs.filter((j) => j.status === "done" || j.status === "failed").length;

  const refresh = usePolling(
    () =>
      api
        .jobs()
        .then((next) => setJobs((current) => keepIfSame(current, next)))
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
        <JobList jobs={jobs} />
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
