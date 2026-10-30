import { Badge } from "@/components/ui/badge";
import type { Job } from "@/lib/api";

const VARIANT = {
  queued: "secondary",
  running: "default",
  done: "outline",
  failed: "destructive",
} as const;

/** A job that stopped only because it couldn't tell which song the video is: it waits
 * for the person to name it (see NameSongForm) rather than having failed. */
export const needsName = (job: Job) => job.status === "failed" && job.unnamed.length > 0;

export function JobStatus({ job }: { job: Job }) {
  if (needsName(job)) {
    return (
      <Badge variant="outline" data-tone="ask">
        needs song name
      </Badge>
    );
  }
  const stage = job.stage === "wait" ? "waiting" : job.stage;
  const label = job.status === "running" ? stage : job.status;
  return <Badge variant={VARIANT[job.status]}>{label}</Badge>;
}

export function jobLabel(job: Job): string {
  if (job.title) return job.title;
  return job.source.replace(/^upload:[0-9a-f]+\//, "");
}
