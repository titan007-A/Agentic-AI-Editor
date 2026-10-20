"use client";

import { useEffect, useState } from "react";

import type { JobEvent } from "@/lib/api";

/** Follows a job's progress stream until the server says it has ended. */
export function useJobEvents(jobId: string, active: boolean, onEnd: () => void) {
  const [events, setEvents] = useState<JobEvent[]>([]);

  useEffect(() => {
    setEvents([]);
    if (!active) return;
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (message) => {
      setEvents((current) => [...current, JSON.parse(message.data)]);
    };
    source.addEventListener("end", () => {
      source.close();
      onEnd();
    });
    return () => source.close();
  }, [jobId, active, onEnd]);

  return events;
}
