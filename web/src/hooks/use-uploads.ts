"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText } from "@/lib/api";
import { isCancelled } from "@/lib/upload.mjs";

export type UploadItem = {
  key: string;
  name: string;
  status: "waiting" | "uploading" | "done" | "failed";
  progress: number;
  source?: string;
  error?: string;
};

/** Uploads files one after another, each with its own progress, cancel and error, so one
 * file that fails doesn't stop the rest. */
export function useUploads() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const queue = useRef<Promise<void>>(Promise.resolve());
  const counter = useRef(0);

  const change = useCallback((key: string, update: Partial<UploadItem>) => {
    setItems((current) =>
      current.map((item) => (item.key === key ? { ...item, ...update } : item)),
    );
  }, []);

  const drop = useCallback((key: string) => {
    controllers.current.get(key)?.abort();
    controllers.current.delete(key);
    setItems((current) => current.filter((item) => item.key !== key));
  }, []);

  const add = useCallback(
    (files: Iterable<File>) => {
      for (const file of files) {
        const key = `${++counter.current}-${file.name}`;
        const controller = new AbortController();
        controllers.current.set(key, controller);
        setItems((current) => [
          ...current,
          { key, name: file.name, status: "waiting", progress: 0 },
        ]);
        queue.current = queue.current.then(async () => {
          if (controller.signal.aborted) return;
          change(key, { status: "uploading" });
          let shown = 0;
          try {
            const uploaded = await api.upload(file, {
              signal: controller.signal,
              onProgress: (fraction) => {
                // A re-render per whole percent is plenty.
                if (Math.floor(fraction * 100) === Math.floor(shown * 100)) return;
                shown = fraction;
                change(key, { progress: fraction });
              },
            });
            change(key, { status: "done", progress: 1, source: uploaded.source });
          } catch (err) {
            if (!isCancelled(err)) change(key, { status: "failed", error: errorText(err) });
          } finally {
            controllers.current.delete(key);
          }
        });
      }
    },
    [change],
  );

  // Leaving the page stops what is still going.
  useEffect(() => {
    const running = controllers.current;
    return () => running.forEach((controller) => controller.abort());
  }, []);

  const clear = useCallback(() => {
    controllers.current.forEach((controller) => controller.abort());
    controllers.current.clear();
    setItems([]);
  }, []);

  return {
    items,
    add,
    remove: drop,
    clear,
    sources: items.flatMap((item) => (item.source ? [item.source] : [])),
    pending: items.some((item) => item.status === "waiting" || item.status === "uploading"),
  };
}
