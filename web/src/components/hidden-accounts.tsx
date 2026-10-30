"use client";

import { Eye } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorText, type HiddenAccount } from "@/lib/api";

/** The Instagram accounts the person hid from their researches ("Never show @x again" on
 * a reel), each of which can be shown again. Nothing shows while none are hidden. */
export function HiddenAccounts({
  version,
  onUnhidden,
}: {
  /** Changes when an account was hidden elsewhere on the page. */
  version: number;
  onUnhidden: () => void;
}) {
  const [hidden, setHidden] = useState<HiddenAccount[]>([]);

  const refresh = useCallback(() => {
    api
      .hiddenAccounts()
      .then(setHidden)
      .catch(() => {});
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh, version]);

  async function unhide(handle: string) {
    try {
      await api.unhideAccount(handle);
      refresh();
      onUnhidden();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  if (hidden.length === 0) return null;
  return (
    <Card className="gap-2 p-4">
      <p className="font-medium">Hidden accounts</p>
      <p className="text-sm text-muted-foreground">
        Researches never show reels from these accounts.
      </p>
      <ul className="flex flex-wrap gap-2">
        {hidden.map((h) => (
          <li key={h.handle}>
            <Button size="sm" variant="outline" onClick={() => unhide(h.handle)}>
              <Eye /> Show @{h.handle} again
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
