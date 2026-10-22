"use client";

import { Check, Plus, Send, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { FeedCard } from "@/components/feed-card";
import { FeedForm } from "@/components/feed-form";
import { OutputCard } from "@/components/output-card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorText, type Feed, type Job, type Verdict } from "@/lib/api";

type PileName = "ready" | "look";

const REFRESH_MS = 15000;

export default function AutopilotPage() {
  const [counts, setCounts] = useState({ ready: 0, look: 0 });
  const refreshCounts = useCallback(() => {
    api
      .pileCounts()
      .then(setCounts)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshCounts();
    const timer = setInterval(refreshCounts, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refreshCounts]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-hand text-4xl font-bold">Autopilot</h1>
        <p className="text-sm text-muted-foreground">
          Feeds edit every new video once. Finished videos wait here: ready to post, or with the
          agent&apos;s doubts for you to look at.
        </p>
      </div>
      <Tabs defaultValue="ready">
        <TabsList>
          <TabsTrigger value="ready">Ready ({counts.ready})</TabsTrigger>
          <TabsTrigger value="look">Needs a look ({counts.look})</TabsTrigger>
          <TabsTrigger value="feeds">Feeds</TabsTrigger>
        </TabsList>
        <TabsContent value="ready">
          <Pile name="ready" onChange={refreshCounts} />
        </TabsContent>
        <TabsContent value="look">
          <Pile name="look" onChange={refreshCounts} />
        </TabsContent>
        <TabsContent value="feeds">
          <Feeds />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Pile({ name, onChange }: { name: PileName; onChange: () => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);

  const load = useCallback(() => {
    api
      .pile(name)
      .then(setJobs)
      .catch((err) => toast.error(errorText(err)));
  }, [name]);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

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
  if (jobs.length === 0) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        {name === "ready" ? "Nothing waiting to be posted." : "Nothing needs a look."}
      </p>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-6 pt-2 md:grid-cols-2">
      {jobs.map((job) => {
        const output = job.outputs[0];
        return (
          <OutputCard
            key={output.id}
            job={job}
            output={output}
            actions={
              <>
                {name === "look" ? (
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
                <Button variant="ghost" onClick={() => decide(output.id, "dismissed", "Dismissed")}>
                  <X /> Dismiss
                </Button>
              </>
            }
          />
        );
      })}
    </div>
  );
}

function Feeds() {
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    api
      .feeds()
      .then(setFeeds)
      .catch((err) => toast.error(errorText(err)));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

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
