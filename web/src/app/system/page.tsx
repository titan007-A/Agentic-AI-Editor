"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type SystemInfo } from "@/lib/api";

const ROWS: { key: keyof SystemInfo; label: string }[] = [
  { key: "accelerator", label: "Accelerator" },
  { key: "encoder", label: "Video encoder" },
  { key: "transcriber", label: "Transcriber" },
  { key: "whisper_model", label: "Whisper model" },
  { key: "llm_model", label: "Planner model" },
  { key: "vision_model", label: "Vision model" },
  { key: "yt_dlp", label: "yt-dlp" },
];

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [updating, setUpdating] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Days finished jobs and their videos are kept, as saved and as typed.
  const [keepDays, setKeepDays] = useState<number | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    api
      .system()
      .then(setInfo)
      .catch((err) => toast.error(errorText(err)));
    api
      .storage()
      .then(({ keep_days }) => {
        setKeepDays(keep_days);
        setTyped(String(keep_days));
      })
      .catch(() => {});
  }, []);

  async function saveKeep(event: React.FormEvent) {
    event.preventDefault();
    try {
      const { keep_days } = await api.setStorage(Number(typed));
      setKeepDays(keep_days);
      toast.success(
        keep_days === 0
          ? "Videos are kept until deleted by hand"
          : `Videos are deleted ${keep_days} day${keep_days === 1 ? "" : "s"} after they're made`,
      );
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function updateYtdlp() {
    setUpdating(true);
    try {
      const result = await api.updateYtdlp();
      setNote(`yt-dlp ${result.yt_dlp} installed, ${result.note}.`);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setUpdating(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Backends</CardTitle>
        </CardHeader>
        <CardContent>
          {info ? (
            <dl className="divide-y text-sm">
              {ROWS.map((row) => (
                <div key={row.key} className="flex justify-between gap-4 py-2">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className="truncate text-right font-mono">{info[row.key]}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground">Loading...</p>
          )}
        </CardContent>
      </Card>

      {keepDays !== null && (
        <Card>
          <CardHeader>
            <CardTitle>Disk space</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={saveKeep} className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Finished jobs and their videos, with the downloads and uploads they came from, are
                deleted this many days after they are made, for everyone. 0 keeps them until deleted
                by hand.
              </p>
              <div className="flex items-end gap-2">
                <div className="space-y-1">
                  <Label htmlFor="keep-days">Delete after (days)</Label>
                  <Input
                    id="keep-days"
                    className="w-28"
                    inputMode="numeric"
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                  />
                </div>
                <Button
                  type="submit"
                  variant="outline"
                  disabled={!/^\d{1,3}$/.test(typed) || Number(typed) === keepDays}
                >
                  Save
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Downloader</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Sites change often and links stop downloading until yt-dlp catches up. Update it when
            downloads start failing.
          </p>
          <Button variant="outline" disabled={updating} onClick={updateYtdlp}>
            <RefreshCw className={updating ? "animate-spin" : undefined} />
            {updating ? "Updating..." : "Update yt-dlp"}
          </Button>
          {note && (
            <Alert>
              <AlertDescription>{note}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
