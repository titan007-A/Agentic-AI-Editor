"use client";

import { useEffect, useState } from "react";

import { onPrefs, readPrefs } from "@/lib/fun-prefs";

export type Festival = "diwali" | "holi" | "christmas" | "newyear" | "tricolour";

// [from, to] inclusive, as month-day; a year's own dates for the festivals that move.
const FIXED: { festival: Festival; from: string; to: string; name: string }[] = [
  { festival: "christmas", from: "12-20", to: "12-26", name: "Christmas" },
  { festival: "newyear", from: "12-31", to: "12-31", name: "New Year" },
  { festival: "newyear", from: "01-01", to: "01-01", name: "New Year" },
  { festival: "tricolour", from: "01-25", to: "01-26", name: "Republic Day" },
  { festival: "tricolour", from: "08-14", to: "08-15", name: "Independence Day" },
];
const MOVING: { festival: Festival; from: string; to: string; name: string }[] = [
  { festival: "diwali", from: "2026-11-06", to: "2026-11-10", name: "Diwali" },
  { festival: "diwali", from: "2027-10-26", to: "2027-10-31", name: "Diwali" },
  { festival: "diwali", from: "2028-10-15", to: "2028-10-19", name: "Diwali" },
  { festival: "holi", from: "2027-03-21", to: "2027-03-22", name: "Holi" },
  { festival: "holi", from: "2028-03-10", to: "2028-03-11", name: "Holi" },
];
export const NAMES: Record<Festival, string> = {
  diwali: "Diwali",
  holi: "Holi",
  christmas: "Christmas",
  newyear: "New Year",
  tricolour: "Independence / Republic Day",
};

/** Which festival's skin a day wears, if any. */
export function festivalOn(day: Date): Festival | null {
  const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  const md = iso.slice(5);
  for (const f of MOVING) if (iso >= f.from && iso <= f.to) return f.festival;
  for (const f of FIXED) if (md >= f.from && md <= f.to) return f.festival;
  return null;
}

const DIYAS = 9;
const SPLASHES = ["#ff4fa3", "#ffd23f", "#3bd16f", "#3aa0ff", "#b45cff"];

/** The day's festival on the page, lightly: diyas along the bottom for Diwali, colour
 * splashes for Holi, snow and fairy lights for Christmas, gold sparkle for New Year, a
 * tricolour ribbon on the national days. `?festival=<name>` shows one any day. */
export function FestivalSkin() {
  const [on, setOn] = useState(() => readPrefs().festivals);
  useEffect(() => onPrefs((p) => setOn(p.festivals)), []);
  const asked =
    typeof window === "undefined"
      ? null
      : (new URLSearchParams(window.location.search).get("festival") as Festival | null);
  const festival = asked && asked in NAMES ? asked : festivalOn(new Date());
  if (!on || !festival) return null;
  return (
    <div className={`festival festival-${festival}`} aria-hidden data-festival={festival}>
      {festival === "diwali" && (
        <div className="festival-diyas">
          {Array.from({ length: DIYAS }, (_, i) => (
            <span key={i} className="diya" style={{ animationDelay: `${(i * 0.37) % 2}s` }} />
          ))}
        </div>
      )}
      {festival === "holi" &&
        SPLASHES.map((colour, i) => (
          <span
            key={colour}
            className="holi-splash"
            style={{
              background: `radial-gradient(circle, ${colour} 0 35%, transparent 70%)`,
              left: `${[2, 78, 8, 70, 40][i]}%`,
              top: `${[8, 12, 70, 78, 92][i]}%`,
            }}
          />
        ))}
      {festival === "christmas" && (
        <>
          <div className="festival-lights" />
          <div className="festival-snow" />
        </>
      )}
      {festival === "newyear" && <div className="festival-sparkle" />}
      {festival === "tricolour" && <div className="festival-tricolour" />}
    </div>
  );
}
