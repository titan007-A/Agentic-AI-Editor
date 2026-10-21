"use client";

import { useEffect, useState } from "react";

import type { JobEvent } from "@/lib/api";

/** Follows a job's progress stream until the server says it has ended. */
export function useJobEvents(jobId: string, active: boolean, onEnd: () => void) {
  const [stream, setStream] = useState<{ jobId: string; events: JobEvent[] }>({
    jobId: "",
    events: [],
  });

  useEffect(() => {
    if (!active) return;
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onopen = () => setStream({ jobId, events: [] });
    source.onmessage = (message) => {
      setStream((current) => ({ jobId, events: [...current.events, JSON.parse(message.data)] }));
    };
    source.addEventListener("end", () => {
      source.close();
      onEnd();
    });
    return () => source.close();
  }, [jobId, active, onEnd]);

  return active && stream.jobId === jobId ? stream.events : [];
}
