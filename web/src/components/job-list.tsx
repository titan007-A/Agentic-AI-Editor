"use client";

import Link from "next/link";

import { JobStatus, jobLabel } from "@/components/job-status";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { Job } from "@/lib/api";

export function JobList({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">No jobs yet.</p>;
  }
  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <Link key={job.id} href={`/job/?id=${job.id}`} className="block">
          <Card className="gap-2 px-4 py-3 transition-transform hover:-rotate-[0.6deg] hover:bg-[#fdf3dc]">
            <div className="flex items-center justify-between gap-3">
              <span className="truncate text-sm font-medium">{jobLabel(job)}</span>
              <JobStatus job={job} />
            </div>
            <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
              <span>{job.presets.join(", ")}</span>
              <span>{new Date(job.created_at + "Z").toLocaleString()}</span>
            </div>
            {(job.status === "running" || job.status === "queued") && (
              <Progress value={job.progress * 100} className="h-1.5" />
            )}
          </Card>
        </Link>
      ))}
    </div>
  );
}
