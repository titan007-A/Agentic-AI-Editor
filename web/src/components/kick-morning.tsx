"use client";

import { Loader2, Sunrise } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type KickDaily } from "@/lib/api";

const FIELD = "min-h-11 border-[1.5px] border-foreground bg-[var(--field)]";
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** The account's morning Kick clips: every day at its hour, GTA clips and the rest from its
 * streamers' new streams, filed into that day's folders (clipper/daily.py). */
export function KickMorning() {
  const [daily, setDaily] = useState<KickDaily | null>(null);
  const [names, setNames] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .kickDaily()
      .then((d) => {
        setDaily(d);
        setNames(d.streamers.join(", "));
      })
      .catch(() => setDaily(null));
  }, []);

  if (!daily) return null;
  const set = <K extends keyof KickDaily>(key: K, value: KickDaily[K]) =>
    setDaily((d) => (d ? { ...d, [key]: value } : d));
  const count = (value: string) => Math.max(0, Math.min(60, Number.parseInt(value, 10) || 0));

  async function save() {
    if (!daily) return;
    setSaving(true);
    try {
      const saved = await api.setKickDaily({
        on: daily.on,
        hour: daily.hour,
        streamers: names.split(/[\s,]+/).filter(Boolean),
        gta: daily.gta,
        footage: daily.footage,
        streams: daily.streams,
      });
      setDaily(saved);
      setNames(saved.streamers.join(", "));
      toast.success(saved.on ? `Every morning at ${saved.hour}:00` : "Morning clips off");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card data-testid="kick-morning">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sunrise className="size-5" /> Every morning
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <label className="flex items-center justify-between gap-3">
          <span>Clip these streamers every morning</span>
          <Switch checked={daily.on} onCheckedChange={(on) => set("on", on)} />
        </label>
        <div className="space-y-1">
          <Label htmlFor="morning-streamers">Streamers</Label>
          <Input
            id="morning-streamers"
            className={FIELD}
            value={names}
            placeholder="n3on, ddg, kaneljoseph"
            onChange={(e) => setNames(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="space-y-1">
            <Label htmlFor="morning-hour">At</Label>
            <select
              id="morning-hour"
              className="min-h-11 w-full rounded-md border-[1.5px] border-foreground bg-[var(--field)] px-2"
              value={daily.hour}
              onChange={(e) => set("hour", Number(e.target.value))}
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="morning-gta">GTA clips</Label>
            <Input
              id="morning-gta"
              className={FIELD}
              inputMode="numeric"
              value={daily.gta}
              onChange={(e) => set("gta", count(e.target.value))}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="morning-footage">Other clips</Label>
            <Input
              id="morning-footage"
              className={FIELD}
              inputMode="numeric"
              value={daily.footage}
              onChange={(e) => set("footage", count(e.target.value))}
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          From each streamer&apos;s new streams (the newest {daily.streams} at most; the newest one
          again, other moments of it, when there&apos;s none new). A stream with the streamer&apos;s
          camera over the game is clipped in {daily.facecam_preset}, the others in{" "}
          {daily.full_preset}. They go into that day&apos;s folders, like 24-NOV-GTA and
          24-NOV-FOOTAGE.
        </p>
        {daily.last_on && (
          <p className="text-xs text-muted-foreground">
            Last: {daily.last_on} - {daily.last_result || "started"}
          </p>
        )}
        <Button className="min-h-11" variant="outline" onClick={save} disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />} Save
        </Button>
      </CardContent>
    </Card>
  );
}
