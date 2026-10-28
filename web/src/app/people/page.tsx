"use client";

import { KeyRound, SlidersHorizontal, Trash2, UserPlus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMe } from "@/hooks/use-me";
import { api, errorText, type Account } from "@/lib/api";

const MIN_PASSWORD = 8;

function seen(at: string | null): string {
  // The server's times are UTC without saying so, like a job's created_at.
  return at ? new Date(/[Zz+]/.test(at.slice(10)) ? at : at + "Z").toLocaleString() : "never";
}

export default function PeoplePage() {
  const me = useMe();
  const [people, setPeople] = useState<Account[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // The account a dialog is open for: a new password, or removing it.
  const [resetting, setResetting] = useState<Account | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [removing, setRemoving] = useState<Account | null>(null);
  const [deleteWork, setDeleteWork] = useState(false);

  const load = useCallback(
    () =>
      api
        .accounts()
        .then(setPeople)
        .catch((err) => toast.error(errorText(err))),
    [],
  );

  useEffect(() => {
    if (me?.role === "admin") load();
  }, [me?.role, load]);

  if (me && me.role !== "admin") {
    return <p className="text-sm text-muted-foreground">Only the admin manages accounts.</p>;
  }
  if (people === null) return <p className="text-sm text-muted-foreground">Loading...</p>;

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const made = await api.createAccount(username.trim().toLowerCase(), password);
      toast.success(`${made.username} can log in now, with ${made.presets} presets of their own`);
      setUsername("");
      setPassword("");
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (!resetting) return;
    setBusy(true);
    try {
      await api.resetPassword(resetting.id, newPassword);
      toast.success(`New password set; ${resetting.username} is logged out everywhere`);
      setResetting(null);
      setNewPassword("");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.removeAccount(removing.id, deleteWork);
      toast.success(
        deleteWork
          ? `${removing.username} and their jobs are removed`
          : `${removing.username} is removed; their jobs are yours now`,
      );
      setRemoving(null);
      setDeleteWork(false);
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-hand text-4xl font-bold">People</h1>
        <p className="text-sm text-muted-foreground">
          {people.length} {people.length === 1 ? "account" : "accounts"}. Each employee has presets
          of their own and sees only their own jobs; you see everyone&apos;s.
        </p>
      </div>

      <Card className="tape">
        <CardHeader>
          <CardTitle>Add an employee</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="space-y-2">
              <Label htmlFor="new-username">Username</Label>
              <Input
                id="new-username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="e.g. riya"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password">Password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                placeholder={`at least ${MIN_PASSWORD} characters`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <Button
              type="submit"
              disabled={busy || username.trim().length < 3 || password.length < MIN_PASSWORD}
            >
              <UserPlus /> Add
            </Button>
          </form>
          <p className="mt-3 text-xs text-muted-foreground">
            They start with a copy of your presets. New accounts are always employees: an admin is
            only made on the Mac (aiedit user add NAME --admin).
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        {people.map((person) => (
          <Card key={person.id}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span className="break-all">{person.username}</span>
                <Badge variant={person.role === "admin" ? "default" : "secondary"}>
                  {person.role === "admin" ? "admin" : "employee"}
                </Badge>
                {person.id === me?.id && <Badge variant="outline">you</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {person.jobs} {person.jobs === 1 ? "job" : "jobs"} · {person.presets} presets · last
                seen {seen(person.last_seen_at)}
              </p>
              {person.role === "member" && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" asChild>
                    <Link href={`/presets/?owner=${person.id}`}>
                      <SlidersHorizontal /> Presets
                    </Link>
                  </Button>
                  <Button variant="outline" onClick={() => setResetting(person)}>
                    <KeyRound /> New password
                  </Button>
                  <Button variant="outline" onClick={() => setRemoving(person)}>
                    <Trash2 /> Remove
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog open={resetting !== null} onOpenChange={(open) => !open && setResetting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New password for {resetting?.username}</DialogTitle>
            <DialogDescription>
              They are logged out everywhere and log in again with this one.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reset-password">Password</Label>
            <Input
              id="reset-password"
              type="password"
              autoComplete="new-password"
              placeholder={`at least ${MIN_PASSWORD} characters`}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button onClick={reset} disabled={busy || newPassword.length < MIN_PASSWORD}>
              Set password
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {removing?.username}?</DialogTitle>
            <DialogDescription>
              Their account and presets are deleted and they are logged out. Their jobs and feeds
              become yours, unless you delete those too.
            </DialogDescription>
          </DialogHeader>
          <label className="flex min-h-11 items-center gap-3 text-sm">
            <Checkbox
              checked={deleteWork}
              onCheckedChange={(checked) => setDeleteWork(checked === true)}
            />
            Also delete their jobs and feeds
          </label>
          <DialogFooter>
            <Button variant="destructive" onClick={remove} disabled={busy}>
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
