"use client";

import { CalendarClock } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ChannelPicker, channelsOf, channelWarning } from "@/components/buffer-channels";
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
import { api, errorText, type BufferMode, type Output, type OutputBuffer } from "@/lib/api";

const MODES: { value: BufferMode; label: string }[] = [
  { value: "addToQueue", label: "Add to the queue" },
  { value: "shareNext", label: "Next in the queue" },
  { value: "shareNow", label: "Post now" },
  { value: "customScheduled", label: "At a time I pick" },
];

const same = (a: string[], b: string[]) => a.length === b.length && a.every((id) => b.includes(id));

/** Sends a finished reel to Buffer, to the accounts its preset is linked to on the Buffer
 * page (the reel owner's keys and links, also when an admin posts it for them). */
export function BufferButton({ output, title }: { output: Output; title: string }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<OutputBuffer | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [others, setOthers] = useState(false);
  const [remember, setRemember] = useState(false);
  const [text, setText] = useState("");
  const [comment, setComment] = useState("");
  const [mode, setMode] = useState<BufferMode>("addToQueue");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);

  if (!me) return null;

  async function show() {
    setOpen(true);
    setText(output.caption || title);
    setData(null);
    try {
      const next = await api.outputBuffer(output.id);
      const reachable = new Set(channelsOf(next.keys).map(({ channel }) => channel.id));
      const linked = next.linked.filter((id) => reachable.has(id));
      setData(next);
      setChosen(linked);
      setOthers(linked.length === 0);
      // Someone else's preset links are theirs to change, on their Buffer page.
      setRemember(next.linked.length === 0 && !next.owner);
    } catch (err) {
      toast.error(errorText(err));
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
        remember,
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

  const all = data ? channelsOf(data.keys).map(({ channel }) => channel) : [];
  const reachable = new Set(all.map((c) => c.id));
  const linked = data ? data.linked.filter((id) => reachable.has(id)) : [];
  const gone = data ? data.linked.length - linked.length : 0;
  const picked = all.filter((c) => chosen.includes(c.id));
  const instagram = picked.some((c) => c.service === "instagram");
  const warned = picked.filter((c) => c.broken || c.personal);
  const ready = chosen.length > 0 && (mode !== "customScheduled" || when !== "");
  const whose = data?.owner ? `${data.owner}'s` : "your";

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
          {data === null && <p className="text-sm text-muted-foreground">Loading...</p>}
          {data && data.keys.length === 0 && (
            <p className="text-sm">
              {data.owner ? (
                `${data.owner} hasn't added a Buffer key yet.`
              ) : (
                <>
                  No Buffer keys yet: add one on the{" "}
                  <Link className="underline" href="/buffer/">
                    Buffer page
                  </Link>
                  .
                </>
              )}
            </p>
          )}
          {data && data.keys.length > 0 && (
            <div className="space-y-4">
              {data.owner && (
                <p className="text-sm text-muted-foreground">
                  {data.owner}&apos;s reel: it goes out with {data.owner}&apos;s Buffer keys.
                </p>
              )}
              <div className="space-y-2">
                <p className="text-sm font-medium">
                  {linked.length
                    ? `Accounts for ${data.preset_label}`
                    : `${data.preset_label} isn't linked to any accounts yet`}
                </p>
                {gone > 0 && (
                  <p className="text-sm text-destructive">
                    {gone} of the preset&apos;s accounts {gone === 1 ? "is" : "are"} in none of{" "}
                    {whose} keys any more: refresh the keys on the Buffer page.
                  </p>
                )}
                {linked.length > 0 && (
                  <ChannelPicker
                    keys={data.keys}
                    chosen={chosen}
                    onChange={setChosen}
                    only={(id) => linked.includes(id)}
                    idPrefix={`buffer-${output.id}-linked`}
                  />
                )}
                {linked.length > 0 && all.length > linked.length && (
                  <button
                    type="button"
                    className="min-h-11 text-sm text-muted-foreground underline sm:min-h-0"
                    onClick={() => setOthers((now) => !now)}
                  >
                    {others ? "Hide other accounts" : "Other accounts"}
                  </button>
                )}
                {others && (
                  <ChannelPicker
                    keys={data.keys}
                    chosen={chosen}
                    onChange={setChosen}
                    only={(id) => !linked.includes(id)}
                    idPrefix={`buffer-${output.id}-other`}
                  />
                )}
              </div>
              {warned.map((c) => (
                <p key={c.id} className="text-sm text-destructive">
                  {c.name}: {channelWarning(c)}
                </p>
              ))}
              {chosen.length > 0 && !same(chosen, linked) && !data.owner && (
                <label
                  htmlFor={`buffer-remember-${output.id}`}
                  className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"
                >
                  <Checkbox
                    id={`buffer-remember-${output.id}`}
                    checked={remember}
                    onCheckedChange={(on) => setRemember(on === true)}
                  />
                  Use these accounts for {data.preset_label} from now on
                </label>
              )}
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
            </div>
          )}
          {data && data.keys.length > 0 && (
            <DialogFooter>
              <Button disabled={busy || !ready} onClick={send}>
                {busy ? "Sending..." : `Send to ${chosen.length} account(s)`}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
