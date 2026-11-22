"use client";

import { KeyRound, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, type SiteLogin } from "@/lib/api";

function day(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "";
}

type Site = {
  key: "instagram" | "youtube";
  name: string;
  domain: string;
  about: ReactNode;
  notSet: string;
  saved: string;
};

/**
 * A site's login that downloads use (a browser's cookies, pasted). Admins see whether it is
 * set and until when; the primary account pastes or removes it. The cookies themselves are
 * never shown again.
 */
function SiteLoginCard({ site, primary }: { site: Site; primary: boolean }) {
  const [login, setLogin] = useState<SiteLogin | null>(null);
  const [cookies, setCookies] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .siteLogin(site.key)
      .then(setLogin)
      .catch(() => {});
  }, [site.key]);

  async function save() {
    setBusy(true);
    try {
      setLogin(await api.setSiteLogin(site.key, cookies));
      setCookies("");
      toast.success(site.saved);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    try {
      setLogin(await api.forgetSiteLogin(site.key));
      toast.success(`${site.name} login removed`);
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  const expired = login?.until ? new Date(login.until) < new Date() : false;
  const field = `${site.key}-cookies`;
  return (
    <Card data-testid={`${site.key}-login`}>
      <CardHeader>
        <CardTitle>
          <KeyRound className="mr-1 inline size-5" /> {site.name} login for downloads
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">{site.about}</p>
        {login && (
          <p className={expired ? "font-semibold text-destructive" : "font-semibold"}>
            {login.set
              ? `Set since ${day(login.since)}${
                  login.until ? `; ${expired ? "expired" : "lasts until"} ${day(login.until)}` : ""
                }`
              : site.notSet}
          </p>
        )}
        {primary ? (
          <>
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
              <li>
                In a private window, sign in to {site.name} with a spare account (not your own:{" "}
                {site.name} may block an account used for downloads).
              </li>
              <li>
                Export {site.domain}&apos;s cookies with an extension such as &quot;Get cookies.txt
                LOCALLY&quot;, and paste them below. Close the window without signing out.
              </li>
            </ol>
            <div className="space-y-1">
              <Label htmlFor={field}>cookies.txt</Label>
              <Textarea
                id={field}
                rows={4}
                className="font-mono text-xs"
                placeholder="# Netscape HTTP Cookie File"
                value={cookies}
                onChange={(e) => setCookies(e.target.value)}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || cookies.trim().length < 20} onClick={save}>
                {busy ? "Saving..." : login?.set ? "Replace login" : "Save login"}
              </Button>
              {login?.set && (
                <Button variant="ghost" onClick={forget}>
                  <Trash2 /> Remove
                </Button>
              )}
            </div>
          </>
        ) : (
          <p className="text-muted-foreground">The primary account sets this login.</p>
        )}
      </CardContent>
    </Card>
  );
}

const YOUTUBE: Site = {
  key: "youtube",
  name: "YouTube",
  domain: "youtube.com",
  about:
    'A computer that looks up many videos is sometimes asked by YouTube to "confirm you\'re not a bot", and then every YouTube download, research lookup and feed check fails until it signs in. With a login here they sign in; it is checked before it is kept.',
  notSet: "Not set: if YouTube asks this computer to sign in, YouTube downloads fail",
  saved: "YouTube login saved: retry the YouTube jobs that failed",
};

const INSTAGRAM: Site = {
  key: "instagram",
  name: "Instagram",
  domain: "instagram.com",
  about:
    "Instagram shows some reels only to people signed in (ones it marks sensitive, or not for everyone), and those fail to download without a login. With one here, such a reel is asked for again signed in; every other download goes on without it.",
  notSet: "Not set: reels that need a login fail to download",
  saved: "Instagram login saved: retry the reels that needed it",
};

export function YoutubeLoginCard({ primary }: { primary: boolean }) {
  return <SiteLoginCard site={YOUTUBE} primary={primary} />;
}

export function InstagramLoginCard({ primary }: { primary: boolean }) {
  return <SiteLoginCard site={INSTAGRAM} primary={primary} />;
}
