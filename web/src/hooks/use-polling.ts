"use client";

import { useCallback, useEffect, useRef } from "react";

import { createPoller, type Poller } from "@/lib/poller.mjs";

/**
 * Calls `run` now and then every `delay` ms while the page is visible; `null` pauses it.
 * Returns a function that asks for a fresh call right away.
 */
export function usePolling(run: () => unknown, delay: number | null): () => void {
  const latest = useRef(run);
  const poller = useRef<Poller | null>(null);
  const wait = useRef(delay);
  const enabled = delay !== null;

  useEffect(() => {
    latest.current = run;
    wait.current = delay;
  });

  useEffect(() => {
    if (!enabled) return;
    const created = createPoller(() => latest.current(), wait.current ?? 0);
    poller.current = created;
    created.start();
    return () => {
      created.stop();
      poller.current = null;
    };
  }, [enabled]);

  useEffect(() => {
    if (delay !== null) poller.current?.setDelay(delay);
  }, [delay]);

  return useCallback(() => poller.current?.now(), []);
}
