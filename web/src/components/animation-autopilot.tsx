"use client";

import { Bot, Play, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  api,
  errorText,
  type Animation,
  type AnimationAutopilot,
  type AnimationVoice,
} from "@/lib/api";

const SELECT =
  "min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2 text-sm";
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/**
 * The account's daily reels, made by themselves: topics from the list (one per line, first
 * goes next), else new ones from the niche. Each is written, its facts checked, rendered,
 * then waits for approval; after enough approvals it may post by itself.
 */
export function AnimationAutopilotCard({
  voices,
  styles,
  kinds,
  lengths,
  onStarted,
}: {
  voices: AnimationVoice[];
  styles: readonly (readonly [string, string])[];
  kinds: readonly (readonly [string, string])[];
  lengths: readonly (readonly [string, string])[];
  onStarted: (a: Animation) => void;
}) {
  const [pilot, setPilot] = useState<AnimationAutopilot | null>(null);
  const [topics, setTopics] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .animationAutopilot()
      .then((p) => {
        setPilot(p);
        setTopics(p.topics.join("\n"));
      })
      .catch(() => {});
  }, []);

  if (!pilot) return null;
  const set = (change: Partial<AnimationAutopilot>) => setPilot({ ...pilot, ...change });
  const canAuto = pilot.approved >= pilot.needs;

  async function save() {
    if (!pilot) return;
    setBusy(true);
    try {
      const saved = await api.setAnimationAutopilot({
        active: pilot.active,
        niche: pilot.niche,
        topics: topics
          .split("\n")
          .map((t) => t.trim())
          .filter(Boolean),
        hour: pilot.hour,
        per_day: pilot.per_day,
        kind: pilot.kind,
        style: pilot.style,
        voice: pilot.voice,
        length: pilot.length,
        channel_id: pilot.channel_id,
        auto_post: pilot.auto_post,
      });
      setPilot(saved);
      setTopics(saved.topics.join("\n"));
      toast.success("Autopilot saved");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function now() {
    setBusy(true);
    try {
      await save();
      const started = await api.animationAutopilotNow();
      onStarted(started);
      const next = await api.animationAutopilot();
      setPilot(next);
      setTopics(next.topics.join("\n"));
      toast.success("Making one now: it will wait for your approval");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-5" /> Autopilot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex items-center justify-between gap-2 text-sm">
          <span>Make reels by itself every day</span>
          <Switch
            checked={pilot.active}
            onCheckedChange={(v) => set({ active: Boolean(v) })}
            aria-label="Autopilot on"
          />
        </label>
        <div className="space-y-1">
          <Label htmlFor="pilot-niche">Page niche (new topics come from it)</Label>
          <Input
            id="pilot-niche"
            placeholder="e.g. History & mysteries"
            value={pilot.niche}
            onChange={(e) => set({ niche: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="pilot-topics">Your topics, one per line (used first, top first)</Label>
          <Textarea
            id="pilot-topics"
            rows={4}
            placeholder={"The Dancing Plague of 1518\nThe lost city of Ubar"}
            value={topics}
            onChange={(e) => setTopics(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="pilot-hour">Starts at</Label>
            <select
              id="pilot-hour"
              className={SELECT}
              value={pilot.hour}
              onChange={(e) => set({ hour: Number(e.target.value) })}
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-per-day">Reels a day</Label>
            <select
              id="pilot-per-day"
              className={SELECT}
              value={pilot.per_day}
              onChange={(e) => set({ per_day: Number(e.target.value) })}
            >
              {[1, 2, 3].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-kind">Content</Label>
            <select
              id="pilot-kind"
              className={SELECT}
              value={pilot.kind}
              onChange={(e) => set({ kind: e.target.value })}
            >
              {kinds.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-style">Style</Label>
            <select
              id="pilot-style"
              className={SELECT}
              value={pilot.style}
              onChange={(e) => set({ style: e.target.value })}
            >
              {styles.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-length">Length</Label>
            <select
              id="pilot-length"
              className={SELECT}
              value={pilot.length}
              onChange={(e) => set({ length: e.target.value })}
            >
              {lengths.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="pilot-voice">Voice</Label>
            <select
              id="pilot-voice"
              className={SELECT}
              value={pilot.voice}
              onChange={(e) => set({ voice: e.target.value })}
            >
              <option value="auto">Auto: AI recommends</option>
              <option value="narrator">Deep narrator</option>
              <option value="calm">Calm</option>
              <option value="energetic">Energetic</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="pilot-page">Instagram page to post to</Label>
          <select
            id="pilot-page"
            className={SELECT}
            value={pilot.channel_id ?? ""}
            onChange={(e) => set({ channel_id: e.target.value || null })}
          >
            <option value="">None yet: only make them (approve to keep)</option>
            {pilot.channels.map((c) => (
              <option key={c.id} value={c.id}>
                @{c.username}
              </option>
            ))}
          </select>
          {!pilot.channels.length && (
            <p className="text-xs text-muted-foreground">
              Connect the new page on the Instagram tab first.
            </p>
          )}
        </div>
        <label className="flex items-center justify-between gap-2 text-sm">
          <span>
            Post by itself
            <span className="block text-xs text-muted-foreground">
              {canAuto
                ? "Allowed. A reel whose facts are in doubt still waits for you."
                : `After ${pilot.needs} approved reels (${pilot.approved} so far).`}
            </span>
          </span>
          <Switch
            checked={pilot.auto_post}
            disabled={!canAuto && !pilot.auto_post}
            onCheckedChange={(v) => set({ auto_post: Boolean(v) })}
            aria-label="Post by itself"
          />
        </label>
        {pilot.waiting > 0 && (
          <p className="text-sm font-semibold">
            {pilot.waiting} reel{pilot.waiting > 1 ? "s" : ""} waiting for your approval below.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button onClick={save} disabled={busy}>
            <Save className="size-4" /> Save
          </Button>
          <Button
            variant="outline"
            onClick={now}
            disabled={busy || (!pilot.niche.trim() && !topics.trim())}
          >
            <Play className="size-4" /> Make one now
          </Button>
        </div>
        {pilot.used.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Made lately: {pilot.used.slice(-5).join(" · ")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
