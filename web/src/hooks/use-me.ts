"use client";

import { useEffect, useState } from "react";

import { api, type Me } from "@/lib/api";

let asked: Promise<Me> | null = null;

/** Who is signed in, asked once per page load and shared by everything that needs it.
 * Off on the login page, where asking would only send it back to the login page. */
export function useMe(enabled = true): Me | null {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (!enabled) return;
    asked ??= api.me();
    asked.then(setMe).catch(() => {
      asked = null;
    });
  }, [enabled]);
  return me;
}

/** Forget who was signed in, after logging out or in. */
export function forgetMe() {
  asked = null;
}
