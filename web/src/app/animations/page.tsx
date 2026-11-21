"use client";

import {
  Clapperboard,
  Film,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Undo2,
  Wand2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AnimationAutopilotCard } from "@/components/animation-autopilot";
import { AnimationPreview, type PreviewHandle } from "@/components/animation-preview";
import { AnimationScenes } from "@/components/animation-scenes";
import { AnimationTimeline } from "@/components/animation-timeline";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import { api, errorText, type Animation, type AnimationPlan, type AnimationVoice } from "@/lib/api";
import { cn } from "@/lib/utils";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";
const KINDS = [
  ["auto", "Auto: AI decides"],
  ["facts", "Facts / explainer"],
  ["lesson", "Life lesson"],
  ["story", "Story"],
  ["news", "News explainer"],
] as const;
const STYLES = [
  ["auto", "Auto: by the content"],
  ["glow", "Neon Glow"],
  ["paper", "Clean Paper"],
  ["story", "Character story"],
  ["kinetic", "Bold kinetic text"],
  ["stick", "Stick figure"],
] as const;
const LENGTHS = [
  ["auto", "Auto: AI decides"],
  ["short", "20-35 seconds"],
  ["medium", "35-60 seconds"],
  ["long", "60-90 seconds"],
] as const;
const GROUPS = [
  ["auto", "Auto: AI recommends"],
  ["narrator", "Deep narrator"],
  ["calm", "Calm"],
  ["energetic", "Energetic"],
] as const;
const BUSY = ["queued", "drafting", "changing", "voicing"];
const POSTED: Record<string, string> = {
  rendering: "rendering",
  awaiting: "waiting for your approval",
  posting: "posting",
  posted: "posted",
  approved: "approved",
  skipped: "skipped",
  failed: "failed",
};
const STATUS: Record<Animation["status"], string> = {
  queued: "waiting",
  drafting: "writing",
  changing: "changing",
  voicing: "recording voice",
  ready: "ready",
  failed: "failed",
};

function VoiceSelect({
  id,
  value,
  voices,
  onChange,
  withAuto = true,
}: {
  id: string;
  value: string;
  voices: AnimationVoice[];
  onChange: (v: string) => void;
  withAuto?: boolean;
}) {
  return (
    <select id={id} className={SELECT} value={value} onChange={(e) => onChange(e.target.value)}>
      {GROUPS.filter(([k]) => withAuto || k !== "auto").map(([k, label]) => (
        <option key={k} value={k}>
          {label}
        </option>
      ))}
      <optgroup label="Every voice">
        {voices.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name}: {v.about}
          </option>
        ))}
      </optgroup>
    </select>
  );
}

export default function AnimationsPage() {
  const me = useMe();
  const allowed = me?.features?.includes("animation_studio") ?? false;
  const [list, setList] = useState<Animation[]>([]);
  const [current, setCurrent] = useState<Animation | null>(null);
  const [draft, setDraft] = useState<AnimationPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [voices, setVoices] = useState<AnimationVoice[]>([]);
  const [idea, setIdea] = useState("");
  const [kind, setKind] = useState("auto");
  const [style, setStyle] = useState("auto");
  const [voice, setVoice] = useState("auto");
  const [length, setLength] = useState("auto");
  const [request, setRequest] = useState("");
  const [sending, setSending] = useState(false);
  const [shrunk, setShrunk] = useState<string[]>([]);
  const preview = useRef<PreviewHandle>(null);
  const clock = useRef(0);
  const sample = useRef<HTMLAudioElement | null>(null);

  const loadList = useCallback(async () => {
    try {
      setList(await api.animations());
    } catch {
      /* shown on the next try */
    }
  }, []);

  const open = useCallback(async (id: string) => {
    try {
      const found = await api.animation(id);
      setCurrent(found);
      setDraft(found.plan ?? null);
      setDirty(false);
    } catch (err) {
      toast.error(errorText(err));
    }
  }, []);

  useEffect(() => {
    if (!allowed) return;
    let live = true;
    api
      .animations()
      .then((found) => {
        if (!live) return;
        setList(found);
        if (found.length) void open(found[0].id);
      })
      .catch(() => {});
    api
      .animationVoices()
      .then((v) => live && setVoices(v))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [allowed, open]);

  // While something is being written, voiced or rendered, look again every 2 seconds.
  const working =
    (current &&
      (BUSY.includes(current.status) ||
        ["queued", "running"].includes(current.render?.status ?? ""))) ||
    list.some(
      (a) => BUSY.includes(a.status) || ["rendering", "posting"].includes(a.post_status ?? ""),
    );
  useEffect(() => {
    if (!working) return;
    const every = window.setInterval(async () => {
      void loadList();
      if (!current) return;
      try {
        const next = await api.animation(current.id);
        setCurrent(next);
        // A finished change replaces the scenes shown, unless the person is editing them.
        if (!dirty) setDraft(next.plan ?? null);
      } catch {
        /* next time */
      }
    }, 2000);
    return () => window.clearInterval(every);
  }, [working, current, dirty, loadList]);

  async function act(work: () => Promise<Animation>, done?: string) {
    setSending(true);
    try {
      const next = await work();
      setCurrent(next);
      setDraft(next.plan ?? null);
      setDirty(false);
      void loadList();
      if (done) toast.success(done);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSending(false);
    }
  }

  async function create() {
    if (idea.trim().length < 3) {
      toast.error("Write the idea in a few words first");
      return;
    }
    await act(() => api.createAnimation({ idea, kind, style, voice, length }), "Writing the draft");
    setIdea("");
  }

  async function remove(id: string) {
    if (!window.confirm("Delete this animation? Its rendered videos stay in Jobs.")) return;
    try {
      await api.deleteAnimation(id);
      if (current?.id === id) {
        setCurrent(null);
        setDraft(null);
      }
      await loadList();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  function playSample(id: string) {
    if (!id || ["auto", "narrator", "calm", "energetic"].includes(id)) return;
    sample.current?.pause();
    sample.current = new Audio(`/api/animation-voices/${id}/sample`);
    void sample.current.play().catch(() => toast.error("The sample couldn't play"));
  }

  const busy = current ? BUSY.includes(current.status) : false;
  const rendering = ["queued", "running"].includes(current?.render?.status ?? "");
  const starts = useMemo(
    () => (current?.player?.scenes ?? []).map((s) => s.start),
    [current?.player],
  );
  const soundUrl =
    current?.player && current.status === "ready"
      ? `/api/animations/${current.id}/sound?v=${encodeURIComponent(current.updated_at)}`
      : null;

  if (!me) return <p className="text-sm text-muted-foreground">Loading...</p>;
  if (!allowed)
    return (
      <p className="text-sm text-muted-foreground">This page isn&apos;t on for your account.</p>
    );

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-hand text-4xl font-bold">
          <span className="marker">Animations</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          One line in, a narrated animated reel out: the AI writes the script and the scenes (facts
          from Wikipedia and the web), a voice reads it, music and sound effects are made for it.
          Watch it here, change anything, then render the video; it lands in Jobs, ready to post.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card className="tape">
            <CardHeader>
              <CardTitle>New reel</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="idea">Idea</Label>
                <Textarea
                  id="idea"
                  rows={3}
                  placeholder="e.g. The lost city of Ubar that vanished into the desert"
                  value={idea}
                  onChange={(e) => setIdea(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="kind">Content</Label>
                  <select
                    id="kind"
                    className={SELECT}
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    {KINDS.map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="style">Style</Label>
                  <select
                    id="style"
                    className={SELECT}
                    value={style}
                    onChange={(e) => setStyle(e.target.value)}
                  >
                    {STYLES.map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="length">Length</Label>
                  <select
                    id="length"
                    className={SELECT}
                    value={length}
                    onChange={(e) => setLength(e.target.value)}
                  >
                    {LENGTHS.map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="voice">Voice</Label>
                  <VoiceSelect id="voice" value={voice} voices={voices} onChange={setVoice} />
                </div>
              </div>
              {!["auto", "narrator", "calm", "energetic"].includes(voice) && (
                <Button size="sm" variant="ghost" onClick={() => playSample(voice)}>
                  Hear this voice
                </Button>
              )}
              <Button className="w-full" onClick={create} disabled={sending}>
                <Wand2 className="size-4" /> Write the draft
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Your reels</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {!list.length && <p className="text-sm text-muted-foreground">None yet.</p>}
              {list.map((a) => (
                <div
                  key={a.id}
                  className={cn(
                    "flex items-center gap-2 rounded-md border-[1.5px] border-foreground/30 p-2",
                    current?.id === a.id && "border-foreground bg-[var(--field)]",
                  )}
                >
                  <button
                    type="button"
                    className="min-h-11 flex-1 text-left"
                    onClick={() => open(a.id)}
                  >
                    <div className="line-clamp-1 text-sm font-semibold">{a.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {STATUS[a.status]}
                      {a.duration ? ` · ${Math.round(a.duration)} s` : ""}
                      {a.scenes ? ` · ${a.scenes} scenes` : ""}
                      {a.render?.status === "done" ? " · rendered" : ""}
                      {a.post_status
                        ? ` · autopilot: ${POSTED[a.post_status] ?? a.post_status}`
                        : ""}
                    </div>
                  </button>
                  {BUSY.includes(a.status) && <Loader2 className="size-4 animate-spin" />}
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => remove(a.id)}
                    aria-label={`Delete ${a.title}`}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>

          <AnimationAutopilotCard
            voices={voices}
            styles={STYLES}
            kinds={KINDS}
            lengths={LENGTHS}
            onStarted={(a) => {
              void loadList();
              void open(a.id);
            }}
          />
        </div>

        {current ? (
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2">
                  <Clapperboard className="size-5" /> {current.title}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {busy && (
                  <div className="flex items-center gap-2 text-sm">
                    <Loader2 className="size-4 animate-spin" /> {current.message}
                  </div>
                )}
                {current.status === "failed" && (
                  <div className="space-y-2 rounded-md border-[1.5px] border-destructive p-3 text-sm text-destructive">
                    <p>{current.error ?? "Something went wrong."}</p>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => act(() => api.retryAnimation(current.id))}
                    >
                      <RefreshCw className="size-3.5" /> Try again
                    </Button>
                  </div>
                )}
                {current.player && (
                  <AnimationPreview
                    ref={preview}
                    timeRef={clock}
                    data={current.player}
                    soundUrl={soundUrl}
                    onProblems={setShrunk}
                  />
                )}
                {(current.notes.length > 0 || shrunk.length > 0) && (
                  <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
                    {current.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                    {shrunk.map((s) => (
                      <li key={s}>Text is long for its place: {s}</li>
                    ))}
                  </ul>
                )}
                {current.plan && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label>Style</Label>
                      <div className="flex flex-wrap gap-2">
                        {STYLES.filter(([s]) => s !== "auto").map(([s, label]) => (
                          <Button
                            key={s}
                            size="sm"
                            variant={current.plan?.style === s ? "default" : "outline"}
                            disabled={busy || dirty || current.plan?.style === s}
                            onClick={() => act(() => api.setAnimation(current.id, { style: s }))}
                          >
                            {label}
                          </Button>
                        ))}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="current-voice">Voice</Label>
                      <div className="flex gap-2">
                        <VoiceSelect
                          id="current-voice"
                          value={current.plan.voice}
                          voices={voices}
                          withAuto={false}
                          onChange={(v) => act(() => api.setAnimation(current.id, { voice: v }))}
                        />
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => playSample(current.plan?.voice ?? "")}
                        >
                          Hear
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      act(() => api.renderAnimation(current.id), "Rendering: it will be in Jobs")
                    }
                    disabled={current.status !== "ready" || dirty || rendering || sending}
                  >
                    <Film className="size-4" /> Render the video
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => act(() => api.undoAnimation(current.id))}
                    disabled={!current.can_undo || busy || dirty || sending}
                  >
                    <Undo2 className="size-4" /> Undo last change
                  </Button>
                </div>
                {current.autopilot_id && (
                  <div className="space-y-2 rounded-md border-[1.5px] border-foreground p-3 text-sm">
                    <div className="font-semibold">
                      Made by the autopilot ·{" "}
                      {POSTED[current.post_status ?? ""] ?? current.post_status ?? "being made"}
                    </div>
                    {(current.doubts ?? []).length > 0 && (
                      <div>
                        <div className="text-xs font-semibold">
                          The fact check found no source for:
                        </div>
                        <ul className="list-disc pl-5 text-xs">
                          {(current.doubts ?? []).map((d) => (
                            <li key={d}>{d}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {current.post_error && (
                      <p className="text-xs text-destructive">{current.post_error}</p>
                    )}
                    {["awaiting", "failed"].includes(current.post_status ?? "") &&
                      current.render?.status === "done" && (
                        <div className="flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            onClick={() => act(() => api.approveAnimation(current.id), "Approved")}
                          >
                            Approve &amp; post
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => act(() => api.skipAnimation(current.id))}
                          >
                            Skip
                          </Button>
                        </div>
                      )}
                  </div>
                )}
                {current.render && (
                  <div className="space-y-2 rounded-md border-[1.5px] border-foreground/40 p-3 text-sm">
                    {rendering && (
                      <>
                        <div>Rendering: {current.render.stage}</div>
                        <Progress value={Math.round(current.render.progress * 100)} />
                      </>
                    )}
                    {current.render.status === "failed" && (
                      <p className="text-destructive">Render failed: {current.render.error}</p>
                    )}
                    {current.render.status === "done" && current.render.output_id && (
                      <>
                        <video
                          className="mx-auto aspect-[9/16] w-full max-w-[260px] rounded-lg border-[1.5px] border-foreground"
                          src={`/api/outputs/${current.render.output_id}/video`}
                          controls
                          playsInline
                        />
                        <p className="text-center">
                          <a className="underline" href={`/job/?id=${current.render.job_id}`}>
                            Open it in Jobs to post it (Post Reel, Buffer, caption)
                          </a>
                        </p>
                      </>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            {current.plan && (
              <Card>
                <CardHeader>
                  <CardTitle>Change it in words</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <Textarea
                    rows={2}
                    placeholder="e.g. make the ending more emotional; scene 3's number is wrong, it's 1992"
                    value={request}
                    onChange={(e) => setRequest(e.target.value)}
                  />
                  <Button
                    disabled={busy || dirty || sending || !request.trim()}
                    onClick={async () => {
                      await act(() => api.changeAnimation(current.id, { request }), "Changing it");
                      setRequest("");
                    }}
                  >
                    <Sparkles className="size-4" /> Change it
                  </Button>
                </CardContent>
              </Card>
            )}

            {draft && (
              <Card>
                <CardHeader>
                  <CardTitle>Scenes</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {dirty && (
                    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-md border-[1.5px] border-foreground bg-background p-2 text-sm">
                      <span className="flex-1">
                        Unsaved changes: the preview shows the saved version.
                      </span>
                      <Button
                        size="sm"
                        disabled={busy || sending}
                        onClick={() =>
                          act(
                            () => api.editAnimation(current.id, draft),
                            "Saved: recording the voice",
                          )
                        }
                      >
                        <Save className="size-3.5" /> Save
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setDraft(current.plan ?? null);
                          setDirty(false);
                        }}
                      >
                        Discard
                      </Button>
                    </div>
                  )}
                  {current.player && (
                    <AnimationTimeline
                      plan={draft}
                      saved={current.plan}
                      player={current.player}
                      clock={clock}
                      onChange={(p) => {
                        setDraft(p);
                        setDirty(true);
                      }}
                      onSeek={(s) => preview.current?.seek(s)}
                    />
                  )}
                  <AnimationScenes
                    plan={draft}
                    starts={starts}
                    busy={busy || dirty || sending}
                    onChange={(p) => {
                      setDraft(p);
                      setDirty(true);
                    }}
                    onRedo={(i) =>
                      act(
                        () => api.changeAnimation(current.id, { request: "", scene: i }),
                        `Writing scene ${i + 1} again`,
                      )
                    }
                    onPlayFrom={(s) => preview.current?.seek(s, true)}
                  />
                </CardContent>
              </Card>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Write a draft, or open one of your reels.</p>
        )}
      </div>
    </div>
  );
}
