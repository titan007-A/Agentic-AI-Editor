"use client";

import { useState } from "react";

import { api, errorText, type InstagramAccount, type Performance } from "@/lib/api";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const day = (when: string) =>
  new Date(when + "Z").toLocaleDateString("en", { day: "numeric", month: "short" });

/** How the account's own reels do, from the snapshot taken each time it is learned:
 * likes a reel against the snapshot before, and each reel's likes and growth. Search
 * engines find only part of a page, so it is a sample of its reels, not all of them. */
export function PagePerformance({ account }: { account: InstagramAccount }) {
  const [shown, setShown] = useState<Performance | null>(null);
  const [error, setError] = useState("");

  function load(open: boolean) {
    if (!open) return;
    api
      .pagePerformance(account.id)
      .then((p) => {
        setShown(p);
        setError("");
      })
      .catch((err) => setError(errorText(err)));
  }

  const change =
    shown?.average && shown.previous_average
      ? Math.round(((shown.average - shown.previous_average) / shown.previous_average) * 100)
      : null;
  return (
    <details className="text-sm" onToggle={(e) => load(e.currentTarget.open)}>
      <summary className="cursor-pointer text-muted-foreground">Page performance</summary>
      {error && <p className="mt-1 text-destructive">{error}</p>}
      {shown && !shown.taken_at && (
        <p className="mt-1 text-muted-foreground">
          None of its reels turned up yet; &ldquo;Learn again&rdquo; looks once more.
        </p>
      )}
      {shown?.taken_at && (
        <div className="mt-1 space-y-2">
          <p>
            {shown.reels.length} of its reels found on {day(shown.taken_at)}
            {shown.average !== null && <> · {compact.format(shown.average)} likes a reel</>}
            {change !== null && shown.previous_at && (
              <span className={change >= 0 ? "text-primary" : "text-destructive"}>
                {" "}
                ({change >= 0 ? "+" : ""}
                {change}% since {day(shown.previous_at)})
              </span>
            )}
          </p>
          <ol className="list-inside list-decimal space-y-1">
            {shown.reels.slice(0, 8).map((r) => (
              <li key={r.code} className="break-words">
                <a href={r.url} target="_blank" rel="noreferrer" className="underline">
                  {r.caption || r.code}
                </a>{" "}
                <span className="text-muted-foreground">
                  · {r.likes === null ? "?" : compact.format(r.likes)} likes
                  {r.gained !== null &&
                    r.gained !== 0 &&
                    ` (${r.gained > 0 ? "+" : ""}${compact.format(r.gained)})`}
                  {r.per_day !== null && ` · ${compact.format(r.per_day)} a day`}
                </span>
              </li>
            ))}
          </ol>
          <p className="text-xs text-muted-foreground">
            Search engines find only part of a page, so this is a sample of its reels. A new
            snapshot is taken each time the account is learned.
          </p>
        </div>
      )}
    </details>
  );
}
