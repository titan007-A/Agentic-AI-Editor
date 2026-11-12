"use client";

import { Settings2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { NAMES, type Festival } from "@/components/festival-skin";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { readPrefs, writePrefs } from "@/lib/fun-prefs";

/** The fun extras' settings on the Team tab: the weather's city, festival skins and the
 * penguin on or off (this browser), and a look at any festival's skin. */
export function FunSettings() {
  const [prefs, setPrefs] = useState(readPrefs);
  const [city, setCity] = useState(prefs.city);
  const set = (next: Partial<typeof prefs>) => {
    writePrefs(next);
    setPrefs({ ...prefs, ...next });
  };
  return (
    <Card data-testid="fun-settings">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Settings2 className="size-5" /> Fun settings
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="space-y-1"
          onSubmit={(e) => {
            e.preventDefault();
            set({ city: city.trim() });
            toast.success(city.trim() ? `Weather from ${city.trim()}` : "Weather mood off");
          }}
        >
          <Label htmlFor="fun-city">Weather mood: your city</Label>
          <div className="flex gap-2">
            <Input
              id="fun-city"
              placeholder="e.g. Delhi (empty: off)"
              value={city}
              onChange={(e) => setCity(e.target.value)}
            />
            <Button type="submit" variant="outline">
              Save
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            The page&apos;s background follows the real weather there (Open-Meteo, free).
          </p>
        </form>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <Checkbox
            checked={prefs.festivals}
            onCheckedChange={(v) => set({ festivals: v === true })}
          />
          Festival skins (Diwali, Holi, Christmas, New Year, national days)
        </label>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <Checkbox checked={prefs.penguin} onCheckedChange={(v) => set({ penguin: v === true })} />
          The penguin
        </label>
        <div className="space-y-1 text-sm">
          <p className="font-semibold">Look at a festival&apos;s skin now</p>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(NAMES) as Festival[]).map((f) => (
              <Button key={f} asChild size="sm" variant="outline">
                <a href={`/team/?festival=${f}`}>{NAMES[f]}</a>
              </Button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
