"use client";

import { ChevronDown, Clapperboard, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { FestivalSkin } from "@/components/festival-skin";
import { FortuneCookie } from "@/components/fortune-cookie";
import { LofiButton } from "@/components/lofi-button";
import { Penguin } from "@/components/penguin";
import { WeatherMood } from "@/components/weather-mood";
import { ThemeToggle } from "@/components/theme-toggle";
import { UsageNotice } from "@/components/usage-notice";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { forgetMe, useMe } from "@/hooks/use-me";
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
  { href: "/research/", label: "Researcher" },
  { href: "/presets/", label: "Presets" },
  { href: "/buffer/", label: "Buffer" },
  { href: "/instagram/", label: "Instagram" },
];
// Only admins run the Mac's side of things.
const ADMIN_LINKS = [
  { href: "/system/", label: "System" },
  { href: "/people/", label: "People" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const onLogin = pathname.startsWith("/login");
  const me = useMe(!onLogin);
  // Accepted on the server, or just now in this page.
  const [acceptedNow, setAcceptedNow] = useState(false);
  const noticeAccepted = !me || me.notice_accepted || acceptedNow;
  const extra = [
    ...(me?.features?.includes("fun") ? [{ href: "/team/", label: "Team" }] : []),
    ...(me?.features?.includes("niche_finder") ? [{ href: "/accounts/", label: "Accounts" }] : []),
  ];
  const links = [...LINKS, ...extra, ...(me?.role === "admin" ? ADMIN_LINKS : [])];

  if (onLogin) return <main className="mx-auto w-full max-w-sm px-4 py-12">{children}</main>;

  async function logout() {
    if (!mayLeave()) return;
    await api.logout();
    forgetMe();
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
            data-logo
          >
            <span className="grid size-9 -rotate-6 place-items-center rounded-[50%_45%_55%_40%] border-2 border-foreground bg-[var(--mustard)]">
              <Clapperboard className="size-5" />
            </span>
            <span className="hidden font-hand text-2xl font-bold sm:inline">Agentic AI Editor</span>
          </Link>
          <NavLinks links={links} pathname={pathname} />
          {me && (
            <span
              // With many tabs the name gives its room to them (the log out button says it).
              className={cn(
                "hidden text-sm text-muted-foreground",
                links.length <= 7 && "md:inline",
              )}
              title={me.role}
            >
              {me.user}
            </span>
          )}
          {me?.features?.includes("fun") && <LofiButton />}
          <ThemeToggle me={me} />
          <Button
            variant="ghost"
            size="icon"
            onClick={logout}
            aria-label={me ? `Log out ${me.user}` : "Log out"}
          >
            <LogOut />
          </Button>
        </div>
      </header>
      <main
        className={cn(
          "mx-auto w-full max-w-5xl px-4 py-6",
          // Room at the bottom so the penguin never sits on the last button.
          me?.features?.includes("fun") && "pb-28",
        )}
      >
        {children}
      </main>
      <UsageNotice open={!noticeAccepted} onAccept={() => setAcceptedNow(true)} />
      {me?.features?.includes("fun") && (
        <>
          <Penguin />
          <FestivalSkin />
          <WeatherMood />
          {noticeAccepted && <FortuneCookie />}
        </>
      )}
    </>
  );
}

type NavLink = { href: string; label: string };

function isActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" || pathname.startsWith("/job") : pathname.startsWith(href);
}

const LINK_CLASS =
  // Tall enough to tap on a phone; the underline stays on the word.
  "inline-flex min-h-11 shrink-0 items-center px-1.5 text-sm font-semibold whitespace-nowrap transition-colors sm:min-h-0 sm:px-3";

/** The header's links: as many as fit in a row, the rest under "More", so every page can be
 * reached on a phone and on a computer alike (a row that scrolls sideways hides them). */
function NavLinks({ links, pathname }: { links: NavLink[]; pathname: string }) {
  const row = useRef<HTMLElement>(null);
  const ruler = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(links.length);

  useEffect(() => {
    const room = row.current;
    const sizes = ruler.current;
    if (!room || !sizes) return;
    // How many links fit beside a "More" button, measured on an invisible copy of the row.
    const fit = () => {
      const widths = Array.from(sizes.children, (el) => (el as HTMLElement).offsetWidth);
      const more = widths.pop() ?? 0;
      const all = widths.reduce((sum, w) => sum + w, 0);
      if (all <= room.clientWidth) {
        setShown(widths.length);
        return;
      }
      let used = more;
      let count = 0;
      while (count < widths.length && used + widths[count] <= room.clientWidth) {
        used += widths[count];
        count += 1;
      }
      setShown(count);
    };
    const watcher = new ResizeObserver(fit);
    watcher.observe(room);
    watcher.observe(sizes);
    return () => watcher.disconnect();
  }, [links]);

  const inline = links.slice(0, shown);
  const rest = links.slice(shown);
  const restActive = rest.find((link) => isActive(link.href, pathname));
  return (
    <nav
      ref={row}
      aria-label="Pages"
      className="relative flex min-w-0 flex-1 gap-0.5 overflow-hidden sm:gap-1"
    >
      <div
        ref={ruler}
        aria-hidden
        className="pointer-events-none invisible absolute top-0 left-0 flex gap-0.5 sm:gap-1"
      >
        {links.map((link) => (
          <span key={link.href} className={LINK_CLASS}>
            <span className="py-1">{link.label}</span>
          </span>
        ))}
        <span className={LINK_CLASS}>
          <span className="py-1">More</span>
          <ChevronDown className="ml-0.5 size-4" />
        </span>
      </div>
      {inline.map((link) => {
        const active = isActive(link.href, pathname);
        return (
          <Link
            key={link.href}
            href={link.href}
            onNavigate={guard}
            aria-current={active ? "page" : undefined}
            className={cn(
              LINK_CLASS,
              active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            <span className={cn("py-1", active && "scribble")}>{link.label}</span>
          </Link>
        );
      })}
      {rest.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                LINK_CLASS,
                restActive ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
              aria-label={restActive ? `More pages (now on ${restActive.label})` : "More pages"}
            >
              <span className={cn("py-1", restActive && "scribble")}>
                {restActive ? restActive.label : "More"}
              </span>
              <ChevronDown className="ml-0.5 size-4" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-44">
            {rest.map((link) => {
              const active = isActive(link.href, pathname);
              return (
                <DropdownMenuItem key={link.href} asChild className="min-h-11">
                  <Link
                    href={link.href}
                    onNavigate={guard}
                    aria-current={active ? "page" : undefined}
                    className={cn("font-semibold", active && "underline underline-offset-4")}
                  >
                    {link.label}
                  </Link>
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </nav>
  );
}
