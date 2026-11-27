"use client";

import {
  Clapperboard,
  Combine,
  Film,
  ImagePlus,
  Layers,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AnimationAutopilotCard } from "@/components/animation-autopilot";
import { AnimationPreview, type PreviewHandle } from "@/components/animation-preview";
import { AnimationScenes } from "@/components/animation-scenes";
import { AnimationTimeline } from "@/components/animation-timeline";
import { ClaudeConnectCard } from "@/components/claude-connect";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import {
  api,
  errorText,
  type Animation,
  type AnimationPart,
  type AnimationPlan,
  type AnimationSong,
  type AnimationVoice,
} from "@/lib/api";
import { partStatus, playingParts } from "@/lib/song-parts.mjs";
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
  ["map", "World map (country by country)"],
  ["song", "Song story (a song's hook)"],
  ["singer", "Singer edit (a song + the singer's photos)"],
] as const;
// The autopilot writes from topics: a Song story is made from a song someone names, a
// singer edit from a song and someone's photos.
const PILOT_STYLES = STYLES.filter(([s]) => s !== "song" && s !== "singer");
const MAX_PHOTOS = 6;
const MAP_LOOKS = [
  ["cards", "Picture cards"],
  ["horror", "Gold horror"],
  ["neon", "Neon food labels"],
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
const BUSY = ["queued", "drafting", "changing", "painting", "voicing"];
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
  painting: "painting pictures",
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

function clock(seconds: number) {
  const m = Math.floor(seconds / 60);
  return `${m}:${(seconds - m * 60).toFixed(1).padStart(4, "0")}`;
}

/**
 * A Song story's song: what was found and the part playing; another part to play, a reel
 * of every other part (made one after another, each rendered), two parts joined into one
 * reel, and the song's other reels.
 */
function SongPanel({
  song,
  parts,
  disabled,
  onChoose,
  onJoin,
  onMakeParts,
  onOpen,
}: {
  song: AnimationSong;
  parts: AnimationPart[];
  disabled: boolean;
  onChoose: (choice: number) => void;
  onJoin: (first: number, second: number) => void;
  onMakeParts: () => void;
  onOpen: (id: string) => void;
}) {
  const playing = playingParts(song);
  const joined = (song.pieces?.length ?? 0) > 1;
  const spans = joined ? (song.pieces ?? []) : [{ start: song.start, end: song.end }];
  const [first, setFirst] = useState(playing[0] ?? 0);
  // The second part offered by default: the one joined after it, else the best other one.
  const [second, setSecond] = useState(
    playing[1] ??
      Math.max(
        0,
        song.choices.findIndex((_, n) => n !== (playing[0] ?? 0)),
      ),
  );
  const partName = (n: number) => {
    const c = song.choices[n];
    return `${clock(c.start)}-${clock(c.end)} · \u201c${c.first}\u201d`;
  };
  return (
    <div className="space-y-3 rounded-md border-[1.5px] border-foreground/40 p-3 text-sm">
      <div>
        <span className="font-semibold">{song.title}</span>
        {song.artist ? ` · ${song.artist}` : ""} · playing{" "}
        {spans.map((x) => `${clock(x.start)}-${clock(x.end)}`).join(" + ")}
        {joined ? " (two parts joined)" : ""}
        {song.url && (
          <>
            {" · "}
            <a className="underline" href={song.url} target="_blank" rel="noreferrer">
              the song on YouTube
            </a>
          </>
        )}
      </div>
      {song.lyrics === "heard" && (
        <p className="text-xs text-muted-foreground">
          No lyrics were found online: the words are as the AI heard them. Check them in the scenes.
        </p>
      )}
      {song.choices.length > 1 && (
        <div className="space-y-1">
          <Label>Play another part of the song</Label>
          <div className="flex flex-col gap-1.5">
            {song.choices.map((c, n) => {
              const on = playing.includes(n);
              return (
                <Button
                  key={c.start}
                  size="sm"
                  variant={on ? "default" : "outline"}
                  className="h-auto min-h-11 justify-start text-left text-xs whitespace-normal"
                  disabled={disabled || (on && !joined)}
                  onClick={() => onChoose(n)}
                >
                  {partName(n)}
                </Button>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground">
            Another part is timed again and gets a new story.
          </p>
        </div>
      )}
      {song.choices.length > 1 && (
        <div className="space-y-1" data-testid="join-parts">
          <Label htmlFor="join-first">Join two parts into one reel</Label>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-center">
            <select
              id="join-first"
              aria-label="Start with"
              className={SELECT}
              value={first}
              onChange={(e) => setFirst(Number(e.target.value))}
            >
              {song.choices.map((c, n) => (
                <option key={c.start} value={n}>
                  Start with {partName(n)}
                </option>
              ))}
            </select>
            <select
              id="join-second"
              aria-label="Then"
              className={SELECT}
              value={second}
              onChange={(e) => setSecond(Number(e.target.value))}
            >
              {song.choices.map((c, n) => (
                <option key={c.start} value={n}>
                  then {partName(n)}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              className="min-h-11"
              disabled={disabled || first === second}
              onClick={() => onJoin(first, second)}
            >
              <Combine className="size-4" /> Join them
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            About 30 s in all: a stretch of each, the sound crossfaded on a beat where they meet,
            and one new story across both.
          </p>
        </div>
      )}
      {song.choices.length > 1 && (
        <div className="space-y-1">
          <Button
            size="sm"
            variant="outline"
            className="h-auto min-h-11 whitespace-normal"
            disabled={disabled}
            onClick={onMakeParts}
          >
            <Layers className="size-4" /> Make every part
          </Button>
          <p className="text-xs text-muted-foreground">
            A reel of its own for each other part of the song, made one after another and each
            rendered when it&apos;s ready.
          </p>
        </div>
      )}
      {parts.length > 0 && (
        <div className="space-y-1" data-testid="song-parts">
          <Label>Reels of this song</Label>
          <ul className="space-y-1.5">
            {parts.map((part) => (
              <li
                key={part.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border-[1.5px] border-foreground/30 p-2 text-xs"
              >
                <span className="font-semibold">
                  {part.spans.map(([a, b]) => `${clock(a)}-${clock(b)}`).join(" + ")}
                </span>
                {part.title && <span className="min-w-0 break-words">{part.title}</span>}
                <span className="text-muted-foreground">
                  {part.first ? "the first reel · " : ""}
                  {partStatus(part)}
                </span>
                {BUSY.includes(part.status) && <Loader2 className="size-3.5 animate-spin" />}
                <span className="ml-auto flex flex-wrap items-center gap-2">
                  {part.render?.status === "done" && (
                    <a className="underline" href={`/job/?id=${part.render.job_id}`}>
                      Video in Jobs
                    </a>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="min-h-9"
                    onClick={() => onOpen(part.id)}
                  >
                    Open
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
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
  const [part, setPart] = useState("");
  const [kind, setKind] = useState("auto");
  const [style, setStyle] = useState("auto");
  const [voice, setVoice] = useState("auto");
  const [length, setLength] = useState("auto");
  const [pictures, setPictures] = useState(true);
  const [ideas, setIdeas] = useState<string[]>([]);
  const [photos, setPhotos] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
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
        // A link to one reel (?id=, as Claude gives them) opens it; else the newest.
        const wanted = new URLSearchParams(window.location.search).get("id");
        if (wanted) void open(wanted);
        else if (found.length) void open(found[0].id);
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
    ) ||
    (current?.parts ?? []).some(
      (p) => BUSY.includes(p.status) || ["queued", "running"].includes(p.render?.status ?? ""),
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

  const singerEdit = style === "singer";
  // A Song story and a singer edit are made from a song: its name, not an idea.
  const sung = style === "song" || singerEdit;

  async function create() {
    if (idea.trim().length < 3) {
      toast.error(sung ? "Write the song's name first" : "Write the idea in a few words first");
      return;
    }
    if (singerEdit && !photos.length) {
      toast.error("Add at least one photo of the singer");
      return;
    }
    // A Song story: the song's name, then the words of the part wanted after a "|".
    const asked = style === "song" && part.trim() ? `${idea.trim()} | ${part.trim()}` : idea;
    await act(
      () =>
        api.createAnimation({
          idea: asked,
          kind,
          style,
          voice,
          length,
          images: pictures,
          ...(singerEdit ? { photos } : {}),
        }),
      singerEdit
        ? "Finding the song and making the edit"
        : sung
          ? "Finding the song"
          : "Writing the draft",
    );
    setIdea("");
    setPart("");
    setPhotos([]);
  }

  async function playPart(choice: number) {
    if (!current) return;
    await act(
      () => api.changeAnimation(current.id, { request: "", choice }),
      "Timing that part of the song",
    );
  }

  async function joinParts(first: number, second: number) {
    if (!current) return;
    await act(
      () => api.changeAnimation(current.id, { request: "", join: [first, second] }),
      "Joining the two parts",
    );
  }

  async function makeParts() {
    if (!current) return;
    await act(
      () => api.makeAnimationParts(current.id),
      "Making a reel of each other part, one after another; each renders when it's ready",
    );
  }

  async function addPhotos(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const file of Array.from(files).slice(0, MAX_PHOTOS - photos.length)) {
        try {
          const { name, notes } = await api.uploadAnimationPhoto(file);
          if (notes?.length) toast.info(`${file.name}: ${notes.join("; ")}`);
          setPhotos((had) => (had.includes(name) ? had : [...had, name].slice(0, MAX_PHOTOS)));
        } catch (err) {
          toast.error(`${file.name}: ${errorText(err)}`);
        }
      }
    } finally {
      setUploading(false);
    }
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

  // World map reels have a kind of topic of their own: offer some, never one made already.
  const loadIdeas = useCallback(async (forStyle: string) => {
    if (forStyle !== "map") {
      setIdeas([]);
      return;
    }
    try {
      setIdeas((await api.animationIdeas(forStyle)).ideas);
    } catch {
      setIdeas([]);
    }
  }, []);

  function chooseStyle(next: string) {
    setStyle(next);
    void loadIdeas(next);
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
                <Label htmlFor="idea">{sung ? "Song (name and singer)" : "Idea"}</Label>
                <Textarea
                  id="idea"
                  rows={sung ? 2 : 3}
                  placeholder={
                    sung
                      ? "e.g. the song's name and the singer"
                      : "e.g. The lost city of Ubar that vanished into the desert"
                  }
                  value={idea}
                  onChange={(e) => setIdea(e.target.value)}
                />
              </div>
              {style === "song" && (
                <div className="space-y-1">
                  <Label htmlFor="part">Which part (optional)</Label>
                  <Textarea
                    id="part"
                    rows={2}
                    placeholder="A few words of its lyrics, as you remember them. Empty: the part people replay most."
                    value={part}
                    onChange={(e) => setPart(e.target.value)}
                  />
                </div>
              )}
              {singerEdit && (
                <div className="space-y-2 rounded-md border-[1.5px] border-dashed border-foreground/50 p-2">
                  <p className="text-xs text-muted-foreground">
                    Singer edit: the song is found and downloaded, its catchy part cut, and every
                    sung word slapped on as a torn-paper sticker in time with the singing, over warm
                    gritty scenes with the singer cut out of the photos standing in them.
                  </p>
                  <div className="flex flex-wrap gap-2" data-testid="singer-photos">
                    {photos.map((name) => (
                      <div key={name} className="relative">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`/api/animation-images/${name}`}
                          alt="Photo of the singer"
                          className="h-24 w-[4.5rem] rounded border-[1.5px] border-foreground object-cover"
                        />
                        <button
                          type="button"
                          className="absolute -top-2 -right-2 flex size-7 items-center justify-center rounded-full border-[1.5px] border-foreground bg-background"
                          onClick={() => setPhotos((had) => had.filter((n) => n !== name))}
                          aria-label="Remove this photo"
                        >
                          <X className="size-3.5" />
                        </button>
                      </div>
                    ))}
                    {photos.length < MAX_PHOTOS && (
                      <label className="flex h-24 w-[4.5rem] cursor-pointer flex-col items-center justify-center gap-1 rounded border-[1.5px] border-dashed border-foreground text-center text-xs">
                        {uploading ? (
                          <Loader2 className="size-5 animate-spin" />
                        ) : (
                          <ImagePlus className="size-5" />
                        )}
                        {uploading ? "Sending" : "Add photos"}
                        <input
                          type="file"
                          accept="image/*,.heic,.heif,.avif"
                          multiple
                          className="sr-only"
                          aria-label="Photos of the singer"
                          disabled={uploading}
                          onChange={(e) => {
                            void addPhotos(e.target.files);
                            e.target.value = "";
                          }}
                        />
                      </label>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Up to {MAX_PHOTOS} photos of the singer, any kind (more photos, more variety).
                    Each is made ready by itself: borders and text taken off, cropped to the singer.
                    What you see here is what will be used.
                  </p>
                </div>
              )}
              <div className={cn("grid gap-3", sung ? "grid-cols-1" : "grid-cols-2")}>
                <div className={cn("space-y-1", sung && "hidden")}>
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
                    onChange={(e) => chooseStyle(e.target.value)}
                  >
                    {STYLES.map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className={cn("space-y-1", sung && "hidden")}>
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
                <div className={cn("space-y-1", sung && "hidden")}>
                  <Label htmlFor="voice">Voice</Label>
                  <VoiceSelect id="voice" value={voice} voices={voices} onChange={setVoice} />
                </div>
              </div>
              {style === "song" && (
                <p className="rounded-md border-[1.5px] border-dashed border-foreground/50 p-2 text-xs text-muted-foreground">
                  Song story: the song is found on YouTube with its lyrics, the part people replay
                  most is chosen (or the part you name), and the AI tells a story over it, one
                  painted moment a lyric line, the words on screen as they are sung. No voice-over:
                  the song is the sound.
                </p>
              )}
              {!sung && !["auto", "narrator", "calm", "energetic"].includes(voice) && (
                <Button size="sm" variant="ghost" onClick={() => playSample(voice)}>
                  Hear this voice
                </Button>
              )}
              {style === "map" && (
                <div className="space-y-2 rounded-md border-[1.5px] border-dashed border-foreground/50 p-2">
                  <p className="text-xs text-muted-foreground">
                    World map: the camera flies over the Earth from country to country, each country
                    lights up and a picture card pops up for every thing named. Write a topic like
                    the ones below.
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {ideas.map((one) => (
                      <Button
                        key={one}
                        size="sm"
                        variant="outline"
                        className="h-auto min-h-9 text-left text-xs whitespace-normal"
                        onClick={() => setIdea(one)}
                      >
                        {one}
                      </Button>
                    ))}
                    <Button size="sm" variant="ghost" onClick={() => void loadIdeas("map")}>
                      <RefreshCw className="size-3.5" /> More ideas
                    </Button>
                  </div>
                </div>
              )}
              <label className="flex items-center justify-between gap-2 text-sm">
                <span>
                  {style === "map"
                    ? "Card pictures painted by the AI"
                    : "Painted pictures behind the scenes"}
                  <span className="block text-xs text-muted-foreground">
                    {style === "map"
                      ? "An AI paints each card's picture (a few minutes more); without it the cards show emoji."
                      : singerEdit
                        ? "An AI paints each line's scene (on the Mac); without it the scenes are drawn from the photos."
                        : "An AI paints each scene (a few minutes more). Not for Stick figure or Bold kinetic text."}
                  </span>
                </span>
                <Switch
                  checked={pictures}
                  onCheckedChange={(v) => setPictures(Boolean(v))}
                  aria-label="Painted pictures"
                />
              </label>
              <Button className="w-full" onClick={create} disabled={sending || uploading}>
                <Wand2 className="size-4" /> {singerEdit ? "Make the edit" : "Write the draft"}
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
            styles={PILOT_STYLES}
            kinds={KINDS}
            lengths={LENGTHS}
            onStarted={(a) => {
              void loadList();
              void open(a.id);
            }}
          />

          <ClaudeConnectCard />
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
                {current.plan?.style === "song" && current.plan.song && (
                  <SongPanel
                    key={current.id}
                    song={current.plan.song}
                    parts={current.parts ?? []}
                    disabled={busy || dirty || sending}
                    onChoose={(n) => void playPart(n)}
                    onJoin={(a, b) => void joinParts(a, b)}
                    onMakeParts={() => void makeParts()}
                    onOpen={(id) => void open(id)}
                  />
                )}
                {current.plan?.singer && (
                  <p className="text-sm">
                    {current.plan.singer.title}
                    {current.plan.singer.artist ? ` · ${current.plan.singer.artist}` : ""}
                    {current.plan.singer.duration
                      ? ` · ${Math.round(current.plan.singer.duration)} s of the song`
                      : ""}
                  </p>
                )}
                {current.plan && !["song", "singer"].includes(current.plan.style) && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label>Style</Label>
                      <div className="flex flex-wrap gap-2">
                        {STYLES.filter(([s]) => !["auto", "song", "singer"].includes(s)).map(
                          ([s, label]) => (
                            <Button
                              key={s}
                              size="sm"
                              variant={current.plan?.style === s ? "default" : "outline"}
                              disabled={busy || dirty || current.plan?.style === s}
                              onClick={() => act(() => api.setAnimation(current.id, { style: s }))}
                            >
                              {label}
                            </Button>
                          ),
                        )}
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
                {current.plan?.style === "map" && (
                  <div className="space-y-1">
                    <Label>Map look</Label>
                    <div className="flex flex-wrap gap-2">
                      {MAP_LOOKS.map(([look, label]) => (
                        <Button
                          key={look}
                          size="sm"
                          variant={(current.plan?.look ?? "cards") === look ? "default" : "outline"}
                          disabled={busy || dirty || (current.plan?.look ?? "cards") === look}
                          onClick={() => act(() => api.setAnimation(current.id, { look }))}
                        >
                          {label}
                        </Button>
                      ))}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Chosen by the topic: horror for haunted or scary ones, neon for food. A
                      country&apos;s number badge (a ranking or a count) is in its scene below.
                    </p>
                  </div>
                )}
                {current.plan && !["stick", "kinetic"].includes(current.plan.style) && (
                  <label className="flex items-center justify-between gap-2 text-sm">
                    <span>
                      {current.plan.style === "map"
                        ? "Card pictures painted by the AI"
                        : "Painted pictures behind the scenes"}
                    </span>
                    <Switch
                      checked={current.images ?? true}
                      disabled={busy || dirty || sending}
                      onCheckedChange={(v) =>
                        act(() => api.setAnimation(current.id, { images: Boolean(v) }))
                      }
                      aria-label="Painted pictures for this reel"
                    />
                  </label>
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
                    placeholder={
                      current.plan.style === "singer"
                        ? "e.g. put line 3 in the mirror; more question marks; line 5 at sunset"
                        : "e.g. make the ending more emotional; scene 3's number is wrong, it's 1992"
                    }
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
                            draft.style === "singer" ? "Saved" : "Saved: recording the voice",
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
                  {/* A Song story and a singer edit are timed by their song: nothing to hold
                      or move on a strip. */}
                  {current.player && !["singer", "song"].includes(draft.style) && (
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
                    saved={current.plan}
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
