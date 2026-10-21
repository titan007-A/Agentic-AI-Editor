"use client";

import { Download, LoaderCircle, Send } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { videoUrl, type Output } from "@/lib/api";
import { canShareFiles, shareVideoFile, videoFileName } from "@/lib/file-share.mjs";

// Phones struggle to hold much more than this in memory for the share sheet.
const MAX_SHARE_BYTES = 128 * 1024 * 1024;

function isPhone() {
  const nav = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
  if (nav.userAgentData?.mobile !== undefined) return nav.userAgentData.mobile;
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

function setupHint() {
  const origin = window.location.origin;
  if (/Android/i.test(navigator.userAgent)) {
    return (
      `Phones only share files from secure pages, and this one is plain http. One-time fix ` +
      `in Chrome: open chrome://flags/#unsafely-treat-insecure-origin-as-secure, add ` +
      `${origin}, set it to Enabled and relaunch Chrome. Downloading works meanwhile.`
    );
  }
  return (
    "Phones only share files from secure (https) pages, and this one is plain http. " +
    "Download the video and attach it in Telegram, or open the app over https."
  );
}

export function ShareButton({ output, name }: { output: Output; name: string }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [sharing, setSharing] = useState(false);
  const request = useRef<AbortController | null>(null);

  async function prepare() {
    setOpen(true);
    setFile(null);
    if (!isPhone()) {
      // Desktop Chrome and Edge claim they can share files and then refuse, so say so
      // up front instead of offering a button that leads nowhere.
      setStatus(
        "Desktop browsers can't hand a file to another app; only phones can. Open this " +
          "page on your phone to share straight into Telegram, or download it here.",
      );
      return;
    }
    if (!window.isSecureContext || !canShareFiles(navigator)) {
      setStatus(setupHint());
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setStatus("Preparing the MP4...");
    try {
      const response = await fetch(videoUrl(output), {
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Could not load the video. Try again.");
      if (Number(response.headers.get("content-length") ?? 0) > MAX_SHARE_BYTES) {
        setStatus("This video is too large to share from a phone. Download it and attach it.");
        return;
      }
      const blob = await response.blob();
      const prepared = new File([blob], videoFileName(name), { type: "video/mp4" });
      if (!navigator.canShare({ files: [prepared] })) {
        setStatus("This browser can't share this MP4. Download it and attach it in Telegram.");
        return;
      }
      setFile(prepared);
      setStatus("Ready. Tap Share, then pick Telegram. The video file itself is sent, not a link.");
    } catch (error) {
      if (!controller.signal.aborted)
        setStatus(error instanceof Error ? error.message : "Could not prepare the video.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  function close(next: boolean) {
    if (next) return;
    request.current?.abort();
    setOpen(false);
    setLoading(false);
    setFile(null);
  }

  function saveToDevice(because: string) {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    setStatus(`${because} The MP4 is saved to this phone; attach it from your downloads.`);
  }

  function share() {
    if (!file || sharing) return;
    // Must be the first thing this tap does; see shareVideoFile.
    let pending: Promise<void>;
    try {
      pending = shareVideoFile(navigator, file);
    } catch {
      saveToDevice("This browser can't share files.");
      return;
    }
    setSharing(true);
    pending
      .then(() => setStatus("Handed to the share menu. Finish sending in Telegram."))
      .catch((error: unknown) => {
        const kind = error instanceof Error ? error.name : "";
        if (kind === "AbortError") {
          setStatus("Sharing cancelled. Tap Share to try again.");
          return;
        }
        // Whatever else went wrong, they still end up with the video.
        saveToDevice(
          kind === "NotAllowedError"
            ? "The browser wouldn't hand the file to the share menu."
            : "Sharing failed.",
        );
      })
      .finally(() => setSharing(false));
  }

  return (
    <>
      <Button variant="outline" onClick={prepare}>
        <Send /> Share
      </Button>
      <Dialog open={open} onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Share to Telegram</DialogTitle>
            <DialogDescription>{videoFileName(name)}</DialogDescription>
          </DialogHeader>
          <p role="status" aria-live="polite" className="text-sm">
            {status}
          </p>
          <DialogFooter className="gap-2">
            <Button variant="outline" asChild>
              <a href={videoUrl(output, true)}>
                <Download /> Download
              </a>
            </Button>
            <Button disabled={!file || loading || sharing} onClick={share}>
              {loading || sharing ? <LoaderCircle className="animate-spin" /> : <Send />}
              {loading ? "Preparing..." : sharing ? "Sharing..." : "Share"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
