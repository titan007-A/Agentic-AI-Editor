"use client";

import { Loader2, Upload } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { UploadList } from "@/components/upload-list";
import { usePolling } from "@/hooks/use-polling";
import { useUploads } from "@/hooks/use-uploads";
import { api, ApiError, errorText, type Preset, type StyleDraft } from "@/lib/api";
import { recall, remember } from "@/lib/session-value.mjs";

const NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const POLL_MS = 2000;
// The draft being learned, so leaving the page or reloading doesn't lose it.
const DRAFT_KEY = "learning-style-draft";

/** Learns a style from a reel and hands back the drafted preset. Waiting carries on
 * after the dialog is closed, and after coming back to the page, so the draft still
 * opens when it is ready. `onDraft` says whether the draft was opened. */
export function LearnStyleDialog({
  open,
  onOpenChange,
  taken,
  onDraft,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taken: string[];
  onDraft: (preset: Preset, notes: string[]) => boolean;
}) {
  const [link, setLink] = useState("");
  const uploads = useUploads();
  const upload = uploads.items.at(-1) ?? null;
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<StyleDraft | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const waiting = draft?.status === "queued" || draft?.status === "running";
  const kept = draft?.status === "done" && draft.preset ? draft.preset : null;
  const source = upload ? (upload.source ?? "") : link.trim();
  const nameProblem =
    name && !NAME_PATTERN.test(name)
      ? "Use lowercase letters, digits and dashes"
      : taken.includes(name)
        ? `${name} already exists`
        : null;

  function forget() {
    remember(DRAFT_KEY, null);
    setDraft(null);
  }

  function openKept(latest: StyleDraft) {
    if (!latest.preset || !onDraft(latest.preset, latest.notes)) return false;
    forget();
    onOpenChange(false);
    setLink("");
    uploads.clear();
    setName("");
    return true;
  }

  function arrived(latest: StyleDraft) {
    setDraft(latest);
    if (latest.status === "done" && latest.preset) {
      toast.success(`Draft ready: ${latest.preset.label}`);
      if (!openKept(latest)) {
        toast("The learned draft is kept", {
          description: "Open it from Learn from a reel when you're ready.",
          action: { label: "Show", onClick: () => onOpenChange(true) },
        });
      }
    } else if (latest.status === "failed") {
      remember(DRAFT_KEY, null);
      toast.error(`Couldn't learn from the reel: ${latest.error}`, { duration: 10000 });
    }
  }

  const draftId = draft?.id;
  usePolling(
    async () => {
      if (draftId) arrived(await api.styleDraft(draftId));
    },
    draftId && waiting ? POLL_MS : null,
  );

  // A draft started before leaving the page picks up where it was.
  const resume = useEffectEvent((latest: StyleDraft) => arrived(latest));
  useEffect(() => {
    const id = recall(DRAFT_KEY);
    if (!id) return;
    api
      .styleDraft(id)
      .then(resume)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) remember(DRAFT_KEY, null);
      });
  }, []);

  function addFile(files: FileList | null) {
    const file = files?.[0];
    if (file) {
      // One reel at a time: a new file replaces the one before.
      uploads.clear();
      uploads.add([file]);
      setLink("");
    }
    if (fileInput.current) fileInput.current.value = "";
  }

  async function start() {
    setBusy(true);
    try {
      const started = await api.learnStyle(source, name.trim() || null);
      remember(DRAFT_KEY, started.id);
      setDraft(started);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Learn a style from a reel</DialogTitle>
          <DialogDescription>
            Give a reel that already looks the way you want. Its layout, text, captions, look and
            sound become a new preset for you to check. Nothing is saved until you press Save.
          </DialogDescription>
        </DialogHeader>

        {kept && draft ? (
          <div className="space-y-2 py-2 text-sm" role="status">
            <p>
              The draft <span className="font-medium">{kept.label}</span> is ready and not opened
              yet.
            </p>
            <p className="text-xs break-all text-muted-foreground">From {draft.source}</p>
          </div>
        ) : waiting ? (
          <div className="flex items-center gap-3 py-4 text-sm" role="status">
            <Loader2 className="size-5 shrink-0 animate-spin" />
            <span>
              {draft?.message}
              <span className="block text-xs text-muted-foreground">
                This takes a minute or two. You can close this; the draft opens when it is ready.
              </span>
            </span>
          </div>
        ) : (
          <div className="space-y-4">
            {draft?.status === "failed" && (
              <p className="text-sm text-destructive">Couldn&apos;t learn from it: {draft.error}</p>
            )}
            <div className="space-y-2">
              <Label htmlFor="learn-link">Link to the reel</Label>
              <Input
                id="learn-link"
                placeholder="https://..."
                value={link}
                disabled={!!upload}
                onChange={(e) => setLink(e.target.value)}
              />
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileInput}
                  type="file"
                  accept="video/*"
                  hidden
                  onChange={(e) => addFile(e.target.files)}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload /> Or upload the video
                </Button>
                <UploadList items={uploads.items} onRemove={uploads.remove} />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="learn-name">File name (optional)</Label>
              <Input
                id="learn-name"
                className="font-mono"
                placeholder="named after the style"
                value={name}
                onChange={(e) => setName(e.target.value.toLowerCase())}
              />
              {nameProblem && <p className="text-xs text-destructive">{nameProblem}</p>}
            </div>
          </div>
        )}

        {kept && draft ? (
          <DialogFooter>
            <Button variant="ghost" onClick={forget}>
              Discard it
            </Button>
            <Button onClick={() => openKept(draft)}>Open the draft</Button>
          </DialogFooter>
        ) : (
          !waiting && (
            <DialogFooter>
              <Button disabled={busy || !source || !!nameProblem} onClick={start}>
                Learn the style
              </Button>
            </DialogFooter>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
