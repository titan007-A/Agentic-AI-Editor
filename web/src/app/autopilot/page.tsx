"use client";

import {
  ArrowLeft,
  Check,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderPlus,
  Pencil,
  Plus,
  Send,
  Trash2,
  X,
} from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { FeedCard } from "@/components/feed-card";
import { FeedForm } from "@/components/feed-form";
import { OutputCard } from "@/components/output-card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePolling } from "@/hooks/use-polling";
import {
  api,
  errorText,
  thumbnailUrl,
  type Feed,
  type Job,
  type PileFolder,
  type Verdict,
} from "@/lib/api";
import {
  folderPaths,
  folders,
  pileHref,
  placeOf,
  subfolders,
  trail,
  within,
} from "@/lib/pile-folders.mjs";
import { keepIfSame } from "@/lib/poller.mjs";

type PileName = "ready" | "look";
type Tab = PileName | "feeds";
const TABS: Tab[] = ["ready", "look", "feeds"];

const REFRESH_MS = 15000;
// Cards shown at first in an open folder, and added by each "Show more".
const PAGE = 6;

export default function AutopilotPage() {
  return (
    <Suspense>
      <Autopilot />
    </Suspense>
  );
}

function Autopilot() {
  // The tab and the open folder live in the address, so the browser's back button closes a
  // folder and a shared link opens the same one.
  const router = useRouter();
  const params = useSearchParams();
  const asked = params.get("tab") as Tab | null;
  const tab: Tab = asked && TABS.includes(asked) ? asked : "ready";
  const folder = tab === "feeds" ? null : params.get("folder");
  // A folder made inside the open preset's folder, by id; none: the preset's folder itself.
  const asksIn = params.get("in") ?? "";
  const inside = folder !== null && /^\d+$/.test(asksIn) ? Number(asksIn) : null;
  const [counts, setCounts] = useState({ ready: 0, look: 0 });
  const refreshCounts = usePolling(
    () =>
      api
        .pileCounts()
        .then((next) => setCounts((current) => keepIfSame(current, next)))
        .catch(() => {}),
    REFRESH_MS,
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-hand text-4xl font-bold">Autopilot</h1>
        <p className="text-sm text-muted-foreground">
          Feeds edit every new video once. Finished videos wait here in a folder for each preset:
          ready to post, or with the agent&apos;s doubts for you to look at.
        </p>
      </div>
      <Tabs value={tab} onValueChange={(next) => router.replace(pileHref(next, null))}>
        <TabsList>
          <TabsTrigger value="ready">Ready ({counts.ready})</TabsTrigger>
          <TabsTrigger value="look">Needs a look ({counts.look})</TabsTrigger>
          <TabsTrigger value="feeds">Feeds</TabsTrigger>
        </TabsList>
        <TabsContent value="ready">
          <Pile name="ready" folder={folder} inside={inside} onChange={refreshCounts} />
        </TabsContent>
        <TabsContent value="look">
          <Pile name="look" folder={folder} inside={inside} onChange={refreshCounts} />
        </TabsContent>
        <TabsContent value="feeds">
          <Feeds />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Pile({
  name,
  folder,
  inside,
  onChange,
}: {
  name: PileName;
  folder: string | null;
  inside: number | null;
  onChange: () => void;
}) {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  // The folders made inside presets' folders; null until they have come.
  const [made, setMade] = useState<PileFolder[] | null>(null);
  // Whether the open folder was opened from the folders here: then "All folders" steps
  // back in the browser's history rather than adding to it.
  const openedHere = useRef(false);

  useEffect(() => {
    // Back on the folders (the browser's back button too): nothing is open from here.
    if (folder === null) openedHere.current = false;
  }, [folder]);

  function open(preset: string) {
    openedHere.current = true;
    router.push(pileHref(name, preset));
  }

  function close() {
    if (openedHere.current) router.back();
    else router.replace(pileHref(name, null));
  }

  const reload = usePolling(
    () =>
      api
        .pile(name)
        .then((next) => setJobs((current) => keepIfSame(current, next)))
        .catch((err) => toast.error(errorText(err))),
    REFRESH_MS,
  );

  const loadMade = useCallback(
    () =>
      api
        .pileFolders()
        .then(setMade)
        .catch(() => setMade((current) => current ?? [])),
    [],
  );
  useEffect(() => {
    void loadMade();
  }, [loadMade]);

  function goInside(id: number | null) {
    if (folder === null) return;
    // The folders are no longer one step back: "All folders" goes there directly.
    openedHere.current = false;
    router.push(pileHref(name, folder, id));
  }

  async function move(outputId: number, folderId: number | null, where: string) {
    try {
      await api.moveOutput(outputId, folderId);
      setJobs(
        (current) =>
          current?.map((j) =>
            j.outputs[0]?.id === outputId
              ? { ...j, outputs: j.outputs.map((o, i) => (i ? o : { ...o, folder_id: folderId })) }
              : j,
          ) ?? null,
      );
      toast.success(`Moved to ${where}`);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  useEffect(() => {
    api
      .presets()
      .then((list) => setLabels(Object.fromEntries(list.map((p) => [p.name, p.label]))))
      .catch(() => {});
  }, []);

  async function decide(outputId: number, verdict: Verdict, message: string) {
    try {
      await api.verdict(outputId, verdict);
      setJobs((current) => current?.filter((j) => j.outputs[0]?.id !== outputId) ?? null);
      toast.success(message);
      onChange();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (jobs === null) return <p className="text-sm text-muted-foreground">Loading...</p>;
  const all = folders(jobs);
  // The name the videos' owner gave the preset, sent with each video; the viewer's own
  // presets only for videos from an older server.
  const label = (preset: string) =>
    all.find((f) => f.preset === preset)?.jobs[0]?.outputs[0]?.preset_label ||
    labels[preset] ||
    preset;

  if (folder !== null) {
    const opened = all.find((f) => f.preset === folder)?.jobs ?? [];
    const mine = made ?? [];
    const known = new Map(mine.map((f) => [f.id, f]));
    const gone = made !== null && inside !== null && !known.has(inside);
    const here = inside !== null && known.has(inside) ? inside : null;
    const crumbs = trail(mine, here);
    const title = crumbs.at(-1)?.name ?? label(folder);
    const children = subfolders(mine, folder, here);
    const direct = opened.filter((j) => placeOf(j, known) === here);
    const paths = folderPaths(mine, folder);
    return (
      <div className="space-y-4 pt-2">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={close}>
            <ArrowLeft /> All folders
          </Button>
          {crumbs.length > 0 && (
            <nav aria-label="Folders" className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
              <button
                type="button"
                className="min-h-11 rounded px-1 underline-offset-2 hover:underline"
                onClick={() => goInside(null)}
              >
                {label(folder)}
              </button>
              {crumbs.map((c, i) => (
                <span key={c.id} className="flex min-w-0 items-center gap-1">
                  <ChevronRight className="size-4 shrink-0" aria-hidden />
                  {i === crumbs.length - 1 ? (
                    <span className="font-semibold break-words" aria-current="page">
                      {c.name}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="min-h-11 rounded px-1 break-words underline-offset-2 hover:underline"
                      onClick={() => goInside(c.id)}
                    >
                      {c.name}
                    </button>
                  )}
                </span>
              ))}
            </nav>
          )}
        </div>
        {gone ? (
          <div className="space-y-3 py-6">
            <p className="text-sm text-muted-foreground">This folder isn&apos;t here any more.</p>
            <Button variant="outline" onClick={() => router.replace(pileHref(name, folder))}>
              Open {label(folder)}
            </Button>
          </div>
        ) : (
          <>
            <FolderTools
              key={here ?? "top"}
              preset={folder}
              here={crumbs.at(-1) ?? null}
              up={crumbs.at(-2)?.name ?? label(folder)}
              onMade={(f) => setMade((current) => [...(current ?? []), f])}
              onRenamed={(f) =>
                setMade((current) => current?.map((x) => (x.id === f.id ? f : x)) ?? null)
              }
              onDeleted={(f) => {
                void loadMade();
                reload();
                router.replace(pileHref(name, folder, f.parent_id));
              }}
            />
            {children.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {children.map((c) => {
                  const inner = within(mine, c.id);
                  const held = opened.filter((j) => {
                    const at = placeOf(j, known);
                    return at !== null && inner.has(at);
                  });
                  return (
                    <FolderTile
                      key={c.id}
                      label={c.name}
                      jobs={held}
                      onOpen={() => goInside(c.id)}
                    />
                  );
                })}
              </div>
            )}
            {direct.length > 0 ? (
              <PresetGroup
                label={title}
                jobs={direct}
                pile={name}
                decide={decide}
                mover={
                  paths.length > 0
                    ? (job) => (
                        <MoveTo
                          job={job}
                          at={placeOf(job, known)}
                          top={label(folder)}
                          paths={paths}
                          onMove={move}
                        />
                      )
                    : undefined
                }
              />
            ) : (
              <p className="py-6 text-sm text-muted-foreground">
                {children.length > 0 ? `No videos straight in ${title}.` : `Nothing in ${title}.`}
              </p>
            )}
          </>
        )}
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        {name === "ready" ? "Nothing waiting to be posted." : "Nothing needs a look."}
      </p>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 pt-2 sm:grid-cols-3 lg:grid-cols-4">
      {all.map((f) => (
        <FolderTile
          key={f.preset}
          label={label(f.preset)}
          jobs={f.jobs}
          onOpen={() => open(f.preset)}
        />
      ))}
    </div>
  );
}

/** A preset's folder (or one made inside it): its name, how many videos wait in it, and the
 * newest one's picture. */
function FolderTile({ label, jobs, onOpen }: { label: string; jobs: Job[]; onOpen: () => void }) {
  const cover = jobs.map((j) => j.outputs[0]).find((o) => o?.has_thumbnail);
  const count = `${jobs.length} ${jobs.length === 1 ? "video" : "videos"}`;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open ${label}, ${count}`}
      className="group flex min-w-0 flex-col overflow-hidden rounded-md border-2 border-foreground/70 bg-card text-left shadow-sm transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <span className="relative block aspect-[4/5] w-full overflow-hidden bg-muted">
        {cover ? (
          <Image
            src={thumbnailUrl(cover, 480)}
            alt=""
            fill
            unoptimized
            sizes="(min-width: 1024px) 25vw, (min-width: 640px) 33vw, 50vw"
            className="object-cover"
          />
        ) : (
          <Folder className="absolute inset-0 m-auto size-12 text-muted-foreground" aria-hidden />
        )}
        <span className="absolute top-2 right-2 rounded-full bg-black/70 px-2 py-0.5 text-sm font-semibold text-white">
          {jobs.length}
        </span>
      </span>
      <span className="flex min-h-11 items-start gap-2 p-2 text-sm font-semibold break-words">
        <FolderOpen className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span className="min-w-0">{label}</span>
      </span>
    </button>
  );
}

/** Make a folder here; and, inside a made folder, rename or delete it. */
function FolderTools({
  preset,
  here,
  up,
  onMade,
  onRenamed,
  onDeleted,
}: {
  preset: string;
  here: PileFolder | null;
  /** The name of the folder `here` is in. */
  up: string;
  onMade: (f: PileFolder) => void;
  onRenamed: (f: PileFolder) => void;
  onDeleted: (f: PileFolder) => void;
}) {
  const [editing, setEditing] = useState<"new" | "rename" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  function begin(what: "new" | "rename") {
    setEditing(what);
    setText(what === "rename" && here ? here.name : "");
  }

  async function save() {
    setBusy(true);
    try {
      if (editing === "rename" && here) {
        onRenamed(await api.renamePileFolder(here.id, text));
        toast.success("Renamed");
      } else {
        const f = await api.makePileFolder(preset, here?.id ?? null, text);
        onMade(f);
        toast.success(`Made the folder ${f.name}`);
      }
      setEditing(null);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!here) return;
    if (!window.confirm(`Delete the folder "${here.name}"? Its videos move up into ${up}.`)) return;
    try {
      await api.deletePileFolder(here.id);
      toast.success(`Deleted ${here.name}`);
      onDeleted(here);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (editing) {
    return (
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Input
          autoFocus
          aria-label={editing === "rename" ? "New name" : "Folder name"}
          placeholder={editing === "rename" ? "New name" : "Folder name"}
          maxLength={60}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-w-0 flex-1 sm:max-w-xs"
        />
        <Button type="submit" disabled={busy || !text.trim()}>
          {editing === "rename" ? "Save" : "Make folder"}
        </Button>
        <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
          Cancel
        </Button>
      </form>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" onClick={() => begin("new")}>
        <FolderPlus /> New folder
      </Button>
      {here && (
        <>
          <Button variant="ghost" onClick={() => begin("rename")}>
            <Pencil /> Rename
          </Button>
          <Button variant="ghost" onClick={remove}>
            <Trash2 /> Delete folder
          </Button>
        </>
      )}
    </div>
  );
}

/** Which folder inside its preset's folder a video sits in, to move it to another. */
function MoveTo({
  job,
  at,
  top,
  paths,
  onMove,
}: {
  job: Job;
  at: number | null;
  top: string;
  paths: { id: number; path: string }[];
  onMove: (outputId: number, folderId: number | null, where: string) => void;
}) {
  const output = job.outputs[0];
  return (
    <select
      aria-label="Move to folder"
      className="min-h-11 max-w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm"
      value={at ?? ""}
      onChange={(e) => {
        const id = e.target.value ? Number(e.target.value) : null;
        const where = paths.find((p) => p.id === id)?.path ?? top;
        onMove(output.id, id, where);
      }}
    >
      <option value="">{top} (no folder)</option>
      {paths.map((p) => (
        <option key={p.id} value={p.id}>
          {p.path}
        </option>
      ))}
    </select>
  );
}

function PresetGroup({
  label,
  jobs,
  pile,
  decide,
  mover,
}: {
  label: string;
  jobs: Job[];
  pile: PileName;
  decide: (outputId: number, verdict: Verdict, message: string) => void;
  /** A "move to folder" control for a video, when there are folders to move it to. */
  mover?: (job: Job) => React.ReactNode;
}) {
  const [shown, setShown] = useState(PAGE);
  const more = jobs.length - shown;
  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 font-hand text-2xl font-bold break-words">
        <FolderOpen className="size-5 shrink-0" aria-hidden />
        {label} <span className="text-base font-normal text-muted-foreground">({jobs.length})</span>
      </h2>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {jobs.slice(0, shown).map((job) => {
          const output = job.outputs[0];
          return (
            <OutputCard
              key={output.id}
              job={job}
              output={output}
              actions={
                <>
                  {pile === "look" ? (
                    <Button
                      variant="outline"
                      onClick={() => decide(output.id, "approved", "Moved to ready")}
                    >
                      <Check /> Looks fine
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => decide(output.id, "posted", "Marked as posted")}
                    >
                      <Send /> Posted
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    onClick={() => decide(output.id, "dismissed", "Dismissed")}
                  >
                    <X /> Dismiss
                  </Button>
                  {mover?.(job)}
                </>
              }
            />
          );
        })}
      </div>
      {more > 0 && (
        <Button
          variant="outline"
          className="w-full sm:w-auto"
          onClick={() => setShown((n) => n + PAGE)}
        >
          Show more {label} ({more} left)
        </Button>
      )}
    </section>
  );
}

function Feeds() {
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [adding, setAdding] = useState(false);

  usePolling(
    () =>
      api
        .feeds()
        .then((next) => setFeeds((current) => keepIfSame(current, next)))
        .catch((err) => toast.error(errorText(err))),
    REFRESH_MS,
  );

  const replace = (feed: Feed) =>
    setFeeds((current) => current?.map((f) => (f.id === feed.id ? feed : f)) ?? null);

  return (
    <div className="space-y-4 pt-2">
      <Button onClick={() => setAdding(true)}>
        <Plus /> Add a feed
      </Button>
      {feeds?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No feeds yet. Add a folder on this computer, or a playlist or channel link.
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {feeds?.map((feed) => (
          <FeedCard
            key={feed.id}
            feed={feed}
            onChange={replace}
            onDelete={() => setFeeds((c) => c?.filter((f) => f.id !== feed.id) ?? null)}
          />
        ))}
      </div>
      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Add a feed</DialogTitle>
          </DialogHeader>
          <FeedForm
            onSaved={(feed) => {
              setAdding(false);
              setFeeds((current) => [...(current ?? []), feed]);
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
