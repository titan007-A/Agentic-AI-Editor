"use client";

import { useCallback, useEffect, useState } from "react";

import { JobList } from "@/components/job-list";
import { NewJobForm } from "@/components/new-job-form";
import { api, type Job } from "@/lib/api";

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);

  const refresh = useCallback(() => {
    api.jobs().then(setJobs).catch(() => {});
  }, []);

  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <NewJobForm onCreated={refresh} />
      <section className="space-y-3">
        <h2 className="text-sm font-medium">Recent jobs</h2>
        <JobList jobs={jobs} />
      </section>
    </div>
  );
}
