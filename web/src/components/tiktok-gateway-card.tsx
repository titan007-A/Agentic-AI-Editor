"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorText, type TikTokGateway } from "@/lib/api";
import { cn } from "@/lib/utils";

function Row({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="flex justify-between gap-4 py-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 text-right break-words", bad && "text-destructive")}>{value}</dd>
    </div>
  );
}

/** What to do about what the check found, in a line. */
function advice(state: TikTokGateway): string | null {
  if (state.tunnel === false)
    return "The tunnel to the gateway Mac is down: that Mac may be off, asleep or off Tailscale. On the live Mac: scripts/macos-tiktok-gateway.sh status";
  if (state.tiktok === false)
    return "The tunnel is up but TikTok doesn't answer: the VPN on the gateway Mac is most likely off.";
  return null;
}

/** The TikTok gateway: a second Mac on a VPN that TikTok goes through, checked when shown. */
export function TikTokGatewayCard() {
  const [state, setState] = useState<TikTokGateway | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    try {
      setState(await api.tiktokGateway());
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    api
      .tiktokGateway()
      .then(setState)
      .catch((err) => setProblem(errorText(err)));
  }, []);

  const told = state && advice(state);
  return (
    <Card data-tiktok-gateway>
      <CardHeader>
        <CardTitle>TikTok gateway</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          TikTok is blocked here, so a second Mac on a VPN carries it. With it set up, TikTok links
          download and the Researcher can search TikTok. Nothing else goes through it.
        </p>
        {state === null && !problem && <p className="text-sm text-muted-foreground">Checking...</p>}
        {state && !state.set && (
          <p className="text-sm">
            Not set up on this server: TikTok links are refused. The steps are in
            docs/tiktok-gateway.md in the editor&apos;s code.
          </p>
        )}
        {state?.set && (
          <dl className="divide-y text-sm">
            <Row label="Gateway" value={state.direct ? "none needed (direct)" : state.proxy} />
            {state.tunnel !== null && (
              <Row
                label="Tunnel"
                value={state.tunnel ? "up" : `down (${state.tunnel_said})`}
                bad={!state.tunnel}
              />
            )}
            <Row
              label="TikTok through it"
              value={
                state.tiktok
                  ? `answers (${state.seconds} s)`
                  : `no answer${state.tiktok_said ? ` (${state.tiktok_said})` : ""}`
              }
              bad={!state.tiktok}
            />
          </dl>
        )}
        {told && <p className="text-sm break-words">{told}</p>}
        {problem && (
          <p className="text-sm break-words text-destructive" role="alert">
            {problem}
          </p>
        )}
        <Button variant="outline" disabled={checking} onClick={check}>
          <RefreshCw className={checking ? "animate-spin" : undefined} />
          {checking ? "Checking..." : "Check again"}
        </Button>
      </CardContent>
    </Card>
  );
}
