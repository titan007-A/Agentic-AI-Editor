"use client";

import { Scissors, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { HiddenAccounts } from "@/components/hidden-accounts";
import { InstagramAccounts, learning } from "@/components/instagram-accounts";
import { ReelCard } from "@/components/reel-card";
import { ResearchChat } from "@/components/research-chat";
import { ResearchQuestions } from "@/components/research-questions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { usePolling } from "@/hooks/use-polling";
import {
  api,
  errorText,
  type InstagramAccount,
  type Preset,
  type Research,
  type ResearchItem,
  type Site,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const RUNNING_POLL_MS = 3000;
const SHOWN_RESEARCHES = 6;

type Order = "best" | "fit" | "viral";
const ORDERS: { key: Order; label: string }[] = [
  { key: "best", label: "Best match" },
  { key: "fit", label: "Best fit" },
  { key: "viral", label: "Most viral" },
];

function sorted(items: ResearchItem[], order: Order): ResearchItem[] {
  if (order === "best") return items;
  const key =
    order === "fit" ? (i: ResearchItem) => i.relevance ?? 0.5 : (i: ResearchItem) => i.virality;
  return [...items].sort((a, b) => key(b) - key(a));
}

/** Busy on its own: searching, or thinking of the questions to ask. */
const running = (r: Research | null) =>
  r?.status === "queued" ||
  r?.status === "running" ||
  (r?.status === "asking" && r.questions.length === 0);
/** Waiting for the person's answers. */
const asking = (r: Research | null) => r?.status === "asking" && r.questions.length > 0;
/** The researcher is answering a chat message. */
const answering = (r: Research | null) =>
  !!r?.messages.some((m) => m.role === "user" && !m.answered);

// Where a research can search, in the order shown and sent.
const SITES: { site: Site; label: string }[] = [
  { site: "instagram", label: "Instagram reels" },
  { site: "shorts", label: "YouTube Shorts" },
];

// Of the reels a research gives, how many can be edited at once.
const MAX_CHOSEN = 5;

type Started = { item: ResearchItem; jobId: string };

/** A research's name: the account it was for, and what it asked for. */
function named(r: Research): string {
  if (!r.handle) return r.description;
  return r.description ? `@${r.handle} · ${r.description}` : `@${r.handle}`;
}

/** Why else reels found were not given, in a line: the message names those that didn't fit
 * and those shown before. */
function leftOut(r: Research): string {
  const parts = Object.entries(r.left_out)
    .filter(([why]) => why !== "didn't fit" && why !== "shown before")
    .map(([why, n]) => `${n} ${why}`);
  return parts.length ? ` · also left out: ${parts.join(", ")}` : "";
}

function Researcher() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [list, setList] = useState<Research[] | null>(null);
  const [loaded, setLoaded] = useState<Research | null>(null);
  const current = loaded?.id === id ? loaded : null;
  const [presets, setPresets] = useState<Preset[]>([]);
  const [accounts, setAccounts] = useState<InstagramAccount[]>([]);
  // The account a research is for; "" for a description alone.
  const [forAccount, setForAccount] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [skipQuestions, setSkipQuestions] = useState(false);
  const [sites, setSites] = useState<Site[]>(["instagram", "shorts"]);
  const [busy, setBusy] = useState(false);
  const [order, setOrder] = useState<Order>("best");
  const [all, setAll] = useState(false);
  // The reels chosen to edit in the research shown, each with its preset ("" until one is
  // picked), and the jobs started from it: both start over on another research.
  const [picks, setPicks] = useState<{ of: string; reels: Record<number, string> }>({
    of: "",
    reels: {},
  });
  const [started, setStarted] = useState<{ of: string; jobs: Started[] }>({ of: "", jobs: [] });
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);

  const refreshList = useCallback(
    () =>
      api
        .researches()
        .then(setList)
        .catch((err) => toast.error(errorText(err))),
    [],
  );
  const refreshAccounts = useCallback(
    () =>
      api
        .instagramAccounts()
        .then(setAccounts)
        .catch(() => {}),
    [],
  );

  useEffect(() => {
    refreshList();
    refreshAccounts();
    api
      .presets()
      .then(setPresets)
      .catch(() => {});
  }, [refreshList, refreshAccounts]);
  usePolling(refreshAccounts, accounts.some(learning) ? RUNNING_POLL_MS : null);

  // The first account is chosen until the person picks; none, a description.
  const chosenAccount =
    forAccount === null
      ? (accounts[0]?.id ?? "")
      : accounts.some((a) => a.id === forAccount)
        ? forAccount
        : "";

  // Without one chosen, the latest research is shown.
  useEffect(() => {
    if (!id && list?.length) router.replace(`/research/?id=${list[0].id}`);
  }, [id, list, router]);

  // The research as last fetched, so one that just finished updates the list's count.
  const last = useRef<Research | null>(null);
  const load = useCallback(() => {
    if (!id) return;
    api
      .research(id)
      .then((next) => {
        // Finished, or a chat answer that may have started a follow-up: the list changed.
        const was = last.current?.id === next.id ? last.current : null;
        if ((running(was) && !running(next)) || (answering(was) && !answering(next))) {
          refreshList();
        }
        last.current = next;
        setLoaded(next);
      })
      .catch((err) => toast.error(errorText(err)));
  }, [id, refreshList]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, running(current) || answering(current) ? RUNNING_POLL_MS : null);

  const chosen = picks.of === id ? picks.reels : {};
  const startedHere = started.of === id ? started.jobs : [];

  function choose(item: ResearchItem, preset: string | null) {
    const reels = Object.fromEntries(
      Object.entries(chosen).filter(([key]) => Number(key) !== item.id),
    );
    setPicks({ of: id, reels: preset === null ? reels : { ...reels, [item.id]: preset } });
  }

  async function editChosen() {
    if (!current || editing) return;
    setEditing(true);
    const jobs: Started[] = [];
    try {
      // One job a reel: each has its own preset.
      for (const item of current.items.filter((i) => i.id in chosen)) {
        const [job] = await api.createJobs([item.url], [chosen[item.id]], notes);
        jobs.push({ item, jobId: job.id });
      }
      toast.success(`${jobs.length} edit${jobs.length === 1 ? "" : "s"} started`);
      setPicks({ of: id, reels: {} });
      setNotes("");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setStarted({ of: id, jobs: [...startedHere, ...jobs] });
      setEditing(false);
    }
  }

  async function start(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const started = await api.startResearch(
        description.trim(),
        chosenAccount || null,
        skipQuestions,
        sites,
      );
      setDescription("");
      await refreshList();
      router.push(`/research/?id=${started.id}`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(research: Research) {
    try {
      await api.deleteResearch(research.id);
      const rest = (list ?? []).filter((r) => r.id !== research.id);
      setList(rest);
      router.replace(rest.length ? `/research/?id=${rest[0].id}` : "/research/");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  const shown = all ? (list ?? []) : (list ?? []).slice(0, SHOWN_RESEARCHES);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-hand text-4xl font-bold">Researcher</h1>
        <p className="text-sm text-muted-foreground">
          It searches Instagram and YouTube Shorts through web search engines for reels like your
          account&apos;s best ones, or for what you describe, and gives the 8 that fit best and are
          most viral, never one it showed you before. Choose up to 5 of them to edit.
        </p>
      </div>

      <InstagramAccounts accounts={accounts} presets={presets} onChange={refreshAccounts} />
      <HiddenAccounts
        version={current?.items.filter((i) => i.hidden).length ?? 0}
        onUnhidden={load}
      />

      <Card className="tape">
        <CardHeader>
          <CardTitle>Find reels</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={start} className="space-y-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Find reels for">
              {accounts.map((a) => (
                <Button
                  key={a.id}
                  type="button"
                  size="sm"
                  variant={chosenAccount === a.id ? "default" : "outline"}
                  aria-pressed={chosenAccount === a.id}
                  onClick={() => setForAccount(a.id)}
                >
                  @{a.handle}
                </Button>
              ))}
              {accounts.length > 0 && (
                <Button
                  type="button"
                  size="sm"
                  variant={chosenAccount === "" ? "default" : "outline"}
                  aria-pressed={chosenAccount === ""}
                  onClick={() => setForAccount("")}
                >
                  A description
                </Button>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="research-description">
                {chosenAccount
                  ? "Anything in particular this time? (optional)"
                  : "What are you looking for?"}
              </Label>
              <Textarea
                id="research-description"
                rows={3}
                maxLength={1000}
                placeholder={
                  chosenAccount
                    ? "e.g. Italian ballads, or leave it empty"
                    : "e.g. 80s rock singers live on stage, Bryan Adams, Queen, Italian ballads too"
                }
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <fieldset className="space-y-1">
              <legend className="text-sm font-medium">Search</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {SITES.map(({ site, label }) => (
                  <label key={site} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={sites.includes(site)}
                      onCheckedChange={(on) =>
                        setSites(
                          on
                            ? SITES.map((s) => s.site).filter(
                                (s) => s === site || sites.includes(s),
                              )
                            : sites.filter((s) => s !== site),
                        )
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={skipQuestions}
                onCheckedChange={(on) => setSkipQuestions(on === true)}
              />
              Skip the questions and search at once
            </label>
            <Button
              type="submit"
              disabled={
                busy || sites.length === 0 || (!chosenAccount && description.trim().length < 3)
              }
            >
              <Search /> {busy ? "Starting..." : "Find reels"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {list && list.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Earlier searches</p>
          <ul className="space-y-1">
            {shown.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => router.push(`/research/?id=${r.id}`)}
                  className={cn(
                    "sketch-tile flex w-full items-center justify-between gap-2 p-2 text-left text-sm hover:bg-[#fdf3dc]",
                    r.id === id && "bg-[#fdf3dc]",
                  )}
                >
                  <span className="min-w-0 truncate">
                    {r.owner && <span className="text-muted-foreground">{r.owner}: </span>}
                    {r.daily && <span className="text-muted-foreground">Daily: </span>}
                    {named(r)}
                  </span>
                  <Badge variant={r.status === "failed" ? "destructive" : "outline"}>
                    {r.status === "done" ? `${r.found} reels` : r.status}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
          {list.length > SHOWN_RESEARCHES && (
            <Button variant="ghost" size="sm" onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${list.length}`}
            </Button>
          )}
        </div>
      )}

      {current && (
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <h2 className="text-lg font-semibold break-words">{named(current)}</h2>
              {current.parent_id && (
                <Link
                  href={`/research/?id=${current.parent_id}`}
                  className="text-sm text-muted-foreground underline"
                >
                  Asked for in an earlier research&apos;s chat
                </Link>
              )}
              {current.answers.length > 0 && (
                <ul className="text-sm text-muted-foreground">
                  {current.answers.map((a) => (
                    <li key={a.question} className="break-words">
                      {a.question} <span className="text-foreground">{a.answer}</span>
                    </li>
                  ))}
                </ul>
              )}
              {current.brief && <p className="text-sm break-words">Looked for: {current.brief}</p>}
              {current.status === "done" && (
                <p className="text-sm text-muted-foreground">
                  {current.message}
                  {current.considered > 0 && ` (${current.considered} rated)`}
                  {leftOut(current)}
                </p>
              )}
            </div>
            {!running(current) && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => remove(current)}
                aria-label="Delete this research"
              >
                <Trash2 />
              </Button>
            )}
          </div>

          {running(current) && (
            <Card className="gap-3 p-4">
              <Progress value={current.progress * 100} />
              <p className="text-sm">{current.message}</p>
              <p className="text-xs text-muted-foreground">
                {current.status === "asking"
                  ? "It thinks of a few questions first; this takes a moment."
                  : "This takes a few minutes: it searches slowly so the engines don't turn it away."}
              </p>
            </Card>
          )}

          {asking(current) && (
            <ResearchQuestions
              research={current}
              onAnswered={(next) => {
                // Its status in the list changes too: asking -> searching.
                setLoaded(next);
                refreshList();
              }}
            />
          )}

          {current.status === "failed" && (
            <Card className="border-destructive p-4">
              <p className="text-sm whitespace-pre-wrap text-destructive">{current.error}</p>
            </Card>
          )}

          {current.queries.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted-foreground">
                {current.queries.length} searches made
              </summary>
              <ul className="mt-1 list-inside list-disc text-muted-foreground">
                {current.queries.map((q) => (
                  <li key={q} className="break-words">
                    {q}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {current.status === "done" && current.items.length === 0 && (
            <p className="py-6 text-sm text-muted-foreground">
              {Object.keys(current.left_out).length
                ? "Nothing new: every reel it found was shown or edited before. Try a focus, such as an artist or a song by name."
                : "No reels found; try other words, or an artist or song by name."}
            </p>
          )}

          {current.items.length > 0 && (
            <>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Order">
                {ORDERS.map((o) => (
                  <Button
                    key={o.key}
                    size="sm"
                    variant={order === o.key ? "default" : "outline"}
                    aria-pressed={order === o.key}
                    onClick={() => setOrder(o.key)}
                  >
                    {o.label}
                  </Button>
                ))}
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {sorted(current.items, order).map((item) => (
                  <ReelCard
                    key={item.id}
                    item={item}
                    presets={presets}
                    research={current}
                    chosen={item.id in chosen ? chosen[item.id] : null}
                    full={Object.keys(chosen).length >= MAX_CHOSEN}
                    onChoose={(preset) => choose(item, preset)}
                    onChanged={setLoaded}
                  />
                ))}
              </div>
              <Card className="sticky bottom-2 z-10 gap-3 p-4 shadow-lg">
                <p className="text-sm font-medium">
                  {Object.keys(chosen).length} of up to {MAX_CHOSEN} chosen to edit
                </p>
                {Object.keys(chosen).length > 0 && (
                  <div className="space-y-1">
                    <Label htmlFor="research-notes">
                      Notes for the editor, for each (optional)
                    </Label>
                    <Textarea
                      id="research-notes"
                      rows={2}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="e.g. start on the chorus"
                    />
                  </div>
                )}
                <Button
                  disabled={
                    editing ||
                    Object.keys(chosen).length === 0 ||
                    Object.values(chosen).some((preset) => !preset)
                  }
                  onClick={editChosen}
                >
                  <Scissors /> {editing ? "Starting..." : "Edit selected"}
                </Button>
                {Object.values(chosen).some((preset) => !preset) && (
                  <p className="text-xs text-muted-foreground">
                    Pick a preset for each reel chosen.
                  </p>
                )}
              </Card>
            </>
          )}

          {startedHere.length > 0 && (
            <Card className="gap-2 p-4">
              <p className="text-sm font-medium">Edits started from this research</p>
              <ul className="space-y-1 text-sm">
                {startedHere.map(({ item, jobId }) => (
                  <li key={jobId} className="flex justify-between gap-2">
                    <span className="min-w-0 truncate">
                      #{item.rank + 1} {item.account || item.url}
                    </span>
                    <Link href={`/job/?id=${jobId}`} className="shrink-0 underline">
                      Open the job
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {current.status === "done" && <ResearchChat research={current} onSent={setLoaded} />}
        </div>
      )}
    </div>
  );
}

export default function ResearchPage() {
  return (
    <Suspense>
      <Researcher />
    </Suspense>
  );
}
