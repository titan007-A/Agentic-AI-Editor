"""The autopilot: watches folders, playlists, channels and Instagram accounts and edits each
new video once.

Every check lists what a feed holds, starts jobs for videos it hasn't seen (a few at a
time, and only in its hours), and records every video it starts or passes over, so
nothing is edited twice. Each video is an ordinary job: one that fails is marked failed
and the rest carry on. Finished videos land in the ready pile, or in the needs-a-look
pile when the agent has doubts about them.

An Instagram account can't be listed without signing in (yt-dlp then says "Unable to
extract data"), so its reels are found the way the researcher finds an account's own: web
searches for its name, each reel looked up and kept when it is the account's. Search
engines index only part of Instagram, mostly reels a few days old, so such a feed sees a
new reel some days after it is posted, and may miss some.

A feed with a topic ("GTA 5") edits only videos about it: each new video is downloaded and
the vision model looks at frames from it before a job is started (see judge_topic); the
rest are passed over, with the reason on the feed. Since that waits its turn on the GPU
behind the jobs, such a feed is looked at on its own thread, never holding up the scan.
A feed with a folder puts its finished videos in that folder on the piles (file_outputs).
"""

import logging
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agentic_editor import research
from agentic_editor.db import Feed, FeedItem, Job, PileFolder
from agentic_editor.download import signed_in

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
SETTLING = 60.0  # seconds a file must sit unchanged, so one still being copied is left alone
LIST_LIMIT = 200  # newest entries read from a playlist or channel
CHANNEL = re.compile(r"^https?://(www\.|m\.)?youtube\.com/(@[^/?#]+|(channel|c|user)/[^/?#]+)/?$")
# New videos a topic feed downloads and looks at in one check, at most: the rest wait for
# the next check, so one check never takes the GPU for long.
TOPIC_LOOKS = 8
TOPIC_FRAMES = 6  # frames the vision model sees of each video, spread over its length
# Checks a video that can't be downloaded is tried in before it is passed over: a network
# hiccup clears by the next check, a reel Instagram won't give out doesn't.
TOPIC_TRIES = 3
_tries: dict[tuple[int, str], int] = {}
# What the frames show, asked without the topic: asked "is this GTA 6?", the small vision
# models leaned towards no, and asked about GTA 5 they called the same GTA 6 footage GTA 6.
SEEN_SCHEMA = {
    "type": "object",
    "properties": {
        "game": {"type": "string"},
        "shows": {"type": "string"},
        "text": {"type": "string"},
    },
    "required": ["game", "shows", "text"],
}
# Small questions the code puts together (see fits): asked for one verdict, the small
# model went by its own name for the game and passed over what the video said it was.
FITS_SCHEMA = {
    "type": "object",
    "properties": {
        "same_series": {"type": "boolean"},
        "exact": {"type": "boolean"},
        "named": {"type": "boolean"},
        "named_other": {"type": "boolean"},
        "why": {"type": "string"},
    },
    "required": ["same_series", "exact", "named", "named_other", "why"],
}
_checking: dict[int, threading.Lock] = {}
_checking_lock = threading.Lock()


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


def instagram_handle(url: str) -> str | None:
    """The account's name when the link is an Instagram profile ("instagram.com/name")."""
    if not re.match(r"https?://(www\.|m\.)?instagram\.com/", url.strip()):
        return None
    return research.handle_of(url)


def instagram_entries(handle: str, find=None, info=None, pause: float | None = None) -> list[Entry]:
    """An Instagram account's reels the search engines know, newest first."""
    options = {k: v for k, v in (("find", find), ("pause", pause)) if v is not None}
    reels, failed = research.search([f'"{handle}"', handle], **options)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; the next check tries again")
    reels = reels[: research.LEARN_REELS * 2]
    research.look_up(reels, **({"info": info} if info else {}))
    if reels and not any(r.looked_up for r in reels):
        raise RuntimeError("Instagram didn't show any reel; the next check tries again")
    own = [r for r in reels if r.handle == handle]
    own.sort(key=lambda r: r.posted or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [
        Entry(
            key=f"Instagram-{r.code}",
            title=" ".join((r.caption or r.title or r.code).split())[:80],
            source=r.url,
        )
        for r in own
    ]


def link_entries(url: str) -> list[Entry]:
    """The videos of a playlist or channel, as the site lists them (a channel newest
    first), or an Instagram account's reels (see instagram_entries). A link to a single
    video is a feed of one."""
    if handle := instagram_handle(url):
        return instagram_entries(handle)
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": LIST_LIMIT,
    }
    with signed_in(options) as options, YoutubeDL(options) as ydl:
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


@dataclass(frozen=True)
class Verdict:
    fits: bool | None  # None: it couldn't be looked at
    why: str


def seen_prompt(frames: int) -> str:
    return (
        f"These are {frames} frames, in order, from one short video. What does it show? If "
        "it shows a video game's footage (even beside a streamer's face, under captions, "
        "modded, on a roleplay server, or in a trailer), name the game exactly, with its part "
        "number; otherwise game is empty and say what it shows (real life, a film, a "
        "cartoon...). In text, write the words written on screen (titles, captions, logos), "
        "or nothing."
    )


def fits_prompt(topic: str, seen: dict, caption: str) -> str:
    said = " ".join(caption.split())[:400]
    game, shows = (" ".join(str(seen.get(k) or "").split()) for k in ("game", "shows"))
    text = " ".join(str(seen.get("text") or "").split())[:300]
    return (
        f'Answer about one video and the topic "{topic}". A vision model looked at frames '
        f'from it and saw: game "{game}"; shows "{shows}". The words in the video: its '
        f'caption "{said}"; written on screen "{text}".\n'
        f"- same_series: are the frames {topic}'s footage, or the same game series or "
        "subject, whichever part or number?\n"
        f"- exact: is what the frames show exactly {topic}, the same part if it has parts?\n"
        f"- named: do the caption or the words on screen name {topic} itself (any spelling, "
        "a hashtag too)?\n"
        f"- named_other: do they name another part of the same series, and not {topic}?\n"
        "- why: one short sentence saying what the frames show."
    )


# A part of a series named in a topic: "GTA 6", "GTA VI", "FIFA 24".
PART = re.compile(r"(?<![\w])(\d+|[IVX]{1,4})(?![\w])")


def names_a_part(topic: str) -> bool:
    return PART.search(topic) is not None


def fits(answer: dict, part: bool = True) -> bool:
    """The video is the topic's when the frames show its series and, for a topic that names
    a part of it (`part`), the video names the topic; or, with no other part of the series
    named, the vision model saw the topic itself. The video's own words decide the part:
    the small models call a game newer than their training by an older part's name (GTA 6
    footage "GTA V"). A topic without a part ("GTA") is any part's footage."""
    if answer.get("same_series") is not True:
        return False
    if not part:
        return True
    return answer.get("named") is True or (
        answer.get("named_other") is not True and answer.get("exact") is True
    )


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def judge_topic(topic: str, found: list[Entry], settings=None, router=None) -> list[Verdict]:
    """Whether each video is about `topic`, by frames from it. Each is downloaded first (a
    job given it later finds the download there); a video that can't be opened gets a
    Verdict of None. The vision model first says what the frames show without being told
    the topic, then whether that, with the caption and the words on screen, is the topic:
    the small models know no game newer than their training (GTA 6 footage they call GTA
    V), so a newer part of a series is told apart by what the video says it is. All are
    judged in one turn of the vision model, behind any job waiting for the GPU. A model
    that can't be reached raises."""
    from agentic_editor import ffmpeg, pipeline, slots
    from agentic_editor.backends import get_backends
    from agentic_editor.review import sample_frames
    from agentic_editor.router import ModelRouter
    from agentic_editor.settings import get_settings

    settings = settings or get_settings()
    verdicts: list[Verdict | None] = []
    shown: list[tuple[int, list[Path], str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, entry in enumerate(found):
            try:
                source = pipeline.resolve_source(entry.source, settings)
                duration = ffmpeg.probe(source.path).duration
                frames = sample_frames(source.path, duration, Path(tmp) / str(i), TOPIC_FRAMES)
            except Exception as exc:
                log.info("couldn't open %s to look at it: %s", entry.source, exc)
                verdicts.append(Verdict(None, f"couldn't download it: {_short(exc)}"))
                continue
            verdicts.append(None)
            shown.append((i, frames, source.description or entry.title))
        if shown:
            router = router or ModelRouter(settings, get_backends())
            with slots.behind(), router.vision() as vision:
                for i, frames, caption in shown:
                    seen = vision.chat_json(
                        [{"role": "user", "content": seen_prompt(len(frames))}],
                        SEEN_SCHEMA,
                        images=frames,
                        temperature=0,
                    )
                    answer = vision.chat_json(
                        [{"role": "user", "content": fits_prompt(topic, seen, caption)}],
                        FITS_SCHEMA,
                        temperature=0,
                    )
                    log.info("topic %r: saw %s, answered %s", topic, seen, answer)
                    why = " ".join(str(answer.get("why") or "").split())[:200]
                    verdicts[i] = Verdict(fits(answer, names_a_part(topic)), why)
    return [v for v in verdicts if v is not None]


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


Judge = Callable[[str, list[Entry]], list[Verdict]]


def _lock(feed_id: int) -> threading.Lock:
    with _checking_lock:
        return _checking.setdefault(feed_id, threading.Lock())


def check(
    feed_id: int,
    Session: sessionmaker,
    start: Callable[[str], None],
    lister: Callable[[Feed], list[Entry]] = entries,
    now: datetime | None = None,
    judge: Judge | None = None,
) -> str:
    """Look at one feed and start jobs for videos it hasn't seen. Returns what happened.
    `now` is local time, which the feed's hours are in; `judge` says which videos are
    about a topic feed's topic (judge_topic)."""
    # The scheduled scan and "check now" may look at one feed at the same time; both
    # would start the same new videos.
    with _lock(feed_id):
        return _check(feed_id, Session, start, lister, now or datetime.now(), judge or judge_topic)


def _start_jobs(db: Session, feed: Feed, found: list[Entry]) -> list[str]:
    started = []
    for e in found:
        job = Job(
            source=e.source,
            title=e.title,
            presets=feed.presets,
            notes=feed.notes,
            owner_id=feed.owner_id,
        )
        db.add(job)
        db.flush()
        db.add(FeedItem(feed_id=feed.id, key=e.key, title=e.title, source=e.source, job_id=job.id))
        started.append(job.id)
    return started


def _check(
    feed_id: int,
    Session: sessionmaker,
    start: Callable[[str], None],
    lister: Callable[[Feed], list[Entry]],
    now: datetime,
    judge: Judge,
) -> str:
    started: list[str] = []
    topic, waiting, room = None, [], 0
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
            if feed.topic and room and new:
                # Looked at below, with the database let go meanwhile.
                topic, waiting = feed.topic, new
                result = f"looking at the new videos for {topic}; this takes a few minutes"
            else:
                started = _start_jobs(db, feed, new[:room])
                left = len(new) - len(started)
                result = f"started {len(started)} new video(s)" + (
                    f", {left} waiting" if left else ""
                )
        feed.looked = True
        feed.last_checked, feed.last_result = datetime.now(UTC), result
        db.commit()
    if topic:
        result, started = _check_topic(feed_id, Session, topic, waiting, room, judge)
    for job_id in started:
        start(job_id)
    return result


def _check_topic(
    feed_id: int, Session: sessionmaker, topic: str, new: list[Entry], room: int, judge: Judge
) -> tuple[str, list[str]]:
    """Look at new videos, a few at a time, until `room` are about the topic: start those
    and pass over the rest for good, each with its reason."""
    fits: list[Entry] = []
    passed: list[tuple[Entry, str]] = []
    looked, problem, again = 0, "", 0
    while looked < min(len(new), TOPIC_LOOKS) and len(fits) < room:
        batch = new[looked : looked + min(room - len(fits), TOPIC_LOOKS - looked)]
        try:
            verdicts = judge(topic, batch)
        except Exception as exc:
            log.warning("feed %d: couldn't look at the videos: %s", feed_id, exc)
            problem = f"; couldn't look at the rest ({_short(exc)}), the next check tries again"
            break
        looked += len(batch)
        for entry, verdict in zip(batch, verdicts, strict=True):
            if verdict.fits:
                fits.append(entry)
            elif verdict.fits is None:
                tries = _tries[feed_id, entry.key] = _tries.get((feed_id, entry.key), 0) + 1
                if tries >= TOPIC_TRIES:
                    passed.append((entry, verdict.why))
                    del _tries[feed_id, entry.key]
                else:
                    again += 1
            else:
                passed.append((entry, f"not {topic}: {verdict.why}".rstrip(": ")))
    with Session() as db:
        feed = db.get(Feed, feed_id)
        if feed is None:
            return "no such feed", []
        started = _start_jobs(db, feed, fits)
        for e, why in passed:
            db.add(
                FeedItem(
                    feed_id=feed.id, key=e.key, title=e.title, source=e.source, skipped=True,
                    reason=why,
                )
            )  # fmt: skip
        left = len(new) - looked
        result = f"started {len(started)} new video(s) about {topic}"
        if passed:
            result += f", passed over {len(passed)}"
        if again:
            result += f", {again} couldn't be downloaded (tried again next check)"
        if left:
            result += f", {left} waiting"
        result += problem
        feed.last_checked, feed.last_result = datetime.now(UTC), result
        db.commit()
    return result, started


def file_outputs(db: Session, job: Job) -> None:
    """Put a feed's finished videos in its folder on the piles, made the first time, inside
    each video's preset's folder."""
    item = db.scalar(select(FeedItem).where(FeedItem.job_id == job.id))
    name = " ".join((item.feed.folder or "").split()) if item else ""
    if not name:
        return
    owner = item.feed.owner_id
    for output in job.outputs:
        place = select(PileFolder).where(
            PileFolder.preset == output.preset,
            PileFolder.parent_id.is_(None),
            PileFolder.owner_id.is_(None) if owner is None else PileFolder.owner_id == owner,
        )
        folder = next((f for f in db.scalars(place) if f.name.casefold() == name.casefold()), None)
        if folder is None:
            folder = PileFolder(owner_id=owner, preset=output.preset, name=name)
            db.add(folder)
            db.flush()
        output.folder_id = folder.id


def _check_logged(feed_id: int, Session: sessionmaker, start, **kwargs) -> None:
    try:
        result = check(feed_id, Session, start, **kwargs)
    except Exception:
        log.exception("checking feed %d failed", feed_id)
        return
    log.info("feed %d: %s", feed_id, result)


def check_all(
    Session: sessionmaker, start: Callable[[str], None], **kwargs
) -> list[threading.Thread]:
    """Check every feed that is on. A topic feed is checked on its own thread, unless its
    last check is still going; those threads are returned."""
    with Session() as db:
        feeds = db.execute(select(Feed.id, Feed.topic).where(Feed.enabled)).all()
    threads = []
    for feed_id, topic in feeds:
        if not topic:
            _check_logged(feed_id, Session, start, **kwargs)
        elif not _lock(feed_id).locked():
            thread = threading.Thread(
                target=_check_logged, args=(feed_id, Session, start), kwargs=kwargs, daemon=True
            )
            thread.start()
            threads.append(thread)
    return threads
