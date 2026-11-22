"""Kick, read the way its own website reads it: a channel, its streams (past ones and the
one live now), the clips its viewers made, its chat at any moment of a stream, and the
stream's video, ten seconds at a time.

Kick's site API (kick.com/api/v1 and v2) is public but not documented: its answers are
read loosely, so a field Kick renames costs one signal, not the whole hunt. A stream's
video is HLS: each ten-second piece carries the wall-clock time it was filmed at, which
is how a chat message (stamped in UTC) is placed in the video. A stream still live has
its video so far, growing as it goes.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

API = "https://kick.com/api"
# Kick answers its API only to what looks like a browser.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}
CHAT_SPAN = 5.0  # seconds of chat one read gives, from its start time on
TRIES = 3
SLUG = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
VOD_LINK = re.compile(
    r"kick\.com/(?:(?P<slug>[\w-]+)/videos|video)/(?P<uuid>[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})",
    re.I,
)
CHANNEL_LINK = re.compile(r"kick\.com/(?P<slug>[\w-]+)/?(?:[?#].*)?$", re.I)
NOT_CHANNELS = {"video", "videos", "categories", "category", "search", "auth", "browse", "clips"}
CLIP_LINK = re.compile(r"kick\.com/[\w-]+(?:/clips/|/?\?(?:[^#]+&)?clip=)clip_", re.I)
# kick.com/<streamer>/videos/<id>?t=... is how a person opens a stream at a moment
MOMENT_LINK = "https://kick.com/{slug}/videos/{uuid}?t={seconds}"


class KickError(RuntimeError):
    pass


@dataclass(frozen=True)
class Link:
    kind: str  # "stream" (a past or live stream's video) or "channel"
    slug: str = ""  # the streamer, when the link names them
    uuid: str = ""  # a stream's video id (kind "stream")


def parse_link(text: str) -> Link:
    """What a link (or a bare streamer name) points at; KickError when it's no Kick
    stream or channel."""
    text = text.strip()
    if CLIP_LINK.search(text):
        raise KickError("that's a clip; give the stream it is from, or the streamer")
    if found := VOD_LINK.search(text):
        return Link("stream", (found["slug"] or "").lower(), found["uuid"].lower())
    if found := CHANNEL_LINK.search(text):
        slug = found["slug"].lower()
        if slug not in NOT_CHANNELS:
            return Link("channel", slug)
    bare = text.removeprefix("@")
    if SLUG.match(bare):
        return Link("channel", bare.lower())
    raise KickError("that isn't a Kick stream or streamer (kick.com/<name> or a stream's link)")


@dataclass
class Stream:
    id: int  # the livestream's id (chat and viewers' clips name it)
    title: str
    started: datetime  # UTC
    duration: float  # seconds; 0 while live
    live: bool
    source: str  # the HLS master playlist ("" when Kick doesn't give it)
    uuid: str = ""  # its video's id, for links
    thumbnail: str = ""
    views: int = 0


@dataclass
class Channel:
    id: int
    slug: str
    username: str
    avatar: str = ""
    live: Stream | None = None
    chatroom: int = 0
    vods: bool = True  # whether Kick keeps videos of its streams


@dataclass(frozen=True)
class Line:
    at: datetime  # UTC
    text: str
    user: str = ""


@dataclass(frozen=True)
class ViewerClip:
    id: str
    title: str
    made: datetime  # UTC, when it was made (its end, for a clip made while live)
    duration: float
    views: int
    stream_id: int


@dataclass(frozen=True)
class Piece:
    start: float  # seconds into the stream
    length: float
    at: datetime  # UTC, when it was filmed
    url: str


@dataclass
class Playlist:
    pieces: list[Piece] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.pieces[-1].start + self.pieces[-1].length if self.pieces else 0.0

    def time_of(self, at: datetime) -> float:
        """Seconds into the stream of a wall-clock moment."""
        if not self.pieces:
            return 0.0
        piece = self.pieces[0]
        for p in self.pieces:
            if p.at > at:
                break
            piece = p
        return piece.start + (at - piece.at).total_seconds()

    def at(self, seconds: float) -> datetime:
        """The wall-clock moment of a time in the stream."""
        piece = self.covering(seconds)
        return piece.at + timedelta(seconds=seconds - piece.start)

    def covering(self, seconds: float) -> Piece:
        for p in self.pieces:
            if p.start + p.length > seconds:
                return p
        return self.pieces[-1]

    def between(self, start: float, end: float) -> list[Piece]:
        return [p for p in self.pieces if p.start + p.length > start and p.start < end]


def _when(value) -> datetime | None:
    """Kick's times: "2026-11-19 21:03:03" (UTC), ISO with Z or an offset."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    text = value.strip().replace(" ", "T").replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # Python reads microseconds, not nanoseconds
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return None
    return found.replace(tzinfo=UTC) if found.tzinfo is None else found.astimezone(UTC)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stream(item: dict) -> Stream | None:
    started = _when(item.get("start_time")) or _when(item.get("created_at"))
    if started is None or not item.get("id"):
        return None
    video = item.get("video") or {}
    thumb = item.get("thumbnail")
    if isinstance(thumb, dict):
        thumb = thumb.get("src") or thumb.get("url") or ""
    return Stream(
        id=_int(item["id"]),
        title=" ".join(str(item.get("session_title") or "").split()),
        started=started,
        duration=_int(item.get("duration")) / 1000,
        live=bool(item.get("is_live")),
        source=str(item.get("source") or ""),
        uuid=str(video.get("uuid") or ""),
        thumbnail=thumb if isinstance(thumb, str) else "",
        views=_int(item.get("views") or video.get("views")),
    )


class Client:
    """Kick's site API; `transport` stands in for the network in tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None, pause: float = 1.0):
        self.http = httpx.Client(
            headers=HEADERS, timeout=25, follow_redirects=True, transport=transport
        )
        self.pause = pause  # before asking again after a refusal or a failure

    def close(self) -> None:
        self.http.close()

    def text(self, url: str) -> str:
        for attempt in range(TRIES):
            try:
                answer = self.http.get(url)
            except httpx.HTTPError as exc:
                if attempt == TRIES - 1:
                    raise KickError(f"Kick couldn't be reached: {exc}") from exc
            else:
                if answer.status_code == 404:
                    raise KickError("Kick doesn't know that (404)")
                if answer.status_code in (401, 403) and "/api/" not in url:
                    # The video itself refused (its playlists and pieces), not the site API.
                    raise KickError(
                        f"Kick refuses this stream's video ({answer.status_code}): a "
                        "subscribers-only stream needs a signed-in Kick account"
                    )
                if answer.status_code < 400:
                    return answer.text
                if answer.status_code not in (403, 429) and answer.status_code < 500:
                    raise KickError(f"Kick answered {answer.status_code}")
                if attempt == TRIES - 1:
                    raise KickError(f"Kick answered {answer.status_code}; try again later")
            time.sleep(self.pause * (attempt + 1))
        raise KickError("Kick couldn't be reached")

    def json(self, path: str):
        text = self.text(f"{API}/{path}")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise KickError("Kick answered something that isn't JSON") from exc

    def channel(self, slug: str) -> Channel:
        data = self.json(f"v2/channels/{slug}")
        if not isinstance(data, dict) or not data.get("id"):
            raise KickError(f"no Kick channel {slug!r}")
        user = data.get("user") or {}
        live = data.get("livestream")
        stream = _stream(live) if isinstance(live, dict) and live.get("is_live") else None
        if stream is not None and not stream.source:
            # The channel names the live stream; its video so far is in the streams list.
            found = next((s for s in self.streams(slug) if s.id == stream.id), None)
            if found is not None:
                stream.source, stream.uuid = found.source, found.uuid
        return Channel(
            id=_int(data["id"]),
            slug=str(data.get("slug") or slug),
            username=str(user.get("username") or data.get("slug") or slug),
            avatar=str(user.get("profile_pic") or ""),
            live=stream,
            chatroom=_int((data.get("chatroom") or {}).get("id")),
            vods=data.get("vod_enabled") is not False,
        )

    def streams(self, slug: str) -> list[Stream]:
        """The channel's streams, newest first: the live one (if any) and its past ones."""
        data = self.json(f"v2/channels/{slug}/videos")
        items = data if isinstance(data, list) else []
        return [s for s in (_stream(i) for i in items if isinstance(i, dict)) if s is not None]

    def video(self, uuid: str) -> tuple[str, Stream]:
        """A stream by its video's id: (its streamer's slug, the stream)."""
        data = self.json(f"v1/video/{uuid}")
        live = data.get("livestream") if isinstance(data, dict) else None
        if not isinstance(live, dict):
            raise KickError("Kick doesn't know that stream")
        stream = _stream({**live, "video": {"uuid": uuid}})
        if stream is None:
            raise KickError("Kick doesn't know when that stream was")
        stream.source = str(data.get("source") or live.get("source") or "")
        channel = live.get("channel") or {}
        slug = str(channel.get("slug") or (channel.get("user") or {}).get("username") or "")
        return slug.lower(), stream

    def chat(self, channel_id: int, start: datetime) -> list[Line]:
        """Chat messages sent in the CHAT_SPAN seconds from `start` (fewer, ending sooner,
        when Kick gives only its first page of them), oldest first."""
        stamp = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data = self.json(f"v2/channels/{channel_id}/messages?start_time={stamp}")
        messages = ((data or {}).get("data") or {}).get("messages") or []
        lines = []
        for m in messages:
            at = _when(m.get("created_at")) if isinstance(m, dict) else None
            if at is not None:
                sender = m.get("sender") or {}
                lines.append(Line(at, str(m.get("content") or ""), str(sender.get("username", ""))))
        return sorted(lines, key=lambda line: line.at)

    def clips(self, slug: str, stream: Stream) -> list[ViewerClip]:
        """Clips viewers made of `stream` while it was live: the channel's most watched
        this month and its newest today. Only a clip made live is placed in the stream (it
        ends when it was made); one made later from the video says nothing of where it is.
        Kick's paging repeats pages, so each list is read only from its first page."""
        end = stream.started + timedelta(seconds=stream.duration or 86400) + timedelta(minutes=2)
        found: dict[str, ViewerClip] = {}
        for order, span in (("view", "month"), ("date", "day")):
            try:
                data = self.json(f"v2/channels/{slug}/clips?sort={order}&time={span}")
            except KickError as exc:
                log.info("viewers' clips of %s unread: %s", slug, exc)
                continue
            for item in (data.get("clips") if isinstance(data, dict) else None) or []:
                made = _when(item.get("created_at"))
                if made is None or not stream.started <= made <= end:
                    continue
                if _int(item.get("livestream_id")) != stream.id or not item.get("id"):
                    continue
                found[str(item["id"])] = ViewerClip(
                    id=str(item["id"]),
                    title=" ".join(str(item.get("title") or "").split()),
                    made=made,
                    duration=float(item.get("duration") or 0),
                    views=_int(item.get("views") or item.get("view_count")),
                    stream_id=stream.id,
                )
        return sorted(found.values(), key=lambda c: c.made)

    def variants(self, source: str) -> dict[str, str]:
        """The stream's qualities ("1080p60", "160p30", ...) and their playlists."""
        lines = self.text(source).splitlines()
        found: dict[str, str] = {}
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            uri = next((x for x in lines[i + 1 :] if x and not x.startswith("#")), "")
            name = re.search(r'VIDEO="([^"]+)"', line)
            height = re.search(r"RESOLUTION=\d+x(\d+)", line)
            key = name.group(1) if name else f"{height.group(1) if height else len(found)}p"
            if uri:
                found[key] = urljoin(source, uri)
        if not found:
            raise KickError("the stream's video has no playable quality")
        return found

    def playlist(self, url: str) -> Playlist:
        return parse_playlist(self.text(url), url)


def height_of(name: str) -> int:
    found = re.match(r"(\d+)p", name)
    return int(found.group(1)) if found else 0


def best(variants: dict[str, str], most: int = 1080) -> str:
    """The sharpest quality up to `most` lines tall (60 fps before 30)."""
    ranked = sorted(variants, key=lambda n: (height_of(n) <= most, height_of(n), n), reverse=True)
    return variants[ranked[0]]


def smallest(variants: dict[str, str]) -> str:
    """The lightest quality: enough to hear the stream."""
    return variants[min(variants, key=lambda n: (height_of(n) or 10**6, n))]


def parse_playlist(text: str, url: str) -> Playlist:
    pieces: list[Piece] = []
    at: datetime | None = None
    length = 0.0
    start = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            at = _when(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            try:
                length = float(line.split(":", 1)[1].split(",")[0])
            except ValueError:
                length = 0.0
        elif line and not line.startswith("#"):
            if at is None:
                # A piece without its own time follows on from the one before it.
                at = pieces[-1].at + timedelta(seconds=pieces[-1].length) if pieces else None
            if at is not None and length > 0:
                pieces.append(Piece(start, length, at, urljoin(url, line)))
                start += length
            at, length = None, 0.0
    return Playlist(pieces)
