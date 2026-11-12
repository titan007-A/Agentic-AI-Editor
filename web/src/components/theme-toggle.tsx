"use client";

import { Camera, Moon, NotebookPen, Ruler, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Me } from "@/lib/api";

// Kept per browser: the look is a person's choice on their own screen.
export const THEME_KEY = "aiedit-theme";

export type Look = "light" | "midnight" | "darkroom" | "blueprint";
const LOOKS: { value: Look; label: string; about: string; icon: typeof Sun }[] = [
  { value: "light", label: "Day", about: "cream paper, as always", icon: Sun },
  {
    value: "midnight",
    label: "Midnight notebook",
    about: "our colours on espresso paper",
    icon: NotebookPen,
  },
  { value: "darkroom", label: "Darkroom", about: "red safelight, film amber", icon: Camera },
  { value: "blueprint", label: "Blueprint", about: "storyboard grid, chalk ink", icon: Ruler },
];
const PAPER: Record<Look, string> = {
  light: "#f2eadb",
  midnight: "#1d1714",
  darkroom: "#120c0b",
  blueprint: "#0f2a47",
};

function stored(): Look {
  try {
    const kept = localStorage.getItem(THEME_KEY);
    if (kept === "dark") return "midnight"; // the first night look's choice
    return LOOKS.some((l) => l.value === kept) ? (kept as Look) : "light";
  } catch {
    return "light";
  }
}

function apply(look: Look) {
  const root = document.documentElement;
  root.classList.toggle("dark", look !== "light");
  if (look === "light") root.removeAttribute("data-night");
  else root.setAttribute("data-night", look);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", PAPER[look]);
}

/** The night looks, for admins: the moon opens a menu of the day look and three night
 * ones. Anyone else always gets the day look, even on a browser an admin left dark. */
export function ThemeToggle({ me }: { me: Me | null }) {
  const admin = me?.role === "admin";
  // Picked here; until then what this browser keeps. Only read once `me` is known, which
  // is never on the server, so the first render there and here agree.
  const [choice, setChoice] = useState<Look | null>(null);
  const look: Look = admin ? (choice ?? stored()) : "light";

  useEffect(() => {
    if (me) apply(look);
  }, [me, look]);

  if (!admin) return null;
  function pick(next: string) {
    const chosen = next as Look;
    setChoice(chosen);
    try {
      localStorage.setItem(THEME_KEY, chosen);
    } catch {
      // private window: the look holds until the page reloads
    }
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Look" title="Day or night look">
          {look === "light" ? <Moon /> : <Sun />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Look</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={look} onValueChange={pick}>
          {LOOKS.map(({ value, label, about, icon: Icon }) => (
            <DropdownMenuRadioItem key={value} value={value} className="min-h-11">
              <Icon className="size-4" aria-hidden />
              <span className="flex flex-col">
                <span className="font-semibold">{label}</span>
                <span className="text-xs text-muted-foreground">{about}</span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
