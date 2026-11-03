"use client";

import { Play, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, type PdfChoice, type Preset, type Research } from "@/lib/api";

const select = "h-11 w-full rounded-md border border-input bg-background px-2 text-sm";
const PER_RESEARCH = 30;

/** Picking which of a PDF's songs to research, and how: by ticking songs, by section,
 * number or year, or by saying it in words (read by the planning model into ticks, for
 * the person to check), with a preset per section, where the words under each song go,
 * and how many songs each research takes. Nothing is searched until Start. */
export function PdfChooser({
  research,
  presets,
  onStarted,
}: {
  research: Research;
  presets: Preset[];
  onStarted: () => void;
}) {
  const listing = research.listing;
  const sections = useMemo(() => [...new Set(listing.map((e) => e.section))], [listing]);
  const [keep, setKeep] = useState<Set<number>>(() => new Set(listing.map((_, i) => i)));
  const [choice, setChoice] = useState<Omit<PdfChoice, "keep">>({
    swap: false,
    line: "top",
    presets: {},
  });
  const [perResearch, setPerResearch] = useState(PER_RESEARCH);
  const [kind, setKind] = useState(research.kind || "items");
  const songs = /song|music|canci|canzon|chanson|lied|gaan/i.test(kind);
  const [nameLabel, byLabel] = songs ? ["Song", "Singer"] : ["Name", "By / from"];
  const [words, setWords] = useState("");
  const [reading, setReading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [range, setRange] = useState({ first: "", last: "", from: "", to: "" });

  const inSection = (name: string) => listing.flatMap((e, i) => (e.section === name ? [i] : []));
  const chosenIn = (name: string) => inSection(name).filter((i) => keep.has(i)).length;

  function set(indices: number[], on: boolean) {
    setKeep((before) => {
      const next = new Set(before);
      for (const i of indices) {
        if (on) next.add(i);
        else next.delete(i);
      }
      return next;
    });
  }

  /** Keep, of the songs ticked now, only those in the numbers and years given. */
  function applyRange() {
    const n = (v: string) => (v.trim() ? Number(v) : null);
    const [first, last, from, to] = [n(range.first), n(range.last), n(range.from), n(range.to)];
    setKeep(
      (before) =>
        new Set(
          [...before].filter((i) => {
            const e = listing[i];
            const year = /^\d{4}$/.test(e.year) ? Number(e.year) : null;
            return (
              (first === null || e.number >= first) &&
              (last === null || e.number <= last) &&
              (year === null || ((from === null || year >= from) && (to === null || year <= to)))
            );
          }),
        ),
    );
  }

  async function readWords() {
    setReading(true);
    try {
      const read = await api.chooseByWords(research.id, words.trim());
      setKeep(new Set(read.keep));
      setChoice({
        swap: read.swap,
        line: read.line,
        presets: read.presets,
        understood: read.understood,
      });
      toast.success(`${read.keep.length} ticked from your words: check them below`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setReading(false);
    }
  }

  async function start() {
    setStarting(true);
    try {
      const ordered = [...keep].sort((a, b) => a - b);
      await api.chooseSongs(research.id, { ...choice, keep: ordered }, perResearch, kind.trim());
      toast.success(
        `${ordered.length} to research, in ${Math.ceil(ordered.length / perResearch)} ` +
          "researches run one after another",
      );
      onStarted();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setStarting(false);
    }
  }

  const first = listing[0];
  const [song, singer] = first
    ? choice.swap
      ? [first.singer, first.song]
      : [first.song, first.singer]
    : ["", ""];
  const researches = Math.ceil(keep.size / Math.max(1, perResearch));

  return (
    <div className="space-y-5">
      <p className="text-sm">
        {listing.length} items in the PDF
        {sections.length > 1 ? `, in ${sections.length} sections` : ""}. Choose which to research:
        nothing is searched until you start.
      </p>

      <div className="space-y-1">
        <Label htmlFor="pdf-kind">What the list is of</Label>
        <Input
          id="pdf-kind"
          maxLength={60}
          placeholder="songs, films, military aircraft, football goals..."
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Songs are searched for as performances (live, the singer singing); anything else as
          itself.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="pdf-words">Say it in your words</Label>
        <Textarea
          id="pdf-words"
          rows={2}
          maxLength={1000}
          placeholder="e.g. only the 60s to the 90s, from 31 on, 60s with the Sixties preset; leave out Manzanero"
          value={words}
          onChange={(e) => setWords(e.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          onClick={readWords}
          disabled={reading || words.trim().length < 3}
        >
          <Sparkles /> {reading ? "Reading your words..." : "Tick the songs from my words"}
        </Button>
        {choice.understood && (
          <p className="text-xs text-muted-foreground">Understood: {choice.understood}</p>
        )}
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">
          Or by numbers and years (of the songs ticked)
        </legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {(
            [
              ["first", "From number"],
              ["last", "To number"],
              ["from", "From year"],
              ["to", "To year"],
            ] as const
          ).map(([key, label]) => (
            <div key={key} className="space-y-1">
              <Label htmlFor={`pdf-${key}`}>{label}</Label>
              <Input
                id={`pdf-${key}`}
                type="number"
                inputMode="numeric"
                value={range[key]}
                onChange={(e) => setRange({ ...range, [key]: e.target.value })}
              />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={applyRange}>
            Keep only these
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              set(
                listing.map((_, i) => i),
                true,
              )
            }
          >
            Tick all
          </Button>
          <Button type="button" variant="outline" onClick={() => setKeep(new Set())}>
            Untick all
          </Button>
        </div>
      </fieldset>

      <div className="space-y-2">
        <p className="text-sm font-medium">The items, and a preset for each section</p>
        {sections.map((name) => {
          const indices = inSection(name);
          const ticked = chosenIn(name);
          return (
            <details key={name} className="rounded-md border p-2">
              <summary className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
                <Checkbox
                  aria-label={`All of ${name}`}
                  checked={ticked === indices.length ? true : ticked ? "indeterminate" : false}
                  onClick={(e) => e.stopPropagation()}
                  onCheckedChange={(v) => set(indices, v === true)}
                />
                <span className="min-w-0 flex-1 break-words">
                  {name}{" "}
                  <span className="text-muted-foreground">
                    ({ticked} of {indices.length})
                  </span>
                </span>
              </summary>
              <div className="mt-2 space-y-2">
                <select
                  aria-label={`Preset for ${name}`}
                  className={select}
                  value={choice.presets[name] ?? ""}
                  onChange={(e) =>
                    setChoice({ ...choice, presets: { ...choice.presets, [name]: e.target.value } })
                  }
                >
                  <option value="">Pick the preset per reel later</option>
                  {presets.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.label}
                    </option>
                  ))}
                </select>
                <ul className="max-h-80 space-y-1 overflow-y-auto">
                  {indices.map((i) => {
                    const e = listing[i];
                    const [s, by] = choice.swap ? [e.singer, e.song] : [e.song, e.singer];
                    return (
                      <li key={i}>
                        <label className="flex min-h-11 items-start gap-3 py-1 text-sm">
                          <Checkbox
                            className="mt-0.5"
                            checked={keep.has(i)}
                            onCheckedChange={(v) => set([i], v === true)}
                          />
                          <span className="min-w-0 break-words">
                            {e.number}. {s}
                            {by ? ` - ${by}` : ""}
                            {e.year ? ` (${e.year})` : ""}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </details>
          );
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="pdf-line">The words written for each item</Label>
          <select
            id="pdf-line"
            className={select}
            value={choice.line}
            onChange={(e) => setChoice({ ...choice, line: e.target.value as PdfChoice["line"] })}
          >
            <option value="top">go above the video</option>
            <option value="bottom">go below the video</option>
            <option value="none">aren&apos;t used</option>
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="pdf-per">Items in each research</Label>
          <Input
            id="pdf-per"
            type="number"
            inputMode="numeric"
            min={1}
            max={60}
            value={perResearch}
            onChange={(e) => setPerResearch(Math.min(60, Math.max(1, Number(e.target.value) || 1)))}
          />
        </div>
      </div>

      {first && (
        <div className="space-y-1 rounded-md bg-muted/50 p-2 text-sm">
          <p className="text-xs text-muted-foreground">Check the first item was read right:</p>
          <p className="break-words">
            <span className="text-muted-foreground">{nameLabel}:</span> {song}
            <br />
            <span className="text-muted-foreground">{byLabel}:</span> {singer || "(none)"}
            {first.year && (
              <>
                <br />
                <span className="text-muted-foreground">Year:</span> {first.year}
              </>
            )}
            {first.lines.length > 0 && (
              <>
                <br />
                <span className="text-muted-foreground">Words:</span> {first.lines.join(" ")}
              </>
            )}
          </p>
          <label className="flex min-h-11 items-center gap-3">
            <Checkbox
              checked={choice.swap}
              onCheckedChange={(v) => setChoice({ ...choice, swap: v === true })}
            />
            {nameLabel} and {byLabel.toLowerCase()} are the wrong way round: swap them
          </label>
        </div>
      )}

      <Button type="button" onClick={start} disabled={starting || keep.size === 0}>
        <Play />{" "}
        {starting
          ? "Starting..."
          : `Research ${keep.size} ${keep.size === 1 ? "item" : "items"}` +
            (researches > 1 ? ` (${researches} researches)` : "")}
      </Button>
    </div>
  );
}
