"use client";

import { Scissors, Film } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMe } from "@/hooks/use-me";
import type { Preset } from "@/lib/api";
import { asksTrim } from "@/lib/preset-fields.mjs";

/**
 * Asks, as an edit starts, whether to cut the video to the preset's length or keep all of
 * it: for accounts with the trim_choice feature and presets that aren't about a song.
 * `choose` resolves true (trim), false (keep the whole video) or null (closed: don't start).
 */
export function useTrimChoice() {
  const me = useMe();
  const on = me?.features?.includes("trim_choice") ?? false;
  const [asking, setAsking] = useState<Preset[]>([]);
  const answer = useRef<(trim: boolean | null) => void>(() => {});

  function choose(presets: (Preset | undefined)[]): Promise<boolean | null> {
    const asked = presets.filter((p): p is Preset => p !== undefined && asksTrim(p));
    if (!on || asked.length === 0) return Promise.resolve(true);
    setAsking([...new Map(asked.map((p) => [p.name, p])).values()]);
    return new Promise((resolve) => {
      answer.current = resolve;
    });
  }

  function done(trim: boolean | null) {
    setAsking([]);
    answer.current(trim);
    answer.current = () => {};
  }

  const dialog = (
    <Dialog open={asking.length > 0} onOpenChange={(open) => !open && done(null)}>
      <DialogContent data-testid="trim-choice">
        <DialogHeader>
          <DialogTitle>Trim the video?</DialogTitle>
          <DialogDescription>
            {asking
              .map((p) => `${p.label} makes ${p.min_duration}-${p.max_duration} s reels`)
              .join("; ")}
            . Trim cuts the video to that length; Keep whole uses all of it, start to end.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => done(false)}>
            <Film /> Keep whole video
          </Button>
          <Button onClick={() => done(true)}>
            <Scissors /> Trim
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  return { choose, dialog };
}
