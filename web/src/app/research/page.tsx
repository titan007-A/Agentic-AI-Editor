"use client";

import { Scissors, Search, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { EditDialog, ReelCard } from "@/components/reel-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { usePolling } from "@/hooks/use-polling";
import { api, errorText, type Preset, type Research, type ResearchItem } from "@/lib/api";
import { cn } from "@/lib/utils";

const RUNNING_POLL_MS = 3000;
const SHOWN_RESEARCHES = 6;
const PICK = 5; // reels picked at most from a research, to edit together

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

const running = (r: Research | null) => r?.status === "queued" || r?.status === "running";

function Researcher() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [list, setList] = useState<Research[] | null>(null);
  const [loaded, setLoaded] = useState<Research | null>(null);
  const current = loaded?.id === id ? loaded : null;
  const [presets, setPresets] = useState<Preset[]>([]);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [order, setOrder] = useState<Order>("best");
  // Reels picked to edit together, at most PICK, kept for the research they were picked in.
  const [picks, setPicks] = useState<{ research: string; ids: number[] }>({
    research: "",
    ids: [],
  });
  const picked = picks.research === id ? picks.ids : [];
  const setPicked = (next: (now: number[]) => number[]) =>
    setPicks({ research: id, ids: next(picked) });
  const [editing, setEditing] = useState(false);
  const [all, setAll] = useState(false);

  const refreshList = useCallback(
    () =>
      api
        .researches()
        .then(setList)
        .catch((err) => toast.error(errorText(err))),
    [],
  );

  useEffect(() => {
    refreshList();
    api
      .presets()
      .then(setPresets)
      .catch(() => {});
  }, [refreshList]);

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
        if (last.current?.id === next.id && running(last.current) && !running(next)) {
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
  usePolling(load, running(current) ? RUNNING_POLL_MS : null);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const started = await api.startResearch(description.trim());
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
          Describe the reels you want; it searches Instagram through web search engines and ranks
          what it finds by how well it fits and how viral it is.
        </p>
      </div>

      <Card className="tape">
        <CardHeader>
          <CardTitle>Find reels</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={start} className="space-y-3">
            <div className="space-y-2">
              <Label htmlFor="research-description">What are you looking for?</Label>
              <Textarea
                id="research-description"
                rows={3}
                maxLength={1000}
                placeholder="e.g. 80s rock singers live on stage, Bryan Adams, Queen, Italian ballads too"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <Button type="submit" disabled={busy || description.trim().length < 3}>
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
                    {r.description}
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
              <h2 className="text-lg font-semibold break-words">{current.description}</h2>
              {current.status === "done" && (
                <p className="text-sm text-muted-foreground">
                  {current.message}
                  {current.used > 0 && ` · ${current.used} you edited before left out`}
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
                This takes a minute or two: it searches slowly so the engines don&apos;t turn it
                away.
              </p>
            </Card>
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
              No reels found; try other words, or an artist or song by name.
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
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">
                  Picked {picked.length} of {PICK}
                </span>
                <Button size="sm" disabled={picked.length === 0} onClick={() => setEditing(true)}>
                  <Scissors /> Edit picked
                </Button>
                {picked.length > 0 && (
                  <Button size="sm" variant="ghost" onClick={() => setPicked(() => [])}>
                    Clear
                  </Button>
                )}
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {sorted(current.items, order).map((item) => (
                  <ReelCard
                    key={item.id}
                    item={item}
                    presets={presets}
                    picked={picked.includes(item.id)}
                    full={picked.length >= PICK}
                    onPick={() =>
                      setPicked((now) =>
                        now.includes(item.id)
                          ? now.filter((id) => id !== item.id)
                          : now.length < PICK
                            ? [...now, item.id]
                            : now,
                      )
                    }
                  />
                ))}
              </div>
              <EditDialog
                items={current.items.filter((i) => picked.includes(i.id))}
                presets={presets}
                open={editing}
                onOpenChange={setEditing}
              />
            </>
          )}
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
