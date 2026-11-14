"use client";

import {
  Crown,
  KeyRound,
  ListChecks,
  ShieldMinus,
  ShieldPlus,
  SlidersHorizontal,
  Trash2,
  UserPlus,
} from "lucide-react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMe } from "@/hooks/use-me";
import { api, errorText, type Account, type Assigned } from "@/lib/api";

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
  const [role, setRole] = useState<"member" | "admin">("member");
  // The admin's own password, typed again to make an admin or change an admin's password.
  const [myPassword, setMyPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // The account a dialog is open for: a new password, or removing it.
  const [resetting, setResetting] = useState<Account | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [resetMyPassword, setResetMyPassword] = useState("");
  const [removing, setRemoving] = useState<Account | null>(null);
  const [deleteWork, setDeleteWork] = useState(false);
  const [removeMyPassword, setRemoveMyPassword] = useState("");
  // An account whose role the primary account is changing, and its own password for it.
  const [promoting, setPromoting] = useState<Account | null>(null);
  const [roleMyPassword, setRoleMyPassword] = useState("");
  // Assigning presets to one employee: what they have now, and what is ticked.
  const [assigning, setAssigning] = useState<Account | null>(null);
  const [assigned, setAssigned] = useState<Assigned | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [replace, setReplace] = useState(false);

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
  // The primary account makes, changes and removes the other admins; the others run employees.
  const primary = me?.primary === true;
  const boss = people.find((p) => p.primary);
  const adding = primary ? role : "member";

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const made = await api.createAccount(
        username.trim().toLowerCase(),
        password,
        adding,
        adding === "admin" ? myPassword : "",
      );
      toast.success(
        made.role === "admin"
          ? `${made.username} is an admin and can log in now`
          : `${made.username} can log in now, with ${made.presets} presets of their own`,
      );
      setUsername("");
      setPassword("");
      setRole("member");
      setMyPassword("");
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
      await api.resetPassword(
        resetting.id,
        newPassword,
        resetting.role === "admin" ? resetMyPassword : "",
      );
      toast.success(`New password set; ${resetting.username} is logged out everywhere`);
      setResetting(null);
      setNewPassword("");
      setResetMyPassword("");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function openAssign(person: Account) {
    setAssigning(person);
    setAssigned(null);
    setReplace(false);
    try {
      const now = await api.assignedPresets(person.id);
      setAssigned(now);
      setTicked(new Set(now.presets.filter((p) => p.has).map((p) => p.name)));
    } catch (err) {
      toast.error(errorText(err));
      setAssigning(null);
    }
  }

  function tick(name: string, on: boolean) {
    setTicked((before) => {
      const next = new Set(before);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  async function assign() {
    if (!assigning || !assigned) return;
    setBusy(true);
    try {
      await api.assignPresets(assigning.id, [...ticked], replace);
      const count = ticked.size + assigned.own.length;
      toast.success(`${assigning.username} has ${count} ${count === 1 ? "preset" : "presets"} now`);
      setAssigning(null);
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  // Presets the employee has that would be deleted by unticking them.
  const dropping = assigned?.presets.filter((p) => p.has && !ticked.has(p.name)) ?? [];

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.removeAccount(
        removing.id,
        deleteWork,
        removing.role === "admin" ? removeMyPassword : "",
      );
      toast.success(
        deleteWork
          ? `${removing.username} and their jobs are removed`
          : `${removing.username} is removed; their jobs are yours now`,
      );
      setRemoving(null);
      setDeleteWork(false);
      setRemoveMyPassword("");
      await load();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeRole() {
    if (!promoting) return;
    const next = promoting.role === "admin" ? "member" : "admin";
    setBusy(true);
    try {
      await api.changeRole(promoting.id, next, roleMyPassword);
      toast.success(
        next === "admin"
          ? `${promoting.username} is an admin now`
          : `${promoting.username} is an employee now`,
      );
      setPromoting(null);
      setRoleMyPassword("");
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
          <CardTitle>Add a person</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-2 sm:items-end">
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
            {primary && (
              <div className="space-y-2">
                <Label htmlFor="new-role">Role</Label>
                <Select value={role} onValueChange={(v) => setRole(v as "member" | "admin")}>
                  <SelectTrigger id="new-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">Employee</SelectItem>
                    <SelectItem value="admin">Admin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            {adding === "admin" && (
              <div className="space-y-2">
                <Label htmlFor="my-password">Your password</Label>
                <Input
                  id="my-password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="to confirm it's you"
                  value={myPassword}
                  onChange={(e) => setMyPassword(e.target.value)}
                />
              </div>
            )}
            <Button
              type="submit"
              className="sm:col-span-2 sm:justify-self-start"
              disabled={
                busy ||
                username.trim().length < 3 ||
                password.length < MIN_PASSWORD ||
                (adding === "admin" && !myPassword)
              }
            >
              <UserPlus /> Add {adding === "admin" ? "admin" : "employee"}
            </Button>
          </form>
          <p className="mt-3 text-xs text-muted-foreground">
            {adding === "admin"
              ? "An admin sees everyone's jobs, uses the same presets as you, and manages employees, so type your own password to make one. You can change or remove them here later."
              : "They start with no presets: tick the ones they should use with Assign presets on their card below, or send one preset to several people from the Presets page."}
            {!primary &&
              ` Admins are made, changed and removed by the primary account${boss ? ` (${boss.username})` : ""}.`}
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
                {person.primary && (
                  <Badge variant="outline" className="gap-1">
                    <Crown className="size-3" aria-hidden /> primary
                  </Badge>
                )}
                {person.id === me?.id && <Badge variant="outline">you</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {person.jobs} {person.jobs === 1 ? "job" : "jobs"} · {person.presets} presets · last
                seen {seen(person.last_seen_at)}
              </p>
              {person.role === "admin" && person.id !== me?.id && primary && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => setResetting(person)}>
                    <KeyRound /> New password
                  </Button>
                  <Button variant="outline" onClick={() => setPromoting(person)}>
                    <ShieldMinus /> Make employee
                  </Button>
                  <Button variant="outline" onClick={() => setRemoving(person)}>
                    <Trash2 /> Remove
                  </Button>
                </div>
              )}
              {person.role === "admin" && person.id !== me?.id && !primary && (
                <p className="text-xs text-muted-foreground">
                  {person.primary
                    ? "The primary account: it manages the admins."
                    : "Admins are managed by the primary account."}
                </p>
              )}
              {person.role === "member" && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => openAssign(person)}>
                    <ListChecks /> Assign presets
                  </Button>
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
                  {primary && (
                    <Button variant="outline" onClick={() => setPromoting(person)}>
                      <ShieldPlus /> Make admin
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog
        open={resetting !== null}
        onOpenChange={(open) => {
          if (!open) {
            setResetting(null);
            setResetMyPassword("");
          }
        }}
      >
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
          {resetting?.role === "admin" && (
            <div className="space-y-2">
              <Label htmlFor="reset-my-password">Your password</Label>
              <Input
                id="reset-my-password"
                type="password"
                autoComplete="current-password"
                placeholder="to confirm it's you"
                value={resetMyPassword}
                onChange={(e) => setResetMyPassword(e.target.value)}
              />
            </div>
          )}
          <DialogFooter>
            <Button
              onClick={reset}
              disabled={
                busy ||
                newPassword.length < MIN_PASSWORD ||
                (resetting?.role === "admin" && !resetMyPassword)
              }
            >
              Set password
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={assigning !== null} onOpenChange={(open) => !open && setAssigning(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Presets for {assigning?.username}</DialogTitle>
            <DialogDescription>
              Ticked presets are copied to them; unticking one deletes their copy. Their copies are
              their own, so your later changes reach them only when you replace them.
            </DialogDescription>
          </DialogHeader>
          {assigned === null ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : (
            <div className="space-y-3">
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setTicked(new Set(assigned.presets.map((p) => p.name)))}
                >
                  All
                </Button>
                <Button variant="outline" size="sm" onClick={() => setTicked(new Set())}>
                  None
                </Button>
              </div>
              <div className="max-h-72 space-y-1 overflow-y-auto">
                {assigned.presets.map((p) => (
                  <label key={p.name} className="flex min-h-11 items-center gap-3 text-sm">
                    <Checkbox
                      checked={ticked.has(p.name)}
                      onCheckedChange={(checked) => tick(p.name, checked === true)}
                    />
                    <span className="min-w-0 flex-1 break-words">{p.label}</span>
                    {p.has && <Badge variant="secondary">has it</Badge>}
                  </label>
                ))}
              </div>
              {assigned.own.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Their own, left as they are: {assigned.own.map((p) => p.label).join(", ")}
                </p>
              )}
              <label className="flex min-h-11 items-center gap-3 text-sm">
                <Checkbox
                  checked={replace}
                  onCheckedChange={(checked) => setReplace(checked === true)}
                />
                Replace the copies they already have with your current version
              </label>
              {dropping.length > 0 && (
                <p className="text-sm text-destructive">
                  Deletes their copy of {dropping.map((p) => p.label).join(", ")}, with any changes
                  they made to it.
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button onClick={assign} disabled={busy || assigned === null}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={promoting !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPromoting(null);
            setRoleMyPassword("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Make {promoting?.username} {promoting?.role === "admin" ? "an employee" : "an admin"}?
            </DialogTitle>
            <DialogDescription>
              {promoting?.role === "admin"
                ? "They stop seeing everyone's jobs and People, and get a presets folder of their own (empty: assign presets after). They are logged out everywhere."
                : "They see everyone's jobs, use the admins' presets and manage employees. They are logged out everywhere and log in again."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="role-my-password">Your password</Label>
            <Input
              id="role-my-password"
              type="password"
              autoComplete="current-password"
              placeholder="to confirm it's you"
              value={roleMyPassword}
              onChange={(e) => setRoleMyPassword(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button onClick={changeRole} disabled={busy || !roleMyPassword}>
              {promoting?.role === "admin" ? "Make employee" : "Make admin"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRemoving(null);
            setRemoveMyPassword("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {removing?.username}?</DialogTitle>
            <DialogDescription>
              {removing?.role === "admin"
                ? "Their account is deleted and they are logged out; the admins' presets stay. Their jobs and feeds become yours, unless you delete those too."
                : "Their account and presets are deleted and they are logged out. Their jobs and feeds become yours, unless you delete those too."}
            </DialogDescription>
          </DialogHeader>
          {removing?.role === "admin" && (
            <div className="space-y-2">
              <Label htmlFor="remove-my-password">Your password</Label>
              <Input
                id="remove-my-password"
                type="password"
                autoComplete="current-password"
                placeholder="to confirm it's you"
                value={removeMyPassword}
                onChange={(e) => setRemoveMyPassword(e.target.value)}
              />
            </div>
          )}
          <label className="flex min-h-11 items-center gap-3 text-sm">
            <Checkbox
              checked={deleteWork}
              onCheckedChange={(checked) => setDeleteWork(checked === true)}
            />
            Also delete their jobs and feeds
          </label>
          <DialogFooter>
            <Button
              variant="destructive"
              onClick={remove}
              disabled={busy || (removing?.role === "admin" && !removeMyPassword)}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
