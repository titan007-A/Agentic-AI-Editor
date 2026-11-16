"use client";

import Link from "next/link";

import { JobStatus, jobLabel } from "@/components/job-status";
import { RetryButton } from "@/components/retry-button";
import { Snowman } from "@/components/snowman";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useFun } from "@/hooks/use-fun";
import type { Job } from "@/lib/api";
import { timeLeft } from "@/lib/eta";

export function JobList({ jobs, onChange }: { jobs: Job[]; onChange: () => void }) {
  const fun = useFun();
  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">No jobs yet.</p>;
  }
  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <div key={job.id} className="relative">
          <Link href={`/job/?id=${job.id}`} className="block">
            <Card className="gap-2 px-4 py-3 transition-transform hover:-rotate-[0.6deg] hover:bg-[var(--cream-hover)]">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-sm font-medium">{jobLabel(job)}</span>
                <JobStatus job={job} />
              </div>
              <div
                className={`flex flex-wrap gap-x-3 text-xs text-muted-foreground ${job.status === "failed" ? "pr-24" : ""}`}
              >
                {job.owner && <span className="font-semibold text-foreground">{job.owner}</span>}
                <span>{job.presets.join(", ")}</span>
                {job.trim === false && <span>whole video</span>}
                <span>{new Date(job.created_at + "Z").toLocaleString()}</span>
              </div>
              {(job.status === "running" || job.status === "queued") && (
                <div className="flex items-center gap-2">
                  {fun && <Snowman value={job.progress} size={30} falling={false} />}
                  <Progress value={job.progress * 100} className="h-1.5 flex-1" />
                  {timeLeft(job.eta_seconds, job.status === "queued") && (
                    <span className="shrink-0 text-xs text-muted-foreground" data-testid="eta">
                      {timeLeft(job.eta_seconds, job.status === "queued")}
                    </span>
                  )}
                </div>
              )}
            </Card>
          </Link>
          {job.status === "failed" && (
            <RetryButton job={job} onRetried={onChange} className="absolute right-3 bottom-2" />
          )}
        </div>
      ))}
    </div>
  );
}
