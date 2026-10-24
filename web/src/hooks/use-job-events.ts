"use client";

import { useEffect, useState } from "react";

import type { JobEvent } from "@/lib/api";

// How long to wait before following a job again after its stream broke.
const RETRY_MS = 3000;

/** Follows a job's progress stream until the server says it has ended. `reload` fetches
 * the job again and says whether it is still running. */
export function useJobEvents(jobId: string, active: boolean, reload: () => Promise<boolean>) {
  const [stream, setStream] = useState<{ jobId: string; events: JobEvent[] }>({
    jobId: "",
    events: [],
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!active) return;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    // The server sends every event again on each new connection.
    source.onopen = () => setStream({ jobId, events: [] });
    source.onmessage = (message) => {
      setStream((current) => ({ jobId, events: [...current.events, JSON.parse(message.data)] }));
    };
    source.addEventListener("end", () => {
      source.close();
      reload();
    });
    // The browser's own retry gives up on a refused stream (logged out, job deleted) and
    // repeats a broken one without a word. Ask for the job instead: a 401 goes to the
    // login page, a missing job stops here, and a job still running is followed again.
    source.onerror = () => {
      source.close();
      reload().then((running) => {
        if (running && !closed) retry = setTimeout(() => setAttempt((n) => n + 1), RETRY_MS);
      });
    };
    return () => {
      closed = true;
      clearTimeout(retry);
      source.close();
    };
  }, [jobId, active, reload, attempt]);

  return active && stream.jobId === jobId ? stream.events : [];
}
