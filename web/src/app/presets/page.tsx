"use client";

import { Copy, FileCode, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { PresetSection, SECTIONS, TextBlocks } from "@/components/preset-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, errorText, type Preset } from "@/lib/api";

const NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

export default function PresetsPage() {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [fonts, setFonts] = useState<string[]>([]);
  const [draft, setDraft] = useState<Preset | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [copyName, setCopyName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [yaml, setYaml] = useState<string | null>(null);

  const load = useCallback(
    (select?: string) =>
      api.presets().then((list) => {
        setPresets(list);
        setDraft(list.find((p) => p.name === select) ?? list[0] ?? null);
        setIsNew(false);
        setDirty(false);
      }),
    [],
  );

  useEffect(() => {
    load().catch((err) => toast.error(errorText(err)));
    api
      .fonts()
      .then(setFonts)
      .catch(() => {});
  }, [load]);

  function edit(next: Preset) {
    setDraft(next);
    setDirty(true);
  }

  function choose(name: string) {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setDraft(presets.find((p) => p.name === name) ?? null);
    setIsNew(false);
    setDirty(false);
  }

  function duplicate() {
    if (!draft) return;
    const name = copyName.trim();
    if (!NAME_PATTERN.test(name)) {
      toast.error("Use lowercase letters, digits and dashes");
      return;
    }
    if (presets.some((p) => p.name === name)) {
      toast.error(`${name} already exists`);
      return;
    }
    setDraft({ ...structuredClone(draft), name, label: `${draft.label} (copy)` });
    setIsNew(true);
    setDirty(true);
    setCopyOpen(false);
    setCopyName("");
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    try {
      const saved = await api.savePreset(draft);
      await load(saved.name);
      toast.success(`Saved ${saved.label}`);
    } catch (err) {
      toast.error(errorText(err), { duration: 10000 });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!draft) return;
    setBusy(true);
    try {
      await api.deletePreset(draft.name);
      setDeleteOpen(false);
      await load();
      toast.success(`Deleted ${draft.label}`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function showYaml() {
    if (!draft) return;
    try {
      setYaml((await api.presetYaml(draft.name)).yaml);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (!draft) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <div className="space-y-6 pb-20">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1 space-y-2">
          <Label htmlFor="preset-picker">Preset</Label>
          <Select value={isNew ? "" : draft.name} onValueChange={choose}>
            <SelectTrigger id="preset-picker" className="w-full sm:max-w-sm">
              <SelectValue placeholder={draft.name} />
            </SelectTrigger>
            <SelectContent>
              {presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setCopyOpen(true)}>
            <Copy /> Duplicate
          </Button>
          {!isNew && (
            <>
              <Button variant="outline" onClick={showYaml}>
                <FileCode /> YAML
              </Button>
              <Button variant="outline" onClick={() => setDeleteOpen(true)}>
                <Trash2 /> Delete
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{draft.label}</h1>
        <Badge variant="secondary" className="font-mono">
          {draft.name}
        </Badge>
        {isNew && <Badge>new</Badge>}
        {dirty && <Badge variant="outline">unsaved</Badge>}
      </div>

      {SECTIONS.slice(0, 1).map((section) => (
        <Card key={section.title}>
          <CardHeader>
            <CardTitle className="text-base">{section.title}</CardTitle>
          </CardHeader>
          <CardContent>
            <PresetSection fields={section.fields} preset={draft} fonts={fonts} onChange={edit} />
          </CardContent>
        </Card>
      ))}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">On-screen text</CardTitle>
        </CardHeader>
        <CardContent>
          <TextBlocks preset={draft} fonts={fonts} onChange={edit} />
        </CardContent>
      </Card>

      {SECTIONS.slice(1).map((section) => (
        <Card key={section.title}>
          <CardHeader>
            <CardTitle className="text-base">{section.title}</CardTitle>
          </CardHeader>
          <CardContent>
            <PresetSection fields={section.fields} preset={draft} fonts={fonts} onChange={edit} />
          </CardContent>
        </Card>
      ))}

      <div className="fixed inset-x-0 bottom-0 border-t bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-end gap-2 px-4 py-3">
          {dirty && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => load(isNew ? undefined : draft.name)}
            >
              Discard
            </Button>
          )}
          <Button disabled={busy || !dirty} onClick={save}>
            <Save /> Save
          </Button>
        </div>
      </div>

      <Dialog open={copyOpen} onOpenChange={setCopyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Duplicate {draft.label}</DialogTitle>
            <DialogDescription>
              The new preset starts as a copy. Nothing is saved until you press Save.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="copy-name">File name</Label>
            <Input
              id="copy-name"
              className="font-mono"
              placeholder="vocal-music-fr-9x16"
              value={copyName}
              onChange={(e) => setCopyName(e.target.value.toLowerCase())}
              onKeyDown={(e) => e.key === "Enter" && duplicate()}
            />
          </div>
          <DialogFooter>
            <Button onClick={duplicate}>Duplicate</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {draft.label}?</DialogTitle>
            <DialogDescription>
              This removes presets/{draft.name}.yaml. Finished videos are kept.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="destructive" disabled={busy} onClick={remove}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={yaml !== null} onOpenChange={(open) => !open && setYaml(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{draft.name}.yaml</DialogTitle>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-3 text-xs">{yaml}</pre>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                navigator.clipboard?.writeText(yaml ?? "");
                toast.success("Copied");
              }}
            >
              <Copy /> Copy
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
