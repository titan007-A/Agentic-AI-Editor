"use client";

import { Copy, FileCode, Plus, Save, Sparkles, Trash2, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { LearnStyleDialog } from "@/components/learn-style-dialog";
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
import { useMe } from "@/hooks/use-me";
import { api, errorText, type Account, type Preset } from "@/lib/api";
import { guardLeaving } from "@/lib/leave-guard.mjs";
import { blankPreset } from "@/lib/preset-defaults.mjs";

const NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const UNSAVED = "Your preset changes aren't saved. Leave and discard them?";

export default function PresetsPage() {
  return (
    <Suspense>
      <Presets />
    </Suspense>
  );
}

function Presets() {
  const me = useMe();
  // Whose presets are shown: the admin's own (null) or an employee's, by account id, as
  // opened from People with ?owner= in the address.
  const asked = useSearchParams().get("owner");
  const [owner, setOwner] = useState<string | null>(asked);
  const [people, setPeople] = useState<Account[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fonts, setFonts] = useState<string[]>([]);
  const [draft, setDraft] = useState<Preset | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  // Asking for a file name: for a copy of the preset shown, or for a new one.
  const [naming, setNaming] = useState<"copy" | "new" | null>(null);
  const [copyName, setCopyName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [yaml, setYaml] = useState<string | null>(null);
  const [learnOpen, setLearnOpen] = useState(false);
  const [learned, setLearned] = useState<string[]>([]);

  const load = useCallback(
    (select?: string) =>
      api.presets(owner).then((list) => {
        setPresets(list);
        setLoaded(true);
        setLoadError(null);
        setDraft(list.find((p) => p.name === select) ?? list[0] ?? null);
        setIsNew(false);
        setDirty(false);
        setLearned([]);
      }),
    [owner],
  );

  useEffect(() => {
    if (me?.role !== "admin") return;
    api
      .accounts()
      .then(setPeople)
      .catch(() => {});
  }, [me?.role]);

  function showOwner(next: string | null) {
    if (next === owner) return;
    if (dirty && !window.confirm(UNSAVED)) return;
    setDirty(false);
    setOwner(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("owner", next);
    else url.searchParams.delete("owner");
    window.history.replaceState(null, "", url);
  }
  const members = people.filter((p) => p.role === "member");
  const shownFor = members.find((p) => p.id === owner)?.username;

  const openDraft = useCallback(
    (preset: Preset, notes: string[]) => {
      if (
        dirty &&
        !window.confirm("A learned draft is ready. Discard your unsaved changes to open it?")
      )
        return false;
      setDraft(preset);
      setIsNew(true);
      setDirty(true);
      setLearned(notes);
      return true;
    },
    [dirty],
  );

  useEffect(() => {
    load().catch((err) => setLoadError(errorText(err)));
    api
      .fonts()
      .then(setFonts)
      .catch(() => {});
  }, [load]);

  // Unsaved edits ask before the page is left, closed or reloaded.
  useEffect(() => {
    if (!dirty) return;
    const release = guardLeaving(UNSAVED);
    const onUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => {
      release();
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [dirty]);

  function edit(next: Preset) {
    setDraft(next);
    setDirty(true);
  }

  function choose(name: string) {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setDraft(presets.find((p) => p.name === name) ?? null);
    setIsNew(false);
    setDirty(false);
    setLearned([]);
  }

  async function nameChosen() {
    const name = copyName.trim();
    if (!NAME_PATTERN.test(name)) {
      toast.error("Use lowercase letters, digits and dashes");
      return;
    }
    if (presets.some((p) => p.name === name)) {
      toast.error(`${name} already exists`);
      return;
    }
    if (naming === "copy" && draft) {
      setDraft({ ...structuredClone(draft), name, label: `${draft.label} (copy)` });
    } else {
      try {
        setDraft(blankPreset(await api.presetSchema(), name));
      } catch (err) {
        toast.error(errorText(err));
        return;
      }
      setLearned([]);
    }
    setIsNew(true);
    setDirty(true);
    setNaming(null);
    setCopyName("");
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    try {
      const saved = await api.savePreset(draft, owner);
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
      await api.deletePreset(draft.name, owner);
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
      setYaml((await api.presetYaml(draft.name, owner)).yaml);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (!loaded) {
    return loadError ? (
      <div className="space-y-3">
        <p className="text-sm whitespace-pre-line text-destructive">{loadError}</p>
        <Button
          variant="outline"
          onClick={() => load().catch((err) => setLoadError(errorText(err)))}
        >
          Try again
        </Button>
      </div>
    ) : (
      <p className="text-sm text-muted-foreground">Loading...</p>
    );
  }

  const learnDialog = (
    <LearnStyleDialog
      open={learnOpen}
      onOpenChange={setLearnOpen}
      taken={presets.map((p) => p.name)}
      onDraft={openDraft}
    />
  );

  const nameDialog = (
    <Dialog open={naming !== null} onOpenChange={(open) => !open && setNaming(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {naming === "copy" && draft ? `Duplicate ${draft.label}` : "New preset"}
          </DialogTitle>
          <DialogDescription>
            {naming === "copy"
              ? "The new preset starts as a copy."
              : "It starts with the default settings."}{" "}
            Nothing is saved until you press Save.
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
            onKeyDown={(e) => e.key === "Enter" && nameChosen()}
          />
        </div>
        <DialogFooter>
          <Button onClick={nameChosen}>{naming === "copy" ? "Duplicate" : "Create"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  const ownerPicker = me?.role === "admin" && members.length > 0 && (
    <div className="mb-4 space-y-2">
      <Label htmlFor="owner-picker">Whose presets</Label>
      <Select value={owner ?? "me"} onValueChange={(v) => showOwner(v === "me" ? null : v)}>
        <SelectTrigger id="owner-picker" className="w-full sm:max-w-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="me">Mine ({me.user})</SelectItem>
          {members.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.username}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {shownFor && (
        <p className="text-sm text-muted-foreground">
          Changes here go to {shownFor}&apos;s presets; yours stay as they are.
        </p>
      )}
    </div>
  );

  // The dialogs come first in both views, so switching views keeps their state.
  if (!draft) {
    return (
      <>
        {learnDialog}
        {nameDialog}
        {ownerPicker}
        <div className="mx-auto max-w-xl">
          <Card className="tape">
            <CardHeader>
              <CardTitle>No presets yet</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                A preset says how a reel looks and sounds. Learn one from a reel that already looks
                right, or start from the default settings.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setLearnOpen(true)}>
                  <Sparkles /> Learn from a reel
                </Button>
                <Button variant="outline" onClick={() => setNaming("new")}>
                  <Plus /> Start from scratch
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </>
    );
  }

  return (
    <>
      {learnDialog}
      {nameDialog}
      {ownerPicker}
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
            <Button variant="outline" onClick={() => setLearnOpen(true)}>
              <Sparkles /> Learn from a reel
            </Button>
            <Button variant="outline" onClick={() => setNaming("copy")}>
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
          <h1 className="font-hand text-3xl leading-tight font-bold break-words sm:text-4xl">
            <span className="marker">{draft.label}</span>
          </h1>
          <Badge variant="secondary" className="font-mono">
            {draft.name}
          </Badge>
          {isNew && <Badge>new</Badge>}
          {dirty && <Badge variant="outline">unsaved</Badge>}
        </div>

        {learned.length > 0 && (
          <Card className="tape">
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2">
                What the reel showed
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Hide these notes"
                  onClick={() => setLearned([])}
                >
                  <X />
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {learned.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                Check the settings below, then Save. Nothing is saved yet.
              </p>
            </CardContent>
          </Card>
        )}

        {SECTIONS.slice(0, 1).map((section) => (
          <Card key={section.title}>
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <PresetSection fields={section.fields} preset={draft} fonts={fonts} onChange={edit} />
            </CardContent>
          </Card>
        ))}

        <Card>
          <CardHeader>
            <CardTitle>On-screen text</CardTitle>
          </CardHeader>
          <CardContent>
            <TextBlocks preset={draft} fonts={fonts} onChange={edit} />
          </CardContent>
        </Card>

        {SECTIONS.slice(1).map((section) => (
          <Card key={section.title}>
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <PresetSection fields={section.fields} preset={draft} fonts={fonts} onChange={edit} />
            </CardContent>
          </Card>
        ))}

        <div className="hand-rule-top fixed inset-x-0 bottom-0 bg-background/90 backdrop-blur">
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
              {dirty && (
                <DialogDescription>
                  This is the saved file. Your unsaved changes aren&apos;t in it until you press
                  Save.
                </DialogDescription>
              )}
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
    </>
  );
}
