"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { counts, fieldLabel, nextValues } from "@/lib/preset-fields.mjs";

const KEY = "preset-fields";

/** The words the chosen presets need given ({day}, {song_number}, {year}), as last typed on
 * this device: the same day and the song after the last one come back next time. */
function kept(): Record<string, string> {
  try {
    const values = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return values && typeof values === "object" ? values : {};
  } catch {
    return {}; // storage blocked or unreadable (or the page being built): start empty
  }
}

export function useFieldValues() {
  // Read once, on the first render: the inputs only show once the presets have loaded.
  const [values, setValues] = useState<Record<string, string>>(kept);

  /** After jobs were made: keep what comes next (see nextValues). */
  function keep(used: Record<string, number>) {
    const next = nextValues(values, used);
    setValues(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Not kept; typed again next time.
    }
  }

  return { values, setValues, keep };
}

export const filled = (fields: string[], values: Record<string, string>) =>
  fields.every((name) => (values[name] ?? "").trim());

/** An input for each word the chosen presets write that only the person knows, so a job
 * doesn't fail later for want of it. */
export function PresetFieldInputs({
  fields,
  values,
  onChange,
  videos,
  idPrefix,
}: {
  fields: string[];
  values: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  /** How many videos the jobs are for: a number counts up across them. */
  videos: number;
  idPrefix: string;
}) {
  if (fields.length === 0) return null;
  return (
    <fieldset className="space-y-2 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">The preset writes these on the reel</legend>
      <div className="grid grid-cols-2 gap-3">
        {fields.map((name) => {
          const value = values[name] ?? "";
          const start = /^\d+$/.test(value.trim()) ? Number(value.trim()) : null;
          return (
            <div key={name} className="space-y-1">
              <Label htmlFor={`${idPrefix}-${name}`}>{fieldLabel(name)}</Label>
              <Input
                id={`${idPrefix}-${name}`}
                required
                inputMode={counts(name) || name === "year" ? "numeric" : undefined}
                maxLength={60}
                value={value}
                onChange={(e) => onChange({ ...values, [name]: e.target.value })}
              />
              {counts(name) && videos > 1 && start !== null && (
                <p className="text-xs text-muted-foreground">
                  {start} to {start + videos - 1} for the {videos} videos
                </p>
              )}
              {name === "year" && (
                <p className="text-xs text-muted-foreground">The year of the song</p>
              )}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
