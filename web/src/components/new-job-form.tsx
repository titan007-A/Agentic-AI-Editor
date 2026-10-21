"use client";

import { Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, type Job, type Preset } from "@/lib/api";

type Upload = { source: string; name: string };

export function NewJobForm({ onCreated }: { onCreated: (jobs: Job[]) => void }) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [links, setLinks] = useState("");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .presets()
      .then(setPresets)
      .catch(() => {});
  }, []);

  const sources = [
    ...links
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean),
    ...uploads.map((u) => u.source),
  ];

  function toggle(name: string, on: boolean) {
    setChosen((current) => (on ? [...current, name] : current.filter((n) => n !== name)));
  }

  async function addFiles(files: FileList | null) {
    if (!files) return;
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        const uploaded = await api.upload(file);
        setUploads((current) => [...current, uploaded]);
      }
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const jobs = await api.createJobs(sources, chosen, notes);
      toast.success(`${jobs.length} job${jobs.length === 1 ? "" : "s"} queued`);
      setLinks("");
      setUploads([]);
      setNotes("");
      onCreated(jobs);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="tape">
      <CardHeader>
        <CardTitle>New edit</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="links">Video links, one per line</Label>
            <Textarea
              id="links"
              rows={3}
              placeholder="https://..."
              value={links}
              onChange={(e) => setLinks(e.target.value)}
            />
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileInput}
                type="file"
                accept="video/*"
                multiple
                hidden
                onChange={(e) => addFiles(e.target.files)}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
              >
                <Upload /> Upload files
              </Button>
              {uploads.map((u) => (
                <span
                  key={u.source}
                  className="flex items-center gap-1 rounded-md bg-secondary px-2 py-1 text-xs"
                >
                  {u.name}
                  <button
                    type="button"
                    aria-label={`Remove ${u.name}`}
                    onClick={() => setUploads((c) => c.filter((x) => x.source !== u.source))}
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
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
                    checked={chosen.includes(p.name)}
                    onCheckedChange={(on) => toggle(p.name, on === true)}
                  />
                  <span className="space-y-0.5">
                    <span className="block text-sm font-medium">{p.label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {p.resolution} · {p.min_duration}-{p.max_duration}s
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="space-y-2">
            <Label htmlFor="notes">Notes for the editor (optional)</Label>
            <Textarea
              id="notes"
              rows={2}
              placeholder="e.g. start on the chorus, keep it under 40 seconds"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <Button
            type="submit"
            className="w-full sm:w-auto"
            disabled={busy || sources.length === 0 || chosen.length === 0}
          >
            Start {sources.length > 1 ? `${sources.length} jobs` : "job"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
