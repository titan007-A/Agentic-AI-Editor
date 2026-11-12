"use client";

import { useEffect, useState } from "react";

import { onPrefs, readPrefs } from "@/lib/fun-prefs";

export type Weather = "sun" | "night" | "clouds" | "fog" | "rain" | "storm" | "snow";
const CACHE = "aiedit-weather";
const FRESH_MS = 30 * 60 * 1000;

/** WMO weather code (Open-Meteo) and day/night to a mood. */
export function moodOf(code: number, day: boolean): Weather {
  if (code >= 95) return "storm";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
  if (code === 45 || code === 48) return "fog";
  if (code >= 2) return "clouds";
  return day ? "sun" : "night";
}

async function weatherIn(city: string): Promise<{ mood: Weather; label: string } | null> {
  try {
    const kept = JSON.parse(localStorage.getItem(CACHE) ?? "null");
    if (kept && kept.city === city && Date.now() - kept.at < FRESH_MS) return kept.value;
  } catch {
    // look it up again
  }
  const place = await fetch(
    `https://geocoding-api.open-meteo.com/v1/search?count=1&name=${encodeURIComponent(city)}`,
  ).then((r) => r.json());
  const spot = place?.results?.[0];
  if (!spot) return null;
  const now = await fetch(
    `https://api.open-meteo.com/v1/forecast?latitude=${spot.latitude}&longitude=${spot.longitude}&current=weather_code,is_day,temperature_2m`,
  ).then((r) => r.json());
  const current = now?.current;
  if (!current) return null;
  const value = {
    mood: moodOf(Number(current.weather_code), current.is_day === 1),
    label: `${spot.name}: ${Math.round(current.temperature_2m)}°`,
  };
  try {
    localStorage.setItem(CACHE, JSON.stringify({ city, at: Date.now(), value }));
  } catch {
    // fine: asked again next time
  }
  return value;
}

/** The page's background follows the real weather in the person's city (set on the Team
 * tab): rain drops when it rains, a warm glow in sunshine, a dim sky at night, drifting
 * haze in fog, snow, and now and then a flash in a storm. Free Open-Meteo, no key;
 * `?weather=<mood>` shows one. */
export function WeatherMood() {
  const [city, setCity] = useState(() => readPrefs().city);
  const [mood, setMood] = useState<{ mood: Weather; label: string } | null>(null);
  useEffect(() => onPrefs((p) => setCity(p.city)), []);
  const asked =
    typeof window === "undefined"
      ? null
      : (new URLSearchParams(window.location.search).get("weather") as Weather | null);

  useEffect(() => {
    if (!city || asked) return;
    let live = true;
    const look = () =>
      weatherIn(city)
        .then((found) => live && setMood(found))
        .catch(() => {});
    look();
    const every = window.setInterval(look, FRESH_MS);
    return () => {
      live = false;
      window.clearInterval(every);
    };
  }, [city, asked]);

  const shown = asked ? { mood: asked, label: asked } : city ? mood : null;
  if (!shown) return null;
  return (
    <div
      className={`weather weather-${shown.mood}`}
      aria-hidden
      data-weather={shown.mood}
      title={shown.label}
    />
  );
}
