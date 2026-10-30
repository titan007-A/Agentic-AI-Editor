"use client";

import { Search, SkipForward } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorText, type Research } from "@/lib/api";

/** The questions the researcher asks before searching: a suggested answer to tap, or
 * words of one's own, or both; what is left blank isn't asked about again. */
export function ResearchQuestions({
  research,
  onAnswered,
}: {
  research: Research;
  onAnswered: (next: Research) => void;
}) {
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [written, setWritten] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function send(skip: boolean) {
    setBusy(true);
    try {
      const answers = skip
        ? []
        : research.questions.map(({ question }) => ({
            question,
            answer: [picked[question], written[question]?.trim()].filter(Boolean).join("; "),
          }));
      onAnswered(await api.answerResearch(research.id, answers));
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="gap-4 p-4">
      <div>
        <p className="font-medium">A few questions first</p>
        <p className="text-sm text-muted-foreground">
          Tap an answer or write your own; skip any you don&apos;t mind about.
        </p>
      </div>
      {research.questions.map(({ question, choices }, n) => (
        <fieldset key={question} className="space-y-2">
          <legend className="text-sm font-medium break-words">{question}</legend>
          <div className="flex flex-wrap gap-2">
            {choices.map((choice) => {
              const on = picked[question] === choice;
              return (
                <Button
                  key={choice}
                  type="button"
                  size="sm"
                  variant={on ? "default" : "outline"}
                  aria-pressed={on}
                  onClick={() => setPicked({ ...picked, [question]: on ? "" : choice })}
                >
                  {choice}
                </Button>
              );
            })}
          </div>
          <Input
            aria-label={`Your own answer to: ${question}`}
            id={`answer-${n}`}
            placeholder="Or in your own words"
            maxLength={200}
            value={written[question] ?? ""}
            onChange={(e) => setWritten({ ...written, [question]: e.target.value })}
          />
        </fieldset>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => send(false)}>
          <Search /> Search with these answers
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => send(true)}>
          <SkipForward /> Skip questions
        </Button>
      </div>
    </Card>
  );
}
