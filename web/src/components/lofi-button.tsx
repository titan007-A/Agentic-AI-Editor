"use client";

import { Music } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { lofiPlaying, startLofi, stopLofi } from "@/lib/lofi";
import { cn } from "@/lib/utils";

/** Lofi focus radio in the header (fun feature): one tap on, one tap off. */
export function LofiButton() {
  const [on, setOn] = useState(lofiPlaying);
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={on ? "Stop the lofi radio" : "Play the lofi radio"}
      aria-pressed={on}
      title={on ? "Lofi radio: on" : "Lofi radio"}
      onClick={() => {
        if (on) stopLofi();
        else startLofi();
        setOn(!on);
      }}
      className={cn(on && "lofi-on")}
    >
      <Music />
    </Button>
  );
}
