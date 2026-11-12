"use client";

import { useMe } from "@/hooks/use-me";

/** Whether this account tries the fun extras (the team board, the snowman, sounds). */
export function useFun(): boolean {
  return useMe()?.features?.includes("fun") ?? false;
}
