"""The channel autopilot: a connected Instagram account that runs itself (ChannelPilot).

A day of it:
1. At its research hour it reads the account's own posts through Meta's API (learn): their
   captions, likes and comments, and the insights Meta gives the login (views, reach,
   saves), when it gives any. The best liked are the research's taste; every song in them
   is one the account never posts again (the owner's data: reposts killed the channel).
2. Its research account's research runs (research.py) for the account and its preset,
   leaving out every song posted or waiting.
3. Of what the research gave, the best scored that fit well enough (MIN_FIT) are picked, as
   many as keep BUFFER_DAYS of posts waiting (TRIES for each one wanted), and checked one at
   a time before anything is edited (checks.py): not posted before, the right length, one
   song Shazam knows, by the singer its post names, vocals that separate cleanly. One that
   fails is skipped, with why; one that passes is edited with the preset as an ordinary
   job, behind people's own (jobs.automatic_jobs).
4. The reel is checked again (checks.py): the edit's own doubts, its caption, its panels
   and labels, where the sound changes, instruments left in the vocals, loudness. Any doubt
   leaves it for a person ("look", the doubts on the reel too): it is never posted.
5. At each post time (local, GRACE late at most) the oldest ready reel is posted through
   the app's own Instagram connection (instagram.post_reel), at most posts_per_day a day,
   with its post caption. The first `needs` reels wait for a person's approval; after that
   it posts alone. In dry run everything happens but the post itself. A post that fails
   pauses the autopilot and says why on its page.
6. A day and two days after a post its likes, comments and insights are read; the next
   learn takes them in with the rest of the account's posts.

The scan (tick, every few minutes) only looks and queues; the work runs as queue tasks
(jobs.channel_*), one autopilot's at a time, giving way to people's own jobs.
"""

import logging
import threading
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from agentic_editor import (
    accounts,
    buffer_posts,
    download,
    features,
    ffmpeg,
    fun,
    instagram,
    research,
    slots,
)
from agentic_editor.analysis import separation
from agentic_editor.analyze import file_hash, isolate_vocals, transcript_file
from agentic_editor.channel import checks
from agentic_editor.db import (
    AccountSnapshot,
    ChannelPilot,
    ChannelPost,
    InstagramChannel,
    Job,
    Output,
    Research,
    ResearchAccount,
    User,
)
from agentic_editor.edl import EDL
from agentic_editor.llm import LLMError, parse
from agentic_editor.presets import Preset, load_presets
from agentic_editor.render import split as split_render
from agentic_editor.settings import Settings
from agentic_editor.transcript import Transcript, Word

log = logging.getLogger(__name__)

FEATURE = "channel_autopilot"
# The owner's channel data (2026-11-24): five a day did best, at these hours (IST); 00:00-
# 01:59 did worst, and seven or more a day worse than five.
DEFAULT_TIMES = ("02:30", "04:00", "06:00", "18:30", "21:30")
DEFAULT_PER_DAY = 5
MOST_PER_DAY = 6
MIN_FIT = 0.8  # the research's fit (relevance) a video needs to be picked
BUFFER_DAYS = 2  # days of posts kept checked and edited ahead
TRIES = 3  # videos checked for each one wanted
MOST_PICKED = 20  # videos one research's picking checks, at most
PER_ARTIST = 2  # of one singer or band among them: the page mixes its artists
GRACE = timedelta(hours=1)  # a post time missed by more is left (the next one comes)
INSIGHTS_AFTER = (timedelta(hours=24), timedelta(hours=48))  # a post's numbers read then
INSIGHT_POSTS = 30  # newest posts whose insights a learn reads, when Meta gives them
READS = 20  # posts whose numbers one read looks at
BEST = 12  # best liked posts the research is told of
SNAPSHOTS = 30  # learned snapshots kept per research account (as jobs.SNAPSHOTS)
CAPTION_KEPT = 600  # characters of each post's caption kept
WORK_RETRY = 60  # seconds the work waits while people's jobs need the workers
WORK_AGING = timedelta(minutes=30)  # then it goes on anyway
# A video's statuses: waiting to be checked, edited or posted; passed its first checks.
WAITING = ("queued", "checking", "editing", "reviewing", "awaiting", "ready", "due", "posting")
PASSED = ("editing", "reviewing", "awaiting", "ready", "due", "posting")
HOLDS_SONG = (*WAITING, "look", "posted", "failed")  # its song is never found again
PERMISSION = {10, 200}  # Graph API: the login lacks the permission
BEFORE_EDIT = {"posted before", "original", "length", "download", "one song", "named", "vocals",
               "checks"}  # fmt: skip


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def local(at: datetime) -> datetime:
    """A stored (naive UTC) time in the server's local time, which post times are in."""
    return at.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)


def times_of(pilot: ChannelPilot) -> list[time]:
    """Its post times, earliest first, as many as it posts a day."""
    found = []
    for text in pilot.post_times or []:
        try:
            hour, minute = (int(x) for x in text.split(":"))
            found.append(time(hour, minute))
        except ValueError:
            continue
    return sorted(set(found))[: max(0, pilot.posts_per_day)]


def channel_of(row: InstagramChannel) -> instagram.Channel:
    return instagram.Channel(id=row.id, username=row.username, access_token=row.access_token)


def preset_of(db: Session, pilot: ChannelPilot, settings: Settings) -> Preset | None:
    owner = db.get(User, pilot.owner_id) if pilot.owner_id else None
    return load_presets(accounts.presets_dir(owner, settings.presets_dir)).get(pilot.preset)


def song_of(post: ChannelPost) -> tuple[str, str]:
    """The song as Shazam heard it, else as the research named it."""
    if post.heard_song:
        return post.heard_song, post.heard_artist
    return post.song, post.artist


def name_of(post: ChannelPost) -> str:
    song, artist = song_of(post)
    return f"“{song}” by {artist}" if song and artist else f"“{song or post.code}”"


def title_of(item) -> str:
    """A research item's song title alone (it is stored "Title - Artist")."""
    tail = f" - {item.artist}"
    return item.song.removesuffix(tail) if item.artist and item.song.endswith(tail) else item.song


# ---- what the account posted ---------------------------------------------------------


def held_songs(db: Session, pilot: ChannelPilot, but: int | None = None) -> list[tuple[str, str]]:
    """The songs its videos hold: waiting, posted (in dry run only while it is still in dry
    run), failed to post (it may have gone out) or left for a person."""
    rows = db.scalars(
        select(ChannelPost).where(
            ChannelPost.pilot_id == pilot.id, ChannelPost.status.in_(HOLDS_SONG)
        )
    )
    pairs = []
    for post in rows:
        if post.id == but or (post.status == "posted" and post.dry_run and not pilot.dry_run):
            continue
        song, artist = song_of(post)
        if song:
            pairs.append((song, artist))
    return pairs


def known(db: Session, pilot: ChannelPilot, but: int | None = None) -> research.Posted:
    """Every song the account posted (its posts' captions) or has waiting."""
    captions = [h.get("caption", "") for h in pilot.history or []]
    return research.Posted(held_songs(db, pilot, but), captions)


def reactions(entry: dict) -> int:
    return (entry.get("likes") or 0) + 2 * (entry.get("comments") or 0)


def best_lines(pilot: ChannelPilot, count: int = BEST) -> list[str]:
    """Its best liked posts, a line each, as the research is told them."""
    best = sorted(pilot.history or [], key=reactions, reverse=True)[:count]
    lines = []
    for entry in best:
        first = " ".join((entry.get("caption") or "").strip().split("\n", 1)[0].split())[:110]
        numbers = [f"{entry.get('likes') or 0} likes"]
        for key in ("views", "reach", "saved"):
            if entry.get(key) is not None:
                numbers.append(f"{entry[key]} {key}")
        lines.append(f"{first or entry.get('link', '')} ({', '.join(numbers)})")
    return lines


def context_for(db: Session, account_id: str | None) -> dict:
    """What a research for `account_id` takes from the channel autopilot it serves (see
    research.Context): the best liked posts, the songs waiting or posted, the captions."""
    if not account_id:
        return {}
    pilot = db.scalar(select(ChannelPilot).where(ChannelPilot.research_account_id == account_id))
    if pilot is None:
        return {}
    return {
        "best": best_lines(pilot),
        "avoid": held_songs(db, pilot),
        "posted": [h.get("caption", "") for h in pilot.history or []],
    }


# ---- the scan ------------------------------------------------------------------------


_queued_work: set[str] = set()
_queued_lock = threading.Lock()
_running = threading.Lock()  # one autopilot's work at a time
_deferred: dict[str, datetime] = {}  # when its work first gave way to people's jobs


def default_queue() -> SimpleNamespace:
    from agentic_editor import jobs

    return SimpleNamespace(
        work=jobs.channel_work, post=jobs.channel_post, read=jobs.channel_read,
        learn=jobs.channel_learn, find=jobs.find_reels,
    )  # fmt: skip


def wanted(db: Session, pilot: ChannelPilot) -> int:
    """Videos still needed to keep BUFFER_DAYS of posts checked and edited ahead."""
    have = db.scalar(
        select(func.count())
        .select_from(ChannelPost)
        .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status.in_(PASSED))
    )
    return pilot.posts_per_day * BUFFER_DAYS - have


def research_due(pilot: ChannelPilot, now: datetime) -> bool:
    return pilot.active and now.hour >= pilot.research_hour and pilot.research_on != now.date()


def start_research(
    db: Session, pilot: ChannelPilot, now: datetime, fresh: bool = False
) -> str | None:
    """Its research for today: the research account's morning one when that started today
    already (unless `fresh`), else a new one. The id of a new one (queued once the account
    is learned)."""
    today = now.date()
    pilot.research_on = today
    account = db.get(ResearchAccount, pilot.research_account_id or "")
    if account is None:
        pilot.note = "no research account is linked: nothing to find videos with"
        return None
    if wanted(db, pilot) <= 0:
        pilot.note = "enough reels are waiting: no research needed today"
        return None
    if account.daily_on == today and not fresh:
        found = db.scalar(
            select(Research)
            .where(Research.account_id == account.id, Research.daily)
            .order_by(Research.created_at.desc())
        )
        if found is not None and local(found.created_at).date() == today:
            pilot.research_id = found.id
            pilot.note = "using the research account's morning research"
            return None
    found = Research(
        description=pilot.focus.strip(), owner_id=pilot.owner_id, account_id=account.id,
        handle=account.handle, sites=list(research.DAILY_SITES), daily=True,
        status="queued", message="reading the account's posts first",
    )  # fmt: skip
    db.add(found)
    db.flush()
    account.daily_on = today
    pilot.research_id = found.id
    pilot.note = "today's research is starting"
    return found.id


def pick(db: Session, pilot: ChannelPilot, found: Research) -> int:
    """Pick the research's best videos to check (status "queued"), best score first, at most
    PER_ARTIST of one singer: those under MIN_FIT and songs posted or waiting are noted as
    skipped. How many were picked."""
    pilot.picked_id = found.id
    want = wanted(db, pilot)
    if want <= 0:
        pilot.note = "enough reels are waiting: nothing picked"
        return 0
    room = min(want * TRIES, MOST_PICKED)
    posted = known(db, pilot)
    codes = set(db.scalars(select(ChannelPost.code).where(ChannelPost.pilot_id == pilot.id)))
    picked = Counter()
    artists = Counter()
    for item in sorted(found.items, key=lambda i: (-i.score, i.rank)):
        if item.code in codes:
            continue
        song = title_of(item)
        row = ChannelPost(
            pilot_id=pilot.id, owner_id=pilot.owner_id, research_id=found.id, code=item.code,
            url=item.url, title=(item.caption or "")[:500], song=song, artist=item.artist,
            score=item.score, relevance=item.relevance, duration=item.duration,
        )  # fmt: skip
        fit = item.relevance or 0.0
        if fit < MIN_FIT - 1e-9:
            row.status = "skipped"
            row.reason = f"fits {fit * 10:.0f}/10; needs {MIN_FIT * 10:.0f}"
            picked["below fit"] += 1
        elif posted.has(song, item.artist):
            row.status, row.reason = "skipped", f"“{song}” was posted or is waiting already"
            picked["posted before"] += 1
        elif picked["picked"] >= room:
            continue  # not needed: never looked at
        elif (who := research.plain(item.artist)) and artists[who] >= PER_ARTIST:
            continue  # enough of this singer today; another day's research may bring it
        else:
            row.status = "queued"
            posted.add(song, item.artist)
            picked["picked"] += 1
            artists[research.plain(item.artist)] += 1
        db.add(row)
        codes.add(item.code)
    left = ", ".join(f"{n} {why}" for why, n in picked.items() if why != "picked" and n)
    pilot.note = f"picked {picked['picked']} of the {len(found.items)} videos found to check" + (
        f" ({left})" if left else ""
    )
    return picked["picked"]


def follow_edits(db: Session, pilot: ChannelPilot) -> bool:
    """Edits that ended: a reel made (and its caption written) goes to its checks
    ("reviewing"); an edit that failed or was deleted is a skip. Whether any is ready."""
    ready = False
    for post in db.scalars(
        select(ChannelPost).where(ChannelPost.pilot_id == pilot.id, ChannelPost.status == "editing")
    ):
        job = db.get(Job, post.job_id) if post.job_id else None
        if job is None:
            post.status, post.reason = "skipped", "its edit was deleted"
        elif job.status == "failed":
            post.status, post.reason = "skipped", f"the edit failed: {job.error or '?'}"
        elif job.status == "done":
            outputs = [o for o in job.outputs if o.preset == pilot.preset] or list(job.outputs)
            if not outputs:
                post.status, post.reason = "skipped", "the edit made no reel"
            elif outputs[0].caption_status in ("queued", "writing"):
                continue  # its caption first
            else:
                post.output_id, post.status = outputs[0].id, "reviewing"
                ready = True
    return ready


def post_due(db: Session, pilot: ChannelPilot, now: datetime) -> ChannelPost | None:
    """The reel to post now, when one of its post times came (GRACE late at most) and
    nothing went out since, and fewer than posts_per_day went out today. `now`: local."""
    attempts = [
        local(at)
        for at in db.scalars(
            select(ChannelPost.posted_at).where(
                ChannelPost.pilot_id == pilot.id,
                ChannelPost.status.in_(("due", "posting", "posted", "failed")),
                ChannelPost.posted_at.is_not(None),
            )
        )
    ]
    if sum(a.date() == now.date() for a in attempts) >= pilot.posts_per_day:
        return None
    slot = None
    for at in reversed(times_of(pilot)):
        start = datetime.combine(now.date(), at)
        if start <= now < start + GRACE:
            slot = start
            break
        if start <= now:
            break  # the latest post time is past its grace
    if slot is None or any(a >= slot for a in attempts):
        return None
    return db.scalar(
        select(ChannelPost)
        .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status == "ready")
        .order_by(ChannelPost.approved.desc(), ChannelPost.id)
    )


def insights_due(db: Session, pilot: ChannelPilot, now: datetime) -> bool:
    """Whether a real post's numbers are due (a day after it went out, then two)."""
    for post in db.scalars(
        select(ChannelPost).where(
            ChannelPost.pilot_id == pilot.id,
            ChannelPost.status == "posted",
            ChannelPost.dry_run.is_(False),
            ChannelPost.posted_at.is_not(None),
        )
    ):
        if read_due(post, now):
            return True
    return False


def read_due(post: ChannelPost, now: datetime) -> bool:
    """`now`: UTC, naive."""
    if not post.post_id or post.posted_at is None:
        return False
    for after in INSIGHTS_AFTER:
        if now >= post.posted_at + after and (
            post.insights_at is None or post.insights_at < post.posted_at + after
        ):
            return True
    return False


def tick(Session: sessionmaker, now: datetime | None = None, queue=None) -> list[str]:
    """Look at every channel autopilot (the server's scan): start a research when it's due,
    pick from a finished one, send ended edits to their checks, queue the work, post when
    a post time came and read posts' numbers. `now`: local time. What it did."""
    queue = queue or default_queue()
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    utc_now = now.astimezone(UTC).replace(tzinfo=None)
    did: list[str] = []
    calls: list[tuple] = []
    with Session() as db:
        for pilot in db.scalars(select(ChannelPilot)).all():
            owner = db.get(User, pilot.owner_id) if pilot.owner_id else None
            channel = db.get(InstagramChannel, pilot.channel_id)
            if owner is None or channel is None or not features.enabled(owner, FEATURE, db):
                continue
            if pilot.approved >= pilot.needs:  # posting alone now: those waiting may go
                db.execute(
                    update(ChannelPost)
                    .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status == "awaiting")
                    .values(status="ready")
                )
            follow_edits(db, pilot)
            if (
                pilot.active
                and research_due(pilot, now)
                and (research_id := start_research(db, pilot, now))
            ):
                calls.append((queue.learn, pilot.id, research_id))
                did.append(f"research {research_id} for @{channel.username}")
            found = db.get(Research, pilot.research_id) if pilot.research_id else None
            if pilot.active and found is not None and pilot.picked_id != found.id:
                if found.status == "done":
                    picked = pick(db, pilot, found)
                    did.append(f"picked {picked} for @{channel.username}")
                elif found.status == "failed":
                    pilot.picked_id = found.id
                    pilot.note = f"today's research failed: {found.error or '?'}"
            if pilot.active and (due := post_due(db, pilot, now)) is not None:
                due.status, due.posted_at = "due", utc_now
                calls.append((queue.post, due.id))
                did.append(f"posting {due.id} on @{channel.username}")
            waiting = Counter(
                db.scalars(
                    select(ChannelPost.status).where(
                        ChannelPost.pilot_id == pilot.id,
                        ChannelPost.status.in_(("queued", "reviewing")),
                    )
                )
            )
            # A paused autopilot still checks the reels already made; it picks nothing new.
            if waiting["reviewing"] or (pilot.active and waiting["queued"]):
                with _queued_lock:
                    if pilot.id not in _queued_work and not _running.locked():
                        _queued_work.add(pilot.id)
                        calls.append((queue.work, pilot.id))
            if insights_due(db, pilot, utc_now):
                calls.append((queue.read, pilot.id))
        db.commit()
    for line in did:
        log.info("channel autopilot: %s", line)
    for call, *args in calls:
        call(*args)
    return did


# ---- the work: checks before and after the edit ----------------------------------------


def next_unit(db: Session, pilot_id: str) -> tuple[str, int] | None:
    """The next piece of work: a reel to check ("review"), else a picked video to check
    ("check", marked "checking"); None when there is none (picked ones no longer needed
    are marked unused)."""
    pilot = db.get(ChannelPilot, pilot_id)
    if pilot is None:
        return None
    review = db.scalar(
        select(ChannelPost.id)
        .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status == "reviewing")
        .order_by(ChannelPost.id)
    )
    if review is not None:
        return "review", review
    if not pilot.active:
        return None  # paused: picked videos wait
    queued = db.scalars(
        select(ChannelPost)
        .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status == "queued")
        .order_by(ChannelPost.id)
    ).all()
    if not queued:
        return None
    if wanted(db, pilot) <= 0:
        for post in queued:
            post.status, post.reason = "unused", "not needed: enough others passed"
        return None
    post = queued[0]
    post.status = "checking"
    return "check", post.id


def work(
    pilot_id: str,
    Session: sessionmaker,
    settings: Settings,
    router=None,
    enqueue=None,
    fetch=None,
    ask=None,
    look=None,
    workers: int | None = None,
) -> str:
    """Check picked videos and finished reels, one at a time, until none is left: "done";
    "later" when people's own jobs need the workers (the task comes back WORK_RETRY later);
    "busy" while another autopilot's work runs (the scan queues this again)."""
    from agentic_editor import jobs

    if not _running.acquire(blocking=False):
        with _queued_lock:
            _queued_work.discard(pilot_id)
        return "busy"
    later = False  # it comes back by itself: the scan needn't queue it again
    try:
        router = router or _router(settings)
        enqueue = enqueue or jobs.process
        workers = workers or settings.max_concurrent_jobs
        while True:
            with Session() as db:
                first = _deferred.get(pilot_id)
                if jobs.people_waiting(db, workers) and (
                    first is None or _utcnow() - first < WORK_AGING
                ):
                    _deferred.setdefault(pilot_id, _utcnow())
                    later = True
                    return "later"
                _deferred.pop(pilot_id, None)
                unit = next_unit(db, pilot_id)
                db.commit()
            if unit is None:
                return "done"
            kind, post_id = unit
            with slots.behind():
                if kind == "check":
                    check_video(post_id, Session, settings, router, enqueue, fetch, ask, look)
                else:
                    review(post_id, Session, settings, router)
    finally:
        _running.release()
        if not later:
            with _queued_lock:
                _queued_work.discard(pilot_id)


def _router(settings: Settings):
    from agentic_editor.backends import get_backends
    from agentic_editor.router import ModelRouter

    return ModelRouter(settings, get_backends(), lambda *args: None)


def _attempt(name: str, run) -> checks.Check:
    try:
        return run()
    except Exception as exc:
        log.warning("channel check %s couldn't be made: %s", name, exc)
        return checks.Check(name, False, f"couldn't check: {type(exc).__name__}: {exc}")


def check_video(
    post_id: int,
    Session: sessionmaker,
    settings: Settings,
    router,
    enqueue,
    fetch=None,
    ask=None,
    look=None,
) -> str:
    """A picked video's checks before its edit (checks.py), in order, stopping at the first
    that fails; one that passes them all is edited with the preset. Its new status."""
    fetch = fetch or download.fetch
    with Session() as db:
        post = db.get(ChannelPost, post_id)
        pilot = db.get(ChannelPilot, post.pilot_id)
        preset = preset_of(db, pilot, settings)
        posted = known(db, pilot, but=post.id)
        url, song, artist, duration = post.url, post.song, post.artist, post.duration
        title = post.title.strip().split("\n", 1)[0]
    rows: list[checks.Check] = []

    def end(status: str, reason: str = "", **fields) -> str:
        log.info("channel video %s %s: %s", post_id, status, reason)
        with Session() as db:
            found = db.get(ChannelPost, post_id)
            found.status, found.reason = status, reason
            found.checks = [c.row() for c in rows]
            for key, value in fields.items():
                setattr(found, key, value)
            db.commit()
        return status

    def passed(check: checks.Check) -> bool:
        rows.append(check)
        return check.ok

    if preset is None:
        return end("skipped", f"the preset {pilot.preset!r} isn't there")
    try:
        if not passed(checks.not_posted(song, artist, posted, "the research")):
            return end("skipped", rows[-1].detail)
        if title and not passed(checks.original(title, by="what the research read")):
            return end("skipped", rows[-1].detail)
        if duration is not None and not passed(checks.length(duration, preset)):
            return end("skipped", rows[-1].detail)
        try:
            source = fetch(url, settings.media_dir / "sources")
        except Exception as exc:
            rows.append(checks.Check("download", False, str(exc)[:300]))
            return end("skipped", f"couldn't download it: {str(exc)[:200]}")
        if not passed(checks.original(source.title, source.description, "the video's title")):
            return end("skipped", rows[-1].detail)
        info = ffmpeg.probe(source.path)
        known_length = duration is not None and abs(info.duration - duration) <= 1
        if not known_length and not passed(checks.length(info.duration, preset)):
            return end("skipped", rows[-1].detail)
        work_dir = settings.cache_dir / file_hash(source.path)
        work_dir.mkdir(parents=True, exist_ok=True)
        heard = checks.hear(source.path, work_dir, info.duration, ask, look)
        check, found = checks.one_song(heard)
        if not passed(check):
            return end("skipped", check.detail, work_dir=str(work_dir))
        heard_song, heard_artist = found
        if not passed(checks.named(found, source.title, source.description, source.uploader)):
            return end("skipped", rows[-1].detail, heard_song=heard_song,
                       heard_artist=heard_artist, work_dir=str(work_dir))  # fmt: skip
        if not passed(checks.not_posted(heard_song, heard_artist, posted, "Shazam")):
            return end("skipped", rows[-1].detail, heard_song=heard_song,
                       heard_artist=heard_artist, work_dir=str(work_dir))  # fmt: skip
        original = work_dir / "audio.wav"
        if not original.exists():
            ffmpeg.extract_audio(source.path, original)
        try:
            isolated, free = isolate_vocals(original, work_dir, router, lambda *a: None)
            failed = ""
        except separation.SeparationFailed as exc:
            isolated, free, failed = None, False, str(exc)
        if not passed(checks.vocals(isolated, free, failed)):
            return end("skipped", rows[-1].detail, heard_song=heard_song,
                       heard_artist=heard_artist, work_dir=str(work_dir))  # fmt: skip
    except Exception as exc:
        log.exception("checking channel video %s failed", post_id)
        rows.append(checks.Check("checks", False, f"{type(exc).__name__}: {exc}"[:300]))
        return end("skipped", f"couldn't be checked: {type(exc).__name__}: {exc}"[:300])
    with Session() as db:
        found_post = db.get(ChannelPost, post_id)
        pilot = db.get(ChannelPilot, found_post.pilot_id)
        job = Job(
            source=url, title=f"{heard_song} - {heard_artist}", presets=[pilot.preset],
            notes=f"song: {heard_song}\nartist: {heard_artist}", owner_id=pilot.owner_id,
            trim=True,
        )  # fmt: skip
        db.add(job)
        db.flush()
        found_post.job_id, found_post.status, found_post.reason = job.id, "editing", ""
        found_post.heard_song, found_post.heard_artist = heard_song, heard_artist
        found_post.duration, found_post.work_dir = info.duration, str(work_dir)
        found_post.checks = [c.row() for c in rows]
        db.commit()
        job_id = job.id
    log.info("channel video %s passed its checks: editing as job %s", post_id, job_id)
    enqueue(job_id)
    return "editing"


def words_of(transcript: Transcript, edl: EDL) -> list[Word]:
    """The reel's words in its own time (as render.compose.timeline_words)."""
    clip = edl.clips[0]
    return [
        w.model_copy(
            update={
                "start": max(w.start, clip.start) - clip.start,
                "end": min(w.end, clip.end) - clip.start,
            }
        )
        for w in transcript.between(clip.start, clip.end)
    ]


def review(post_id: int, Session: sessionmaker, settings: Settings, router) -> str:
    """The reel's checks after its edit (checks.py). All passed: it waits for a person's
    approval, or its post time once enough were approved. Any failed: "look", with the
    doubts on the reel itself too. Its new status."""
    with Session() as db:
        post = db.get(ChannelPost, post_id)
        pilot = db.get(ChannelPilot, post.pilot_id)
        output = db.get(Output, post.output_id) if post.output_id else None
        preset = preset_of(db, pilot, settings)
        if output is None or preset is None or not Path(output.path).is_file():
            post.status = "skipped"
            post.reason = "its reel is gone" if preset else f"the preset {pilot.preset!r} is gone"
            db.commit()
            return post.status
        path, edl = Path(output.path), EDL.model_validate(output.edl)
        score, doubts, issues = output.confidence, list(output.doubts), list(output.issues)
        text, status, notes = output.caption, output.caption_status, list(output.caption_notes)
        work_dir = Path(post.work_dir)
    rows = [
        checks.confidence(score, doubts, issues),
        checks.caption(text, status, notes),
        checks.reel_length(edl.duration, preset),
    ]
    scratch = work_dir / "channel-review" / str(post_id)
    scratch.mkdir(parents=True, exist_ok=True)
    seen: dict = {}

    def picture() -> checks.Check:
        panels = checks.panels_of(checks.read_frames(path, preset.size), preset)
        seen["at"] = checks.change_seen(panels) / checks.FPS
        return checks.panels(panels, checks.change_seen(panels))

    rows.append(_attempt("panels", picture))
    at_seen = seen.get("at")
    rows.append(_attempt("labels", lambda: checks.labels(
        checks.grab(path, max(0.5, (at_seen or 1.0) / 2), preset.size), preset,
        settings.fonts_dir,
    )))  # fmt: skip

    def sound() -> list[checks.Check]:
        language = preset.audio.language
        heard = Transcript.model_validate_json(
            transcript_file(work_dir, "vocals", router, language).read_text(encoding="utf-8")
        )
        words = words_of(heard, edl)
        clip, rate = edl.clips[0], 16000
        # As the render chose it (render.compose.split_soundtrack).
        quiet = split_render.voice_pauses(work_dir / "vocals.wav", clip.start, clip.end)
        at = split_render.switch_time(edl.duration, preset.split, words, quiet)
        voice = ffmpeg.read_audio(work_dir / "vocals.wav", rate)
        voice = voice[int(clip.start * rate) : int(clip.end * rate)]
        found = [
            checks.switch(at_seen if at_seen is not None else -1.0, at, words, voice, rate),
            checks.voice(voice[int(at * rate) :], voice, rate),
        ]
        rest = edl.duration - at - 0.2 - preset.audio.fade_out - 0.1
        part = checks.cut(path, at + 0.2, rest, scratch / "vocals-part.wav")
        first = checks.cut(path, 0.0, max(0.5, at - 0.05), scratch / "whole-part.wav")
        heard = 22050
        found.append(_attempt("leak", lambda: checks.leak(
            ffmpeg.read_audio(part, heard), heard, ffmpeg.read_audio(first, heard)
        )))  # fmt: skip
        second = checks.cut(path, at + 0.05, edl.duration - at - 0.05, scratch / "second.wav")
        found.append(_attempt("loudness", lambda: checks.loudness(path, first, second, preset)))
        return found

    try:
        rows += sound()
    except Exception as exc:
        log.exception("channel reel %s: the sound couldn't be checked", post_id)
        rows.append(checks.Check("sound", False, f"couldn't check: {type(exc).__name__}: {exc}"))
    failed = [c for c in rows if not c.ok]
    with Session() as db:
        post = db.get(ChannelPost, post_id)
        pilot = db.get(ChannelPilot, post.pilot_id)
        # The checks before the edit, then these (once: a review made again replaces them).
        before = [c for c in post.checks if c.get("name") in BEFORE_EDIT]
        post.checks = [*before, *(c.row() for c in rows)]
        if failed:
            post.status = "look"
            post.reason = f"{failed[0].name}: {failed[0].detail}"
            output = db.get(Output, post.output_id)
            if output is not None:  # the piles say so too
                said = [f"Autopilot {c.name}: {c.detail}" for c in failed]
                output.doubts = [*output.doubts, *(s for s in said if s not in output.doubts)]
        else:
            post.status = "ready" if pilot.approved >= pilot.needs else "awaiting"
            post.reason = ""
        db.commit()
        log.info("channel reel %s %s %s", post_id, post.status, post.reason)
        return post.status


# ---- posting -------------------------------------------------------------------------


def post(
    post_id: int,
    Session: sessionmaker,
    settings: Settings,
    send=instagram.post_reel,
    stats=instagram.media_stats,
) -> str:
    """Post a reel whose time came ("due") with its post caption, through the app's own
    Instagram connection; in dry run, only mark it so. A post that fails pauses the
    autopilot with why. What came of it."""
    with Session() as db:
        claimed = db.execute(
            update(ChannelPost)
            .where(ChannelPost.id == post_id, ChannelPost.status == "due")
            .values(status="posting")
        ).rowcount
        db.commit()
        if not claimed:
            return "not due"
        row = db.get(ChannelPost, post_id)
        pilot = db.get(ChannelPilot, row.pilot_id)
        channel = db.get(InstagramChannel, pilot.channel_id)
        output = db.get(Output, row.output_id) if row.output_id else None
        name, dry = name_of(row), pilot.dry_run
        try:
            if channel is None:
                raise instagram.InstagramError("the Instagram page isn't connected any more")
            if output is None or not Path(output.path).is_file():
                raise instagram.InstagramError("the reel's video is gone")
            text = output.caption.strip()
            if not text:
                raise instagram.InstagramError("the reel has no caption")
            if dry:
                media_id = f"dry-run-{row.id}"
            else:
                base = (settings.public_url or pilot.base_url).rstrip("/")
                if not base:
                    raise instagram.InstagramError("the site's public address isn't known yet")
                pub = buffer_posts.make_pub(settings, Path(output.path))
                media_id = send(channel_of(channel), buffer_posts.pub_link(base, pub), text, True)
        except Exception as exc:
            log.warning("channel autopilot: posting %s failed: %s", post_id, exc)
            row.status, row.reason = "failed", f"posting failed: {exc}"
            pilot.active = False
            pilot.paused_reason = (
                f"Paused {local(_utcnow()):%d %b %H:%M}: posting {name} failed: {exc}. Fix it, "
                "then resume; the reel is under Failed (Try again sends it once more)."
            )
            db.commit()
            return "failed"
        now = _utcnow()
        row.status, row.post_id, row.dry_run, row.caption = "posted", media_id, dry, text
        row.posted_at, row.reason = now, ""
        if dry:
            pilot.note = f"dry run: would have posted {name} at {local(now):%H:%M}"
        else:
            output.verdict = "posted"
            fun.mark(db, output.id, posted=1)
            channel.last_post_at = now
            pilot.note = f"posted {name} at {local(now):%H:%M}"
            try:
                row.permalink = stats(channel_of(channel), media_id).get("permalink", "")
            except instagram.InstagramError as exc:
                log.info("couldn't ask for the post's link: %s", exc)
        db.commit()
        log.info("channel autopilot: %s %s -> %s", "dry-ran" if dry else "posted", name, media_id)
        return "posted"


def approve(db: Session, row: ChannelPost) -> None:
    """A person approved a reel: it posts at the next post time, and counts towards
    posting alone."""
    pilot = db.get(ChannelPilot, row.pilot_id)
    if row.status not in ("awaiting", "look"):
        raise ValueError("it isn't waiting for approval")
    row.status, row.approved, row.reason = "ready", True, ""
    pilot.approved += 1


# ---- what the account's posts did ----------------------------------------------------


def read_numbers(
    pilot_id: str, Session: sessionmaker, now: datetime | None = None, api=instagram
) -> int:
    """Read the numbers of its posts that are due (read_due): the insights Meta gives, and
    likes and comments always. How many were read."""
    now = now or _utcnow()
    read = 0
    with Session() as db:
        pilot = db.get(ChannelPilot, pilot_id)
        channel = db.get(InstagramChannel, pilot.channel_id) if pilot else None
        if channel is None:
            return 0
        chan = channel_of(channel)
        due = [
            p
            for p in db.scalars(
                select(ChannelPost).where(
                    ChannelPost.pilot_id == pilot.id,
                    ChannelPost.status == "posted",
                    ChannelPost.dry_run.is_(False),
                )
            )
            if read_due(p, now)
        ][:READS]
        for row in due:
            found: dict = {}
            if pilot.insights_allowed is not False:
                try:
                    found = api.media_insights(chan, row.post_id)
                    pilot.insights_allowed = True
                except instagram.InstagramError as exc:
                    if exc.code in PERMISSION:
                        pilot.insights_allowed = False
                    elif exc.refused:
                        break
            try:
                stats = api.media_stats(chan, row.post_id)
            except instagram.InstagramError as exc:
                if exc.refused:
                    break
                stats = {}
            if not found and not stats:
                continue
            found.setdefault("likes", stats.get("like_count"))
            found.setdefault("comments", stats.get("comments_count"))
            row.permalink = stats.get("permalink") or row.permalink
            hours = round((now - row.posted_at).total_seconds() / 3600)
            row.insights = {k: v for k, v in found.items() if v is not None} | {"hours": hours}
            row.insights_at = now
            read += 1
        db.commit()
    return read


LEARN_PROMPT = research.LEARN


def learn(
    pilot_id: str, Session: sessionmaker, planner=None, api=instagram, now: datetime | None = None
) -> str:
    """Read the account's own posts (and their insights, when Meta gives them) into the
    autopilot's history, and its research account: its own reels (left out of researches),
    its best liked, a snapshot for its performance page, and what the planning model reads
    in them. What came of it."""
    now = now or _utcnow()
    with Session() as db:
        pilot = db.get(ChannelPilot, pilot_id)
        channel = db.get(InstagramChannel, pilot.channel_id) if pilot else None
        if channel is None:
            return "the page isn't connected"
        chan = channel_of(channel)
        allowed = pilot.insights_allowed
    try:
        media = api.get_media(chan)
    except instagram.InstagramError as exc:
        note = f"couldn't read the page's posts: {exc}"
        with Session() as db:
            db.get(ChannelPilot, pilot_id).learn_note = note
            db.commit()
        return note
    history = []
    for m in media:
        link = m.get("permalink") or ""
        history.append({
            "id": str(m.get("id")), "caption": (m.get("caption") or "")[:CAPTION_KEPT],
            "likes": m.get("like_count"), "comments": m.get("comments_count"),
            "at": m.get("timestamp"), "link": link, "code": research.code_of(link) or "",
        })  # fmt: skip
    if allowed is not False and history:
        for entry in history[:INSIGHT_POSTS]:
            try:
                entry.update(api.media_insights(chan, entry["id"]))
                allowed = True
            except instagram.InstagramError as exc:
                if exc.code in PERMISSION:
                    allowed = False
                if exc.code in PERMISSION or exc.refused:
                    break
    insights = (
        "insights (views, reach, saves) read too"
        if allowed
        else "Meta gives no insights to this login: learning from likes and comments"
    )
    note = f"read {len(history)} posts; {insights}"
    summary = ""
    if planner is not None and history:
        lines = "\n".join(f"- {line}" for line in best_lines(SimpleNamespace(history=history), 15))
        try:
            with planner() as llm:
                prompt = LEARN_PROMPT.format(handle=chan.username, reels=lines)
                summary = parse(
                    research.Summary,
                    llm.chat_json([{"role": "user", "content": prompt}],
                                  research.Summary.model_json_schema()),
                ).summary.strip()  # fmt: skip
        except (LLMError, Exception) as exc:  # the posts are still worth keeping
            log.warning("couldn't read what @%s posts: %s", chan.username, exc)
    with Session() as db:
        pilot = db.get(ChannelPilot, pilot_id)
        pilot.history, pilot.learned_at, pilot.learn_note = history, now, note
        pilot.insights_allowed = allowed
        account = db.get(ResearchAccount, pilot.research_account_id or "")
        if account is not None:
            best = sorted(history, key=reactions, reverse=True)
            account.own = [h["code"] for h in history if h["code"]]
            account.best = [
                {"code": h["code"], "likes": h["likes"], "caption": h["caption"][:200]}
                for h in best[:5]
            ]
            if summary:
                account.summary = summary
            account.learned_at = datetime.now(UTC)
            account.learn_status, account.learn_message = "done", f"read {len(history)} posts"
            db.add(AccountSnapshot(account_id=account.id, reels=[
                {"code": h["code"], "likes": h["likes"], "comments": h["comments"],
                 "posted": _iso(h["at"]), "caption": h["caption"][:120]}
                for h in history if h["code"]
            ]))  # fmt: skip
            db.flush()
            older = db.scalars(
                select(AccountSnapshot.id)
                .where(AccountSnapshot.account_id == account.id)
                .order_by(AccountSnapshot.taken_at.desc(), AccountSnapshot.id.desc())
                .offset(SNAPSHOTS)
            ).all()
            if older:
                db.execute(delete(AccountSnapshot).where(AccountSnapshot.id.in_(older)))
        db.commit()
    return note


def _iso(stamp: str | None) -> str | None:
    """Instagram's "2026-11-14T08:15:00+0000" as an ISO time."""
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").isoformat()
    except ValueError:
        return None
