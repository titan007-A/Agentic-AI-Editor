"use client";

import { AlertTriangle, CalendarClock, Check, Clock, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type BufferPost, type BufferPosts } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Said on the window when posts changed (a reel was sent to Buffer, a post sent again). */
export const POSTS_CHANGED = "aiedit-buffer-posts";

export function postsChanged() {
  window.dispatchEvent(new Event(POSTS_CHANGED));
}

type Tone = "ok" | "wait" | "bad" | "muted";

const TONES: Record<Tone, string> = {
  ok: "border-[var(--teal)] text-[var(--teal)]",
  wait: "border-muted-foreground/50 text-muted-foreground",
  bad: "border-destructive text-destructive",
  muted: "border-muted-foreground/30 text-muted-foreground/80",
};

export function statusOf(post: BufferPost): { label: string; tone: Tone } {
  switch (post.status) {
    case "sent":
      return { label: "Posted", tone: "ok" };
    case "error":
      return { label: "Failed", tone: "bad" };
    case "scheduled":
      return { label: post.auto ? "Trying again" : "Scheduled", tone: "wait" };
    case "sending":
      return { label: "Going out", tone: "wait" };
    case "needs_approval":
      return { label: "Waiting for approval", tone: "wait" };
    case "draft":
      return { label: "Draft in Buffer", tone: "muted" };
    case "unknown":
      return { label: "Not out yet", tone: "bad" };
    case "gone":
      return { label: "Removed", tone: "muted" };
    default:
      return { label: post.status, tone: "muted" };
  }
}

function shortTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** When it went out, or is to go out. */
export function timeOf(post: BufferPost): string {
  if (post.status === "sent" && post.sent_at) return shortTime(post.sent_at);
  if (post.due_at) return shortTime(post.due_at);
  return "";
}

/** Why it failed, in words that say what happened. */
export function whyFailed(post: BufferPost): string {
  const said = `${post.error} ${post.raw_error}`.toLowerCase();
  const media = said.includes("media") || said.includes("container") || said.includes("video");
  if (post.instagram_cut)
    return "Instagram's download of the video from the editor stopped before the end.";
  if (media) return "Instagram couldn't take the video. Sending it again usually works.";
  return post.error || "Buffer says it failed.";
}

function Chip({ post }: { post: BufferPost }) {
  const { label, tone } = statusOf(post);
  const Icon = tone === "ok" ? Check : tone === "bad" ? AlertTriangle : Clock;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold",
        TONES[tone],
      )}
    >
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  );
}

/** Send again now, at a time, or take it out of Buffer: the buttons of a failed post. */
function FailedActions({
  post,
  onDone,
  compact = false,
}: {
  post: BufferPost;
  onDone: (next: BufferPost | null) => void;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [at, setAt] = useState("");

  async function again(dueAt: string | null) {
    setBusy(true);
    try {
      const next = await api.retryBufferPost(post.id, dueAt);
      toast.success(
        dueAt ? `Scheduled again for ${post.channel}` : `Sent again to ${post.channel}`,
      );
      setPicking(false);
      onDone(next);
      postsChanged();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Remove this failed post from ${post.channel}'s Buffer queue?`)) return;
    setBusy(true);
    try {
      await api.dismissBufferPost(post.id);
      toast.success("Removed from Buffer");
      onDone(null);
      postsChanged();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy} onClick={() => again(null)}>
          <Send /> {busy ? "Sending..." : "Send again now"}
        </Button>
        {!compact && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setPicking((p) => !p)}>
            <CalendarClock /> At a time
          </Button>
        )}
        {!compact && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={remove}>
            <Trash2 /> Remove
          </Button>
        )}
      </div>
      {picking && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (at) again(new Date(at).toISOString());
          }}
        >
          <div className="space-y-1">
            <Label htmlFor={`again-${post.id}`}>When</Label>
            <Input
              id={`again-${post.id}`}
              type="datetime-local"
              value={at}
              onChange={(e) => setAt(e.target.value)}
            />
          </div>
          <Button size="sm" type="submit" disabled={busy || !at}>
            Schedule
          </Button>
        </form>
      )}
    </div>
  );
}

/** One post as a line: its state, account and time; a failed one says why, with buttons. */
export function PostLine({
  post,
  onChange,
  compact = false,
}: {
  post: BufferPost;
  onChange: (next: BufferPost | null) => void;
  compact?: boolean;
}) {
  const time = timeOf(post);
  return (
    <li className="space-y-1.5" data-post={post.id}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <Chip post={post} />
        <span className="min-w-0 font-semibold break-words">{post.channel || "Buffer"}</span>
        {time && <span className="text-muted-foreground">{time}</span>}
        {post.auto && post.status !== "error" && (
          <span className="text-xs text-muted-foreground">sent again by itself</span>
        )}
      </div>
      {!compact && post.title && (
        <p className="line-clamp-2 text-sm break-words text-muted-foreground">{post.title}</p>
      )}
      {post.status === "error" && (
        <>
          <p className="text-sm break-words text-destructive">{whyFailed(post)}</p>
          {post.auto && (
            <p className="text-xs text-muted-foreground">
              The editor already sent it again once by itself; this was that second try.
            </p>
          )}
          <FailedActions post={post} onDone={onChange} compact={compact} />
        </>
      )}
      {post.status === "unknown" && (
        <p className="text-sm text-muted-foreground">
          Still not out long after its time: look in Buffer (is the queue paused?).
        </p>
      )}
    </li>
  );
}

/** A reel's posts, under its buttons: whether each went out, and a failed one sent again. */
export function ReelPosts({ outputId, posts }: { outputId: number; posts: BufferPost[] }) {
  // Newer than the reel's own list (sent, or sent again, here): shown until the reel's
  // list itself changes.
  const [local, setLocal] = useState<{ base: BufferPost[]; list: BufferPost[] } | null>(null);
  const shown = local && local.base === posts ? local.list : posts;
  const setShown = (change: (all: BufferPost[]) => BufferPost[]) =>
    setLocal({ base: posts, list: change(shown) });

  useEffect(() => {
    const refresh = () =>
      api
        .bufferPosts()
        .then(({ posts: mine }) => {
          const found = mine.filter((p) => p.output_id === outputId);
          if (found.length) setLocal({ base: posts, list: found });
        })
        .catch(() => {});
    window.addEventListener(POSTS_CHANGED, refresh);
    return () => window.removeEventListener(POSTS_CHANGED, refresh);
  }, [outputId, posts]);

  if (shown.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-semibold text-muted-foreground uppercase">In Buffer</p>
      <ul className="space-y-2">
        {shown.map((post) => (
          <PostLine
            key={post.id}
            post={post}
            compact
            onChange={(next) =>
              setShown((all) =>
                next
                  ? all.map((p) => (p.id === post.id ? next : p))
                  : all.filter((p) => p !== post),
              )
            }
          />
        ))}
      </ul>
    </div>
  );
}

/** The Buffer page's list: failed posts first, then the last three days'. */
export function BufferPostList() {
  const [state, setState] = useState<BufferPosts | null>(null);

  const load = useCallback(
    () =>
      api
        .bufferPosts()
        .then(setState)
        .catch((err) => toast.error(errorText(err))),
    [],
  );

  useEffect(() => {
    load();
    const every = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 60_000);
    window.addEventListener(POSTS_CHANGED, load);
    return () => {
      window.clearInterval(every);
      window.removeEventListener(POSTS_CHANGED, load);
    };
  }, [load]);

  async function setAuto(on: boolean) {
    try {
      await api.setBufferAutoRetry(on);
      setState((s) => (s ? { ...s, auto_retry: on } : s));
      toast.success(on ? "Failed posts are sent again once by themselves" : "Left to you");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (state === null) return <p className="text-sm text-muted-foreground">Loading posts...</p>;
  const failed = state.posts.filter((p) => p.status === "error");
  return (
    <section className="space-y-3" aria-labelledby="posts-heading">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h2 id="posts-heading" className="font-hand text-2xl font-bold">
          Posts
        </h2>
        {failed.length > 0 && (
          <span className="text-sm font-semibold text-destructive">{failed.length} failed</span>
        )}
      </div>
      <div className="flex items-start gap-3">
        <Switch
          id="auto-retry"
          checked={state.auto_retry}
          onCheckedChange={setAuto}
          aria-describedby="auto-retry-note"
        />
        <div className="space-y-0.5">
          <Label htmlFor="auto-retry">Send a failed post again by itself</Label>
          <p id="auto-retry-note" className="text-xs text-muted-foreground">
            When Instagram couldn&apos;t take a video, the editor sends it once more a few minutes
            later. If that fails too, it waits for you here.
          </p>
        </div>
      </div>
      {state.posts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Posts sent from the editor show here, with whether they went out.
        </p>
      ) : (
        <ul className="space-y-4">
          {state.posts.map((post) => (
            <PostLine key={post.id} post={post} onChange={() => load()} />
          ))}
        </ul>
      )}
    </section>
  );
}
