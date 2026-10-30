"use client";

import { EyeOff, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errorText, type Feedback, type Research, type ResearchItem } from "@/lib/api";

const REASONS = ["Wrong music", "Wrong artist", "Poor quality", "Not viral enough"];

/** Thumbs up or down on a reel a research gave, with why it wasn't for them: later
 * researches find more like the liked ones and fewer like these, and never show a hidden
 * account's reels. */
export function ReelFeedback({
  item,
  onChanged,
}: {
  item: ResearchItem;
  onChanged: (next: Research) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [own, setOwn] = useState("");

  async function send(feedback: Feedback) {
    setBusy(true);
    try {
      onChanged(await api.giveFeedback(item.id, feedback));
      if (feedback.hide_account) toast.success(`@${item.handle} won't be shown again`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const bad = item.verdict === "bad";
  return (
    <div className="space-y-2 rounded-md border p-2">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={item.verdict === "good" ? "default" : "outline"}
          aria-pressed={item.verdict === "good"}
          disabled={busy}
          onClick={() => send({ verdict: item.verdict === "good" ? null : "good" })}
        >
          <ThumbsUp /> Good pick
        </Button>
        <Button
          size="sm"
          variant={bad ? "default" : "outline"}
          aria-pressed={bad}
          disabled={busy}
          onClick={() => send({ verdict: bad ? null : "bad" })}
        >
          <ThumbsDown /> Not for me
        </Button>
      </div>
      {bad && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">Why not? Later researches learn from it.</p>
          <div className="flex flex-wrap gap-2">
            {REASONS.map((reason) => (
              <Button
                key={reason}
                size="sm"
                variant={item.verdict_reason === reason ? "secondary" : "ghost"}
                aria-pressed={item.verdict_reason === reason}
                disabled={busy}
                onClick={() => send({ verdict: "bad", reason })}
              >
                {reason}
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (own.trim()) send({ verdict: "bad", reason: own.trim() });
            }}
          >
            <Input
              aria-label="Why it wasn't for you, in your own words"
              placeholder="Or in your own words"
              maxLength={300}
              value={own}
              onChange={(e) => setOwn(e.target.value)}
            />
            <Button type="submit" size="sm" variant="outline" disabled={busy || !own.trim()}>
              Save
            </Button>
          </form>
          {item.verdict_reason && !REASONS.includes(item.verdict_reason) && (
            <p className="text-xs break-words text-muted-foreground">
              You said: {item.verdict_reason}
            </p>
          )}
          {item.handle && !item.hidden && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                send({ verdict: "bad", reason: item.verdict_reason, hide_account: true })
              }
            >
              <EyeOff /> Never show @{item.handle} again
            </Button>
          )}
        </div>
      )}
      {item.hidden && (
        <p className="text-xs text-muted-foreground">
          @{item.handle} is hidden from your researches.
        </p>
      )}
    </div>
  );
}
