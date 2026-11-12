"use client";

import { Eye, EyeOff, Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { presetPreview, type Preset, type PreviewShape } from "@/lib/api";

const SHAPES: { value: PreviewShape; label: string }[] = [
  { value: "16:9", label: "16:9" },
  { value: "4:3", label: "4:3" },
  { value: "1:1", label: "1:1" },
  { value: "4:5", label: "4:5" },
  { value: "9:16", label: "9:16" },
];
// Wait this long after the last change before drawing, so typing doesn't ask for a still
// on every letter.
const SETTLE_MS = 350;
const HIDDEN_KEY = "preset-preview-hidden";
const SHAPE_KEY = "preset-preview-shape";

/** The video shape a preset is drawn on first: the one last picked for it, else its own
 * frame's (a 9:16 preset is mostly given 9:16 reels, and a reel's shape decides how it
 * is cut between the text), so the preview starts as the render will look. */
function startShape(preset: Preset): PreviewShape {
  try {
    const kept = window.localStorage.getItem(`${SHAPE_KEY}:${preset.name}`);
    if (SHAPES.some((s) => s.value === kept)) return kept as PreviewShape;
  } catch {}
  const [w, h] = String(preset.resolution ?? "")
    .split("x")
    .map(Number);
  if (!(w > 0 && h > 0)) return "9:16";
  const own = SHAPES.map((s) => {
    const [a, b] = s.value.split(":").map(Number);
    return { value: s.value, off: Math.abs(Math.log(a / b / (w / h))) };
  }).sort((x, y) => x.off - y.off)[0];
  return own.value;
}

function frameAspect(resolution: string): string {
  const [w, h] = resolution.split("x").map(Number);
  return w > 0 && h > 0 ? `${w} / ${h}` : "9 / 16";
}

/** A small phone-like screen that keeps showing what the preset being edited makes. */
export function PresetPreview({ preset }: { preset: Preset }) {
  const [shape, setShapeNow] = useState<PreviewShape>(() => startShape(preset));
  // Another preset opened: drawn on its own shape (what was last picked for it).
  const [shapeFor, setShapeFor] = useState(preset.name);
  if (shapeFor !== preset.name) {
    setShapeFor(preset.name);
    setShapeNow(startShape(preset));
  }
  function setShape(next: PreviewShape) {
    setShapeNow(next);
    try {
      window.localStorage.setItem(`${SHAPE_KEY}:${preset.name}`, next);
    } catch {}
  }
  const [url, setUrl] = useState<string | null>(null);
  // What the still on screen was drawn for; it's out of date while that isn't `key`.
  const [drawnFor, setDrawnFor] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  // Only drawn once a preset has loaded in the browser, so window is there.
  const [hidden, setHidden] = useState(() => {
    try {
      return window.localStorage.getItem(HIDDEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const shown = useRef<string | null>(null);
  const wanted = JSON.stringify(preset);
  const key = `${shape} ${wanted}`;
  const stale = drawnFor !== key;

  useEffect(() => {
    if (hidden && !open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const drawn = await presetPreview(JSON.parse(wanted), shape, controller.signal);
        if (drawn === null) {
          setBroken(true);
          setDrawnFor(key);
          return;
        }
        setNotes(drawn.notes);
        const next = URL.createObjectURL(drawn.image);
        if (shown.current) URL.revokeObjectURL(shown.current);
        shown.current = next;
        setUrl(next);
        setBroken(false);
        setDrawnFor(key);
      } catch {
        // Aborted by a newer change, or offline: the last still stays.
      }
    }, SETTLE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [key, wanted, shape, hidden, open]);

  useEffect(
    () => () => {
      if (shown.current) URL.revokeObjectURL(shown.current);
    },
    [],
  );

  function hide(next: boolean) {
    setHidden(next);
    try {
      window.localStorage.setItem(HIDDEN_KEY, next ? "1" : "0");
    } catch {}
  }

  const aspect = frameAspect(preset.resolution);
  const screen = (big: boolean) => (
    <div
      className={`relative overflow-hidden rounded-[10px] border-[3px] border-[#2b2b2b] bg-black ${
        big ? "mx-auto max-h-[70vh] w-auto" : "w-full"
      }`}
      style={{ aspectRatio: aspect, ...(big ? { height: "70vh", maxWidth: "100%" } : {}) }}
    >
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={`How ${preset.label} looks on a ${shape} video`}
          className={`h-full w-full object-contain transition-opacity ${stale ? "opacity-70" : ""}`}
        />
      ) : (
        <p className="p-2 text-center text-[10px] text-white/70">Drawing...</p>
      )}
      {broken && (
        <p className="absolute inset-x-0 bottom-0 bg-[#c2410c] p-1 text-center text-[10px] leading-tight text-white">
          Fix the highlighted settings to update
        </p>
      )}
    </div>
  );

  const shapes = (
    <div className="flex flex-wrap justify-center gap-1" role="radiogroup" aria-label="Video shape">
      {SHAPES.map((s) => (
        <button
          key={s.value}
          type="button"
          role="radio"
          aria-checked={shape === s.value}
          onClick={() => setShape(s.value)}
          className={`min-h-8 min-w-11 rounded-md border px-1.5 text-xs ${
            shape === s.value
              ? "border-[#1f5f63] bg-[#1f5f63] text-white"
              : "border-border bg-background"
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  );

  return (
    <>
      {hidden ? (
        <Button
          variant="outline"
          size="sm"
          className="fixed right-3 bottom-20 z-40 shadow-md"
          onClick={() => hide(false)}
        >
          <Eye /> Preview
        </Button>
      ) : (
        <aside
          aria-label="Preview"
          className="sketch-tile fixed right-3 bottom-20 z-40 w-[132px] space-y-1.5 bg-background p-2 shadow-lg sm:w-[200px]"
        >
          <div className="flex items-center justify-between">
            <span className="font-hand text-lg leading-none font-bold">Preview</span>
            <span className="flex">
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Bigger preview"
                onClick={() => setOpen(true)}
              >
                <Maximize2 />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Hide the preview"
                onClick={() => hide(true)}
              >
                <EyeOff />
              </Button>
            </span>
          </div>
          <button
            type="button"
            className="block w-full"
            onClick={() => setOpen(true)}
            aria-label="Open a bigger preview"
          >
            {screen(false)}
          </button>
          {notes.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen(true)}
              className="w-full rounded-md bg-[var(--note-bg)] px-1 py-1 text-left text-[11px] leading-tight text-[var(--note-ink)]"
            >
              {notes.length === 1 ? "1 thing to check" : `${notes.length} things to check`}
            </button>
          )}
          <div className="hidden sm:block">{shapes}</div>
          <button
            type="button"
            className="min-h-8 w-full rounded-md border text-xs sm:hidden"
            onClick={() =>
              setShape(
                SHAPES[(SHAPES.findIndex((s) => s.value === shape) + 1) % SHAPES.length].value,
              )
            }
            aria-label={`Video shape ${shape}; tap for the next`}
          >
            Video {shape}
          </button>
        </aside>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[95vh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{preset.label}</DialogTitle>
            <DialogDescription>
              A drawn stand-in for the video, in the shape you pick, with sample words for
              {" {song}"} and the like. Captions show size and place; their letters can differ a
              little in the real video.
            </DialogDescription>
          </DialogHeader>
          {shapes}
          {notes.length > 0 && (
            <ul className="list-disc space-y-1 rounded-md bg-[var(--note-bg)] py-2 pr-2 pl-6 text-sm text-[var(--note-ink)]">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {screen(true)}
        </DialogContent>
      </Dialog>
    </>
  );
}
