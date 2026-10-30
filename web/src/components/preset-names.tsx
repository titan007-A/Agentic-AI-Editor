"use client";

import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorText, type PresetName } from "@/lib/api";

/** The admin's list of every preset anyone has, each name editable: a new name is shown
 * for the admin and every employee who has that preset, their own settings untouched. */
export function PresetNames({ onRenamed }: { onRenamed: () => void }) {
  const [names, setNames] = useState<PresetName[]>([]);
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .presetNames()
      .then(setNames)
      .catch(() => {});
  }, []);

  const changed = Object.fromEntries(
    names
      .map((n) => [n.name, (typed[n.name] ?? n.label).trim()] as const)
      .filter(([name, label]) => label !== names.find((n) => n.name === name)?.label),
  );
  const count = Object.keys(changed).length;
  const empty = Object.values(changed).some((label) => !label);

  async function save() {
    setBusy(true);
    try {
      setNames(await api.renamePresets(changed));
      setTyped({});
      toast.success(`${count} preset${count === 1 ? "" : "s"} renamed for everyone`);
      onRenamed();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (names.length === 0) return null;
  return (
    <Card className="mb-6 gap-3 p-4">
      <details>
        <summary className="cursor-pointer font-medium">Preset names (for everyone)</summary>
        <div className="mt-3 space-y-3">
          <p className="text-sm text-muted-foreground">
            A new name here is shown for you and every employee who has the preset. Their own
            settings stay as they are.
          </p>
          <ul className="space-y-3">
            {names.map((n) => (
              <li key={n.name} className="space-y-1">
                <Input
                  aria-label={`Name of ${n.name}`}
                  value={typed[n.name] ?? n.label}
                  maxLength={120}
                  onChange={(e) => setTyped({ ...typed, [n.name]: e.target.value })}
                />
                <p className="text-xs break-all text-muted-foreground">
                  {n.name} · {n.who.join(", ")}
                </p>
              </li>
            ))}
          </ul>
          <Button onClick={save} disabled={busy || count === 0 || empty}>
            <Save />{" "}
            {busy ? "Saving..." : count ? `Save ${count} name${count === 1 ? "" : "s"}` : "Save"}
          </Button>
        </div>
      </details>
    </Card>
  );
}
