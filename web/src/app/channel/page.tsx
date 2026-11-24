"use client";

import {
  Bot,
  Check,
  ChevronDown,
  ExternalLink,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  X,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { usePolling } from "@/hooks/use-polling";
import {
  api,
  errorText,
  type ChannelPilot,
  type ChannelPilotSettings,
  type ChannelPilots,
  type ChannelPost,
} from "@/lib/api";
import {
  STATUS,
  TROUBLE,
  actions,
  numbers,
  passed,
  short,
  songName,
  timesFor,
} from "@/lib/channel-pilot.mjs";
import { cn } from "@/lib/utils";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";
const HOURS = Array.from({ length: 24 }, (_, h) => h);
const SHOWS = [
  ["queue", "In the works"],
  ["posted", "Posted"],
  ["skipped", "Skipped"],
] as const;
type Show = (typeof SHOWS)[number][0];

// The server's times are UTC without saying so: read them as UTC, shown in local time.
function when(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(/[Zz+]/.test(iso.slice(10)) ? iso : iso + "Z");
  return at.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function settingsOf(pilot: ChannelPilot | null, defaults: ChannelPilotSettings) {
  const base = pilot ?? defaults;
  return {
    active: base.active,
    dry_run: base.dry_run,
    research_account_id: base.research_account_id,
    preset: base.preset,
    focus: base.focus,
    surprise: base.surprise,
    research_hour: base.research_hour,
    posts_per_day: base.posts_per_day,
    post_times: base.post_times,
    needs: base.needs,
  } satisfies ChannelPilotSettings;
}

/**
 * An Instagram page that runs itself: each day a research finds videos for it, the best are
 * checked, edited with its preset, checked again and posted at its post times through the
 * app's own Instagram connection. The first reels wait for approval; dry run posts nothing.
 */
export default function ChannelAutopilotPage() {
  const [data, setData] = useState<ChannelPilots | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api
        .channelPilots()
        .then((found) => {
          setData(found);
          setChosen((now) => now ?? found.channels[0]?.id ?? null);
        })
        .catch((err) => toast.error(errorText(err))),
    [],
  );
  useEffect(() => {
    void load();
  }, [load]);
  usePolling(() => void load(), 20000);

  const channel = data?.channels.find((c) => c.id === chosen) ?? null;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="space-y-1">
        <h1 className="flex items-center gap-2 font-hand text-4xl font-bold">
          <Bot className="size-8" aria-hidden /> Channel autopilot
        </h1>
        <p className="text-sm text-muted-foreground">
          The page finds trending videos like its best posts, checks them, edits them with its
          preset, checks the reel again and posts it at its post times. Anything in doubt waits for
          you; nothing is posted twice.
        </p>
      </div>
      {data === null && <p className="text-sm text-muted-foreground">Loading...</p>}
      {data !== null && data.channels.length === 0 && (
        <p className="text-sm">
          Connect the page on the{" "}
          <Link href="/instagram/" className="underline">
            Instagram tab
          </Link>{" "}
          first.
        </p>
      )}
      {data !== null && data.channels.length > 1 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="Instagram page">
          {data.channels.map((c) => (
            <Button
              key={c.id}
              size="sm"
              variant={c.id === chosen ? "default" : "outline"}
              aria-pressed={c.id === chosen}
              onClick={() => setChosen(c.id)}
            >
              @{c.username}
            </Button>
          ))}
        </div>
      )}
      {data !== null && channel && (
        <>
          <PilotCard
            key={channel.id}
            channelId={channel.id}
            username={channel.username}
            pilot={channel.pilot}
            data={data}
            onSaved={load}
          />
          {channel.pilot && <Videos channelId={channel.id} pilot={channel.pilot} />}
        </>
      )}
    </div>
  );
}

function PilotCard({
  channelId,
  username,
  pilot,
  data,
  onSaved,
}: {
  channelId: string;
  username: string;
  pilot: ChannelPilot | null;
  data: ChannelPilots;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<ChannelPilotSettings>(() => settingsOf(pilot, data.defaults));
  const [busy, setBusy] = useState(false);
  const set = (change: Partial<ChannelPilotSettings>) => setForm((f) => ({ ...f, ...change }));
  const presets = data.presets;
  const split = presets.filter((p) => p.split);
  const DEFAULT_TIMES = data.defaults.post_times;

  async function save(change: Partial<ChannelPilotSettings> = {}) {
    const body = { ...form, ...change };
    if (!body.research_account_id) body.research_account_id = "new";
    setBusy(true);
    try {
      const saved = await api.saveChannelPilot(channelId, body);
      setForm(settingsOf(saved, data.defaults));
      toast.success(
        !saved.active
          ? "Autopilot paused"
          : saved.dry_run
            ? "Saved: dry run, nothing is posted"
            : "Saved: it posts for real",
      );
      onSaved();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function run(what: "find" | "learn") {
    setBusy(true);
    try {
      if (what === "find") await api.channelFindNow(channelId);
      else await api.channelLearnNow(channelId);
      toast.success(what === "find" ? "Looking for videos now" : "Reading the page's posts");
      onSaved();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function setPerDay(perDay: number) {
    const times = timesFor(perDay, form.post_times, DEFAULT_TIMES, pilot?.post_times ?? []);
    set({ posts_per_day: perDay, post_times: times });
  }

  function setTime(index: number, value: string) {
    const times = [...form.post_times];
    times[index] = value;
    set({ post_times: times });
  }

  const left = pilot ? Math.max(0, pilot.needs - pilot.approved) : form.needs;

  return (
    <Card data-testid="channel-pilot">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="break-all">@{username}</CardTitle>
          <div className="flex flex-wrap gap-1 text-xs font-semibold">
            <span
              className={cn(
                "rounded-full px-2 py-0.5",
                pilot?.active ? "bg-primary text-primary-foreground" : "bg-muted",
              )}
            >
              {pilot ? (pilot.active ? "Running" : "Paused") : "Not set up"}
            </span>
            {(pilot?.dry_run ?? form.dry_run) && (
              <span className="rounded-full bg-amber-200 px-2 py-0.5 text-amber-950">
                Dry run: nothing is posted
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {pilot?.paused_reason && (
          <p
            role="alert"
            className="rounded-md border-2 border-destructive/50 p-2 text-sm break-words text-destructive"
          >
            {pilot.paused_reason}
          </p>
        )}
        {pilot && <Status pilot={pilot} />}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
            <span>
              Running
              <span className="block text-xs text-muted-foreground">
                Off: it finds and posts nothing (checks already started finish)
              </span>
            </span>
            <Switch
              checked={form.active}
              onCheckedChange={(v) => set({ active: Boolean(v) })}
              aria-label="Autopilot running"
            />
          </label>
          <label className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
            <span>
              Dry run
              <span className="block text-xs text-muted-foreground">
                Does everything except the post itself
              </span>
            </span>
            <Switch
              checked={form.dry_run}
              onCheckedChange={(v) => set({ dry_run: Boolean(v) })}
              aria-label="Dry run"
            />
          </label>
        </div>
        {!form.dry_run && (pilot?.dry_run ?? true) && (
          <p className="rounded-md border-2 border-amber-500/60 p-2 text-sm">
            Saving with dry run off makes it post to @{username} for real at its post times.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="pilot-preset">Preset its reels are edited with</Label>
            <select
              id="pilot-preset"
              className={SELECT}
              value={form.preset}
              onChange={(e) => set({ preset: e.target.value })}
            >
              <option value="">Choose a preset</option>
              {(split.length ? split : presets).map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label || p.name}
                </option>
              ))}
              {split.length > 0 &&
                presets
                  .filter((p) => !p.split)
                  .map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.label || p.name}
                    </option>
                  ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-research">Research account</Label>
            <select
              id="pilot-research"
              className={SELECT}
              value={form.research_account_id ?? "new"}
              onChange={(e) => set({ research_account_id: e.target.value })}
            >
              {!data.research_accounts.some((a) => a.handle === username.toLowerCase()) && (
                <option value="new">Make one for @{username}</option>
              )}
              {data.research_accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  @{a.handle}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="pilot-focus">What to look for (besides what its best posts show)</Label>
          <Textarea
            id="pilot-focus"
            rows={3}
            maxLength={600}
            placeholder="e.g. well-known songs with a distinctive lead voice, surprising picks from the 60s to the 2010s, official videos or live performances"
            value={form.focus}
            onChange={(e) => set({ focus: e.target.value })}
          />
        </div>
        <label className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
          <span>
            Prefer surprising picks
            <span className="block text-xs text-muted-foreground">
              A recognisable song people don&apos;t expect over an artist&apos;s most famous hit
            </span>
          </span>
          <Switch
            checked={form.surprise}
            onCheckedChange={(v) => set({ surprise: Boolean(v) })}
            aria-label="Prefer surprising picks"
          />
        </label>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="pilot-hour">Research starts at</Label>
            <select
              id="pilot-hour"
              className={SELECT}
              value={form.research_hour}
              onChange={(e) => set({ research_hour: Number(e.target.value) })}
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-per-day">Posts a day</Label>
            <select
              id="pilot-per-day"
              className={SELECT}
              value={form.posts_per_day}
              onChange={(e) => setPerDay(Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-needs">Approve the first</Label>
            <Input
              id="pilot-needs"
              type="number"
              min={0}
              max={500}
              value={form.needs}
              onChange={(e) => set({ needs: Number(e.target.value) })}
            />
          </div>
        </div>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">Post times</legend>
          <div className="flex flex-wrap gap-2">
            {form.post_times.map((t, i) => (
              <Input
                key={i}
                type="time"
                aria-label={`Post time ${i + 1}`}
                value={t}
                onChange={(e) => setTime(i, e.target.value)}
                className="w-32"
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            The server&apos;s time. A post time missed by more than an hour is left for the next
            one.
          </p>
        </fieldset>
        <p className="text-sm">
          {left > 0
            ? `It posts alone after ${form.needs} approved reels (${pilot?.approved ?? 0} so far); until then each one waits for you.`
            : "It posts alone: a reel in any doubt still waits for you."}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => save()} disabled={busy || !form.preset}>
            <Save /> Save
          </Button>
          {pilot && (
            <Button
              variant="outline"
              onClick={() => save({ active: !pilot.active })}
              disabled={busy || !form.preset}
            >
              {pilot.active ? <Pause /> : <Play />} {pilot.active ? "Pause" : "Resume"}
            </Button>
          )}
          {pilot && (
            <Button variant="outline" onClick={() => run("find")} disabled={busy || !pilot.active}>
              <Search /> Find videos now
            </Button>
          )}
          {pilot && (
            <Button variant="ghost" onClick={() => run("learn")} disabled={busy}>
              <RefreshCw /> Read the page&apos;s posts
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Status({ pilot }: { pilot: ChannelPilot }) {
  const counts = pilot.counts;
  const waiting = (counts.awaiting ?? 0) + (counts.look ?? 0);
  return (
    <div className="space-y-2 rounded-md bg-muted/50 p-3 text-sm">
      {pilot.note && <p className="break-words">Last: {pilot.note}</p>}
      {pilot.research && (
        <p className="break-words">
          Today&apos;s research: {pilot.research.status}
          {pilot.research.status === "running" &&
            ` (${Math.round(pilot.research.progress * 100)}%)`}
          {pilot.research.message && `, ${pilot.research.message}`}
        </p>
      )}
      <p>
        {pilot.posted_today} posted today{pilot.dry_run && " (dry run)"}
        {pilot.next_post && ` · next post ${pilot.next_post}`}
        {` · ${counts.ready ?? 0} ready · ${(counts.editing ?? 0) + (counts.reviewing ?? 0)} being edited`}
        {waiting > 0 && <span className="font-semibold"> · {waiting} waiting for you</span>}
      </p>
      <p className="text-xs text-muted-foreground">
        {pilot.learned_at
          ? `Read ${pilot.posts_read} of its posts ${when(pilot.learned_at)}: ${pilot.learn_note}`
          : "Its posts are read before each research."}
      </p>
      {pilot.insights_allowed === false && (
        <p className="text-xs">
          Meta gives this login no insights (views, reach, saves), so it learns from likes and
          comments. To add them, add the instagram_manage_insights permission to the app&apos;s
          login configuration in Meta, then connect the page again on the Instagram tab.
        </p>
      )}
      {pilot.best.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-medium">Its best liked posts</summary>
          <ol className="mt-1 list-decimal space-y-1 pl-5">
            {pilot.best.map((b, i) => (
              <li key={i} className="break-words">
                {b.link ? (
                  <a href={b.link} target="_blank" rel="noreferrer" className="underline">
                    {b.caption || "post"}
                  </a>
                ) : (
                  b.caption
                )}{" "}
                ({short(b.likes ?? 0)} likes)
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

function Videos({ channelId, pilot }: { channelId: string; pilot: ChannelPilot }) {
  const [show, setShow] = useState<Show>("queue");
  // The list as last loaded, with which it is: another one shows "Loading" until it comes.
  const [found, setFound] = useState<{ show: Show; posts: ChannelPost[] } | null>(null);
  const posts = found?.show === show ? found.posts : null;

  const load = useCallback(
    () =>
      api
        .channelPosts(channelId, show)
        .then((list) => setFound({ show, posts: list }))
        .catch(() => {}),
    [channelId, show],
  );
  useEffect(() => {
    void load();
  }, [load]);
  usePolling(() => void load(), 20000);

  const replace = (next: ChannelPost) =>
    setFound((now) =>
      now ? { ...now, posts: now.posts.map((p) => (p.id === next.id ? next : p)) } : now,
    );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-hand text-2xl font-bold">Videos</h2>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Which videos">
          {SHOWS.map(([value, label]) => (
            <Button
              key={value}
              size="sm"
              variant={show === value ? "default" : "outline"}
              aria-pressed={show === value}
              onClick={() => setShow(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      {posts === null ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : posts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {show === "queue"
            ? pilot.active
              ? "Nothing in the works. Videos come after the day's research."
              : "Nothing in the works; it is paused."
            : "None yet."}
        </p>
      ) : show === "posted" ? (
        <PostedTable posts={posts} onChange={replace} />
      ) : (
        <ul className="space-y-3">
          {posts.map((p) => (
            <VideoCard key={p.id} post={p} onChange={replace} />
          ))}
        </ul>
      )}
    </section>
  );
}

function VideoCard({ post, onChange }: { post: ChannelPost; onChange: (p: ChannelPost) => void }) {
  const [busy, setBusy] = useState(false);
  const can = actions(post.status);

  async function act(what: "approve" | "skip" | "retry") {
    setBusy(true);
    try {
      const next =
        what === "approve"
          ? await api.approveChannelPost(post.id)
          : what === "skip"
            ? await api.skipChannelPost(post.id)
            : await api.retryChannelPost(post.id);
      onChange(next);
      toast.success(
        what === "approve"
          ? "Approved: it posts at the next post time"
          : what === "skip"
            ? "Skipped"
            : "It goes at the next post time",
      );
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="sketch-tile space-y-2 p-3" data-testid="channel-video">
      <div className="flex gap-3">
        {post.output_id && post.has_thumbnail && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`/api/outputs/${post.output_id}/thumbnail`}
            alt=""
            className="h-24 w-14 shrink-0 rounded object-cover"
          />
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="min-w-0 font-semibold break-words">{songName(post)}</span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs font-semibold",
                TROUBLE.includes(post.status) ? "bg-destructive/15 text-destructive" : "bg-muted",
              )}
            >
              {STATUS[post.status] ?? post.status}
            </span>
          </div>
          {post.reason && <p className="text-sm break-words">{post.reason}</p>}
          <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <a href={post.url} target="_blank" rel="noreferrer" className="underline">
              source <ExternalLink className="inline size-3" aria-hidden />
            </a>
            {post.output_id && (
              <a
                href={`/api/outputs/${post.output_id}/video`}
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                watch the reel
              </a>
            )}
            {post.job_id && (
              <Link href={`/job/?id=${post.job_id}`} className="underline">
                the edit
              </Link>
            )}
            {post.relevance !== null && <span>fit {Math.round(post.relevance * 10)}/10</span>}
            {post.duration !== null && <span>{Math.round(post.duration)} s</span>}
            <span>{passed(post.checks)}</span>
          </p>
        </div>
      </div>
      {post.checks.length > 0 && (
        <details className="text-xs">
          <summary className="flex cursor-pointer items-center gap-1 font-medium">
            <ChevronDown className="size-3" aria-hidden /> Checks
          </summary>
          <ul className="mt-1 space-y-1">
            {post.checks.map((c, i) => (
              <li key={i} className="flex gap-1 break-words">
                {c.ok ? (
                  <Check className="size-4 shrink-0 text-green-700" aria-label="passed" />
                ) : (
                  <X className="size-4 shrink-0 text-destructive" aria-label="failed" />
                )}
                <span>
                  <span className="font-semibold">{c.name}</span>: {c.detail}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {post.caption && (
        <details className="text-xs">
          <summary className="flex cursor-pointer items-center gap-1 font-medium">
            <ChevronDown className="size-3" aria-hidden /> Caption
          </summary>
          <p className="mt-1 break-words whitespace-pre-wrap">{post.caption}</p>
        </details>
      )}
      {(can.approve || can.skip || can.retry) && (
        <div className="flex flex-wrap gap-2">
          {can.approve && (
            <Button size="sm" onClick={() => act("approve")} disabled={busy}>
              <Check /> Approve
            </Button>
          )}
          {can.retry && (
            <Button size="sm" onClick={() => act("retry")} disabled={busy}>
              <RotateCcw /> Try again
            </Button>
          )}
          {can.skip && (
            <Button size="sm" variant="ghost" onClick={() => act("skip")} disabled={busy}>
              <X /> Skip
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function PostedTable({
  posts,
  onChange,
}: {
  posts: ChannelPost[];
  onChange: (p: ChannelPost) => void;
}) {
  const failed = posts.filter((p) => p.status === "failed");
  const done = posts.filter((p) => p.status === "posted");
  return (
    <div className="space-y-3">
      {failed.length > 0 && (
        <ul className="space-y-3">
          {failed.map((p) => (
            <VideoCard key={p.id} post={p} onChange={onChange} />
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="bg-muted/50 text-xs">
              <tr>
                <th className="p-2 font-medium">When</th>
                <th className="p-2 font-medium">Song</th>
                <th className="p-2 font-medium">Numbers (a day or two after)</th>
              </tr>
            </thead>
            <tbody>
              {done.map((p) => {
                const shown = numbers(p.insights);
                return (
                  <tr key={p.id} className="border-t align-top">
                    <td className="p-2 whitespace-nowrap">
                      {when(p.posted_at)}
                      {p.dry_run && (
                        <span className="ml-1 rounded-full bg-amber-200 px-1.5 text-xs text-amber-950">
                          dry run
                        </span>
                      )}
                    </td>
                    <td className="p-2 break-words">
                      {p.permalink ? (
                        <a
                          href={p.permalink}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          {songName(p)}
                        </a>
                      ) : (
                        songName(p)
                      )}
                    </td>
                    <td className="p-2 text-xs">
                      {p.dry_run
                        ? "not posted"
                        : shown.length
                          ? shown.map(([label, value]) => `${label} ${short(value)}`).join(" · ")
                          : "read a day after posting"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
