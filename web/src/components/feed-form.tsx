"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import { api, errorText, type Feed, type FeedFields, type Preset } from "@/lib/api";
import { wholeNumber } from "@/lib/form-values.mjs";

const BLANK: FeedFields = {
  name: "",
  kind: "folder",
  location: "",
  presets: [],
  notes: null,
  enabled: true,
  active_from: null,
  active_to: null,
  batch: 3,
  include_existing: false,
};

const HOURS = Array.from({ length: 24 }, (_, h) => h);
// The server's limits on videos in progress at once per feed.
const MIN_BATCH = 1;
const MAX_BATCH = 20;

export const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

export function FeedForm({ feed, onSaved }: { feed?: Feed; onSaved: (feed: Feed) => void }) {
  // A folder is on the server's own disk, which only the admin may watch.
  const me = useMe();
  const folders = me?.role === "admin";
  const [chosen, setFields] = useState<FeedFields>(feed ?? BLANK);
  const fields: FeedFields =
    me && !folders && chosen.kind === "folder" && !feed ? { ...chosen, kind: "link" } : chosen;
  const [presets, setPresets] = useState<Preset[]>([]);
  const [busy, setBusy] = useState(false);
  // Kept as typed, so the field can be cleared and retyped.
  const [batchText, setBatchText] = useState(String(fields.batch));
  const batch = wholeNumber(batchText, MIN_BATCH, MAX_BATCH);
  const timed = fields.active_from !== null;

  useEffect(() => {
    api
      .presets()
      .then(setPresets)
      .catch(() => {});
  }, []);

  function set<K extends keyof FeedFields>(key: K, value: FeedFields[K]) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  function togglePreset(name: string, on: boolean) {
    set("presets", on ? [...fields.presets, name] : fields.presets.filter((n) => n !== name));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (batch === null) return;
      const body = { ...fields, batch, notes: fields.notes?.trim() || null };
      const saved = feed ? await api.updateFeed(feed.id, body) : await api.createFeed(body);
      toast.success(feed ? "Feed saved" : `Feed added: ${saved.last_result ?? "checked"}`);
      onSaved(saved);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="feed-name">Name</Label>
        <Input
          id="feed-name"
          value={fields.name}
          placeholder="e.g. Concert uploads"
          onChange={(e) => set("name", e.target.value)}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[12rem_1fr]">
        <div className="space-y-2">
          <Label htmlFor="feed-kind">Watch</Label>
          <Select value={fields.kind} onValueChange={(v) => set("kind", v as FeedFields["kind"])}>
            <SelectTrigger id="feed-kind" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {folders && <SelectItem value="folder">A folder</SelectItem>}
              <SelectItem value="link">A playlist or channel</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="feed-location">{fields.kind === "folder" ? "Folder" : "Link"}</Label>
          <Input
            id="feed-location"
            value={fields.location}
            placeholder={
              fields.kind === "folder"
                ? "/home/you/Videos/to-edit"
                : "https://www.youtube.com/@channel or a playlist link"
            }
            onChange={(e) => set("location", e.target.value)}
          />
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Presets</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {presets.map((p) => (
            <label
              key={p.name}
              className="sketch-tile flex cursor-pointer items-start gap-3 p-3 hover:bg-[#fdf3dc]"
            >
              <Checkbox
                checked={fields.presets.includes(p.name)}
                onCheckedChange={(on) => togglePreset(p.name, on === true)}
              />
              <span className="text-sm font-medium">{p.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="space-y-2">
        <Label htmlFor="feed-notes">Instruction for every video (optional)</Label>
        <Textarea
          id="feed-notes"
          rows={2}
          placeholder="e.g. Spanish subtitles, under 40 seconds"
          value={fields.notes ?? ""}
          onChange={(e) => set("notes", e.target.value)}
        />
      </div>

      <div className="space-y-3 rounded-md border border-dashed p-3">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Only start videos at certain hours</span>
          <Switch
            checked={timed}
            onCheckedChange={(on) =>
              setFields((c) => ({ ...c, active_from: on ? 23 : null, active_to: on ? 7 : null }))
            }
          />
        </label>
        {timed && (
          <div className="flex items-center gap-2 text-sm">
            <span aria-hidden>From</span>
            <HourPicker
              label="Start at"
              value={fields.active_from ?? 23}
              onChange={(h) => set("active_from", h)}
            />
            <span aria-hidden>to</span>
            <HourPicker
              label="Stop at"
              value={fields.active_to ?? 7}
              onChange={(h) => set("active_to", h)}
            />
          </div>
        )}
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Also edit the videos already there</span>
          <Switch
            checked={fields.include_existing}
            disabled={!!feed}
            onCheckedChange={(on) => set("include_existing", on)}
          />
        </label>
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>
            Videos in progress at once
            {batch === null && (
              <span className="block text-xs text-destructive">
                A whole number from {MIN_BATCH} to {MAX_BATCH}
              </span>
            )}
          </span>
          <Input
            type="number"
            inputMode="numeric"
            min={MIN_BATCH}
            max={MAX_BATCH}
            className="w-20"
            aria-invalid={batch === null}
            value={batchText}
            onChange={(e) => setBatchText(e.target.value)}
          />
        </label>
      </div>

      <Button
        type="submit"
        disabled={
          busy ||
          batch === null ||
          !fields.name.trim() ||
          !fields.location.trim() ||
          !fields.presets.length
        }
      >
        {busy ? "Checking..." : feed ? "Save feed" : "Add feed"}
      </Button>
    </form>
  );
}

function HourPicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (hour: number) => void;
}) {
  return (
    <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger className="w-24" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {HOURS.map((h) => (
          <SelectItem key={h} value={String(h)}>
            {hourLabel(h)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
