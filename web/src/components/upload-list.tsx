"use client";

import { X } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import type { UploadItem } from "@/hooks/use-uploads";

const STATUS = { waiting: "waiting", done: "uploaded", failed: "failed" } as const;

/** Files being uploaded, each with its progress and a button that cancels or removes it. */
export function UploadList({
  items,
  onRemove,
}: {
  items: UploadItem[];
  onRemove: (key: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul className="w-full space-y-1.5">
      {items.map((item) => {
        const going = item.status === "waiting" || item.status === "uploading";
        return (
          <li key={item.key} className="sketch-tile flex items-center gap-2 py-1 pr-1 pl-3 text-xs">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex justify-between gap-2">
                <span className="truncate font-medium">{item.name}</span>
                <span
                  className={
                    item.status === "failed"
                      ? "shrink-0 text-destructive"
                      : "shrink-0 text-muted-foreground"
                  }
                >
                  {item.status === "uploading"
                    ? `${Math.round(item.progress * 100)}%`
                    : STATUS[item.status]}
                </span>
              </div>
              {going && (
                <Progress value={item.progress * 100} aria-label={`Uploading ${item.name}`} />
              )}
              {item.error && <p className="break-words text-destructive">{item.error}</p>}
            </div>
            <button
              type="button"
              aria-label={`${going ? "Cancel" : "Remove"} ${item.name}`}
              className="grid size-11 shrink-0 place-items-center rounded-md hover:bg-[rgb(233_185_73/0.3)] sm:size-8"
              onClick={() => onRemove(item.key)}
            >
              <X className="size-4" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
