"""The song and artist of a video recognised from its sound, for a preset with auto_name
when neither the video's name nor the notes say them (an Instagram link is "Video by
<account>"). After the owner's automated_naming tool: the sound is the evidence, never a
model's guess, so no answer means asking for the name rather than inventing one.

Shazam is asked through `shazamio`, a community library that speaks to Shazam's own
endpoint: free and without a key, but not a documented API, so it can stop working
without notice. Then it just finds nothing and the job asks for the name."""

import asyncio
import json
import logging
from pathlib import Path

from agentic_editor import ffmpeg

log = logging.getLogger(__name__)

# Shazam matches from a short excerpt; more audio doesn't help. A reel's first seconds are
# often an intro or talk, so it hears a little way in first, then the middle.
EXCERPT = 20.0
OFFSET = 5.0
TIMEOUT = 45.0  # seconds for one excerpt, retries included


def song(video: Path, work_dir: Path, ask=None) -> tuple[str, str] | None:
    """(song, artist), or None when Shazam can't tell. A match is kept in the work dir;
    no match isn't, so the next try asks again."""
    cache = work_dir / "shazam.json"
    if cache.exists():
        found = json.loads(cache.read_text())
        return found["song"], found["artist"]
    ask = ask or _recognise
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = ffmpeg.probe(video).duration
    for start in starts(duration):
        clip = work_dir / "shazam.wav"
        try:
            ffmpeg.run(["-y", "-ss", f"{start:.2f}", "-i", str(video), "-map", "0:a:0",
                        "-t", f"{EXCERPT:g}", "-ac", "1", "-ar", "44100", "-vn",
                        str(clip)])  # fmt: skip
            track = ask(clip)
        except Exception as exc:  # the network layer is a third party's
            log.warning("Shazam couldn't be asked about %s: %s", video.name, exc)
            return None
        finally:
            clip.unlink(missing_ok=True)
        if found := named(track):
            log.info("Shazam heard %s - %s at %.0f s", *found, start)
            cache.write_text(json.dumps({"song": found[0], "artist": found[1]}))
            return found
    log.info("Shazam didn't recognise %s", video.name)
    return None


def starts(duration: float) -> list[float]:
    """Where the excerpts start: a little way in, then the middle of a long video."""
    first = OFFSET if duration > EXCERPT + OFFSET else 0.0
    middle = max(0.0, duration / 2 - EXCERPT / 2)
    return [first, middle] if middle > first + EXCERPT else [first]


def named(track: dict | None) -> tuple[str, str] | None:
    song, artist = (str((track or {}).get(k) or "").strip() for k in ("title", "subtitle"))
    return (song, artist) if song and artist else None


def _recognise(clip: Path) -> dict | None:
    from aiohttp_retry import ExponentialRetry
    from shazamio import Shazam
    from shazamio.client import HTTPClient

    # shazamio's own client retries 20 times for up to a minute each: a job would wait
    # minutes on a busy Shazam, where asking for the name takes a second.
    retry = ExponentialRetry(attempts=3, max_timeout=10, statuses={429, 500, 502, 503, 504})

    async def ask() -> dict | None:
        async with HTTPClient(retry_options=retry) as client:
            found = await asyncio.wait_for(Shazam(http_client=client).recognize(str(clip)), TIMEOUT)
        return (found or {}).get("track")

    return asyncio.run(ask())
