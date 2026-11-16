"use client";

import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorText, type Job } from "@/lib/api";

/** Puts a failed job back in the queue, as it was asked for. */
export function RetryButton({
  job,
  onRetried,
  className,
}: {
  job: Job;
  onRetried: (job: Job) => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);

  async function retry() {
    setBusy(true);
    try {
      onRetried(await api.retryJob(job.id));
      toast.success("Back in the queue");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button variant="outline" size="sm" disabled={busy} onClick={retry} className={className}>
      <RotateCcw /> {busy ? "Retrying..." : "Retry"}
    </Button>
  );
}
