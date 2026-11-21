"use client";

import { Pause, Play, RotateCcw } from "lucide-react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { AnimationPlayerData } from "@/lib/api";

const PLAYER = "/api/animation-player/";
const POSTER = 1.2; // seconds into the reel the preview shows before it is played

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export type PreviewHandle = { seek: (t: number, play?: boolean) => void };

/**
 * The reel played live: the same player the renderer draws frames with (an iframe of
 * animate/player), driven from here with the preview's mixed sound as the clock. What
 * shows here is what the MP4 will be, frame for frame.
 */
export const AnimationPreview = forwardRef<
  PreviewHandle,
  {
    data: AnimationPlayerData;
    soundUrl: string | null;
    onProblems?: (p: string[]) => void;
    /** Kept at the preview's time, for a timeline's playhead. */
    timeRef?: MutableRefObject<number>;
  }
>(function AnimationPreview({ data, soundUrl, onProblems, timeRef }, ref) {
  const frame = useRef<HTMLIFrameElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [guides, setGuides] = useState(false);
  const started = useRef<{ at: number; from: number } | null>(null);
  // Showing the poster frame, not played or moved yet: Play starts from the beginning.
  const fresh = useRef(true);
  const duration = data.duration || 0;

  const post = useCallback((message: Record<string, unknown>) => {
    frame.current?.contentWindow?.postMessage(message, window.location.origin);
  }, []);

  const send = useCallback(() => {
    setReady(false);
    post({
      type: "load",
      data,
      t: 0,
      fontBase: PLAYER,
      emojiFont: `${PLAYER}NotoColorEmoji.ttf`,
    });
  }, [data, post]);

  // The player says when its page is up (then it gets the reel) and when it has drawn it.
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow)
        return;
      if (event.data?.type === "player") send();
      if (event.data?.type === "ready") {
        setReady(true);
        onProblems?.(event.data.problems ?? []);
        // Shown first: the hook as it stands once its words are up, not the empty first frame.
        const poster = Math.min(POSTER, data.duration || 0);
        fresh.current = true;
        setT(poster);
        post({ type: "seek", t: poster });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [send, onProblems, data.duration, post]);

  // A new version of the reel: draw it again from the start.
  useEffect(() => {
    setPlaying(false);
    audio.current?.pause();
    setT(0);
    send();
  }, [send]);

  useEffect(() => {
    if (ready) post({ type: "guides", on: guides });
  }, [guides, ready, post]);

  // While playing, a clock sets the time every frame and the sound follows it: the picture
  // never waits for the sound to load, and the sound joins at the right moment once it has.
  useEffect(() => {
    if (!playing) return;
    let id = 0;
    const tick = () => {
      const now = started.current
        ? started.current.from + (performance.now() - started.current.at) / 1000
        : 0;
      if (now >= duration) {
        setPlaying(false);
        setT(duration);
        post({ type: "seek", t: duration });
        audio.current?.pause();
        return;
      }
      const sound = audio.current;
      if (sound && soundUrl && sound.readyState >= 2) {
        if (sound.paused) void sound.play().catch(() => undefined);
        if (Math.abs(sound.currentTime - now) > 0.25) sound.currentTime = now;
      }
      setT(now);
      if (timeRef) timeRef.current = now;
      post({ type: "seek", t: now });
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [playing, duration, post, soundUrl, timeRef]);

  const seek = useCallback(
    (to: number, play = false) => {
      const at = Math.min(Math.max(0, to), duration);
      fresh.current = false;
      setT(at);
      if (timeRef) timeRef.current = at;
      post({ type: "seek", t: at });
      if (audio.current) audio.current.currentTime = at;
      started.current = { at: performance.now(), from: at };
      if (play) {
        setPlaying(true);
        void audio.current?.play().catch(() => undefined);
      }
    },
    [duration, post, timeRef],
  );

  useImperativeHandle(ref, () => ({ seek }), [seek]);

  function toggle() {
    if (playing) {
      setPlaying(false);
      audio.current?.pause();
      return;
    }
    const from = fresh.current || t >= duration - 0.05 ? 0 : t;
    seek(from, true);
  }

  return (
    <div className="space-y-2">
      <div
        className="relative mx-auto aspect-[9/16] w-full max-w-[340px] overflow-hidden rounded-xl border-[1.5px] border-foreground bg-black"
        data-testid="animation-preview"
      >
        <iframe
          ref={frame}
          title="Animation preview"
          src={`${PLAYER}player.html?mode=preview`}
          className="absolute inset-0 h-full w-full"
        />
        {!ready && (
          <div className="absolute inset-0 grid place-items-center text-xs text-white/70">
            Drawing the reel...
          </div>
        )}
      </div>
      {soundUrl && <audio ref={audio} src={soundUrl} preload="auto" />}
      <div className="mx-auto flex max-w-[340px] items-center gap-2">
        <Button
          size="icon"
          variant="outline"
          onClick={toggle}
          disabled={!ready}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
        <input
          type="range"
          min={0}
          max={duration}
          step={0.01}
          value={t}
          onChange={(e) => {
            setPlaying(false);
            audio.current?.pause();
            seek(Number(e.target.value));
          }}
          className="h-11 flex-1 accent-[var(--foreground)]"
          aria-label="Time"
        />
        <span className="w-20 text-right text-xs text-muted-foreground tabular-nums">
          {clock(t)} / {clock(duration)}
        </span>
        <Button size="icon" variant="ghost" onClick={() => seek(0)} aria-label="Back to the start">
          <RotateCcw className="size-4" />
        </Button>
      </div>
      <label className="mx-auto flex max-w-[340px] items-center gap-2 text-xs text-muted-foreground">
        <Checkbox checked={guides} onCheckedChange={(v) => setGuides(Boolean(v))} />
        Show where Instagram&apos;s buttons and caption cover the video
      </label>
    </div>
  );
});
