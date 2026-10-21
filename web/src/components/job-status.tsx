import { Badge } from "@/components/ui/badge";
import type { Job } from "@/lib/api";

const VARIANT = {
  queued: "secondary",
  running: "default",
  done: "outline",
  failed: "destructive",
} as const;

export function JobStatus({ job }: { job: Job }) {
  const label = job.status === "running" ? job.stage : job.status;
  return <Badge variant={VARIANT[job.status]}>{label}</Badge>;
}

export function jobLabel(job: Job): string {
  if (job.title) return job.title;
  return job.source.replace(/^upload:[0-9a-f]+\//, "");
}
