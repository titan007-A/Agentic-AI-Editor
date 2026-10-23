"use client";

import { useState } from "react";

import { JobList } from "@/components/job-list";
import { NewJobForm } from "@/components/new-job-form";
import { usePolling } from "@/hooks/use-polling";
import { api, type Job } from "@/lib/api";
import { keepIfSame } from "@/lib/poller.mjs";

// Quick while something is being edited; slower otherwise, so jobs the autopilot or
// another device starts still show up.
const BUSY_MS = 3000;
const IDLE_MS = 15000;

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");

  const refresh = usePolling(
    () =>
      api
        .jobs()
        .then((next) => setJobs((current) => keepIfSame(current, next)))
        .catch(() => {}),
    busy ? BUSY_MS : IDLE_MS,
  );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <NewJobForm onCreated={refresh} />
      <section className="space-y-3">
        <h2 className="font-hand text-3xl font-bold">Recent jobs</h2>
        <JobList jobs={jobs} />
      </section>
    </div>
  );
}
