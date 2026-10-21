"use client";

import { ArrowLeft, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { JobStatus, jobLabel } from "@/components/job-status";
import { OutputCard } from "@/components/output-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useJobEvents } from "@/hooks/use-job-events";
import { api, errorText, type Job } from "@/lib/api";

function JobDetail() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);

  const load = useCallback(() => {
    api
      .job(id)
      .then(setJob)
      .catch((err) => toast.error(errorText(err)));
  }, [id]);

  useEffect(() => {
    if (id) load();
  }, [id, load]);

  const active = job?.status === "queued" || job?.status === "running";
  const events = useJobEvents(id, active, load);
  const latest = events.at(-1);
  const progress = latest?.progress ?? job?.progress ?? 0;

  async function remove() {
    try {
      await api.deleteJob(id);
      router.push("/");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (!job) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/" aria-label="Back to jobs">
            <ArrowLeft />
          </Link>
        </Button>
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="truncate text-lg font-semibold">{jobLabel(job)}</h1>
          <p className="truncate text-xs text-muted-foreground">{job.source}</p>
          {job.notes && <p className="text-sm">Notes: {job.notes}</p>}
        </div>
        <JobStatus job={job} />
        {!active && (
          <Button variant="ghost" size="icon" onClick={remove} aria-label="Delete job">
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

      {job.status === "failed" && (
        <Card className="border-destructive p-4">
          <p className="text-sm whitespace-pre-wrap text-destructive">{job.error}</p>
        </Card>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        {job.outputs.map((output) => (
          <OutputCard key={output.id} job={job} output={output} />
        ))}
      </div>
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
