"use client";

import { Play } from "lucide-react";
import Image from "next/image";
import { useRef, useState } from "react";

import { thumbnailUrl, videoUrl, type Output } from "@/lib/api";

/** The video behind its thumbnail. Nothing of the video loads until it is played, and the
 * thumbnail only once it scrolls near the screen, so long lists stay light on a phone. */
export function OutputVideo({ output }: { output: Output }) {
  const video = useRef<HTMLVideoElement>(null);
  const [started, setStarted] = useState(false);
  // Most presets are 9:16; the thumbnail gives the real shape once it loads.
  const [ratio, setRatio] = useState("9 / 16");

  function play() {
    setStarted(true);
    // Called inside the tap itself, so phones let it play with sound.
    video.current?.play().catch(() => {});
  }

  return (
    <div
      className="relative mx-auto max-h-[70vh] w-full overflow-hidden rounded-sm bg-black"
      style={{ aspectRatio: ratio }}
    >
      <video
        ref={video}
        className="absolute inset-0 size-full object-contain"
        src={videoUrl(output)}
        controls={started}
        playsInline
        preload="none"
      />
      {!started && (
        <button
          type="button"
          aria-label={`Play ${output.preset}`}
          className="group absolute inset-0 grid place-items-center"
          onClick={play}
        >
          {output.has_thumbnail && (
            <Image
              src={thumbnailUrl(output)}
              alt=""
              fill
              unoptimized
              sizes="(min-width: 768px) 50vw, 100vw"
              className="object-contain"
              onLoad={(e) => {
                const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                if (w && h) setRatio(`${w} / ${h}`);
              }}
            />
          )}
          <span className="relative grid size-14 place-items-center rounded-[50%_45%_55%_40%] border-2 border-[var(--paper-light)] bg-black/55 text-[var(--paper-light)] transition-transform group-hover:scale-105">
            <Play className="ml-0.5 size-6 fill-current" />
          </span>
        </button>
      )}
    </div>
  );
}
