"use client";

import { useCallback, useEffect, useState } from "react";

import { JobList } from "@/components/job-list";
import { NewJobForm } from "@/components/new-job-form";
import { api, type Job } from "@/lib/api";

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);

  const refresh = useCallback(() => {
    api
      .jobs()
      .then(setJobs)
      .catch(() => {});
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
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <NewJobForm onCreated={refresh} />
      <section className="space-y-3">
        <h2 className="font-hand text-3xl font-bold">Recent jobs</h2>
        <JobList jobs={jobs} />
      </section>
    </div>
  );
}
