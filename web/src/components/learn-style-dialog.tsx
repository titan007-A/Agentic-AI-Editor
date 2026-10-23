"use client";

import { Loader2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
import { api, errorText, type Preset, type StyleDraft } from "@/lib/api";

const NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const POLL_MS = 2000;

/** Learns a style from a reel and hands back the drafted preset. Waiting carries on
 * after the dialog is closed, so the draft still opens when it is ready. */
export function LearnStyleDialog({
  open,
  onOpenChange,
  taken,
  onDraft,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taken: string[];
  onDraft: (preset: Preset, notes: string[]) => void;
}) {
  const [link, setLink] = useState("");
  const [upload, setUpload] = useState<{ source: string; name: string } | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<StyleDraft | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const waiting = draft?.status === "queued" || draft?.status === "running";
  const source = upload?.source ?? link.trim();
  const nameProblem =
    name && !NAME_PATTERN.test(name)
      ? "Use lowercase letters, digits and dashes"
      : taken.includes(name)
        ? `${name} already exists`
        : null;

  const draftId = draft?.id;
  useEffect(() => {
    if (!draftId || !waiting) return;
    const timer = setInterval(async () => {
      try {
        const latest = await api.styleDraft(draftId);
        setDraft(latest);
        if (latest.status === "done" && latest.preset) {
          toast.success(`Draft ready: ${latest.preset.label}`);
          onDraft(latest.preset, latest.notes);
          onOpenChange(false);
          setDraft(null);
          setLink("");
          setUpload(null);
          setName("");
        }
      } catch {
        // The next poll tries again.
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [draftId, waiting, onDraft, onOpenChange]);

  async function addFile(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      setUpload(await api.upload(file));
      setLink("");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function start() {
    setBusy(true);
    try {
      setDraft(await api.learnStyle(source, name.trim() || null));
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

        {waiting ? (
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
                {upload && (
                  <span className="flex items-center gap-1 rounded-md bg-secondary px-2 py-1 text-xs">
                    {upload.name}
                    <button
                      type="button"
                      aria-label={`Remove ${upload.name}`}
                      onClick={() => setUpload(null)}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                )}
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

        {!waiting && (
          <DialogFooter>
            <Button disabled={busy || !source || !!nameProblem} onClick={start}>
              Learn the style
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
