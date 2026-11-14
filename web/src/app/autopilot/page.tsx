"use client";

import {
  ArrowLeft,
  Check,
  CheckSquare,
  ChevronRight,
  ClipboardPaste,
  Folder,
  FolderOpen,
  FolderPlus,
  Pencil,
  Plus,
  Scissors,
  Send,
  Trash2,
  X,
} from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { FeedCard } from "@/components/feed-card";
import { jobLabel } from "@/components/job-status";
import { FeedForm } from "@/components/feed-form";
import { OutputCard } from "@/components/output-card";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { type Clip, clipSays, loadClip, pasteProblem, saveClip } from "@/lib/pile-clip.mjs";
import { keepIfSame } from "@/lib/poller.mjs";
import { cn } from "@/lib/utils";

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
  // What was cut, kept for the browser tab so it survives tabs, folders and reloads.
  const [clip, setClipState] = useState<Clip | null>(() => loadClip());
  const setClip = useCallback((next: Clip | null) => {
    saveClip(next);
    setClipState(next);
  }, []);
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
          <Pile
            name="ready"
            folder={folder}
            inside={inside}
            onChange={refreshCounts}
            clip={clip}
            setClip={setClip}
          />
        </TabsContent>
        <TabsContent value="look">
          <Pile
            name="look"
            folder={folder}
            inside={inside}
            onChange={refreshCounts}
            clip={clip}
            setClip={setClip}
          />
        </TabsContent>
        <TabsContent value="feeds">
          <Feeds />
        </TabsContent>
      </Tabs>
    </div>
  );
}

type Picked = { videos: Set<number>; folders: Set<number> };
const NONE: Picked = { videos: new Set(), folders: new Set() };

// Whether a key press is meant for a text field rather than for the page's shortcuts.
function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

function Pile({
  name,
  folder,
  inside,
  onChange,
  clip,
  setClip,
}: {
  name: PileName;
  folder: string | null;
  inside: number | null;
  onChange: () => void;
  clip: Clip | null;
  setClip: (clip: Clip | null) => void;
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

  // Put videos in a folder on the page at once (the server has already moved them).
  function place(ids: number[], folderId: number | null) {
    const moved = new Set(ids);
    setJobs(
      (current) =>
        current?.map((j) =>
          moved.has(j.outputs[0]?.id)
            ? { ...j, outputs: j.outputs.map((o, i) => (i ? o : { ...o, folder_id: folderId })) }
            : j,
        ) ?? null,
    );
  }

  async function move(outputId: number, folderId: number | null, where: string) {
    try {
      await api.moveOutput(outputId, folderId);
      place([outputId], folderId);
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
    return (
      <OpenFolder
        key={`${folder}:${inside ?? ""}`}
        name={name}
        folder={folder}
        inside={inside}
        opened={all.find((f) => f.preset === folder)?.jobs ?? []}
        made={made}
        label={label}
        clip={clip}
        setClip={setClip}
        close={close}
        goInside={goInside}
        decide={decide}
        move={move}
        afterPaste={(pasted, to) => {
          place(pasted.videos, to);
          setMade(
            (current) =>
              current?.map((f) => (pasted.folders.includes(f.id) ? { ...f, parent_id: to } : f)) ??
              null,
          );
          reload();
          void loadMade();
        }}
        onMade={(f) => setMade((current) => [...(current ?? []), f])}
        onRenamed={(f) =>
          setMade((current) => current?.map((x) => (x.id === f.id ? f : x)) ?? null)
        }
        onDeleted={(f) => {
          void loadMade();
          reload();
          router.replace(pileHref(name, folder, f.parent_id));
        }}
        onGone={() => router.replace(pileHref(name, folder))}
      />
    );
  }

  const bar = clip && (
    <ActionBar>
      <span className="min-w-0 text-sm">
        <Scissors className="mr-1 inline size-4" aria-hidden />
        {clipSays(clip)} cut. {pasteProblem(made ?? [], clip, null, null, label(clip.preset))}.
      </span>
      <Button variant="ghost" onClick={() => setClip(null)}>
        <X /> Cancel
      </Button>
    </ActionBar>
  );
  if (jobs.length === 0) {
    return (
      <>
        <p className="py-6 text-sm text-muted-foreground">
          {name === "ready" ? "Nothing waiting to be posted." : "Nothing needs a look."}
        </p>
        {bar}
      </>
    );
  }
  return (
    <div className={cn("pt-2", clip && "pb-28")}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {all.map((f) => (
          <FolderTile
            key={f.preset}
            label={label(f.preset)}
            jobs={f.jobs}
            onOpen={() => open(f.preset)}
          />
        ))}
      </div>
      {bar}
    </div>
  );
}

/** A preset's folder, or a folder made in it, open: its folders and videos, the tools to
 * make, rename and delete folders, and cut and paste: Select, tick videos and folders, Cut,
 * open another folder of the same preset, Paste (Ctrl/Cmd+X, Ctrl/Cmd+V and Esc too). */
function OpenFolder({
  name,
  folder,
  inside,
  opened,
  made,
  label,
  clip,
  setClip,
  close,
  goInside,
  decide,
  move,
  afterPaste,
  onMade,
  onRenamed,
  onDeleted,
  onGone,
}: {
  name: PileName;
  folder: string;
  inside: number | null;
  opened: Job[];
  made: PileFolder[] | null;
  label: (preset: string) => string;
  clip: Clip | null;
  setClip: (clip: Clip | null) => void;
  close: () => void;
  goInside: (id: number | null) => void;
  decide: (outputId: number, verdict: Verdict, message: string) => void;
  move: (outputId: number, folderId: number | null, where: string) => void;
  afterPaste: (pasted: Clip, to: number | null) => void;
  onMade: (f: PileFolder) => void;
  onRenamed: (f: PileFolder) => void;
  onDeleted: (f: PileFolder) => void;
  onGone: () => void;
}) {
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<Picked>(NONE);
  const [pasting, setPasting] = useState(false);

  const mine = made ?? [];
  const known = new Map(mine.map((f) => [f.id, f]));
  const gone = made !== null && inside !== null && !known.has(inside);
  const here = inside !== null && known.has(inside) ? inside : null;
  const crumbs = trail(mine, here);
  const title = crumbs.at(-1)?.name ?? label(folder);
  const children = subfolders(mine, folder, here);
  const direct = opened.filter((j) => placeOf(j, known) === here);
  const paths = folderPaths(mine, folder);
  const count = picked.videos.size + picked.folders.size;
  const problem = clip ? pasteProblem(mine, clip, folder, here, label(clip.preset)) : null;

  function toggle(kind: keyof Picked, id: number) {
    setPicked((current) => {
      const next = new Set(current[kind]);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...current, [kind]: next };
    });
  }

  function selectAll() {
    setPicked({
      videos: new Set(direct.flatMap((j) => (j.outputs[0] ? [j.outputs[0].id] : []))),
      folders: new Set(children.map((c) => c.id)),
    });
  }

  function stopSelecting() {
    setSelecting(false);
    setPicked(NONE);
  }

  function cut() {
    if (count === 0) return;
    const next: Clip = {
      preset: folder,
      videos: [...picked.videos],
      folders: [...picked.folders],
      from: title,
    };
    setClip(next);
    stopSelecting();
    toast.success(`Cut ${clipSays(next)}: open a folder and paste it there`);
  }

  async function paste() {
    if (!clip || problem || pasting) return;
    setPasting(true);
    try {
      await api.pastePile(clip.preset, clip.videos, clip.folders, here);
      toast.success(`Pasted ${clipSays(clip)} into ${title}`);
      setClip(null);
      afterPaste(clip, here);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setPasting(false);
    }
  }

  // The keyboard on a computer: Ctrl/Cmd+X cuts what is ticked, Ctrl/Cmd+V pastes here,
  // Ctrl/Cmd+A ticks everything while selecting, Esc stops selecting or drops the cut.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (typing(e.target)) return;
      const key = e.key.toLowerCase();
      const mod = e.ctrlKey || e.metaKey;
      if (mod && key === "x" && selecting && count > 0) {
        e.preventDefault();
        cut();
      } else if (mod && key === "v" && clip && !problem) {
        e.preventDefault();
        void paste();
      } else if (mod && key === "a" && selecting) {
        e.preventDefault();
        selectAll();
      } else if (key === "escape") {
        if (selecting) stopSelecting();
        else if (clip) setClip(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const barShown = selecting || clip !== null;
  return (
    <div className={cn("space-y-4 pt-2", barShown && "pb-28")}>
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
          <Button variant="outline" onClick={onGone}>
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
            onMade={onMade}
            onRenamed={onRenamed}
            onDeleted={onDeleted}
          >
            {!selecting && (direct.length > 0 || children.length > 0) && (
              <Button variant="outline" onClick={() => setSelecting(true)}>
                <CheckSquare /> Select
              </Button>
            )}
          </FolderTools>
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
                    selecting={selecting}
                    picked={picked.folders.has(c.id)}
                    onPick={() => toggle("folders", c.id)}
                    cut={clip?.folders.includes(c.id) ?? false}
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
              selecting={selecting}
              picked={picked.videos}
              onPick={(id) => toggle("videos", id)}
              cut={new Set(clip?.preset === folder ? clip.videos : [])}
              mover={
                paths.length > 0 && !selecting
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
      {selecting ? (
        <ActionBar>
          <span className="text-sm font-semibold" aria-live="polite">
            {count} selected
          </span>
          <Button variant="outline" onClick={selectAll}>
            Select all
          </Button>
          <Button onClick={cut} disabled={count === 0}>
            <Scissors /> Cut
          </Button>
          <Button variant="ghost" onClick={stopSelecting}>
            <X /> Cancel
          </Button>
        </ActionBar>
      ) : (
        clip && (
          <ActionBar>
            <span className="min-w-0 text-sm">
              <Scissors className="mr-1 inline size-4" aria-hidden />
              {clipSays(clip)} cut{clip.from ? ` from ${clip.from}` : ""}
              {problem && <span className="block text-xs text-muted-foreground">{problem}</span>}
            </span>
            <Button onClick={paste} disabled={!!problem || pasting}>
              <ClipboardPaste /> {pasting ? "Pasting..." : `Paste in ${title}`}
            </Button>
            <Button variant="ghost" onClick={() => setClip(null)}>
              <X /> Cancel
            </Button>
          </ActionBar>
        )
      )}
    </div>
  );
}

/** A bar fixed to the bottom of the screen for what is selected or cut. */
function ActionBar({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="region"
      aria-label="Cut and paste"
      className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-foreground bg-card px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-lg"
    >
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

/** A preset's folder (or one made inside it): its name, how many videos wait in it, and the
 * newest one's picture. While selecting, a tap ticks it instead of opening it; a cut one is
 * shown faded. */
function FolderTile({
  label,
  jobs,
  onOpen,
  selecting = false,
  picked = false,
  onPick,
  cut = false,
}: {
  label: string;
  jobs: Job[];
  onOpen: () => void;
  selecting?: boolean;
  picked?: boolean;
  onPick?: () => void;
  cut?: boolean;
}) {
  const cover = jobs.map((j) => j.outputs[0]).find((o) => o?.has_thumbnail);
  const count = `${jobs.length} ${jobs.length === 1 ? "video" : "videos"}`;
  return (
    <button
      type="button"
      onClick={selecting && onPick ? onPick : onOpen}
      aria-label={selecting ? `Select ${label}, ${count}` : `Open ${label}, ${count}`}
      aria-pressed={selecting ? picked : undefined}
      className={cn(
        "group flex min-w-0 flex-col overflow-hidden rounded-md border-2 border-foreground/70 bg-card text-left shadow-sm transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-2",
        picked && "ring-4 ring-primary",
        cut && "opacity-50",
      )}
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
        {selecting && (
          <span
            className={cn(
              "absolute top-2 left-2 flex size-7 items-center justify-center rounded-md border-2 border-foreground bg-card",
              picked && "bg-primary text-primary-foreground",
            )}
            aria-hidden
          >
            {picked && <Check className="size-5" />}
          </span>
        )}
        <span className="absolute top-2 right-2 rounded-full bg-black/70 px-2 py-0.5 text-sm font-semibold text-white">
          {jobs.length}
        </span>
        {cut && (
          <span className="absolute bottom-2 left-2 rounded-full bg-black/70 px-2 py-0.5 text-xs font-semibold text-white">
            Cut
          </span>
        )}
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
  children,
}: {
  preset: string;
  here: PileFolder | null;
  /** The name of the folder `here` is in. */
  up: string;
  onMade: (f: PileFolder) => void;
  onRenamed: (f: PileFolder) => void;
  onDeleted: (f: PileFolder) => void;
  /** More buttons for the same row. */
  children?: React.ReactNode;
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
      {children}
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
  selecting = false,
  picked,
  onPick,
  cut,
}: {
  label: string;
  jobs: Job[];
  pile: PileName;
  decide: (outputId: number, verdict: Verdict, message: string) => void;
  /** A "move to folder" control for a video, when there are folders to move it to. */
  mover?: (job: Job) => React.ReactNode;
  /** Ticking videos to cut them: which are ticked, and the tick. */
  selecting?: boolean;
  picked?: Set<number>;
  onPick?: (outputId: number) => void;
  /** Videos cut and not pasted yet, shown faded. */
  cut?: Set<number>;
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
          const ticked = picked?.has(output.id) ?? false;
          return (
            <div
              key={output.id}
              className={cn(
                "min-w-0 space-y-2 rounded-md",
                ticked && "ring-4 ring-primary ring-offset-2",
                cut?.has(output.id) && "opacity-50",
              )}
            >
              {selecting && (
                <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border-2 border-foreground bg-card px-3 text-sm font-semibold">
                  <Checkbox
                    checked={ticked}
                    onCheckedChange={() => onPick?.(output.id)}
                    aria-label={`Select ${output.title || jobLabel(job)}`}
                    className="size-6"
                  />
                  {ticked ? "Selected" : "Select"}
                </label>
              )}
              {cut?.has(output.id) && !selecting && (
                <p className="text-xs font-semibold text-muted-foreground">
                  Cut: paste it in another folder
                </p>
              )}
              <OutputCard
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
            </div>
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
