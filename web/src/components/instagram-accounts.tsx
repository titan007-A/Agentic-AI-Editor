"use client";

import { AtSign, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type InstagramAccount, type Preset } from "@/lib/api";
import { cn } from "@/lib/utils";

export const learning = (a: InstagramAccount) =>
  a.learn_status === "queued" || a.learn_status === "running";

/** Preset names as toggles; at least one stays chosen. */
function PresetChoice({
  presets,
  chosen,
  onChange,
  label,
}: {
  presets: Preset[];
  chosen: string[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={label}>
      {presets.map((p) => {
        const on = chosen.includes(p.name);
        return (
          <button
            key={p.name}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? chosen.filter((n) => n !== p.name) : [...chosen, p.name])}
            className={cn(
              "sketch-tile px-2 py-1 text-left text-sm hover:bg-[#fdf3dc]",
              on && "bg-[#fdf3dc] font-medium ring-2 ring-primary",
            )}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

/** The person's Instagram accounts: each has its own researcher, which learns what the
 * account posts and finds reels like its best ones for the presets chosen for it. */
export function InstagramAccounts({
  accounts,
  presets,
  onChange,
}: {
  accounts: InstagramAccount[];
  presets: Preset[];
  onChange: () => void;
}) {
  const [handle, setHandle] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.addInstagramAccount(handle.trim(), chosen);
      setHandle("");
      setChosen([]);
      toast.success("Added; it is learning what the account posts");
      onChange();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="tape tape-right">
      <CardHeader>
        <CardTitle>Your Instagram accounts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Give an account you post on and the presets you edit for it. The researcher reads what the
          account posts and looks for reels like its best ones.
        </p>
        {accounts.map((account) => (
          <AccountRow key={account.id} account={account} presets={presets} onChange={onChange} />
        ))}
        <form onSubmit={add} className="space-y-3 rounded-md border p-3">
          <div className="space-y-1">
            <Label htmlFor="instagram-handle">Instagram account</Label>
            <Input
              id="instagram-handle"
              placeholder="@youraccount or its link"
              value={handle}
              maxLength={200}
              onChange={(e) => setHandle(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">Presets for it</p>
            <PresetChoice
              presets={presets}
              chosen={chosen}
              onChange={setChosen}
              label="Presets for the new account"
            />
          </div>
          <Button type="submit" disabled={busy || !handle.trim() || chosen.length === 0}>
            <Plus /> Add account
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function AccountRow({
  account,
  presets,
  onChange,
}: {
  account: InstagramAccount;
  presets: Preset[];
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [chosen, setChosen] = useState(account.presets);
  const labels = Object.fromEntries(presets.map((p) => [p.name, p.label]));

  async function act(what: () => Promise<unknown>, done: string) {
    try {
      await what();
      toast.success(done);
      onChange();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1 font-medium break-all">
          <AtSign className="size-4 shrink-0" />
          {account.handle}
        </p>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={learning(account)}
            onClick={() =>
              act(() => api.learnInstagramAccount(account.id), `Learning @${account.handle} again`)
            }
          >
            <RefreshCw /> Learn again
          </Button>
          <Button
            size="icon"
            variant="ghost"
            disabled={learning(account)}
            aria-label={`Remove @${account.handle}`}
            onClick={() =>
              act(() => api.removeInstagramAccount(account.id), `Removed @${account.handle}`)
            }
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      {editing ? (
        <div className="space-y-2">
          <PresetChoice
            presets={presets}
            chosen={chosen}
            onChange={setChosen}
            label={`Presets for @${account.handle}`}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={chosen.length === 0}
              onClick={() =>
                act(() => api.updateInstagramAccount(account.id, chosen), "Presets saved").then(
                  () => setEditing(false),
                )
              }
            >
              Save presets
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1">
          {account.presets.map((name) => (
            <Badge key={name} variant="outline">
              {labels[name] ?? name}
            </Badge>
          ))}
          <Button size="sm" variant="link" onClick={() => setEditing(true)}>
            Change presets
          </Button>
        </div>
      )}
      {learning(account) ? (
        <p className="text-sm text-muted-foreground">
          Learning what it posts{account.learn_message && `: ${account.learn_message}`}...
        </p>
      ) : account.learn_status === "failed" ? (
        <p className="text-sm text-destructive">Couldn&apos;t learn it: {account.learn_error}</p>
      ) : account.summary ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            What it posts (read from {account.own} of its reels)
          </summary>
          <p className="mt-1 break-words">{account.summary}</p>
        </details>
      ) : (
        account.learned_at && (
          <p className="text-sm text-muted-foreground">
            None of its reels turned up in search; it goes by the presets.
          </p>
        )
      )}
    </div>
  );
}
