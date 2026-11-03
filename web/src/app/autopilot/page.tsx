"use client";

import { ArrowLeft, Check, Folder, FolderOpen, Plus, Send, X } from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { FeedCard } from "@/components/feed-card";
import { FeedForm } from "@/components/feed-form";
import { OutputCard } from "@/components/output-card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePolling } from "@/hooks/use-polling";
import { api, errorText, thumbnailUrl, type Feed, type Job, type Verdict } from "@/lib/api";
import { folders, pileHref } from "@/lib/pile-folders.mjs";
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
          <Pile name="ready" folder={folder} onChange={refreshCounts} />
        </TabsContent>
        <TabsContent value="look">
          <Pile name="look" folder={folder} onChange={refreshCounts} />
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
  onChange,
}: {
  name: PileName;
  folder: string | null;
  onChange: () => void;
}) {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
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

  usePolling(
    () =>
      api
        .pile(name)
        .then((next) => setJobs((current) => keepIfSame(current, next)))
        .catch((err) => toast.error(errorText(err))),
    REFRESH_MS,
  );

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
    const opened = all.find((f) => f.preset === folder);
    return (
      <div className="space-y-4 pt-2">
        <Button variant="outline" onClick={close}>
          <ArrowLeft /> All folders
        </Button>
        {opened ? (
          <PresetGroup label={label(folder)} jobs={opened.jobs} pile={name} decide={decide} />
        ) : (
          <p className="py-6 text-sm text-muted-foreground">Nothing left in {label(folder)}.</p>
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

/** A preset's folder: its name, how many videos wait in it, and the newest one's picture. */
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

function PresetGroup({
  label,
  jobs,
  pile,
  decide,
}: {
  label: string;
  jobs: Job[];
  pile: PileName;
  decide: (outputId: number, verdict: Verdict, message: string) => void;
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
