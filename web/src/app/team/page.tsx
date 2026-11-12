"use client";

import { FunPanel } from "@/components/fun-panel";
import { useMe } from "@/hooks/use-me";

/** The team board on its own tab: leaderboard, streak, quest, badges and Wrapped. */
export default function TeamPage() {
  const me = useMe();
  if (!me) return <p className="text-sm text-muted-foreground">Loading...</p>;
  if (!me.features?.includes("fun")) {
    return (
      <p className="text-sm text-muted-foreground">This page isn&apos;t on for your account.</p>
    );
  }
  return (
    <div className="grid gap-6">
      <FunPanel tick={0} />
    </div>
  );
}
