"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";

export function UsageNotice({ open, onAccept }: { open: boolean; onAccept: () => void }) {
  async function accept() {
    await api.acceptNotice();
    onAccept();
  }

  return (
    <Dialog open={open}>
      <DialogContent showCloseButton={false} onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Before you start</DialogTitle>
          <DialogDescription>
            Only use this editor on videos you own or have permission to use. Downloading, editing
            and reposting other people&apos;s work can break copyright law and the terms of the
            sites you download from.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button onClick={accept}>I understand</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
