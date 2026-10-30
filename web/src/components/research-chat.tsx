"use client";

import { Send } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorText, type Research } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Chat with the researcher about a finished research: "why this one?" is answered from
 * its reasoning; "more Italian" starts a follow-up research, linked here. */
export function ResearchChat({
  research,
  onSent,
}: {
  research: Research;
  onSent: (next: Research) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const waiting = research.messages.some((m) => m.role === "user" && !m.answered);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy || waiting) return;
    setBusy(true);
    try {
      onSent(await api.chatOnResearch(research.id, text.trim()));
      setText("");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="gap-3 p-4">
      <div>
        <p className="font-medium">Ask the researcher</p>
        <p className="text-sm text-muted-foreground">
          Ask why it chose a reel, or ask for something else (&ldquo;more Italian&rdquo;,
          &ldquo;older concerts&rdquo;) and it searches again, never showing these reels twice.
        </p>
      </div>
      {research.messages.length > 0 && (
        <ul className="space-y-2" aria-label="Chat">
          {research.messages.map((m) => (
            <li
              key={m.id}
              className={cn(
                "max-w-[90%] rounded-md border p-2 text-sm break-words whitespace-pre-wrap",
                m.role === "user" ? "ml-auto bg-[#fdf3dc]" : "bg-muted/40",
              )}
            >
              {m.text}
              {m.follow_up_id && (
                <Link
                  href={`/research/?id=${m.follow_up_id}`}
                  className="mt-1 block font-medium underline"
                >
                  Open the new research
                </Link>
              )}
            </li>
          ))}
          {waiting && <li className="text-sm text-muted-foreground">Thinking...</li>}
        </ul>
      )}
      <form onSubmit={send} className="flex gap-2">
        <Input
          aria-label="Message to the researcher"
          placeholder="e.g. why #2? or: more Italian"
          maxLength={1000}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" disabled={busy || waiting || !text.trim()} aria-label="Send">
          <Send />
        </Button>
      </form>
    </Card>
  );
}
