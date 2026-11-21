"use client";

import { ArrowDown, ArrowUp, Play, Plus, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type {
  AnimationItem,
  AnimationPlan,
  AnimationScene,
  AnimationVisual,
  AnimationVisualKind,
} from "@/lib/api";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";

export const VISUALS: Record<AnimationVisualKind, string> = {
  title: "Headline",
  stat: "Big number",
  icons: "Characters / objects",
  compare: "A vs B",
  list: "List",
  timeline: "Timeline",
  place: "Place on a globe",
  quote: "Quote",
  reveal: "Mystery reveal",
  dots: "Glowing dots",
};

type Field = { key: keyof AnimationVisual; label: string; long?: boolean };

const FIELDS: Record<AnimationVisualKind, Field[]> = {
  title: [
    { key: "text", label: "Headline" },
    { key: "highlight", label: "Highlighted words" },
    { key: "kicker", label: "Small label above (optional)" },
  ],
  stat: [
    { key: "value", label: "Number" },
    { key: "prefix", label: "Before it ($, ~)" },
    { key: "suffix", label: "After it (million, %, years)" },
    { key: "text", label: "What it counts" },
  ],
  icons: [{ key: "text", label: "Caption" }],
  compare: [],
  list: [{ key: "text", label: "Title" }],
  timeline: [],
  place: [
    { key: "text", label: "Place" },
    { key: "detail", label: "Detail (country, era)" },
    { key: "emoji", label: "Pin emoji" },
  ],
  quote: [
    { key: "text", label: "Quote", long: true },
    { key: "by", label: "Who said it" },
  ],
  reveal: [
    { key: "text", label: "Question" },
    { key: "answer", label: "Answer" },
    { key: "emoji", label: "Emoji" },
  ],
  dots: [
    { key: "value", label: "Word or number (max 8)" },
    { key: "text", label: "Caption" },
  ],
};

const ITEMS: Partial<Record<AnimationVisualKind, { keys: (keyof AnimationItem)[]; max: number }>> =
  {
    icons: { keys: ["emoji", "motion"], max: 3 },
    compare: { keys: ["emoji", "text", "value"], max: 2 },
    list: { keys: ["emoji", "text"], max: 5 },
    timeline: { keys: ["year", "text"], max: 5 },
  };
const ITEM_LABEL: Record<keyof AnimationItem, string> = {
  emoji: "Emoji",
  text: "Text",
  year: "Year",
  value: "Value",
  motion: "Motion",
};
const MOTIONS = [
  "pop",
  "float",
  "walk_left",
  "walk_right",
  "shake",
  "spin",
  "grow",
  "fall",
  "pulse",
];

export function blankVisual(kind: AnimationVisualKind = "title"): AnimationVisual {
  return {
    kind,
    text: "",
    highlight: "",
    kicker: "",
    value: "",
    prefix: "",
    suffix: "",
    detail: "",
    by: "",
    answer: "",
    emoji: "",
    items: [],
  };
}

const blankItem = (): AnimationItem => ({
  emoji: "",
  text: "",
  year: "",
  value: "",
  motion: "pop",
});

/**
 * The reel scene by scene: what the voice says and what is shown, each field editable,
 * scenes moved, removed, added, or written again by the AI. Edits are kept here until
 * saved; saving records the changed scenes' voice again.
 */
export function AnimationScenes({
  plan,
  starts,
  busy,
  onChange,
  onRedo,
  onPlayFrom,
}: {
  plan: AnimationPlan;
  /** When each scene starts in the preview (seconds). */
  starts: number[];
  busy: boolean;
  onChange: (plan: AnimationPlan) => void;
  onRedo: (index: number) => void;
  onPlayFrom: (seconds: number) => void;
}) {
  function setScene(i: number, scene: AnimationScene) {
    onChange({ ...plan, scenes: plan.scenes.map((s, j) => (j === i ? scene : s)) });
  }
  function move(i: number, by: number) {
    const scenes = [...plan.scenes];
    const [one] = scenes.splice(i, 1);
    scenes.splice(i + by, 0, one);
    onChange({ ...plan, scenes });
  }
  function remove(i: number) {
    onChange({ ...plan, scenes: plan.scenes.filter((_, j) => j !== i) });
  }
  function add() {
    onChange({
      ...plan,
      scenes: [...plan.scenes, { narration: "", visual: blankVisual("title"), hold: 0.35 }],
    });
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="anim-title">Title (small, at the top)</Label>
          <Input
            id="anim-title"
            value={plan.title}
            onChange={(e) => onChange({ ...plan, title: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="anim-cta">Last line (said and shown with the Follow button)</Label>
          <Input
            id="anim-cta"
            value={plan.cta}
            onChange={(e) => onChange({ ...plan, cta: e.target.value })}
          />
        </div>
      </div>
      {plan.scenes.map((scene, i) => {
        const v = scene.visual;
        const items = ITEMS[v.kind];
        return (
          <div
            key={i}
            className="space-y-3 rounded-lg border-[1.5px] border-foreground p-3"
            data-testid="scene"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-hand text-xl font-bold">Scene {i + 1}</span>
              {starts[i] !== undefined && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onPlayFrom(starts[i])}
                  aria-label={`Play from scene ${i + 1}`}
                >
                  <Play className="size-3.5" /> {starts[i].toFixed(1)} s
                </Button>
              )}
              <div className="ml-auto flex gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                  aria-label="Move up"
                >
                  <ArrowUp className="size-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  disabled={i === plan.scenes.length - 1}
                  onClick={() => move(i, 1)}
                  aria-label="Move down"
                >
                  <ArrowDown className="size-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  disabled={plan.scenes.length <= 1}
                  onClick={() => remove(i)}
                  aria-label="Remove scene"
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor={`narration-${i}`}>Voice says</Label>
              <Textarea
                id={`narration-${i}`}
                rows={2}
                value={scene.narration}
                onChange={(e) => setScene(i, { ...scene, narration: e.target.value })}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor={`kind-${i}`}>Shows</Label>
                <select
                  id={`kind-${i}`}
                  className={SELECT}
                  value={v.kind}
                  onChange={(e) =>
                    setScene(i, {
                      ...scene,
                      visual: { ...v, kind: e.target.value as AnimationVisualKind },
                    })
                  }
                >
                  {Object.entries(VISUALS).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              {FIELDS[v.kind].map((f) => (
                <div key={f.key} className={f.long ? "space-y-1 sm:col-span-2" : "space-y-1"}>
                  <Label htmlFor={`${f.key}-${i}`}>{f.label}</Label>
                  {f.long ? (
                    <Textarea
                      id={`${f.key}-${i}`}
                      rows={2}
                      value={String(v[f.key] ?? "")}
                      onChange={(e) =>
                        setScene(i, { ...scene, visual: { ...v, [f.key]: e.target.value } })
                      }
                    />
                  ) : (
                    <Input
                      id={`${f.key}-${i}`}
                      value={String(v[f.key] ?? "")}
                      onChange={(e) =>
                        setScene(i, { ...scene, visual: { ...v, [f.key]: e.target.value } })
                      }
                    />
                  )}
                </div>
              ))}
            </div>
            {items && (
              <div className="space-y-2">
                {v.items.slice(0, items.max).map((item, k) => (
                  <div key={k} className="flex flex-wrap items-end gap-2">
                    {items.keys.map((key) => (
                      <div
                        key={key}
                        className={key === "text" ? "min-w-40 flex-1 space-y-1" : "w-28 space-y-1"}
                      >
                        <Label htmlFor={`${key}-${i}-${k}`} className="text-xs">
                          {ITEM_LABEL[key]} {k + 1}
                        </Label>
                        {key === "motion" ? (
                          <select
                            id={`${key}-${i}-${k}`}
                            className={SELECT}
                            value={item.motion}
                            onChange={(e) => {
                              const next = v.items.map((it, j) =>
                                j === k ? { ...it, motion: e.target.value } : it,
                              );
                              setScene(i, { ...scene, visual: { ...v, items: next } });
                            }}
                          >
                            {MOTIONS.map((m) => (
                              <option key={m} value={m}>
                                {m.replace("_", " ")}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Input
                            id={`${key}-${i}-${k}`}
                            value={item[key]}
                            onChange={(e) => {
                              const next = v.items.map((it, j) =>
                                j === k ? { ...it, [key]: e.target.value } : it,
                              );
                              setScene(i, { ...scene, visual: { ...v, items: next } });
                            }}
                          />
                        )}
                      </div>
                    ))}
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() =>
                        setScene(i, {
                          ...scene,
                          visual: { ...v, items: v.items.filter((_, j) => j !== k) },
                        })
                      }
                      aria-label={`Remove item ${k + 1}`}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
                {v.items.length < items.max && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setScene(i, { ...scene, visual: { ...v, items: [...v.items, blankItem()] } })
                    }
                  >
                    <Plus className="size-3.5" /> Add item
                  </Button>
                )}
              </div>
            )}
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onRedo(i)}>
              <Sparkles className="size-3.5" /> Write this scene again
            </Button>
          </div>
        );
      })}
      <Button variant="outline" onClick={add} disabled={plan.scenes.length >= 16}>
        <Plus className="size-4" /> Add a scene
      </Button>
      <div className="space-y-1">
        <Label htmlFor="anim-caption">Post caption</Label>
        <Textarea
          id="anim-caption"
          rows={4}
          value={plan.caption}
          onChange={(e) => onChange({ ...plan, caption: e.target.value })}
        />
      </div>
    </div>
  );
}
