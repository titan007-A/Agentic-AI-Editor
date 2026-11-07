"use client";

import { CalendarClock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import { api, errorText, type BufferMode, type BufferState, type Output } from "@/lib/api";

const MODES: { value: BufferMode; label: string }[] = [
  { value: "addToQueue", label: "Add to the queue" },
  { value: "shareNext", label: "Next in the queue" },
  { value: "shareNow", label: "Post now" },
  { value: "customScheduled", label: "At a time I pick" },
];

/** Sends a finished reel to Buffer, with the signed-in account's own Buffer key. */
export function BufferButton({ output, title }: { output: Output; title: string }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<BufferState | null>(null);
  const [key, setKey] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [comment, setComment] = useState("");
  const [mode, setMode] = useState<BufferMode>("addToQueue");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);

  if (!me) return null;

  async function show() {
    setOpen(true);
    setText(output.caption || title);
    setState(null);
    try {
      const next = await api.buffer();
      setState(next);
      setChosen(next.channels.filter((c) => c.service === "instagram").map((c) => c.id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function connect() {
    setBusy(true);
    try {
      const next = await api.connectBuffer(key.trim());
      setState(next);
      setKey("");
      setChosen(next.channels.filter((c) => c.service === "instagram").map((c) => c.id));
      toast.success(`Buffer connected: ${next.channels.length} channel(s)`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    try {
      const due = mode === "customScheduled" && when ? new Date(when).toISOString() : null;
      const { results } = await api.postToBuffer(output.id, {
        channels: chosen,
        text,
        mode,
        due_at: due,
        first_comment: comment,
      });
      const failed = results.filter((r) => !r.ok);
      for (const r of results.filter((r) => r.ok)) toast.success(`Sent to Buffer: ${r.channel}`);
      for (const r of failed) toast.error(`${r.channel}: ${r.error}`);
      if (!failed.length) setOpen(false);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const instagram = state?.channels.some((c) => chosen.includes(c.id) && c.service === "instagram");
  const ready = chosen.length > 0 && (mode !== "customScheduled" || when !== "");

  return (
    <>
      <Button variant="outline" onClick={show}>
        <CalendarClock /> Buffer
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Post to Buffer</DialogTitle>
            <DialogDescription>
              Buffer fetches the video from this editor&apos;s public link when it posts, so keep
              the Mac on until then.
            </DialogDescription>
          </DialogHeader>
          {state === null && <p className="text-sm text-muted-foreground">Asking Buffer...</p>}
          {state && !state.connected && (
            <div className="space-y-2">
              <Label htmlFor={`buffer-key-${output.id}`}>Buffer API key</Label>
              <Input
                id={`buffer-key-${output.id}`}
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Make one in Buffer at{" "}
                <a
                  className="underline"
                  href="https://publish.buffer.com/settings/api"
                  target="_blank"
                  rel="noreferrer"
                >
                  Settings &gt; API
                </a>{" "}
                (the Buffer account&apos;s owner can) and paste it here. It is kept for your account
                only.
              </p>
              <Button disabled={busy || key.trim().length < 8} onClick={connect}>
                Connect
              </Button>
            </div>
          )}
          {state?.connected && (
            <div className="space-y-4">
              {state.error && <p className="text-sm text-destructive">{state.error}</p>}
              <fieldset className="space-y-2">
                <legend className="mb-1 text-sm font-medium">Channels</legend>
                {state.channels.map((c) => (
                  <label
                    key={c.id}
                    className="sketch-tile flex min-h-11 cursor-pointer items-center gap-3 p-2"
                  >
                    <Checkbox
                      checked={chosen.includes(c.id)}
                      onCheckedChange={(on) =>
                        setChosen((now) =>
                          on === true ? [...now, c.id] : now.filter((id) => id !== c.id),
                        )
                      }
                    />
                    {c.avatar && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={c.avatar} alt="" className="size-7 rounded-full" />
                    )}
                    <span className="text-sm">
                      {c.name} <span className="text-muted-foreground">({c.service})</span>
                      {c.paused && <span className="text-muted-foreground"> · queue paused</span>}
                    </span>
                  </label>
                ))}
              </fieldset>
              <div className="space-y-1">
                <Label htmlFor={`buffer-text-${output.id}`}>Caption</Label>
                <Textarea
                  id={`buffer-text-${output.id}`}
                  rows={5}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </div>
              {instagram && (
                <div className="space-y-1">
                  <Label htmlFor={`buffer-comment-${output.id}`}>First comment (Instagram)</Label>
                  <Input
                    id={`buffer-comment-${output.id}`}
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                  />
                </div>
              )}
              <div className="space-y-1">
                <Label htmlFor={`buffer-mode-${output.id}`}>When</Label>
                <Select value={mode} onValueChange={(v) => setMode(v as BufferMode)}>
                  <SelectTrigger id={`buffer-mode-${output.id}`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODES.map((m) => (
                      <SelectItem key={m.value} value={m.value}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {mode === "customScheduled" && (
                <div className="space-y-1">
                  <Label htmlFor={`buffer-when-${output.id}`}>Date and time (your time)</Label>
                  <Input
                    id={`buffer-when-${output.id}`}
                    type="datetime-local"
                    value={when}
                    onChange={(e) => setWhen(e.target.value)}
                  />
                </div>
              )}
              <button
                type="button"
                className="text-xs text-muted-foreground underline"
                onClick={async () => {
                  await api.disconnectBuffer().catch(() => {});
                  setState({ connected: false, channels: [] });
                }}
              >
                Disconnect Buffer
              </button>
            </div>
          )}
          {state?.connected && (
            <DialogFooter>
              <Button disabled={busy || !ready} onClick={send}>
                {busy ? "Sending..." : `Send to ${chosen.length} channel(s)`}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
