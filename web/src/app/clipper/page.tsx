"use client";

import {
  ExternalLink,
  Loader2,
  Plus,
  RefreshCw,
  Radio,
  Scissors,
  Search,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { KickMorning } from "@/components/kick-morning";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useMe } from "@/hooks/use-me";
import { usePolling } from "@/hooks/use-polling";
import {
  api,
  errorText,
  type KickHunt,
  type KickMoment,
  type KickStream,
  type KickStreamer,
  type KickStreams,
  type Preset,
} from "@/lib/api";
import { clipState, clock, streamLength, working } from "@/lib/kick-clips.mjs";
import { cn } from "@/lib/utils";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";
const FIELD = "min-h-11 border-[1.5px] border-foreground bg-[var(--field)]";
const HUNTING: Record<KickHunt["status"], string> = {
  queued: "waiting for a free slot",
  reading: "reading the chat",
  listening: "listening to the stream",
  cutting: "cutting the moments",
  done: "done",
  failed: "failed",
};

function PresetSelect({
  id,
  value,
  presets,
  onChange,
}: {
  id: string;
  value: string;
  presets: Preset[];
  onChange: (v: string) => void;
}) {
  return (
    <select id={id} className={SELECT} value={value} onChange={(e) => onChange(e.target.value)}>
      {presets.map((p) => (
        <option key={p.name} value={p.name}>
          {p.label || p.name}
        </option>
      ))}
    </select>
  );
}

function StreamList({
  found,
  onPick,
}: {
  found: KickStreams;
  onPick: (stream: KickStream) => void;
}) {
  if (!found.streams.length)
    return <p className="text-sm text-muted-foreground">{found.username} has no streams kept.</p>;
  return (
    <div className="space-y-2" data-testid="stream-list">
      <p className="text-xs text-muted-foreground">
        {found.username}&apos;s streams{found.is_live ? " (live now)" : ""}: pick one to clip
      </p>
      {found.streams.map((s) => (
        <button
          key={s.id}
          type="button"
          disabled={!s.has_video}
          onClick={() => onPick(s)}
          className="sketch-tile flex min-h-11 w-full items-center gap-3 p-2 text-left disabled:opacity-50"
        >
          {s.thumbnail ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={s.thumbnail} alt="" className="h-10 w-16 shrink-0 rounded-sm object-cover" />
          ) : null}
          <span className="min-w-0 flex-1">
            <span className="line-clamp-1 text-sm font-semibold">{s.title || "Untitled"}</span>
            <span className="text-xs text-muted-foreground">
              {new Date(s.started).toLocaleString()} · {streamLength(s)}
              {s.has_video ? "" : " · no video yet"}
            </span>
          </span>
          {s.live && <Radio className="size-4 shrink-0 text-red-600" aria-label="live" />}
        </button>
      ))}
    </div>
  );
}

function MomentRow({ moment, index }: { moment: KickMoment; index: number }) {
  const output = moment.job?.outputs[0];
  const busy = moment.job && ["queued", "running"].includes(moment.job.status);
  return (
    <div className="sketch-tile space-y-2 p-3" data-testid="moment">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">
          {index + 1}. {clock(moment.start)} to {clock(moment.end)}
        </span>
        <span className="text-xs text-muted-foreground">peak {clock(moment.peak)}</span>
        {moment.link && (
          <a
            href={moment.link}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs underline"
          >
            on Kick <ExternalLink className="size-3" />
          </a>
        )}
      </div>
      {moment.why.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {moment.why.map((w) => (
            <span key={w} className="rounded-full border px-2 py-0.5 text-xs">
              {w}
            </span>
          ))}
        </div>
      )}
      {moment.hook?.what && (
        <div className="space-y-0.5 text-xs" data-testid="moment-hook">
          <p>{moment.hook.what}</p>
          {moment.hook.crazy && <p className="font-semibold">Crazy: {moment.hook.crazy}</p>}
          {moment.hook.said && (
            <p className="text-muted-foreground">
              {moment.hook.flash ? "Opens cold on" : "Opens on"} “{moment.hook.said}”
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {busy && <Loader2 className="size-3.5 animate-spin" />}
        <span>{clipState(moment)}</span>
        {output && <span>· {Math.round(output.duration)} s</span>}
        {moment.job && (
          <Link href={`/job/?id=${moment.job.id}`} className="underline">
            open the clip
          </Link>
        )}
      </div>
      {moment.job?.status === "failed" && moment.job.error && (
        <p className="text-xs text-destructive">{moment.job.error}</p>
      )}
      {moment.error && <p className="text-xs text-destructive">{moment.error}</p>}
      {output && (
        <video
          className="mx-auto max-h-[60vh] w-full max-w-xs rounded-sm bg-black"
          src={`/api/outputs/${output.id}/video`}
          poster={`/api/outputs/${output.id}/thumbnail?width=360`}
          controls
          playsInline
          preload="none"
        />
      )}
    </div>
  );
}

function HuntCard({
  hunt,
  onChange,
  onRemove,
}: {
  hunt: KickHunt;
  onChange: (h: KickHunt) => void;
  onRemove: (id: string) => void;
}) {
  const looking = ["queued", "reading", "listening", "cutting"].includes(hunt.status);
  const [sending, setSending] = useState(false);

  async function retry() {
    setSending(true);
    try {
      onChange(await api.retryKickHunt(hunt.id));
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <Card data-testid="hunt">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <Scissors className="size-4" />
          <span>{hunt.username}</span>
          {hunt.live && (
            <span className="rounded-full bg-red-600 px-2 py-0.5 text-xs text-white">live</span>
          )}
          <span className="text-sm font-normal text-muted-foreground">
            {clock(hunt.start)} to {hunt.end ? clock(hunt.end) : "the end"}
          </span>
        </CardTitle>
        <p className="line-clamp-1 text-xs text-muted-foreground">
          {hunt.title}
          {hunt.link && (
            <>
              {" · "}
              <a href={hunt.link} target="_blank" rel="noreferrer" className="underline">
                stream
              </a>
            </>
          )}
          {" · "}folder {hunt.folder}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2 text-sm">
          {looking && <Loader2 className="size-4 animate-spin" />}
          <span className={cn(hunt.status === "failed" && "text-destructive")}>
            {looking ? hunt.message || HUNTING[hunt.status] : hunt.error || hunt.message}
          </span>
        </div>
        {hunt.moments.map((m, i) => (
          <MomentRow key={`${hunt.id}-${i}`} moment={m} index={i} />
        ))}
        <div className="flex gap-2">
          {hunt.status === "failed" && (
            <Button size="sm" variant="outline" disabled={sending} onClick={retry}>
              <RefreshCw className="size-3.5" /> Try again
            </Button>
          )}
          {!looking && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onRemove(hunt.id)}
              aria-label="Remove this hunt (its clips stay)"
            >
              <Trash2 className="size-3.5" /> Remove
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function StreamerRow({
  streamer,
  presets,
  onChange,
  onRemove,
  onStreams,
}: {
  streamer: KickStreamer;
  presets: Preset[];
  onChange: (s: KickStreamer) => void;
  onRemove: (id: string) => void;
  onStreams: (name: string) => void;
}) {
  const [folder, setFolder] = useState(streamer.folder);

  async function change(body: Parameters<typeof api.changeKickStreamer>[1]) {
    try {
      onChange(await api.changeKickStreamer(streamer.id, body));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <div className="sketch-tile space-y-2 p-3" data-testid="streamer">
      <div className="flex items-center gap-2">
        {streamer.avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={streamer.avatar} alt="" className="size-8 rounded-full object-cover" />
        ) : null}
        <a
          href={streamer.link}
          target="_blank"
          rel="noreferrer"
          className="min-w-0 flex-1 truncate font-semibold underline"
        >
          {streamer.username}
        </a>
        {streamer.is_live && (
          <span className="rounded-full bg-red-600 px-2 py-0.5 text-xs text-white">live</span>
        )}
        <Button size="sm" variant="outline" onClick={() => onStreams(streamer.slug)}>
          Streams
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onRemove(streamer.id)}
          aria-label={`Remove ${streamer.username}`}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
      <label className="flex items-center justify-between gap-2 text-sm">
        <span>Clip live streams by itself</span>
        <Switch
          checked={streamer.live}
          onCheckedChange={(v) => change({ live: Boolean(v) })}
          aria-label={`Clip ${streamer.username} live`}
        />
      </label>
      {streamer.live && streamer.last_result && (
        <p className="text-xs text-muted-foreground">{streamer.last_result}</p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label htmlFor={`preset-${streamer.id}`}>Preset</Label>
          <PresetSelect
            id={`preset-${streamer.id}`}
            value={streamer.preset}
            presets={presets}
            onChange={(v) => change({ preset: v })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`hour-${streamer.id}`}>Live clips an hour</Label>
          <select
            id={`hour-${streamer.id}`}
            className={SELECT}
            value={streamer.per_hour}
            onChange={(e) => change({ per_hour: Number(e.target.value) })}
          >
            {[1, 2, 3, 4, 6, 8, 12].map((n) => (
              <option key={n} value={n}>
                up to {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor={`folder-${streamer.id}`}>Folder on the piles</Label>
        <Input
          id={`folder-${streamer.id}`}
          className={FIELD}
          placeholder={`Kick ${streamer.username}`}
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          onBlur={() => folder !== streamer.folder && change({ folder })}
        />
      </div>
    </div>
  );
}

export default function ClipperPage() {
  const me = useMe();
  const allowed = me?.features?.includes("kick_clipper") ?? false;
  const [hunts, setHunts] = useState<KickHunt[]>([]);
  const [streamers, setStreamers] = useState<KickStreamer[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [link, setLink] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [count, setCount] = useState(5);
  const [preset, setPreset] = useState("");
  const [folder, setFolder] = useState("");
  const [found, setFound] = useState<KickStreams | null>(null);
  const [looking, setLooking] = useState(false);
  const [sending, setSending] = useState(false);
  const [newName, setNewName] = useState("");

  const editing = useMemo(() => presets.filter((p) => !p.download_only), [presets]);

  const load = useCallback(async () => {
    try {
      setHunts(await api.kickHunts());
    } catch {
      /* next time */
    }
  }, []);

  useEffect(() => {
    if (!allowed) return;
    let live = true;
    api
      .kickHunts()
      .then((h) => live && setHunts(h))
      .catch(() => {});
    api
      .kickStreamers()
      .then((s) => live && setStreamers(s))
      .catch(() => {});
    api
      .presets()
      .then((p) => {
        if (!live) return;
        setPresets(p);
        const usable = p.filter((x) => !x.download_only);
        const kickPreset = usable.find((x) => x.name === "kick-clips");
        setPreset((kickPreset ?? usable[0])?.name ?? "");
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [allowed]);

  const busy = hunts.some(working);
  usePolling(load, allowed && busy ? 3000 : null);
  // Followed live streamers change on the server (live, next stretch): look now and then.
  const following = streamers.some((s) => s.live);
  usePolling(
    async () => {
      try {
        setStreamers(await api.kickStreamers());
        await load();
      } catch {
        /* next time */
      }
    },
    allowed && following ? 60000 : null,
  );

  async function showStreams(name: string) {
    if (!name.trim()) return;
    setLooking(true);
    try {
      setFound(await api.kickStreams(name.trim()));
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setLooking(false);
    }
  }

  async function hunt() {
    if (!link.trim()) {
      toast.error("Paste a stream's link, or a streamer that is live");
      return;
    }
    setSending(true);
    try {
      const made = await api.startKickHunt({
        link: link.trim(),
        start,
        end,
        count,
        preset,
        folder,
      });
      setHunts((h) => [made, ...h.filter((x) => x.id !== made.id)]);
      toast.success("Looking for its moments");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSending(false);
    }
  }

  async function addStreamer() {
    if (!newName.trim()) return;
    try {
      const made = await api.addKickStreamer({ name: newName.trim(), preset });
      setStreamers((s) => [...s, made]);
      setNewName("");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function removeStreamer(id: string) {
    try {
      await api.removeKickStreamer(id);
      setStreamers((s) => s.filter((x) => x.id !== id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function removeHunt(id: string) {
    try {
      await api.removeKickHunt(id);
      setHunts((h) => h.filter((x) => x.id !== id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (!allowed)
    return (
      <p className="text-sm text-muted-foreground">This page isn&apos;t on for your account.</p>
    );

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-hand text-4xl font-bold">
          <span className="marker">Kick Clipper</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          Clips of 15-30 seconds from Kick streams. It reads the stream&apos;s chat (bursts,
          &quot;KEKW&quot;, &quot;clip it&quot;), its sound and the clips viewers made, cuts each
          moment from the stream and makes it with your preset. The clips land in Jobs and in a
          folder on the piles, ready to post.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card className="tape">
            <CardHeader>
              <CardTitle>Find clips</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="kick-link">Stream link, or a streamer that is live</Label>
                <div className="flex gap-2">
                  <Input
                    id="kick-link"
                    className={FIELD}
                    placeholder="kick.com/name/videos/… or kick.com/name"
                    value={link}
                    onChange={(e) => setLink(e.target.value)}
                  />
                  <Button
                    variant="outline"
                    className="min-h-11"
                    disabled={looking}
                    onClick={() => showStreams(link)}
                    aria-label="Show this streamer's streams"
                  >
                    {looking ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Search className="size-4" />
                    )}
                  </Button>
                </div>
              </div>
              {found && (
                <StreamList
                  found={found}
                  onPick={(s) => {
                    setLink(s.live ? `https://kick.com/${found.slug}` : s.link);
                    toast.success(s.live ? "The live stream so far" : "Stream chosen");
                  }}
                />
              )}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="kick-start">From (optional)</Label>
                  <Input
                    id="kick-start"
                    className={FIELD}
                    placeholder="1:00:00"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="kick-end">To (optional)</Label>
                  <Input
                    id="kick-end"
                    className={FIELD}
                    placeholder="1:03:00"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="kick-count">Clips, at most</Label>
                  <select
                    id="kick-count"
                    className={SELECT}
                    value={count}
                    onChange={(e) => setCount(Number(e.target.value))}
                  >
                    {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="kick-preset">Preset</Label>
                  <PresetSelect
                    id="kick-preset"
                    value={preset}
                    presets={editing}
                    onChange={setPreset}
                  />
                </div>
              </div>
              <div className="space-y-1">
                <Label htmlFor="kick-folder">Folder on the piles (optional)</Label>
                <Input
                  id="kick-folder"
                  className={FIELD}
                  placeholder="Kick <streamer>"
                  value={folder}
                  onChange={(e) => setFolder(e.target.value)}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                Without From/To the whole stream is read (a live one: what was streamed so far).
                Every clip is 15-30 seconds.
              </p>
              <Button className="min-h-11 w-full" disabled={sending} onClick={hunt}>
                {sending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Scissors className="size-4" />
                )}
                Find clips
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Streamers</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <Input
                  className={FIELD}
                  placeholder="streamer name or kick.com link"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addStreamer()}
                  aria-label="Streamer to add"
                />
                <Button className="min-h-11" variant="outline" onClick={addStreamer}>
                  <Plus className="size-4" /> Add
                </Button>
              </div>
              {streamers.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  Add the streamers you clip: their streams are one tap away, and with &quot;Clip
                  live streams&quot; on, clips are made by themselves every 10 minutes while they
                  stream.
                </p>
              )}
              {streamers.map((s) => (
                <StreamerRow
                  key={s.id}
                  streamer={s}
                  presets={editing}
                  onChange={(next) =>
                    setStreamers((all) => all.map((x) => (x.id === next.id ? next : x)))
                  }
                  onRemove={removeStreamer}
                  onStreams={(name) => {
                    setLink(name);
                    void showStreams(name);
                  }}
                />
              ))}
            </CardContent>
          </Card>

          <KickMorning />
        </div>

        <div className="space-y-4">
          {hunts.length === 0 ? (
            <Card>
              <CardContent className="space-y-2 py-6 text-sm text-muted-foreground">
                <p>No clips yet. Paste a stream&apos;s link and press Find clips.</p>
              </CardContent>
            </Card>
          ) : (
            hunts.map((h) => (
              <HuntCard
                key={h.id}
                hunt={h}
                onChange={(next) =>
                  setHunts((all) => all.map((x) => (x.id === next.id ? next : x)))
                }
                onRemove={removeHunt}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
