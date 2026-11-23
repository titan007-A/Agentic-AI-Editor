"use client";

import { Bot, Copy, KeyRound, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type McpConnections } from "@/lib/api";

function when(iso: string | null): string {
  return iso ? new Date(iso + "Z").toLocaleString() : "never";
}

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error("Couldn't copy; select the text and copy it");
  }
}

/**
 * Claude (claude.ai or Claude Code) working this tab over MCP, as this account: the address
 * to give it, keys for Claude Code, and every connection with its posting switch.
 */
export function ClaudeConnectCard() {
  const [state, setState] = useState<McpConnections | null>(null);
  const [label, setLabel] = useState("Claude Code");
  const [posting, setPosting] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .mcpConnections()
      .then(setState)
      .catch(() => {});
  }, []);

  if (!state) return null;
  const command = key
    ? `claude mcp add --transport http animations ${state.url} --header "Authorization: Bearer ${key}"`
    : "";

  async function makeKey() {
    setBusy(true);
    try {
      const made = await api.newMcpKey({ label: label.trim() || "Claude Code", posting });
      setKey(made.key ?? null);
      setState(made);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function setPostingOf(id: string, on: boolean) {
    try {
      await api.setMcpPosting(id, on);
      setState(await api.mcpConnections());
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  async function remove(id: string) {
    if (!window.confirm("Disconnect it? Claude can't use this tab through it any more.")) return;
    try {
      await api.removeMcpConnection(id);
      setState(await api.mcpConnections());
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-5" /> Connect Claude
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Claude can write, change and render your reels here, as you. It sees only your own reels.
        </p>
        <div className="space-y-1">
          <Label htmlFor="mcp-url">Server address</Label>
          <div className="flex gap-2">
            <Input id="mcp-url" readOnly value={state.url} onFocus={(e) => e.target.select()} />
            <Button
              size="icon"
              variant="outline"
              onClick={() => copy(state.url, "Address")}
              aria-label="Copy the address"
            >
              <Copy className="size-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            claude.ai: Customize, Connectors, add a custom connector with this address; it opens
            this site to ask you. Claude Code: make a key below.
          </p>
        </div>

        <div className="space-y-2 rounded-md border-[1.5px] border-foreground/30 p-3">
          <div className="space-y-1">
            <Label htmlFor="mcp-label">Key for Claude Code</Label>
            <Input
              id="mcp-label"
              value={label}
              maxLength={80}
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <label className="flex items-center justify-between gap-2">
            <span>
              Let it post reels
              <span className="block text-xs text-muted-foreground">
                To your Instagram pages and autopilot approvals, only when you ask. Off is safer.
              </span>
            </span>
            <Switch
              checked={posting}
              onCheckedChange={(v) => setPosting(Boolean(v))}
              aria-label="Let this key post reels"
            />
          </label>
          <Button onClick={makeKey} disabled={busy}>
            <KeyRound className="size-4" /> Make a key
          </Button>
          {key && (
            <div className="space-y-2 rounded-md border-[1.5px] border-foreground bg-[var(--field)] p-2">
              <p className="font-semibold">
                Copy it now: it is shown only this once. In a terminal on your computer:
              </p>
              <code className="block text-xs break-all">{command}</code>
              <Button size="sm" variant="outline" onClick={() => copy(command, "Command")}>
                <Copy className="size-3.5" /> Copy the command
              </Button>
            </div>
          )}
        </div>

        <div className="space-y-2">
          <Label>Connected</Label>
          {!state.connections.length && <p className="text-muted-foreground">Nothing yet.</p>}
          {state.connections.map((c) => (
            <div
              key={c.id}
              className="flex flex-wrap items-center gap-2 rounded-md border-[1.5px] border-foreground/30 p-2"
            >
              <div className="min-w-0 flex-1">
                <div className="line-clamp-1 font-semibold">
                  {c.label}
                  {c.kind === "key" && c.tail ? ` · key …${c.tail}` : ""}
                </div>
                <div className="text-xs text-muted-foreground">
                  {c.kind === "oauth" ? "signed in through this site" : "key"} · made{" "}
                  {when(c.created_at)} · last used {when(c.last_used_at)}
                </div>
              </div>
              <label className="flex items-center gap-1 text-xs">
                posts
                <Switch
                  checked={c.posting}
                  onCheckedChange={(v) => setPostingOf(c.id, Boolean(v))}
                  aria-label={`Let ${c.label} post reels`}
                />
              </label>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => remove(c.id)}
                aria-label={`Disconnect ${c.label}`}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            Changing your password disconnects them all.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
