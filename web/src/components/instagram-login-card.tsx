"use client";

import { KeyRound, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorText, type InstagramLogin } from "@/lib/api";

function day(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "";
}

/**
 * The Instagram login downloads use for the reels Instagram shows only to people signed in
 * (marked sensitive, or not for everyone). Admins see whether it is set; the primary account
 * pastes or removes it. The cookies themselves are never shown again.
 */
export function InstagramLoginCard({ primary }: { primary: boolean }) {
  const [login, setLogin] = useState<InstagramLogin | null>(null);
  const [cookies, setCookies] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .instagramLogin()
      .then(setLogin)
      .catch(() => {});
  }, []);

  async function save() {
    setBusy(true);
    try {
      setLogin(await api.setInstagramLogin(cookies));
      setCookies("");
      toast.success("Instagram login saved: retry the reels that needed it");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    try {
      setLogin(await api.forgetInstagramLogin());
      toast.success("Instagram login removed");
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  const expired = login?.until ? new Date(login.until) < new Date() : false;
  return (
    <Card data-testid="instagram-login">
      <CardHeader>
        <CardTitle>
          <KeyRound className="mr-1 inline size-5" /> Instagram login for downloads
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Instagram shows some reels only to people signed in (ones it marks sensitive, or not for
          everyone), and those fail to download without a login. With one here, such a reel is asked
          for again signed in; every other download goes on without it.
        </p>
        {login && (
          <p className={expired ? "font-semibold text-destructive" : "font-semibold"}>
            {login.set
              ? `Set since ${day(login.since)}${
                  login.until ? `; ${expired ? "expired" : "lasts until"} ${day(login.until)}` : ""
                }`
              : "Not set: reels that need a login fail to download"}
          </p>
        )}
        {primary ? (
          <>
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
              <li>
                In a private window, sign in to Instagram with a spare account (not your page:
                Instagram may block an account used for downloads).
              </li>
              <li>
                Export instagram.com&apos;s cookies with an extension such as &quot;Get cookies.txt
                LOCALLY&quot;, and paste them below. Close the window without signing out.
              </li>
            </ol>
            <div className="space-y-1">
              <Label htmlFor="ig-cookies">cookies.txt</Label>
              <Textarea
                id="ig-cookies"
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
