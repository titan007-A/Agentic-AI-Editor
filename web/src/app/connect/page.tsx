"use client";

import { Bot } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type McpAsk } from "@/lib/api";

export default function ConnectPage() {
  return (
    <Suspense>
      <Connect />
    </Suspense>
  );
}

/**
 * An app (claude.ai's connector, Claude Code) asks to use the Animations tab as the signed-in
 * account: who asks, where the browser goes back to, and whether it may post. Reached from
 * the app's /authorize; a person who isn't signed in logs in first and comes back here.
 */
function Connect() {
  const ask = useSearchParams().get("request") ?? "";
  const [found, setFound] = useState<McpAsk | null>(null);
  const [error, setError] = useState("");
  const [posting, setPosting] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!ask) return;
    api
      .mcpAsk(ask)
      .then(setFound)
      .catch((err) => setError(errorText(err)));
  }, [ask]);
  const problem =
    error || (ask ? "" : "This link has no request in it. Start connecting again from the app.");

  async function answer(allow: boolean) {
    setBusy(true);
    try {
      const { redirect } = await api.answerMcpAsk(ask, { allow, posting: allow && posting });
      window.location.href = redirect;
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md py-8">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-5" /> Connect to your Animations tab
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {problem && <p className="text-destructive">{problem}</p>}
          {!found && !problem && <p className="text-muted-foreground">Loading…</p>}
          {found && (
            <>
              <p>
                <strong>{found.app}</strong> wants to use the Animations tab as{" "}
                <strong>{found.account}</strong>: list your reels, write new ones, change and render
                them. It sees only your own reels.
              </p>
              <p className="text-muted-foreground">
                After you answer, you go back to <strong>{found.goes_to}</strong>. Only allow it if
                you started connecting there just now.
              </p>
              {found.studio ? (
                <>
                  <label className="flex items-center justify-between gap-2">
                    <span>
                      Let it post reels
                      <span className="block text-xs text-muted-foreground">
                        To your Instagram pages and autopilot approvals, only when you ask. You can
                        change this later on the Animations tab.
                      </span>
                    </span>
                    <Switch
                      checked={posting}
                      onCheckedChange={(v) => setPosting(Boolean(v))}
                      aria-label="Let it post reels"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => answer(true)} disabled={busy}>
                      Allow
                    </Button>
                    <Button variant="outline" onClick={() => answer(false)} disabled={busy}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-destructive">
                    The Animations tab isn&apos;t on for this account, so there is nothing to
                    connect to.
                  </p>
                  <Button variant="outline" onClick={() => answer(false)} disabled={busy}>
                    Go back
                  </Button>
                </>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
