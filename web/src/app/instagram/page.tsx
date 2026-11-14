"use client";

import { ExternalLink, MessageCircle, RefreshCw, Send, Share2, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { usePolling } from "@/hooks/use-polling";
import { api, errorText, type IgChannel, type IgComment, type ReplyMode } from "@/lib/api";
import { cn } from "@/lib/utils";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";

const MODES: [ReplyMode, string][] = [
  ["off", "Off"],
  ["review", "Draft replies, I send them"],
  ["auto", "Reply by itself"],
];

// What a failed login says, in plain words.
const LOGIN_ERRORS: Record<string, string> = {
  access_denied: "The login was cancelled.",
  invalid_state: "That login link was old or opened twice. Try connecting again.",
  no_accounts_shared:
    "No Instagram account was shared. Connect again, pick the business portfolio that owns the account and tick the Instagram account.",
};

const STATUS: Record<string, string> = {
  new: "waiting for a draft",
  drafted: "reply ready",
  look: "needs you",
  failed: "couldn't send",
  sending: "sending",
  sent: "sent",
  skipped: "skipped",
};

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Instagram accounts connected through Meta's Unified Login, and their comments answered:
 * drafted by the model for a person to send, or sent by themselves within a limit. */
export default function InstagramPage() {
  const [channels, setChannels] = useState<IgChannel[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState<"open" | "sent" | "skipped">("open");
  const [comments, setComments] = useState<IgComment[] | null>(null);

  const loadChannels = useCallback(
    () =>
      api
        .igChannels()
        .then(setChannels)
        .catch((err) => toast.error(errorText(err))),
    [],
  );
  const loadComments = useCallback(
    () =>
      api
        .igComments(show)
        .then(setComments)
        .catch(() => {}),
    [show],
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("ig_connected");
    const failed = params.get("ig_error");
    if (connected) {
      const names =
        connected === "1"
          ? ""
          : ` ${connected
              .split(",")
              .map((n) => "@" + n)
              .join(", ")}`;
      toast.success(`Connected${names}`);
    }
    if (failed) toast.error(LOGIN_ERRORS[failed] ?? `Instagram said: ${failed}`);
    if (connected || failed) window.history.replaceState({}, "", "/instagram/");
    void loadChannels();
  }, [loadChannels]);

  useEffect(() => {
    void loadComments();
  }, [loadComments]);

  const answering = channels?.some((c) => c.reply_mode !== "off") ?? false;
  usePolling(
    () => {
      void loadComments();
      void loadChannels();
    },
    answering ? 30000 : null,
  );

  async function disconnect(id: string) {
    if (!window.confirm("Disconnect this account? Its comments here go too.")) return;
    setBusy(true);
    try {
      await api.removeIgChannel(id);
      setChannels((prev) => prev?.filter((c) => c.id !== id) ?? []);
      toast.success("Instagram account disconnected");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const replace = (next: IgChannel) =>
    setChannels((prev) => prev?.map((c) => (c.id === next.id ? next : c)) ?? null);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="space-y-1">
        <h1 className="font-hand text-4xl font-bold">Instagram</h1>
        <p className="text-sm text-muted-foreground">
          Connect Instagram Business or Creator accounts with your Instagram (or Facebook) login to
          post reels and answer comments. While the app is in testing, only accounts added as
          testers on our Meta app can connect.
        </p>
      </div>

      <Button asChild>
        <a href="/auth/instagram">
          <Share2 /> Connect Instagram account
        </a>
      </Button>

      {channels === null && <p className="text-sm text-muted-foreground">Loading...</p>}
      {channels !== null && channels.length === 0 && (
        <p className="text-sm text-muted-foreground">No accounts connected yet.</p>
      )}

      {channels?.map((ch) => (
        <ChannelCard
          key={ch.id}
          channel={ch}
          busy={busy}
          onChange={replace}
          onChecked={() => {
            void loadChannels();
            void loadComments();
          }}
          onDisconnect={() => disconnect(ch.id)}
        />
      ))}

      {channels !== null && channels.length > 0 && (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-hand text-2xl font-bold">Comments</h2>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Which comments">
              {(
                [
                  ["open", "To answer"],
                  ["sent", "Sent"],
                  ["skipped", "Skipped"],
                ] as const
              ).map(([value, label]) => (
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
          {comments === null ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : comments.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {show === "open"
                ? answering
                  ? "Nothing to answer right now. New comments are looked at every few minutes."
                  : "Turn on answering for an account above to see its new comments here."
                : "None yet."}
            </p>
          ) : (
            <ul className="space-y-3">
              {comments.map((c) => (
                <CommentCard
                  key={c.id}
                  comment={c}
                  onDone={(next) =>
                    setComments(
                      (prev) =>
                        prev?.flatMap((x) =>
                          x.id !== next.id ? [x] : show === "open" ? [] : [next],
                        ) ?? null,
                    )
                  }
                  onFailed={(next) =>
                    setComments((prev) => prev?.map((x) => (x.id === next.id ? next : x)) ?? null)
                  }
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function ChannelCard({
  channel,
  busy,
  onChange,
  onChecked,
  onDisconnect,
}: {
  channel: IgChannel;
  busy: boolean;
  onChange: (c: IgChannel) => void;
  onChecked: () => void;
  onDisconnect: () => void;
}) {
  const [mode, setMode] = useState<ReplyMode>(channel.reply_mode);
  const [style, setStyle] = useState(channel.reply_style);
  const [limit, setLimit] = useState(String(channel.reply_limit));
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const changed =
    mode !== channel.reply_mode ||
    style.trim() !== channel.reply_style ||
    Number(limit) !== channel.reply_limit;

  async function save() {
    setSaving(true);
    try {
      const next = await api.changeIgChannel(channel.id, {
        reply_mode: mode,
        reply_style: style,
        reply_limit: Number(limit),
      });
      onChange(next);
      toast.success(
        next.reply_mode === "off"
          ? `Not answering @${next.username}'s comments`
          : `Answering @${next.username}'s new comments from now on`,
      );
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  async function check() {
    setChecking(true);
    try {
      await api.checkIgComments(channel.id);
      toast.success("Looking at the comments now");
      window.setTimeout(onChecked, 4000);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setChecking(false);
    }
  }

  return (
    <Card data-testid="ig-channel">
      <CardHeader>
        <div className="flex items-center gap-3">
          {channel.avatar && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={channel.avatar} alt="" className="size-10 shrink-0 rounded-full" />
          )}
          <div className="min-w-0 flex-1">
            <CardTitle className="break-all">@{channel.username}</CardTitle>
            <p className="text-xs text-muted-foreground">
              {channel.token_expires_at
                ? `Login lasts until ${when(channel.token_expires_at)}`
                : "Login doesn't expire"}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            disabled={busy}
            onClick={onDisconnect}
            aria-label={`Disconnect @${channel.username}`}
          >
            <Trash2 className="text-destructive" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <div className="space-y-1">
            <Label htmlFor={`mode-${channel.id}`}>Answer comments</Label>
            <select
              id={`mode-${channel.id}`}
              className={SELECT}
              value={mode}
              onChange={(e) => setMode(e.target.value as ReplyMode)}
            >
              {MODES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`limit-${channel.id}`}>Replies an hour, at most</Label>
            <Input
              id={`limit-${channel.id}`}
              type="number"
              min={1}
              max={60}
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="sm:w-40"
            />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`style-${channel.id}`}>How replies should sound</Label>
          <Textarea
            id={`style-${channel.id}`}
            rows={2}
            maxLength={1000}
            placeholder="e.g. Hinglish, short and funny, like the page talks; a 🔥 now and then"
            value={style}
            onChange={(e) => setStyle(e.target.value)}
          />
        </div>
        {mode === "auto" && (
          <p className="text-xs text-muted-foreground">
            Replies go out by themselves, only to comments made from now on. Complaints and
            questions it can&apos;t answer wait for you.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={saving || !changed}>
            {saving ? "Saving..." : "Save"}
          </Button>
          {channel.reply_mode !== "off" && (
            <Button variant="outline" onClick={check} disabled={checking}>
              <RefreshCw /> Check now
            </Button>
          )}
          {channel.waiting > 0 && (
            <span className="text-sm font-semibold">{channel.waiting} to answer</span>
          )}
        </div>
        {channel.checked_at && (
          <p className="text-xs text-muted-foreground">
            Last looked {when(channel.checked_at)}: {channel.check_note}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function CommentCard({
  comment,
  onDone,
  onFailed,
}: {
  comment: IgComment;
  onDone: (c: IgComment) => void;
  onFailed: (c: IgComment) => void;
}) {
  const [text, setText] = useState(comment.draft);
  const [sending, setSending] = useState(false);
  const open = !["sent", "skipped", "sending"].includes(comment.status);

  async function send() {
    setSending(true);
    try {
      const next = await api.sendIgReply(comment.id, text);
      toast.success(`Replied to @${comment.username}`);
      onDone(next);
    } catch (err) {
      toast.error(errorText(err));
      onFailed({ ...comment, status: "failed", error: errorText(err) });
    } finally {
      setSending(false);
    }
  }

  async function skip() {
    try {
      onDone(await api.skipIgComment(comment.id));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <li className="sketch-tile space-y-2 p-3" data-testid="ig-comment">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
        <span className="min-w-0 break-words">
          <MessageCircle className="mr-1 inline size-4" aria-hidden />
          <span className="font-semibold">@{comment.username}</span> on @{comment.channel}
          {comment.commented_at && (
            <span className="text-muted-foreground"> · {when(comment.commented_at)}</span>
          )}
        </span>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-semibold",
            comment.status === "look" || comment.status === "failed"
              ? "bg-destructive/15 text-destructive"
              : "bg-muted",
          )}
        >
          {STATUS[comment.status] ?? comment.status}
        </span>
      </div>
      <p className="text-base break-words">{comment.text}</p>
      {comment.post_caption && (
        <p className="text-xs break-words text-muted-foreground">
          On the post: {comment.post_caption.slice(0, 140)}
          {comment.post_url && (
            <>
              {" "}
              <a
                href={comment.post_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                open <ExternalLink className="size-3" aria-hidden />
              </a>
            </>
          )}
        </p>
      )}
      {comment.why && <p className="text-xs text-muted-foreground">Model: {comment.why}</p>}
      {comment.error && <p className="text-xs text-destructive">{comment.error}</p>}
      {open ? (
        <>
          <Textarea
            aria-label={`Reply to @${comment.username}`}
            rows={2}
            maxLength={300}
            placeholder={comment.status === "look" ? "Write the answer yourself" : "Reply"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={send} disabled={sending || !text.trim()}>
              <Send /> {sending ? "Sending..." : "Send reply"}
            </Button>
            <Button variant="ghost" onClick={skip}>
              <X /> Skip
            </Button>
          </div>
        </>
      ) : (
        comment.draft && (
          <p className="border-l-2 border-foreground/40 pl-2 text-sm break-words">
            {comment.status === "sent" ? "Replied: " : "Draft: "}
            {comment.draft}
            {comment.sent_by === "auto" && (
              <span className="text-muted-foreground"> (by itself)</span>
            )}
          </p>
        )
      )}
    </li>
  );
}
