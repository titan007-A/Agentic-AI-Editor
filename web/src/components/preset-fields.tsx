"use client";

import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { api, errorText, type AssetKind, type Preset } from "@/lib/api";
import { fieldLabel, textFields } from "@/lib/preset-fields.mjs";

type Field =
  | {
      path: string;
      label: string;
      kind: "text" | "textarea" | "color" | "font" | "switch" | "language";
    }
  | { path: string; label: string; kind: "number"; step?: number }
  | { path: string; label: string; kind: "select"; options: string[] }
  | { path: string; label: string; kind: "file"; accept: AssetKind };

const ACCEPT: Record<AssetKind, string> = {
  image: ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp",
  video: ".mp4,.mov,.mkv,.webm,.m4v,.avi,video/*",
  audio: ".mp3,.m4a,.aac,.wav,.flac,.ogg,.opus,audio/*",
  lut: ".cube",
};
// Where the editor's uploads are kept; a file there can be shown back by its name.
const UPLOADED = /(^|\/)media\/assets\/([0-9a-f]{12}-[\w-]+\.[a-z0-9]+)$/;

// The languages the editor names (agent.LANGUAGES), by two-letter code.
const LANGUAGES: [string, string][] = [
  ["ar", "Arabic"],
  ["bn", "Bengali"],
  ["zh", "Chinese"],
  ["nl", "Dutch"],
  ["en", "English"],
  ["fr", "French"],
  ["de", "German"],
  ["hi", "Hindi"],
  ["id", "Indonesian"],
  ["it", "Italian"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
  ["mr", "Marathi"],
  ["pl", "Polish"],
  ["pt", "Portuguese"],
  ["pa", "Punjabi"],
  ["ru", "Russian"],
  ["es", "Spanish"],
  ["ta", "Tamil"],
  ["te", "Telugu"],
  ["th", "Thai"],
  ["tr", "Turkish"],
  ["uk", "Ukrainian"],
  ["ur", "Urdu"],
  ["vi", "Vietnamese"],
];

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
  "one_liner.language",
  "grade.lut",
  "audio.language",
  "framing.background_image",
  "framing.text_gap_above",
  "framing.text_gap_below",
]);

/** A preset that only makes the video bigger and sharper: nothing else in it is used. */
export const UPSCALE_FIELDS: Field[] = [
  {
    path: "upscale.enabled",
    label: "Only upscale (no cutting, text or sound changes)",
    kind: "switch",
  },
  {
    path: "upscale.size",
    label: "Size (the short side: 1080p is 1080x1920 for a reel)",
    kind: "select",
    options: ["1080p", "1440p", "4k"],
  },
  {
    path: "upscale.faces",
    label: "Face fix (0 off, 0.5 light, 1 full)",
    kind: "number",
    step: 0.05,
  },
  {
    path: "upscale.denoise",
    label: "Smoothing (0 keeps grain, 1 smooths most)",
    kind: "number",
    step: 0.05,
  },
];

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
      {
        path: "framing.only_with_text",
        label:
          "Smart crop only a video with text on it, cropping the text away (one without stays whole)",
        kind: "switch",
      },
      { path: "framing.background", label: "Background", kind: "color" },
      {
        path: "framing.background_image",
        label: "Background picture (instead of the colour)",
        kind: "file",
        accept: "image",
      },
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
      { path: "grade.lut", label: "LUT file (.cube)", kind: "file", accept: "lut" },
      { path: "grade.strength", label: "Look strength", kind: "number", step: 0.05 },
    ],
  },
  {
    title: "Clean-up",
    fields: [
      { path: "watermarks.blur", label: "Blur watermarks and logos", kind: "switch" },
      { path: "watermarks.strength", label: "Minimum blur (px)", kind: "number" },
      {
        path: "watermarks.cut_text",
        label: "Cut off the video's own text at the picture's edges",
        kind: "switch",
      },
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
      {
        path: "audio.language",
        label: "Language sung or spoken (two letters, e.g. es; empty: heard by itself)",
        kind: "text",
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
      { path: "one_liner.language", label: "Language it is written in", kind: "language" },
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
      {
        path: "post_caption.about",
        label: "About (song: the song in it; video: what the video shows, from watching it)",
        kind: "select",
        options: ["song", "video"],
      },
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
      { path: "logo.file", label: "Logo file", kind: "file", accept: "image" },
      {
        path: "logo.position",
        label: "Logo corner",
        kind: "select",
        options: ["top-left", "top-right", "bottom-left", "bottom-right"],
      },
      { path: "logo.opacity", label: "Logo opacity", kind: "number", step: 0.05 },
      { path: "logo.width", label: "Logo width (px)", kind: "number" },
      { path: "logo.margin", label: "Logo margin (px)", kind: "number" },
      { path: "intro", label: "Intro clip", kind: "file", accept: "video" },
      { path: "outro", label: "Outro clip", kind: "file", accept: "video" },
      { path: "music.file", label: "Background music", kind: "file", accept: "audio" },
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
    case "language": {
      const current = String(value ?? "");
      const known = LANGUAGES.some(
        ([code, name]) => code === current || name.toLowerCase() === current.toLowerCase(),
      );
      const chosen =
        LANGUAGES.find(([, name]) => name.toLowerCase() === current.toLowerCase())?.[0] ?? current;
      return (
        <select
          id={id}
          className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
          value={chosen}
          onChange={(e) => onChange(e.target.value || empty)}
        >
          <option value="">The model picks (or the captions&apos; language)</option>
          {!known && current && <option value={current}>{current}</option>}
          {LANGUAGES.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
      );
    }
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
    case "file":
      return (
        <FileInput
          id={id}
          label={field.label}
          kind={field.accept}
          value={(value as string | null) ?? null}
          onChange={(next) => onChange(next ?? empty)}
        />
      );
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

/** A file a preset uses: sent from this device, or named by its path on the server. */
function FileInput({
  id,
  label,
  kind,
  value,
  onChange,
}: {
  id: string;
  label: string;
  kind: AssetKind;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const uploaded = value ? UPLOADED.exec(value)?.[2] : undefined;

  async function send(file: File | undefined) {
    if (!file) return;
    setSending(true);
    try {
      const { path } = await api.uploadAsset(file, kind);
      onChange(path);
      toast.success(`${file.name} added. Save the preset to keep it.`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSending(false);
      if (picker.current) picker.current.value = "";
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Input
          id={id}
          className="min-w-0 font-mono text-xs"
          placeholder="none"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
        {value && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-11 shrink-0"
            aria-label={`Remove the ${label.toLowerCase()}`}
            onClick={() => onChange(null)}
          >
            <X />
          </Button>
        )}
      </div>
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={sending}
          onClick={() => picker.current?.click()}
        >
          <Upload /> {sending ? "Uploading..." : value ? "Upload another" : "Upload"}
        </Button>
        {kind === "image" && uploaded && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`/api/assets/${uploaded}`}
            alt={label}
            className="h-11 max-w-[120px] rounded border bg-[repeating-conic-gradient(#ddd_0%_25%,#fff_0%_50%)] bg-[length:12px_12px] object-contain"
          />
        )}
        {kind !== "image" && uploaded && (
          <span className="min-w-0 truncate text-xs text-muted-foreground">
            {uploaded.slice(13)}
          </span>
        )}
      </div>
      <input
        ref={picker}
        type="file"
        accept={ACCEPT[kind]}
        className="hidden"
        aria-label={`${label} to upload`}
        onChange={(e) => send(e.target.files?.[0])}
      />
    </div>
  );
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
      <RandomFields preset={preset} onChange={onChange} />
    </div>
  );
}

/** For each word in braces the text writes ({day}, {song_number}): a random number for every
 * reel instead of one typed for each job. */
function RandomFields({
  preset,
  onChange,
}: {
  preset: Preset;
  onChange: (preset: Preset) => void;
}) {
  const names: string[] = textFields(preset);
  if (names.length === 0) return null;
  const ranges: Record<string, [number, number]> = preset.random_fields ?? {};
  const set = (name: string, range: [number, number] | null) => {
    const next = { ...ranges };
    if (range) next[name] = range;
    else delete next[name];
    onChange({ ...preset, random_fields: next });
  };
  const whole = (text: string) => (/^-?\d+$/.test(text.trim()) ? Number(text) : 0);

  return (
    <fieldset className="space-y-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Random numbers</legend>
      <p className="text-xs text-muted-foreground">
        Ticked, a word in braces gets a new random number on every reel, and the job doesn&apos;t
        ask for it. A line like &quot;day: 5&quot; in a job&apos;s notes still wins.
      </p>
      {names.map((name) => {
        const range = ranges[name];
        const id = `random-${name}`;
        return (
          <div key={name} className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id={id}
                checked={!!range}
                onCheckedChange={(on) => set(name, on ? [1, 100] : null)}
              />
              <Label htmlFor={id}>
                {fieldLabel(name)} <span className="text-muted-foreground">{`{${name}}`}</span>
              </Label>
            </div>
            {range && (
              <div className="flex items-center gap-2">
                <Input
                  aria-label={`${fieldLabel(name)} from`}
                  className="w-20"
                  inputMode="numeric"
                  value={String(range[0])}
                  onChange={(e) => set(name, [whole(e.target.value), range[1]])}
                />
                <span className="text-sm">to</span>
                <Input
                  aria-label={`${fieldLabel(name)} to`}
                  className="w-20"
                  inputMode="numeric"
                  value={String(range[1])}
                  onChange={(e) => set(name, [range[0], whole(e.target.value)])}
                />
                {range[0] > range[1] && (
                  <span className="text-xs text-destructive">
                    the first must not be above the second
                  </span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

const IMAGE_FIELDS: Field[] = [
  { path: "file", label: "Picture", kind: "file", accept: "image" },
  { path: "x", label: "From the left (px)", kind: "number" },
  { path: "y", label: "From the top (px)", kind: "number" },
  { path: "width", label: "Width (px)", kind: "number" },
  { path: "opacity", label: "Opacity", kind: "number", step: 0.05 },
];
const NEW_IMAGE: Obj = { file: "", x: 60, y: 120, width: 300, opacity: 1 };

/** Pictures drawn on every video where the preset puts them: a page's avatar and name,
 * a badge. The preview shows where they land. */
export function ImageBlocks({
  preset,
  onChange,
}: {
  preset: Preset;
  onChange: (preset: Preset) => void;
}) {
  const images: Obj[] = preset.images ?? [];
  const update = (next: Obj[]) => onChange({ ...preset, images: next });

  return (
    <div className="space-y-4">
      {images.map((image, i) => (
        <div key={i} className="sketch-tile space-y-4 border-dashed p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Picture {i + 1}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Remove picture ${i + 1}`}
              onClick={() => update(images.filter((_, j) => j !== i))}
            >
              <Trash2 />
            </Button>
          </div>
          <FieldGrid
            prefix={`image-${i}`}
            fields={IMAGE_FIELDS}
            values={image}
            fonts={[]}
            nullable={() => false}
            onChange={(path, value) =>
              update(images.map((b, j) => (j === i ? setPath(b, path, value ?? "") : b)))
            }
          />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => update([...images, NEW_IMAGE])}>
        <Plus /> Add a picture
      </Button>
    </div>
  );
}

const WATERMARK_FIELDS: Field[] = [
  { path: "enabled", label: "Show my watermark", kind: "switch" },
  { path: "text", label: "Words (like @yourpage)", kind: "text" },
  { path: "file", label: "Picture (a logo; above the words)", kind: "file", accept: "image" },
  { path: "font", label: "Font", kind: "font" },
  { path: "size", label: "Text size", kind: "number" },
  { path: "color", label: "Text color", kind: "color" },
  { path: "outline", label: "Outline width", kind: "number", step: 0.5 },
  { path: "outline_color", label: "Outline color", kind: "color" },
  { path: "width", label: "Picture width (px)", kind: "number" },
  { path: "opacity", label: "Opacity (0.05 to 1)", kind: "number", step: 0.05 },
];
// Where Instagram draws over a reel (render/mark.py): its buttons down the right from 45%
// of the height, and its name and caption along the bottom fifth.
const UI_RIGHT = 0.15;
const UI_BOTTOM = 0.2;
const BUTTONS_FROM = 0.45;
const SPOTS: [number, number, string][] = [
  [0, 0, "top left"],
  [0.5, 0, "top middle"],
  [1, 0, "top right"],
  [0, 0.5, "middle left"],
  [0.5, 0.5, "middle"],
  [1, 0.5, "middle right"],
  [0, 1, "bottom left"],
  [0.5, 1, "bottom middle"],
  [1, 1, "bottom right"],
];

const share = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 100) / 100;

/** Where the watermark goes, by dragging a stand-in for it over the frame (or tapping, or
 * the arrow keys). x and y are shares of the room beside the mark, as a render reads them:
 * 0 flush with the left or top edge, 1 with the right or bottom. The preview shows the
 * real mark there. */
export function PositionPad({
  x,
  y,
  resolution,
  instagram,
  onChange,
}: {
  x: number;
  y: number;
  resolution: string;
  instagram: boolean;
  onChange: (x: number, y: number) => void;
}) {
  const pad = useRef<HTMLDivElement>(null);
  const [w, h] = resolution.split("x").map(Number);
  const aspect = w > 0 && h > 0 ? `${w} / ${h}` : "9 / 16";

  function at(clientX: number, clientY: number) {
    const box = pad.current?.getBoundingClientRect();
    if (!box) return;
    onChange(share((clientX - box.left) / box.width), share((clientY - box.top) / box.height));
  }

  function key(e: React.KeyboardEvent) {
    const step = e.shiftKey ? 0.1 : 0.01;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    onChange(share(x + move[0]), share(y + move[1]));
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start gap-4">
        <div
          ref={pad}
          role="group"
          tabIndex={0}
          aria-label={`Watermark position: ${Math.round(x * 100)}% across, ${Math.round(
            y * 100,
          )}% down. Drag, tap, or use the arrow keys (Shift for bigger steps).`}
          className="relative w-[150px] shrink-0 cursor-crosshair touch-none overflow-hidden rounded-md border-[1.5px] border-foreground bg-[#1f2937] focus-visible:ring-2 focus-visible:ring-[#1f5f63]"
          style={{ aspectRatio: aspect }}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            at(e.clientX, e.clientY);
          }}
          onPointerMove={(e) => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) at(e.clientX, e.clientY);
          }}
          onKeyDown={key}
        >
          {instagram && (
            <>
              <div
                className="absolute right-0 bg-[#c2410c]/35"
                style={{ top: `${BUTTONS_FROM * 100}%`, width: `${UI_RIGHT * 100}%`, bottom: 0 }}
              />
              <div
                className="absolute inset-x-0 bottom-0 bg-[#c2410c]/35"
                style={{ height: `${UI_BOTTOM * 100}%` }}
              />
            </>
          )}
          <div
            className="pointer-events-none absolute h-[8%] w-[45%] rounded-sm border-2 border-white bg-white/40"
            style={{
              left: `${x * 100}%`,
              top: `${y * 100}%`,
              transform: `translate(-${x * 100}%, -${y * 100}%)`,
            }}
          />
        </div>
        <div className="space-y-2">
          <div className="grid w-fit grid-cols-3 gap-1">
            {SPOTS.map(([sx, sy, name]) => (
              <button
                key={name}
                type="button"
                aria-label={`Put it ${name}`}
                aria-pressed={x === sx && y === sy}
                onClick={() => onChange(sx, sy)}
                className={`size-11 rounded-md border ${
                  x === sx && y === sy ? "border-[#1f5f63] bg-[#1f5f63]" : "bg-background"
                }`}
              />
            ))}
          </div>
          {instagram && (
            <p className="max-w-[220px] text-xs text-muted-foreground">
              Shaded: where Instagram draws its buttons and caption over a reel.
            </p>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {(
          [
            ["x", "Across (%)", x],
            ["y", "Down (%)", y],
          ] as const
        ).map(([axis, label, value]) => (
          <div key={axis} className="space-y-2">
            <Label htmlFor={`watermark-${axis}`}>{label}</Label>
            <Input
              id={`watermark-${axis}`}
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              value={Math.round(value * 100)}
              onChange={(e) => {
                const v = share(Number(e.target.value) / 100);
                onChange(axis === "x" ? v : x, axis === "y" ? v : y);
              }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/** The preset's own watermark: set here by hand, never learned from a reel. */
export function WatermarkFields({
  preset,
  fonts,
  onChange,
}: {
  preset: Preset;
  fonts: string[];
  onChange: (preset: Preset) => void;
}) {
  const mark: Obj = preset.watermark ?? {};
  const set = (next: Obj) => onChange({ ...preset, watermark: { ...mark, ...next } });
  return (
    <div className="space-y-5">
      <FieldGrid
        prefix="watermark"
        fields={WATERMARK_FIELDS}
        values={mark}
        fonts={fonts}
        nullable={(path) => path === "file"}
        onChange={(path, value) => onChange({ ...preset, watermark: setPath(mark, path, value) })}
      />
      <div className="space-y-2">
        <p className="text-sm font-medium">Position</p>
        <PositionPad
          x={typeof mark.x === "number" ? mark.x : 0.5}
          y={typeof mark.y === "number" ? mark.y : 0.75}
          resolution={preset.resolution ?? "1080x1920"}
          instagram={preset.platform === "instagram_reels"}
          onChange={(x, y) => set({ x, y })}
        />
      </div>
    </div>
  );
}

const NUDGE = 20; // px a tap on an arrow moves the video

/** Where the video sits in the frame, moved by the arrows or typed in px. Shown whole
 * (framing mode none), it moves; what would run into the text above or below, or off the
 * frame, is cut off, so the text never moves. In the other modes the video fills the
 * frame, so there's nothing to move: one tap shows it whole instead. */
export function VideoPosition({
  preset,
  onChange,
}: {
  preset: Preset;
  onChange: (preset: Preset) => void;
}) {
  const framing: Obj = preset.framing ?? {};
  const x = Number(framing.picture_x ?? 0) || 0;
  const y = Number(framing.picture_y ?? 0) || 0;
  const whole = framing.mode === "none";
  const follows = framing.text_follows_video !== false;
  const set = (next: Obj) => onChange({ ...preset, framing: { ...framing, ...next } });
  const move = (dx: number, dy: number) => set({ picture_x: x + dx, picture_y: y + dy });
  const arrow = (label: string, icon: React.ReactNode, dx: number, dy: number) => (
    <Button
      type="button"
      variant="outline"
      size="icon"
      className="size-11"
      aria-label={label}
      onClick={() => move(dx, dy)}
    >
      {icon}
    </Button>
  );

  if (!whole) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted-foreground">
          In framing mode &ldquo;{String(framing.mode ?? "smart")}&rdquo; the video fills the whole
          frame, so there&apos;s nowhere to move it. Show it whole (mode none) to place it between
          the text.
        </p>
        <Button type="button" variant="outline" onClick={() => set({ mode: "none" })}>
          Show the video whole so it can be moved
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <label className="flex min-h-11 items-center gap-3 text-sm">
        <Checkbox
          checked={follows}
          onCheckedChange={(v) => set({ text_follows_video: v === true })}
        />
        Text moves with the video
      </label>
      <div className="flex flex-wrap items-center gap-4">
        <div className="grid w-fit grid-cols-3 gap-1">
          <span />
          {arrow(`Move the video up ${NUDGE} px`, <ArrowUp />, 0, -NUDGE)}
          <span />
          {arrow(`Move the video left ${NUDGE} px`, <ArrowLeft />, -NUDGE, 0)}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-11"
            aria-label="Put the video back where it goes"
            onClick={() => set({ picture_x: 0, picture_y: 0 })}
          >
            <RotateCcw />
          </Button>
          {arrow(`Move the video right ${NUDGE} px`, <ArrowRight />, NUDGE, 0)}
          <span />
          {arrow(`Move the video down ${NUDGE} px`, <ArrowDown />, 0, NUDGE)}
          <span />
        </div>
        <p className="max-w-xs text-xs text-muted-foreground">
          {follows
            ? "The text above and below, the one-liner, the title and the captions move up and down with the video, so none of it is cut."
            : "The text stays where it is: what of the video would run into it is cut off."}{" "}
          Sideways only the video moves, when it is narrower than the frame. The preview shows it.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {(
          [
            ["picture_x", "Right (px; minus: left)", x],
            ["picture_y", "Down (px; minus: up)", y],
          ] as const
        ).map(([key, label, value]) => (
          <div key={key} className="space-y-2">
            <Label htmlFor={`video-${key}`}>{label}</Label>
            <Input
              id={`video-${key}`}
              type="number"
              inputMode="numeric"
              step={10}
              value={value}
              onChange={(e) => set({ [key]: Math.round(Number(e.target.value) || 0) })}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
