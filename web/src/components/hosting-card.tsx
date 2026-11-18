"use client";

import { Cloud } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Hosting, type HostingForm } from "@/lib/api";

const EMPTY: HostingForm = {
  account_id: "",
  access_key_id: "",
  secret_access_key: "",
  bucket: "",
  public_url: "",
};

const FIELDS: { key: keyof HostingForm; label: string; placeholder: string; secret?: boolean }[] = [
  { key: "account_id", label: "Account ID", placeholder: "32 letters and digits" },
  { key: "access_key_id", label: "Access key ID", placeholder: "from the R2 API token" },
  {
    key: "secret_access_key",
    label: "Secret access key",
    placeholder: "from the R2 API token",
    secret: true,
  },
  { key: "bucket", label: "Bucket", placeholder: "aiedit-reels" },
  { key: "public_url", label: "Public address", placeholder: "https://media.example.com" },
];

function gigabytes(bytes = 0): string {
  return `${(bytes / 1e9).toFixed(bytes < 1e8 ? 2 : 1)} GB`;
}

/** Cloudflare R2 for the videos Buffer fetches: set up here by an admin, tested first. */
export function HostingCard() {
  const [state, setState] = useState<Hosting | null>(null);
  const [form, setForm] = useState<HostingForm>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  function show(found: Hosting) {
    setState(found);
    if (found.configured)
      setForm({
        account_id: found.account_id ?? "",
        access_key_id: found.access_key_id ?? "",
        secret_access_key: "",
        bucket: found.bucket ?? "",
        public_url: found.public_url ?? "",
      });
  }

  useEffect(() => {
    api
      .hosting()
      .then(show)
      .catch(() => {});
  }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      show(await api.setHosting(form));
      toast.success("R2 works: Buffer gets its videos from there now");
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function turn(on: boolean) {
    try {
      show(await api.switchHosting(on));
      toast.success(on ? "Videos go to Buffer from R2" : "Videos go to Buffer from this machine");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (state === null) return null;
  const needsSecret = !state.configured;
  const ready =
    FIELDS.every((f) => f.secret || form[f.key].trim()) &&
    (!needsSecret || form.secret_access_key.trim());
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cloud className="size-5" aria-hidden /> Video hosting for Buffer
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Buffer and Instagram download each video when its post goes out. From this machine&apos;s
          home connection a big video sometimes fails then. With Cloudflare R2 set up, each video is
          put there first and Buffer gets R2&apos;s link; it&apos;s deleted an hour after the post
          went out.
        </p>
        {state.configured && (
          <div className="flex flex-wrap items-center gap-3 rounded-md border p-3">
            <Switch
              id="hosting-on"
              checked={state.enabled}
              onCheckedChange={turn}
              aria-label="Use R2 for new posts"
            />
            <Label htmlFor="hosting-on">{state.enabled ? "On" : "Off"}</Label>
            <span className="text-sm text-muted-foreground" data-hosting-usage>
              {state.files} video{state.files === 1 ? "" : "s"} in the bucket,{" "}
              {gigabytes(state.bytes)} of {gigabytes(state.cap)}
            </span>
          </div>
        )}
        <details open={!state.configured}>
          <summary className="cursor-pointer text-sm font-semibold">
            {state.configured ? "Change the R2 settings" : "How to set it up"}
          </summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
            <li>
              In{" "}
              <a
                className="underline"
                href="https://dash.cloudflare.com/?to=/:account/r2/overview"
                target="_blank"
                rel="noreferrer"
              >
                Cloudflare R2
              </a>
              , create a bucket. The Account ID is on that page.
            </li>
            <li>
              In the bucket&apos;s Settings, under Custom Domains, connect a subdomain such as
              media.yourdomain.com. That is the public address.
            </li>
            <li>
              Under Manage API tokens, create a token with Object Read &amp; Write on that bucket
              and copy its Access key ID and Secret access key.
            </li>
          </ol>
        </details>
        <form onSubmit={save} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`r2-${f.key}`}>{f.label}</Label>
                <Input
                  id={`r2-${f.key}`}
                  type={f.secret ? "password" : "text"}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={
                    f.secret && state.configured
                      ? `saved (ends ${state.secret_tail})`
                      : f.placeholder
                  }
                  value={form[f.key]}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                />
              </div>
            ))}
          </div>
          {problem && (
            <p className="text-sm break-words text-destructive" role="alert">
              {problem}
            </p>
          )}
          <Button type="submit" disabled={busy || !ready}>
            {busy ? "Testing..." : "Save and test"}
          </Button>
          <p className="text-xs text-muted-foreground">
            The test puts a small file in the bucket, reads it back from the public address and
            deletes it. Nothing is saved unless it works.
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
