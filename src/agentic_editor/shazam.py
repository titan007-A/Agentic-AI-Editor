"""The song and artist of a video recognised from its sound, for a preset with auto_name
when neither the video's name nor the notes say them (an Instagram link is "Video by
<account>"). After the owner's automated_naming tool: the sound is the evidence, never a
model's guess, so no answer means asking for the name rather than inventing one.

Shazam is asked through `shazamio`, a community library that speaks to Shazam's own
endpoint: free and without a key, but not a documented API, so it can stop working
without notice. Then it just finds nothing and the job asks for the name.

Shazam's names carry notes about the take, "Summer of '69 (Live)", "Payphone (feat. Wiz
Khalifa)", "Bohemian Rhapsody - Remastered 2011", and the reel shows only the song and
artist. A bracket that says what it is goes; any other may be the name's own, "(I Can't Get
No) Satisfaction", so MusicBrainz (free, open, canonical titles) says whether the song is
known by the name without it. Unsure, the bracket stays: a word too many beats a cut name."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from agentic_editor import ffmpeg

log = logging.getLogger(__name__)

# Shazam matches from a short excerpt; more audio doesn't help. A reel's first seconds are
# often an intro or talk, so it hears a little way in first, then the middle.
EXCERPT = 20.0
OFFSET = 5.0
TIMEOUT = 45.0  # seconds for one excerpt, retries included

# A bracket, or a " - " tail, after the name.
GROUP = re.compile(r"\s*[(\[{]([^)\]}]*)[)\]}]|\s+[-–—]\s+(.*)$")
# Words that make a group a note about the take, not part of the name; a year or any
# number does too.
NOTE = re.compile(
    r"(?<!\w)(?:live|remaster(?:ed)?|version|edit|mix|remix|acoustic|demo|instrumental|mono"
    r"|stereo|radio|single|bonus|deluxe|anniversary|unplugged|session|feat\.?|ft\.|featuring"
    r"|with|from|soundtrack|ost|theme|explicit|karaoke|re-?recorded)(?!\w)|\d",
    re.I,
)
FEATURING = re.compile(r"\s+(?:feat\.?|ft\.|featuring)\s.*$", re.I)
EDGES = " \t-_,;:|/\\"
MUSICBRAINZ = "https://musicbrainz.org/ws/2/recording"
# MusicBrainz asks every client to say who it is.
_asked = [0.0]  # when MusicBrainz was last asked
AGENT = "agentic-editor/1.0 (https://github.com/titan007-A/Agentic-AI-Editor)"


def song(video: Path, work_dir: Path, ask=None, look=None) -> tuple[str, str] | None:
    """(song, artist) without Shazam's notes, or None when Shazam can't tell. A match is
    kept in the work dir; no match isn't, so the next try asks again."""
    cache = work_dir / "shazam-v2.json"
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
        if heard := named(track):
            found = names_only(*heard, look or titles)
            log.info("Shazam heard %s - %s at %.0f s: %s - %s", *heard, start, *found)
            cache.write_text(json.dumps({"song": found[0], "artist": found[1], "heard": heard}))
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


def names_only(
    song: str, artist: str, look: Callable[[str, str], set[str] | None]
) -> tuple[str, str]:
    """The song and artist without Shazam's notes; `look` gives the titles MusicBrainz
    knows for a name by the artist (None when it can't be asked)."""
    artist = _tidy(FEATURING.sub("", _without_notes(artist))) or artist
    noted = _tidy(_without_notes(song)) or song
    bare = _tidy(GROUP.sub("", noted))
    if not bare or bare == noted:
        return noted, artist
    # The name with its bracket first: an artist's live takes can far outnumber the
    # recordings under the full name, "Satisfaction" against "(I Can't Get No) ...".
    for name in (noted, bare):
        if (known := look(name, artist)) is None:
            break
        if same(name) in known:
            return name, artist
    return noted, artist


def _without_notes(name: str) -> str:
    return GROUP.sub(lambda m: "" if NOTE.search(m.group(1) or m.group(2) or "") else m[0], name)


def _tidy(name: str) -> str:
    return " ".join(name.split()).strip(EDGES)


def same(name: str) -> str:
    """A title as compared: case, quotes and punctuation don't count."""
    return " ".join(re.sub(r"[\W_]+", " ", name.casefold()).split())


def titles(song: str, artist: str, transport=None) -> set[str] | None:
    """Titles of the artist's recordings that have `song` in them, as MusicBrainz names
    them; None when it can't be asked."""

    def quoted(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    query = f"recording:{quoted(song)} AND artist:{quoted(artist)}"
    # MusicBrainz serves one request a second to a client.
    time.sleep(max(0.0, _asked[0] + 1.0 - time.monotonic()))
    _asked[0] = time.monotonic()
    try:
        with httpx.Client(transport=transport, timeout=10, headers={"User-Agent": AGENT}) as http:
            reply = http.get(MUSICBRAINZ, params={"query": query, "fmt": "json", "limit": 50})
            reply.raise_for_status()
            found = reply.json().get("recordings", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("MusicBrainz couldn't be asked about %s - %s: %s", song, artist, exc)
        return None
    return {same(r.get("title", "")) for r in found}


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
