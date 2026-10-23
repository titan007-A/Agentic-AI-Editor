"""The autopilot: watches folders, playlists and channels and edits each new video once.

Every check lists what a feed holds, starts jobs for videos it hasn't seen (a few at a
time, and only in its hours), and records every video it starts or passes over, so
nothing is edited twice. Each video is an ordinary job: one that fails is marked failed
and the rest carry on. Finished videos land in the ready pile, or in the needs-a-look
pile when the agent has doubts about them.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from agentic_editor.db import Feed, FeedItem, Job

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
SETTLING = 60.0  # seconds a file must sit unchanged, so one still being copied is left alone
LIST_LIMIT = 200  # newest entries read from a playlist or channel
CHANNEL = re.compile(r"^https?://(www\.|m\.)?youtube\.com/(@[^/?#]+|(channel|c|user)/[^/?#]+)/?$")


@dataclass(frozen=True)
class Entry:
    key: str  # what identifies the video for good: its path, or the site and video id
    title: str
    source: str  # what a job is given: a file path or a link


def folder_entries(folder: Path, now: float | None = None) -> list[Entry]:
    """Video files in a folder, oldest first, leaving out any still being written."""
    if not folder.is_dir():
        raise FileNotFoundError(f"no such folder: {folder}")
    now = time.time() if now is None else now
    files = [
        f
        for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() in VIDEO_EXTENSIONS
        and not f.name.startswith(".")
        and now - f.stat().st_mtime >= SETTLING
    ]
    files.sort(key=lambda f: f.stat().st_mtime)
    return [Entry(key=str(f.resolve()), title=f.stem, source=str(f.resolve())) for f in files]


def channel_videos(url: str) -> str:
    """A bare channel link lists its tabs rather than its videos; read its videos tab."""
    return url.rstrip("/") + "/videos" if CHANNEL.match(url) else url


def link_entries(url: str) -> list[Entry]:
    """The videos of a playlist or channel, as the site lists them (a channel newest
    first). A link to a single video is a feed of one."""
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": LIST_LIMIT,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(channel_videos(url), download=False)
    site = info.get("extractor_key") or "web"
    if info.get("_type") != "playlist":
        return [Entry(f"{site}-{info.get('id')}", info.get("title") or url, url)]
    entries = []
    for entry in info.get("entries") or []:
        link = entry.get("url") or entry.get("webpage_url")
        if entry.get("_type") == "playlist" or not link or not entry.get("id"):
            continue  # a tab or a broken entry, not a video
        entries.append(
            Entry(
                key=f"{entry.get('ie_key') or site}-{entry['id']}",
                title=entry.get("title") or entry["id"],
                source=link,
            )
        )
    return entries


def entries(feed: Feed) -> list[Entry]:
    if feed.kind == "folder":
        return folder_entries(Path(feed.location).expanduser())
    return link_entries(feed.location)


def active(feed: Feed, hour: int) -> bool:
    """Whether new videos may start at this hour of the day."""
    if feed.active_from is None or feed.active_to is None:
        return True
    if feed.active_from <= feed.active_to:
        return feed.active_from <= hour < feed.active_to
    return hour >= feed.active_from or hour < feed.active_to  # overnight, e.g. 23 to 7


def check(
    feed_id: int,
    Session: sessionmaker,
    start: Callable[[str], None],
    lister: Callable[[Feed], list[Entry]] = entries,
    now: datetime | None = None,
) -> str:
    """Look at one feed and start jobs for videos it hasn't seen. Returns what happened.
    `now` is local time, which the feed's hours are in."""
    now = now or datetime.now()
    started: list[str] = []
    with Session() as db:
        feed = db.get(Feed, feed_id)
        if feed is None:
            return "no such feed"
        # The first look also marks what is already there, whatever the hour, so that
        # later only what arrives after the feed began counts as new. A look that failed
        # to read the feed doesn't count.
        skipping = not feed.looked and not feed.include_existing
        if not feed.enabled or not (skipping or active(feed, now.hour)):
            return "waiting" if feed.enabled else "off"
        try:
            found = lister(feed)
        except Exception as exc:
            log.warning("feed %s: %s", feed.name, exc)
            feed.last_checked, feed.last_result = datetime.now(UTC), f"couldn't read it: {exc}"
            db.commit()
            return feed.last_result
        seen = set(db.scalars(select(FeedItem.key).where(FeedItem.feed_id == feed.id)))
        new = [e for e in found if e.key not in seen]
        if skipping:
            for e in new:
                db.add(
                    FeedItem(
                        feed_id=feed.id, key=e.key, title=e.title, source=e.source, skipped=True
                    )
                )
            result = f"{len(new)} video(s) already there; only new ones will be edited"
        else:
            busy = db.scalar(
                select(func.count())
                .select_from(FeedItem)
                .join(Job, FeedItem.job_id == Job.id)
                .where(FeedItem.feed_id == feed.id, Job.status.in_(("queued", "running")))
            )
            room = max(0, feed.batch - busy) if active(feed, now.hour) else 0
            for e in new[:room]:
                job = Job(source=e.source, title=e.title, presets=feed.presets, notes=feed.notes)
                db.add(job)
                db.flush()
                db.add(
                    FeedItem(
                        feed_id=feed.id, key=e.key, title=e.title, source=e.source, job_id=job.id
                    )
                )
                started.append(job.id)
            waiting = len(new) - len(started)
            result = f"started {len(started)} new video(s)" + (
                f", {waiting} waiting" if waiting else ""
            )
        feed.looked = True
        feed.last_checked, feed.last_result = datetime.now(UTC), result
        db.commit()
    for job_id in started:
        start(job_id)
    return result


def check_all(Session: sessionmaker, start: Callable[[str], None], **kwargs) -> None:
    with Session() as db:
        ids = list(db.scalars(select(Feed.id).where(Feed.enabled)))
    for feed_id in ids:
        result = check(feed_id, Session, start, **kwargs)
        log.info("feed %d: %s", feed_id, result)
