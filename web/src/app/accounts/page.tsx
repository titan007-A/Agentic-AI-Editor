"use client";

import {
  CalendarClock,
  Copy,
  Pause,
  Play,
  ExternalLink,
  MessageCircle,
  Search,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import { Checkbox } from "@/components/ui/checkbox";
import {
  api,
  errorText,
  type NicheAccount,
  type NicheKind,
  type NicheSchedule,
  type NicheSearch,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const MIN = [
  ["0", "Any"],
  ["1000", "1K+"],
  ["10000", "10K+"],
  ["50000", "50K+"],
  ["100000", "100K+"],
  ["500000", "500K+"],
  ["1000000", "1M+"],
] as const;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const KINDS = [
  ["pages", "Pages (theme, fan, meme, news)"],
  ["people", "People (creators, artists, coaches)"],
  ["any", "Any account"],
] as const;
// How an account's kind reads on its card.
const KIND_SAYS: Record<string, string> = { page: "page", person: "person", business: "business" };

function when(s: Pick<NicheSchedule, "hour" | "days">): string {
  const at = `${String(s.hour).padStart(2, "0")}:00`;
  if (!s.days.length || s.days.length === 7) return `every day at ${at}`;
  return `${s.days.map((d) => DAYS[d]).join(", ")} at ${at}`;
}

const MAX = [
  ["", "No limit"],
  ["10000", "10K"],
  ["50000", "50K"],
  ["100000", "100K"],
  ["500000", "500K"],
  ["1000000", "1M"],
] as const;

function followers(n: number | null): string {
  if (n === null) return "?";
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
  return String(n);
}

function AccountCard({
  account,
  onChange,
}: {
  account: NicheAccount;
  onChange: (a: NicheAccount) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState(account.reason);

  async function vote(v: -1 | 0 | 1, why = "") {
    try {
      onChange(await api.voteNiche(account.id, v, why));
      setAsking(false);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <li
      className={cn("sketch-tile space-y-2 p-3", account.vote < 0 && "opacity-60")}
      data-testid="niche-account"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <a
          href={account.url}
          target="_blank"
          rel="noreferrer"
          className="font-semibold break-all underline-offset-2 hover:underline"
        >
          @{account.handle}
        </a>
        <span
          className="text-sm font-semibold tabular-nums"
          title="Followers, as the search engine saw them"
        >
          {followers(account.followers)} followers
        </span>
      </div>
      {account.name && <p className="text-sm">{account.name}</p>}
      {account.why && (
        <p className="text-sm text-muted-foreground">
          {account.fit !== null && <span className="font-semibold">{account.fit}/10 · </span>}
          {KIND_SAYS[account.kind] && (
            <span className="font-semibold" data-testid="niche-kind">
              {KIND_SAYS[account.kind]} ·{" "}
            </span>
          )}
          {account.why}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button asChild size="sm" variant="outline">
          <a href={account.url} target="_blank" rel="noreferrer">
            <ExternalLink /> Open
          </a>
        </Button>
        <Button asChild size="sm">
          <a href={account.message_url} target="_blank" rel="noreferrer">
            <MessageCircle /> Message
          </a>
        </Button>
        <Button
          size="sm"
          variant={account.vote > 0 ? "default" : "ghost"}
          aria-pressed={account.vote > 0}
          aria-label="Good find"
          onClick={() => vote(account.vote > 0 ? 0 : 1)}
        >
          <ThumbsUp />
        </Button>
        <Button
          size="sm"
          variant={account.vote < 0 ? "default" : "ghost"}
          aria-pressed={account.vote < 0}
          aria-label="Not what I want"
          onClick={() => (account.vote < 0 ? vote(0) : setAsking(true))}
        >
          <ThumbsDown />
        </Button>
      </div>
      {asking && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void vote(-1, reason);
          }}
        >
          <Input
            aria-label="Why not (optional)"
            placeholder="Why not? (a brand, wrong language...)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <Button type="submit" size="sm" variant="outline">
            Save
          </Button>
        </form>
      )}
    </li>
  );
}

/** Instagram accounts of a niche, found through public web search, 100 a run and none
 * shown twice; each opens or messages in one tap, and thumbs teach the next run. */
export default function AccountsPage() {
  const me = useMe();
  const allowed = me?.features?.includes("niche_finder") ?? false;
  const [searches, setSearches] = useState<NicheSearch[]>([]);
  const [open, setOpen] = useState<NicheSearch | null>(null);
  const [niche, setNiche] = useState("");
  const [kind, setKind] = useState<NicheKind>("pages");
  const [min, setMin] = useState("10000");
  const [max, setMax] = useState("");
  const [language, setLanguage] = useState("");
  const [country, setCountry] = useState("");
  const [busy, setBusy] = useState(false);
  const [scheduled, setScheduled] = useState(false);
  const [hour, setHour] = useState(7);
  const [days, setDays] = useState<number[]>([]);
  const [schedules, setSchedules] = useState<NicheSchedule[]>([]);

  const load = useCallback(async () => {
    try {
      const next = await api.nicheSearches();
      setSearches(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  const show = useCallback(async (id: string) => {
    try {
      setOpen(await api.nicheSearch(id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }, []);

  useEffect(() => {
    if (!allowed) return;
    let live = true;
    api
      .nicheSearches()
      .then((list) => {
        if (!live) return;
        setSearches(list);
        if (list.length) void show(list[0].id);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [allowed, show]);

  const active = searches.some((s) => s.status === "queued" || s.status === "running");
  useEffect(() => {
    if (!active) return;
    const every = window.setInterval(async () => {
      const list = await load();
      const now = list?.find((s) => s.id === open?.id);
      if (now && open && now.status !== open.status) void show(now.id);
    }, 3000);
    return () => window.clearInterval(every);
  }, [active, load, show, open]);

  useEffect(() => {
    if (!allowed) return;
    let live = true;
    api
      .nicheSchedules()
      .then((list) => live && setSchedules(list))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [allowed]);

  async function schedule() {
    setBusy(true);
    try {
      const made = await api.addNicheSchedule({
        niche,
        kind,
        min_followers: Number(min),
        max_followers: max ? Number(max) : null,
        language,
        country,
        hour,
        days,
      });
      setSchedules((list) => [made, ...list]);
      toast.success(`Scheduled: ${when(made)}`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeSchedule(id: string, change: { active?: boolean }) {
    try {
      const next = await api.changeNicheSchedule(id, change);
      setSchedules((list) => list.map((x) => (x.id === id ? next : x)));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function removeSchedule(id: string) {
    try {
      await api.deleteNicheSchedule(id);
      setSchedules((list) => list.filter((x) => x.id !== id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function start() {
    setBusy(true);
    try {
      const made = await api.startNiche({
        niche,
        kind,
        min_followers: Number(min),
        max_followers: max ? Number(max) : null,
        language,
        country,
      });
      toast.success("Searching: this takes a few minutes");
      await load();
      setOpen(made);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteNiche(id);
      if (open?.id === id) setOpen(null);
      await load();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (!me) return <p className="text-sm text-muted-foreground">Loading...</p>;
  if (!allowed)
    return (
      <p className="text-sm text-muted-foreground">This page isn&apos;t on for your account.</p>
    );

  const accounts = open?.accounts ?? [];
  const running = open && (open.status === "queued" || open.status === "running");
  const current = searches.find((s) => s.id === open?.id) ?? open;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-hand text-4xl font-bold">
          <span className="marker">Accounts</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          Instagram accounts of a niche, found through web search (Instagram itself isn&apos;t
          asked), up to 100 a run, never one you were shown before. Follower counts are what the
          search engine last saw.
        </p>
      </div>
      <Card className="tape">
        <CardHeader>
          <CardTitle>Find accounts</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="niche">Niche</Label>
            <Textarea
              id="niche"
              rows={3}
              placeholder="e.g. vocal coaches who post singing tips, Spanish talent show fan pages"
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
            />
          </div>
          <div className="space-y-1 sm:max-w-xs">
            <Label htmlFor="niche-kind">Looking for</Label>
            <select
              id="niche-kind"
              className="min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value as NicheKind)}
            >
              {KINDS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="space-y-1">
              <Label htmlFor="niche-min">Followers, at least</Label>
              <select
                id="niche-min"
                className="min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
                value={min}
                onChange={(e) => setMin(e.target.value)}
              >
                {MIN.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="niche-max">At most</Label>
              <select
                id="niche-max"
                className="min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
                value={max}
                onChange={(e) => setMax(e.target.value)}
              >
                {MAX.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="niche-language">Language</Label>
              <Input
                id="niche-language"
                placeholder="any"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="niche-country">Country</Label>
              <Input
                id="niche-country"
                placeholder="any"
                value={country}
                onChange={(e) => setCountry(e.target.value)}
              />
            </div>
          </div>
          <label className="flex min-h-11 items-center gap-3 text-sm">
            <Checkbox checked={scheduled} onCheckedChange={(v) => setScheduled(v === true)} />
            Run it on a schedule
          </label>
          {scheduled && (
            <div className="space-y-3 rounded-md border-[1.5px] border-dashed border-foreground/40 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor="niche-hour">At</Label>
                <select
                  id="niche-hour"
                  className="min-h-11 rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                >
                  {Array.from({ length: 24 }, (_, h) => (
                    <option key={h} value={h}>
                      {String(h).padStart(2, "0")}:00
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted-foreground">on</span>
                {DAYS.map((d, i) => (
                  <Button
                    key={d}
                    type="button"
                    size="sm"
                    variant={days.includes(i) ? "default" : "outline"}
                    aria-pressed={days.includes(i)}
                    onClick={() =>
                      setDays((list) =>
                        list.includes(i) ? list.filter((x) => x !== i) : [...list, i],
                      )
                    }
                  >
                    {d}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {when({ hour, days })}. No days picked is every day. Each run brings up to 100
                accounts you haven&apos;t seen; a run the server missed starts once it is back.
              </p>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy || niche.trim().length < 3} onClick={start}>
              <Search /> {busy ? "Starting..." : "Find 100 accounts now"}
            </Button>
            {scheduled && (
              <Button
                variant="outline"
                disabled={busy || niche.trim().length < 3}
                onClick={schedule}
              >
                <CalendarClock /> Save the schedule
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {schedules.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CalendarClock className="size-5" /> Scheduled searches
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2" aria-label="Scheduled searches">
              {schedules.map((s) => (
                <li
                  key={s.id}
                  className={cn(
                    "sketch-tile flex flex-wrap items-center gap-2 p-3 text-sm",
                    !s.active && "opacity-60",
                  )}
                  data-testid="niche-schedule"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium break-words">{s.niche}</span>
                    <span className="text-xs text-muted-foreground">
                      {s.active ? when(s) : "paused"}
                      {s.last_on ? ` · last run ${s.last_on}` : ""}
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => changeSchedule(s.id, { active: !s.active })}
                    aria-label={s.active ? "Pause" : "Resume"}
                  >
                    {s.active ? <Pause /> : <Play />}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => removeSchedule(s.id)}
                    aria-label="Delete schedule"
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <section className="space-y-2" aria-label="Searches">
          <h2 className="font-hand text-2xl font-bold">Your searches</h2>
          {searches.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
          {searches.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => show(s.id)}
              className={cn(
                "sketch-tile block w-full space-y-1 p-3 text-left text-sm hover:bg-[var(--cream-hover)]",
                s.id === open?.id && "bg-[var(--cream-hover)] ring-2 ring-primary",
              )}
            >
              <span className="line-clamp-2 font-medium">{s.niche}</span>
              {s.schedule_id && (
                <span className="block text-xs font-semibold text-[var(--teal)]">
                  scheduled run
                </span>
              )}
              <span className="block text-xs text-muted-foreground">
                {s.status === "done"
                  ? `${s.count} accounts`
                  : s.status === "failed"
                    ? "failed"
                    : s.message || "waiting to start"}
              </span>
              {(s.status === "queued" || s.status === "running") && (
                <Progress value={s.progress * 100} className="h-1.5" />
              )}
            </button>
          ))}
        </section>

        <section className="space-y-3" aria-label="Accounts found">
          {current && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-hand text-2xl font-bold">
                <Users className="mr-1 inline size-5" />
                {current.status === "done" ? `${accounts.length} accounts` : "Searching"}
              </h2>
              <div className="flex gap-2">
                {accounts.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      void navigator.clipboard
                        ?.writeText(accounts.map((a) => `@${a.handle}`).join("\n"))
                        .then(() => toast.success("Handles copied"));
                    }}
                  >
                    <Copy /> Copy handles
                  </Button>
                )}
                {current.status !== "queued" && current.status !== "running" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="Delete this search"
                    onClick={() => remove(current.id)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </div>
            </div>
          )}
          {running && current && (
            <div className="space-y-1">
              <Progress value={current.progress * 100} />
              <p className="text-sm text-muted-foreground">
                {current.message || "Waiting to start..."}
              </p>
            </div>
          )}
          {open?.status === "failed" && <p className="text-sm text-destructive">{open.error}</p>}
          {open?.notes?.map((n) => (
            <p key={n} className="text-sm text-muted-foreground">
              {n}
            </p>
          ))}
          <ul className="grid gap-2 sm:grid-cols-2" aria-label="Accounts">
            {accounts.map((a) => (
              <AccountCard
                key={a.id}
                account={a}
                onChange={(next) =>
                  setOpen((o) =>
                    o
                      ? { ...o, accounts: o.accounts?.map((x) => (x.id === next.id ? next : x)) }
                      : o,
                  )
                }
              />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
