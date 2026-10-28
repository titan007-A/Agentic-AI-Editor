"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { Preset } from "@/lib/api";

type Field =
  | { path: string; label: string; kind: "text" | "textarea" | "color" | "font" | "switch" }
  | { path: string; label: string; kind: "number"; step?: number }
  | { path: string; label: string; kind: "select"; options: string[] };

// Fields the schema allows to be null. An empty input is sent as null rather than "".
const NULLABLE = new Set([
  "intro",
  "outro",
  "logo.file",
  "music.file",
  "title_overlay.box_color",
  "captions.language",
  "one_liner.box_color",
  "one_liner.duration",
  "grade.lut",
  "framing.text_gap_above",
  "framing.text_gap_below",
]);

export const SECTIONS: { title: string; fields: Field[] }[] = [
  {
    title: "General",
    fields: [
      { path: "label", label: "Label", kind: "text" },
      { path: "platform", label: "Platform", kind: "text" },
      { path: "aspect_ratio", label: "Aspect ratio", kind: "text" },
      { path: "resolution", label: "Resolution", kind: "text" },
      { path: "fps", label: "FPS", kind: "number" },
      { path: "bitrate_mbps", label: "Bitrate (Mbps)", kind: "number", step: 0.5 },
      { path: "min_duration", label: "Min length (s)", kind: "number" },
      { path: "max_duration", label: "Max length (s)", kind: "number" },
      { path: "zoom_punch_ins", label: "Zoom on emphasis words", kind: "switch" },
      { path: "style_notes", label: "Style notes for the planner", kind: "textarea" },
    ],
  },
  {
    title: "Captions",
    fields: [
      { path: "captions.enabled", label: "Show captions", kind: "switch" },
      {
        path: "captions.mode",
        label: "What they say",
        kind: "select",
        options: ["speech", "lyrics", "translated", "visual"],
      },
      { path: "captions.language", label: "Translate into", kind: "text" },
      {
        path: "captions.avoid_faces",
        label: "Keep captions off faces and on-screen text",
        kind: "switch",
      },
      { path: "captions.export_srt", label: "Also save an .srt file", kind: "switch" },
      { path: "captions.uppercase", label: "Uppercase", kind: "switch" },
      { path: "captions.font", label: "Font", kind: "font" },
      { path: "captions.size", label: "Size", kind: "number" },
      { path: "captions.color", label: "Color", kind: "color" },
      { path: "captions.highlight_color", label: "Highlight color", kind: "color" },
      { path: "captions.outline_color", label: "Outline color", kind: "color" },
      { path: "captions.outline", label: "Outline width", kind: "number", step: 0.5 },
      {
        path: "captions.position",
        label: "Position",
        kind: "select",
        options: ["top", "middle", "bottom"],
      },
      { path: "captions.margin", label: "Margin (px)", kind: "number" },
      { path: "captions.words_per_line", label: "Words per line", kind: "number" },
      {
        path: "captions.animation",
        label: "Animation",
        kind: "select",
        options: ["highlight", "pop", "karaoke", "none"],
      },
    ],
  },
  {
    title: "Framing",
    fields: [
      {
        path: "framing.mode",
        label: "Mode",
        kind: "select",
        options: ["smart", "face", "center", "none"],
      },
      { path: "framing.background", label: "Background", kind: "color" },
      { path: "framing.auto_background", label: "Background from the footage", kind: "switch" },
      { path: "framing.crop_black_bars", label: "Crop black bars", kind: "switch" },
      {
        path: "framing.picture_width",
        label: "Picture width when not cropped (share of the frame)",
        kind: "number",
        step: 0.01,
      },
      { path: "framing.smoothing", label: "Tracking smoothing", kind: "number", step: 0.05 },
      { path: "framing.text_gap_above", label: "Gap above video (px)", kind: "number" },
      { path: "framing.text_gap_below", label: "Gap below video (px)", kind: "number" },
    ],
  },
  {
    title: "Colour",
    fields: [
      { path: "grade.auto", label: "Correct colour automatically", kind: "switch" },
      {
        path: "grade.look",
        label: "Look",
        kind: "select",
        options: [
          "none",
          "natural",
          "warm",
          "cool",
          "teal-orange",
          "film",
          "moody",
          "vivid",
          "mono",
          "faded",
        ],
      },
      { path: "grade.lut", label: "LUT file (.cube)", kind: "text" },
      { path: "grade.strength", label: "Look strength", kind: "number", step: 0.05 },
    ],
  },
  {
    title: "Clean-up",
    fields: [
      { path: "watermarks.blur", label: "Blur watermarks and logos", kind: "switch" },
      { path: "watermarks.strength", label: "Minimum blur (px)", kind: "number" },
    ],
  },
  {
    title: "Audio",
    fields: [
      {
        path: "audio.source",
        label: "Soundtrack",
        kind: "select",
        options: ["original", "vocals"],
      },
      { path: "audio.highpass_hz", label: "High-pass (Hz)", kind: "number" },
      { path: "audio.loudness_lufs", label: "Loudness (LUFS)", kind: "number", step: 0.5 },
      { path: "audio.true_peak_db", label: "True peak (dB)", kind: "number", step: 0.1 },
      { path: "audio.fade_in", label: "Fade in (s)", kind: "number", step: 0.01 },
      { path: "audio.fade_out", label: "Fade out (s)", kind: "number", step: 0.05 },
      { path: "silence.threshold_db", label: "Silence threshold (dB)", kind: "number" },
      { path: "silence.min_duration", label: "Min silence (s)", kind: "number", step: 0.05 },
    ],
  },
  {
    title: "One-liner",
    fields: [
      { path: "one_liner.enabled", label: "Write a one-line caption", kind: "switch" },
      { path: "one_liner.hint", label: "What it should be about", kind: "textarea" },
      { path: "one_liner.font", label: "Font", kind: "font" },
      { path: "one_liner.size", label: "Size", kind: "number" },
      { path: "one_liner.color", label: "Color", kind: "color" },
      { path: "one_liner.box_color", label: "Box color", kind: "color" },
      {
        path: "one_liner.position",
        label: "Position",
        kind: "select",
        options: ["top", "bottom"],
      },
      { path: "one_liner.offset", label: "Offset from edge (px)", kind: "number" },
      { path: "one_liner.duration", label: "Seconds on screen (empty: all)", kind: "number" },
      { path: "one_liner.max_words", label: "Max words", kind: "number" },
    ],
  },
  {
    title: "Headline",
    fields: [
      {
        path: "headline.hint",
        label: "How to write {headline} (or give it in the notes as headline: ...)",
        kind: "textarea",
      },
      { path: "headline.max_chars", label: "Max letters", kind: "number" },
      { path: "headline.uppercase", label: "In capitals", kind: "switch" },
    ],
  },
  {
    title: "Post caption",
    fields: [
      { path: "post_caption.enabled", label: "Write it after every render", kind: "switch" },
      { path: "post_caption.language", label: "Language (es, en, it...)", kind: "text" },
      { path: "post_caption.style", label: "How to write it (empty: default)", kind: "textarea" },
      { path: "post_caption.min_words", label: "Min words", kind: "number" },
      { path: "post_caption.max_words", label: "Max words", kind: "number" },
    ],
  },
  {
    title: "Beat",
    fields: [
      { path: "beat_sync.snap_trims", label: "Start and end on the beat", kind: "switch" },
      { path: "beat_sync.pulse", label: "Pulse on the beat", kind: "switch" },
      {
        path: "beat_sync.pulse_on",
        label: "Pulse on every",
        kind: "select",
        options: ["bar", "beat"],
      },
      { path: "beat_sync.pulse_scale", label: "Pulse size", kind: "number", step: 0.01 },
    ],
  },
  {
    title: "Hook and title",
    fields: [
      { path: "hook.enabled", label: "Plan a hook", kind: "switch" },
      { path: "hook.overlay", label: "Show hook as text", kind: "switch" },
      { path: "hook.optimize", label: "Pick the strongest opening", kind: "switch" },
      { path: "hook.variants", label: "Versions per video", kind: "number" },
      { path: "title_overlay.enabled", label: "Title overlay", kind: "switch" },
      { path: "title_overlay.font", label: "Title font", kind: "font" },
      { path: "title_overlay.size", label: "Title size", kind: "number" },
      { path: "title_overlay.color", label: "Title color", kind: "color" },
      { path: "title_overlay.box_color", label: "Title box color", kind: "color" },
      { path: "title_overlay.offset", label: "Title offset (px)", kind: "number" },
      { path: "title_overlay.duration", label: "Title duration (s)", kind: "number", step: 0.5 },
    ],
  },
  {
    title: "Branding",
    fields: [
      { path: "logo.file", label: "Logo file", kind: "text" },
      {
        path: "logo.position",
        label: "Logo corner",
        kind: "select",
        options: ["top-left", "top-right", "bottom-left", "bottom-right"],
      },
      { path: "logo.opacity", label: "Logo opacity", kind: "number", step: 0.05 },
      { path: "logo.width", label: "Logo width (px)", kind: "number" },
      { path: "logo.margin", label: "Logo margin (px)", kind: "number" },
      { path: "intro", label: "Intro clip", kind: "text" },
      { path: "outro", label: "Outro clip", kind: "text" },
      { path: "music.file", label: "Background music", kind: "text" },
      { path: "music.volume_db", label: "Music volume (dB)", kind: "number" },
      { path: "music.ducking", label: "Duck music under speech", kind: "switch" },
    ],
  },
];

export const NEW_TEXT_BLOCK = {
  text: "",
  font: "OpenSans-ExtraBold",
  size: 60,
  min_size: 28,
  color: "#FFFFFF",
  anchor: "top",
  offset: 340,
  max_width: 1000,
  line_spacing: 1.0,
  max_shrink: 1.0,
};

const TEXT_BLOCK_FIELDS: Field[] = [
  { path: "font", label: "Font", kind: "font" },
  { path: "color", label: "Color", kind: "color" },
  { path: "anchor", label: "Anchor", kind: "select", options: ["top", "bottom"] },
  { path: "offset", label: "Offset from edge (px)", kind: "number" },
  { path: "size", label: "Size", kind: "number" },
  { path: "min_size", label: "Min size", kind: "number" },
  { path: "max_width", label: "Max width (px)", kind: "number" },
  { path: "max_shrink", label: "Max shrink", kind: "number", step: 0.05 },
  { path: "line_spacing", label: "Line spacing", kind: "number", step: 0.05 },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Obj = Record<string, any>;

export function getPath(obj: Obj, path: string): unknown {
  return path.split(".").reduce<unknown>((o, k) => (o as Obj | undefined)?.[k], obj);
}

export function setPath<T extends Obj>(obj: T, path: string, value: unknown): T {
  const [head, ...rest] = path.split(".");
  return {
    ...obj,
    [head]: rest.length ? setPath(obj[head] ?? {}, rest.join("."), value) : value,
  };
}

function FieldInput({
  id,
  field,
  value,
  nullable,
  fonts,
  onChange,
}: {
  id: string;
  field: Field;
  value: unknown;
  nullable: boolean;
  fonts: string[];
  onChange: (value: unknown) => void;
}) {
  const empty = nullable ? null : "";
  switch (field.kind) {
    case "switch":
      return <Switch id={id} checked={value === true} onCheckedChange={onChange} />;
    case "textarea":
      return (
        <Textarea
          id={id}
          rows={5}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "number":
      return (
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          step={field.step ?? 1}
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? empty : Number(e.target.value))}
        />
      );
    case "color":
      return (
        <div className="flex gap-2">
          <input
            type="color"
            aria-label={`${field.label} picker`}
            className="h-9 w-10 shrink-0 cursor-pointer rounded-[6px_3px_7px_3px] border-[1.5px] border-foreground bg-transparent p-1"
            value={(value as string) ?? "#000000"}
            onChange={(e) => onChange(e.target.value.toUpperCase())}
          />
          <Input
            id={id}
            className="font-mono"
            placeholder={nullable ? "none" : "#RRGGBB"}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value || empty)}
          />
        </div>
      );
    case "select":
    case "font": {
      const options = field.kind === "select" ? field.options : fonts;
      const current = String(value ?? "");
      return (
        <Select value={current} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(options.includes(current) || !current ? options : [current, ...options]).map(
              (option) => (
                <SelectItem key={option} value={option}>
                  {option}
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
      );
    }
    default:
      return (
        <Input
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value || empty)}
        />
      );
  }
}

function FieldGrid({
  prefix,
  fields,
  values,
  fonts,
  nullable,
  onChange,
}: {
  prefix: string;
  fields: Field[];
  values: Obj;
  fonts: string[];
  nullable: (path: string) => boolean;
  onChange: (path: string, value: unknown) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {fields.map((field) => {
        const id = `${prefix}-${field.path}`;
        const wide = field.kind === "textarea";
        const inline = field.kind === "switch";
        return (
          <div
            key={field.path}
            className={
              inline
                ? "sketch-tile flex items-center justify-between gap-3 px-3 py-2"
                : wide
                  ? "space-y-2 sm:col-span-2"
                  : "space-y-2"
            }
          >
            <Label htmlFor={id}>{field.label}</Label>
            <FieldInput
              id={id}
              field={field}
              value={getPath(values, field.path)}
              nullable={nullable(field.path)}
              fonts={fonts}
              onChange={(value) => onChange(field.path, value)}
            />
          </div>
        );
      })}
    </div>
  );
}

export function PresetSection({
  fields,
  preset,
  fonts,
  onChange,
}: {
  fields: Field[];
  preset: Preset;
  fonts: string[];
  onChange: (preset: Preset) => void;
}) {
  return (
    <FieldGrid
      prefix="preset"
      fields={fields}
      values={preset}
      fonts={fonts}
      nullable={(path) => NULLABLE.has(path)}
      onChange={(path, value) => onChange(setPath(preset, path, value))}
    />
  );
}

export function TextBlocks({
  preset,
  fonts,
  onChange,
}: {
  preset: Preset;
  fonts: string[];
  onChange: (preset: Preset) => void;
}) {
  const blocks: Obj[] = preset.text_blocks ?? [];
  const update = (next: Obj[]) => onChange({ ...preset, text_blocks: next });

  return (
    <div className="space-y-4">
      {blocks.map((block, i) => (
        <div key={i} className="sketch-tile space-y-4 border-dashed p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Text {i + 1}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Remove text ${i + 1}`}
              onClick={() => update(blocks.filter((_, j) => j !== i))}
            >
              <Trash2 />
            </Button>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`block-${i}-text`}>Text</Label>
            <Textarea
              id={`block-${i}-text`}
              rows={3}
              value={block.text}
              onChange={(e) =>
                update(blocks.map((b, j) => (j === i ? { ...b, text: e.target.value } : b)))
              }
            />
          </div>
          <FieldGrid
            prefix={`block-${i}`}
            fields={TEXT_BLOCK_FIELDS}
            values={block}
            fonts={fonts}
            nullable={() => false}
            onChange={(path, value) =>
              update(blocks.map((b, j) => (j === i ? setPath(b, path, value) : b)))
            }
          />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => update([...blocks, NEW_TEXT_BLOCK])}>
        <Plus /> Add text
      </Button>
    </div>
  );
}
