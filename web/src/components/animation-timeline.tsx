"use client";

import { useEffect, useRef, useState, type MutableRefObject } from "react";

import type { AnimationPlan, AnimationPlayerData } from "@/lib/api";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  title: "#FF6B4A",
  scene: "#3D7BF2",
  stat: "#2BB673",
  reveal: "#9B5DE5",
  quote: "#E84393",
  cta: "#9CA3AF",
};
const WORDS_PER_SECOND = 2.6;
const MAX_HOLD = 3;

/** Seconds each scene of `plan` lasts: the saved timing where the scene is unchanged (its
 * pause after the voice moved by what was dragged), an estimate from its words otherwise. */
function lengths(
  plan: AnimationPlan,
  saved: AnimationPlan | null | undefined,
  player: AnimationPlayerData,
) {
  return plan.scenes.map((scene, i) => {
    const before = saved?.scenes[i];
    const timed = player.scenes[i];
    if (before && timed && before.narration === scene.narration) {
      return Math.max(0.6, timed.end - timed.start + (scene.hold - before.hold));
    }
    return Math.max(
      1.6,
      scene.narration.split(/\s+/).length / WORDS_PER_SECOND + 0.18 + scene.hold,
    );
  });
}

/**
 * The reel as a strip of scenes, each as long as it plays: drag a scene's right edge to
 * hold it longer after its voice (0 to 3 s), drag a scene onto another to move it there,
 * tap one to play from it. The line moving along is the preview's time.
 */
export function AnimationTimeline({
  plan,
  saved,
  player,
  clock,
  onChange,
  onSeek,
}: {
  plan: AnimationPlan;
  saved: AnimationPlan | null | undefined;
  player: AnimationPlayerData;
  clock: MutableRefObject<number>;
  onChange: (plan: AnimationPlan) => void;
  onSeek: (seconds: number) => void;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const head = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [dragging, setDragging] = useState<number | null>(null);
  const durations = lengths(plan, saved, player);
  const cta = player.scenes.length ? player.scenes[player.scenes.length - 1] : null;
  const ctaLength = cta ? cta.end - cta.start : 2.5;
  const total = durations.reduce((a, b) => a + b, 0) + ctaLength + 0.7;
  const scale = width / total;

  useEffect(() => {
    const node = strip.current;
    if (!node) return;
    const measure = () => setWidth(Math.max(320, node.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // The playhead follows the preview without re-drawing the page every frame.
  useEffect(() => {
    let id = 0;
    const tick = () => {
      if (head.current)
        head.current.style.transform = `translateX(${Math.min(width, clock.current * scale)}px)`;
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [clock, scale, width]);

  function resize(index: number, startX: number, startHold: number) {
    const move = (event: PointerEvent) => {
      const hold = Math.min(MAX_HOLD, Math.max(0, startHold + (event.clientX - startX) / scale));
      onChange({
        ...plan,
        scenes: plan.scenes.map((s, i) =>
          i === index ? { ...s, hold: Math.round(hold * 20) / 20 } : s,
        ),
      });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function moveScene(from: number, to: number) {
    if (from === to) return;
    const scenes = [...plan.scenes];
    const [one] = scenes.splice(from, 1);
    scenes.splice(to, 0, one);
    onChange({ ...plan, scenes });
  }

  // Where each scene starts on the strip, in seconds.
  const starts = durations.map((_, i) => durations.slice(0, i).reduce((a, b) => a + b, 0));
  const ctaStart = durations.reduce((a, b) => a + b, 0);
  return (
    <div className="space-y-1">
      <div
        ref={strip}
        className="relative h-16 w-full touch-none overflow-hidden rounded-md border-[1.5px] border-foreground bg-[var(--field)] select-none"
        data-testid="animation-timeline"
      >
        {plan.scenes.map((scene, i) => {
          const left = starts[i] * scale;
          const w = Math.max(18, durations[i] * scale - 2);
          const start = starts[i];
          return (
            <div
              key={i}
              draggable
              onDragStart={(e) => {
                setDragging(i);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragEnd={() => setDragging(null)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging !== null) moveScene(dragging, i);
                setDragging(null);
              }}
              onClick={() => onSeek(start)}
              className={cn(
                "absolute top-1 bottom-1 flex cursor-grab items-center overflow-hidden rounded px-1.5 text-[11px] font-semibold text-white",
                dragging === i && "opacity-50",
              )}
              style={{ left, width: w, background: COLORS[scene.visual.kind] ?? "#64748B" }}
              title={`Scene ${i + 1}: ${durations[i].toFixed(1)} s (pause after the voice ${scene.hold.toFixed(2)} s)`}
              data-testid="timeline-scene"
            >
              <span className="truncate">
                {i + 1} · {scene.visual.kind}
              </span>
              <span
                role="slider"
                aria-label={`Scene ${i + 1} pause after the voice`}
                aria-valuemin={0}
                aria-valuemax={MAX_HOLD}
                aria-valuenow={scene.hold}
                tabIndex={0}
                className="absolute top-0 right-0 bottom-0 w-3 cursor-ew-resize bg-black/25 hover:bg-black/45"
                onPointerDown={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  resize(i, e.clientX, scene.hold);
                }}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  const by = e.key === "ArrowRight" ? 0.1 : e.key === "ArrowLeft" ? -0.1 : 0;
                  if (!by) return;
                  e.preventDefault();
                  const hold = Math.min(MAX_HOLD, Math.max(0, scene.hold + by));
                  onChange({
                    ...plan,
                    scenes: plan.scenes.map((s, j) =>
                      j === i ? { ...s, hold: Math.round(hold * 20) / 20 } : s,
                    ),
                  });
                }}
              />
            </div>
          );
        })}
        <div
          className="absolute top-1 bottom-1 flex items-center overflow-hidden rounded px-1.5 text-[11px] font-semibold text-white"
          style={{
            left: ctaStart * scale,
            width: Math.max(18, ctaLength * scale - 2),
            background: COLORS.cta,
          }}
        >
          <span className="truncate">Follow</span>
        </div>
        <div
          ref={head}
          className="pointer-events-none absolute top-0 bottom-0 left-0 w-0.5 bg-foreground"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {total.toFixed(0)} s. Drag a scene&apos;s right edge to hold it longer after its words, drag
        a scene onto another to move it, tap one to play from it.
      </p>
    </div>
  );
}
