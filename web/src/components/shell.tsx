"use client";

import { Clapperboard, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { UsageNotice } from "@/components/usage-notice";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Jobs" },
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

  if (onLogin) return <main className="mx-auto w-full max-w-sm px-4 py-16">{children}</main>;

  async function logout() {
    await api.logout();
    router.push("/login/");
  }

  return (
    <>
      <header className="sticky top-0 z-10 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-4 px-4">
          <Link href="/" className="flex items-center gap-2 font-semibold">
            <Clapperboard className="size-5" />
            <span className="hidden sm:inline">Agentic AI Editor</span>
          </Link>
          <nav className="flex flex-1 gap-1">
            {LINKS.map((link) => {
              const active =
                link.href === "/"
                  ? pathname === "/" || pathname.startsWith("/job")
                  : pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm",
                    active ? "bg-secondary text-foreground" : "text-muted-foreground",
                  )}
                >
                  {link.label}
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
