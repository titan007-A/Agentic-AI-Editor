"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

  useEffect(() => {
    api
      .system()
      .then(setInfo)
      .catch((err) => toast.error(errorText(err)));
  }, []);

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
