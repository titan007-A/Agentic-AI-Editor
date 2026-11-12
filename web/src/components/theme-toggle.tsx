"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { Me } from "@/lib/api";

// Kept per browser: the night look is a person's choice on their own screen.
export const THEME_KEY = "aiedit-theme";

function stored(): "dark" | "light" {
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function apply(dark: boolean) {
  document.documentElement.classList.toggle("dark", dark);
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", dark ? "#0d1218" : "#f2eadb");
}

/** The Aurora night look, for admins: a moon to turn it on, a sun to turn it off. Anyone
 * else always gets the day look, even on a browser an admin left dark. */
export function ThemeToggle({ me }: { me: Me | null }) {
  const admin = me?.role === "admin";
  // Set by a tap; until then what this browser keeps. Only read once `me` is known, which
  // is never on the server, so the first render there and here agree.
  const [choice, setChoice] = useState<"dark" | "light" | null>(null);
  const dark = admin && (choice ?? stored()) === "dark";

  useEffect(() => {
    if (me) apply(dark);
  }, [me, dark]);

  if (!admin) return null;
  function flip() {
    const next = dark ? "light" : "dark";
    setChoice(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // private window: the look holds until the page reloads
    }
  }
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={flip}
      aria-label={dark ? "Day look" : "Night look (Aurora)"}
      aria-pressed={dark}
      title={dark ? "Day look" : "Night look (Aurora)"}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  );
}
