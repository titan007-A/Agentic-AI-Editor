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
import { api, errorText, type Feed, type FeedFields, type Preset } from "@/lib/api";

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

export const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

export function FeedForm({ feed, onSaved }: { feed?: Feed; onSaved: (feed: Feed) => void }) {
  const [fields, setFields] = useState<FeedFields>(feed ?? BLANK);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [busy, setBusy] = useState(false);
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
      const body = { ...fields, notes: fields.notes?.trim() || null };
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
              <SelectItem value="folder">A folder</SelectItem>
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
            <span>From</span>
            <HourPicker value={fields.active_from ?? 23} onChange={(h) => set("active_from", h)} />
            <span>to</span>
            <HourPicker value={fields.active_to ?? 7} onChange={(h) => set("active_to", h)} />
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
          <span>Videos in progress at once</span>
          <Input
            type="number"
            min={1}
            max={20}
            className="w-20"
            value={fields.batch}
            onChange={(e) => set("batch", Number(e.target.value) || 1)}
          />
        </label>
      </div>

      <Button
        type="submit"
        disabled={busy || !fields.name.trim() || !fields.location.trim() || !fields.presets.length}
      >
        {busy ? "Checking..." : feed ? "Save feed" : "Add feed"}
      </Button>
    </form>
  );
}

function HourPicker({ value, onChange }: { value: number; onChange: (hour: number) => void }) {
  return (
    <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger className="w-24">
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
