"use client";

import { KeyRound, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ChannelLine, ChannelPicker, channelsOf } from "@/components/buffer-channels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type BufferKey, type BufferPresetLink, type BufferState } from "@/lib/api";

export default function BufferPage() {
  const [state, setState] = useState<BufferState | null>(null);
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .buffer()
      .then(setState)
      .catch((err) => toast.error(errorText(err)));
  }, []);

  async function run(work: () => Promise<BufferState>, done?: string) {
    setBusy(true);
    try {
      setState(await work());
      if (done) toast.success(done);
      return true;
    } catch (err) {
      toast.error(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    const added = await run(() => api.addBufferKey(label.trim(), key.trim()), "Key added");
    if (added) {
      setLabel("");
      setKey("");
    }
  }

  if (state === null) return <p className="text-sm text-muted-foreground">Loading...</p>;
  const reachable = new Set(channelsOf(state.keys).map(({ channel }) => channel.id));

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="font-hand text-4xl font-bold">Buffer</h1>
        <p className="text-sm text-muted-foreground">
          Add your Buffer API keys, then choose which accounts each preset posts to. The Buffer
          button on a finished reel then shows that preset&apos;s accounts. Your keys are yours
          alone: nobody else sees or uses them.
        </p>
      </div>

      <Card className="tape">
        <CardHeader>
          <CardTitle>Add a key</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={add} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="key-label">Name</Label>
                <Input
                  id="key-label"
                  placeholder="Spanish pages"
                  maxLength={80}
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="key-value">API key</Label>
                <Input
                  id="key-value"
                  type="password"
                  autoComplete="off"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </div>
            </div>
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
              while signed in to that Buffer account. One key reaches every account in it.
            </p>
            <Button type="submit" disabled={busy || key.trim().length < 8}>
              <KeyRound /> {busy ? "Asking Buffer..." : "Add key"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="font-hand text-2xl font-bold">Your keys</h2>
        {state.keys.length === 0 && <p className="text-sm text-muted-foreground">No keys yet.</p>}
        {state.keys.map((k) => (
          <KeyCard key={k.id} item={k} busy={busy} run={run} />
        ))}
      </section>

      <section className="space-y-3">
        <h2 className="font-hand text-2xl font-bold">Presets</h2>
        {state.keys.length === 0 ? (
          <p className="text-sm text-muted-foreground">Add a key first.</p>
        ) : (
          state.presets.map((p) => (
            <PresetRow
              key={p.name}
              preset={p}
              keys={state.keys}
              reachable={reachable}
              busy={busy}
              run={run}
            />
          ))
        )}
      </section>
    </div>
  );
}

type Run = (work: () => Promise<BufferState>, done?: string) => Promise<boolean>;

function KeyCard({ item, busy, run }: { item: BufferKey; busy: boolean; run: Run }) {
  const [renaming, setRenaming] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        {renaming === null ? (
          <CardTitle className="min-w-0 break-words">{item.label}</CardTitle>
        ) : (
          <form
            className="flex min-w-0 flex-1 gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              if (await run(() => api.renameBufferKey(item.id, renaming))) setRenaming(null);
            }}
          >
            <Input
              aria-label="Key name"
              maxLength={80}
              value={renaming}
              onChange={(e) => setRenaming(e.target.value)}
              autoFocus
            />
            <Button type="submit" disabled={busy || !renaming.trim()}>
              Save
            </Button>
            <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
          </form>
        )}
        <Badge variant="secondary">••••{item.tail}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {item.error && <p className="text-sm text-destructive">{item.error}</p>}
        <ul className="space-y-1">
          {item.channels.map((c) => (
            <li key={c.id} className="flex items-center gap-2">
              {c.avatar && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={c.avatar} alt="" className="size-6 shrink-0 rounded-full" />
              )}
              <ChannelLine channel={c} />
            </li>
          ))}
          {item.channels.length === 0 && (
            <li className="text-sm text-muted-foreground">Buffer shows no accounts here.</li>
          )}
        </ul>
        <p className="text-xs text-muted-foreground">
          {item.used_today} request{item.used_today === 1 ? "" : "s"} to Buffer today (a free Buffer
          account allows 600 a day)
          {item.refreshed_at &&
            `, accounts checked ${new Date(item.refreshed_at).toLocaleString()}`}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => run(() => api.refreshBufferKey(item.id), "Accounts checked")}
          >
            <RefreshCw /> Refresh accounts
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => setRenaming(item.label)}>
            <Pencil /> Rename
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Remove the key "${item.label}"?`))
                run(() => api.removeBufferKey(item.id), "Key removed");
            }}
          >
            <Trash2 /> Remove
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PresetRow({
  preset,
  keys,
  reachable,
  busy,
  run,
}: {
  preset: BufferPresetLink;
  keys: BufferKey[];
  reachable: Set<string>;
  busy: boolean;
  run: Run;
}) {
  const [editing, setEditing] = useState<string[] | null>(null);
  const names = new Map(channelsOf(keys).map(({ channel }) => [channel.id, channel.name]));
  const gone = preset.channels.filter((id) => !reachable.has(id));
  return (
    <div className="sketch-tile space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{preset.label}</span>
        {preset.channels.length === 0 && (
          <span className="text-sm text-muted-foreground">not linked</span>
        )}
        {preset.channels
          .filter((id) => reachable.has(id))
          .map((id) => (
            <Badge key={id} variant="secondary">
              {names.get(id)}
            </Badge>
          ))}
        {editing === null && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            disabled={busy}
            onClick={() => setEditing(preset.channels.filter((id) => reachable.has(id)))}
          >
            Choose accounts
          </Button>
        )}
      </div>
      {gone.length > 0 && (
        <p className="text-sm text-destructive">
          {gone.length} linked account{gone.length === 1 ? " is" : "s are"} no longer in any of your
          keys: refresh the keys, add the missing one, or choose again.
        </p>
      )}
      {editing !== null && (
        <div className="space-y-3">
          <ChannelPicker
            keys={keys}
            chosen={editing}
            onChange={setEditing}
            idPrefix={`preset-${preset.name}`}
          />
          <div className="flex gap-2">
            <Button
              disabled={busy}
              onClick={async () => {
                const saved = await run(
                  () => api.linkPresetChannels(preset.name, editing),
                  editing.length ? `${preset.label} linked` : `${preset.label} unlinked`,
                );
                if (saved) setEditing(null);
              }}
            >
              Save
            </Button>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
