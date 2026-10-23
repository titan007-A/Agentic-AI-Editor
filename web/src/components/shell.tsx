"use client";

import { Clapperboard, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { UsageNotice } from "@/components/usage-notice";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { mayLeave } from "@/lib/leave-guard.mjs";
import { cn } from "@/lib/utils";

// Pages with unsaved work ask first (see guardLeaving).
const guard = (event: { preventDefault: () => void }) => {
  if (!mayLeave()) event.preventDefault();
};

const LINKS = [
  { href: "/", label: "Jobs" },
  { href: "/autopilot/", label: "Autopilot" },
  { href: "/presets/", label: "Presets" },
  { href: "/system/", label: "System" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const onLogin = pathname.startsWith("/login");
  const [noticeAccepted, setNoticeAccepted] = useState(true);

  useEffect(() => {
    if (onLogin) return;
    api
      .me()
      .then((me) => setNoticeAccepted(me.notice_accepted))
      .catch(() => {});
  }, [onLogin]);

  if (onLogin) return <main className="mx-auto w-full max-w-sm px-4 py-12">{children}</main>;

  async function logout() {
    if (!mayLeave()) return;
    await api.logout();
    router.push("/login/");
  }

  return (
    <>
      <header className="hand-rule sticky top-0 z-10 bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4 sm:gap-4">
          <Link
            href="/"
            onNavigate={guard}
            className="flex min-h-11 items-center gap-2 sm:min-h-0"
            aria-label="Agentic AI Editor, jobs"
          >
            <span className="grid size-9 -rotate-6 place-items-center rounded-[50%_45%_55%_40%] border-2 border-foreground bg-[var(--mustard)]">
              <Clapperboard className="size-5" />
            </span>
            <span className="hidden font-hand text-2xl font-bold sm:inline">Agentic AI Editor</span>
          </Link>
          <nav className="flex min-w-0 flex-1 gap-0.5 sm:gap-1">
            {LINKS.map((link) => {
              const active =
                link.href === "/"
                  ? pathname === "/" || pathname.startsWith("/job")
                  : pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onNavigate={guard}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    // Tall enough to tap on a phone; the underline stays on the word.
                    "inline-flex min-h-11 items-center px-1.5 text-sm font-semibold transition-colors sm:min-h-0 sm:px-3",
                    active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span className={cn("py-1", active && "scribble")}>{link.label}</span>
                </Link>
              );
            })}
          </nav>
          <Button variant="ghost" size="icon" onClick={logout} aria-label="Log out">
            <LogOut />
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl px-4 py-6">{children}</main>
      <UsageNotice open={!noticeAccepted} onAccept={() => setNoticeAccepted(true)} />
    </>
  );
}
