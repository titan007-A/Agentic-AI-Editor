"""Following the posts the editor puts in Buffer, until they went out.

Buffer posts a reel at its time by handing Instagram a link to the video; when Instagram
can't take it, the post just sits in Buffer marked failed. So every post made here is
kept (db.BufferPost) and, a few minutes after its time, Buffer is asked how it went. A
post whose video Instagram couldn't take is sent once more by itself, a few minutes later
and off the round minutes everyone posts at, unless its owner turned that off; any failed
post shows in the editor with a button to send it again.

The videos: each post's file is a hard link in media/pub (served at /api/pub/<name>), and,
when an admin set up Cloudflare R2 (hosting.py), a copy in the bucket, which Buffer gets
the link of instead. Both are deleted once nothing waits for them any more: the bucket's
copy an hour after the post went out, the file here a few days after. Files from before
posts were followed are kept two weeks, and only once Buffer's waiting posts were read
(backfill) so none of those loses its video."""

import contextlib
import logging
import os
import random
import secrets
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agentic_editor import buffer, hosting
from agentic_editor.db import BufferKey, BufferPost, HostedFile, Output, Setting
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

# Buffer still has to send these.
WAITING = ("scheduled", "sending", "needs_approval")
FOLLOWED = (*WAITING, "error", "draft", "unknown")
KNOWN = {"draft", "error", "needs_approval", "scheduled", "sending", "sent"}

# Buffer sends a post a minute or two after its time; first asked after this.
FIRST_LOOK = timedelta(minutes=3)
# How often a post not out yet is asked about, by how long past its time it is.
LOOK_EVERY = (
    (timedelta(minutes=30), timedelta(minutes=5)),
    (timedelta(hours=3), timedelta(minutes=30)),
)
LOOK_LATE = timedelta(hours=2)
GIVE_UP = timedelta(hours=48)  # still not out by then: "unknown", asked twice a day
SLOW_LOOK = timedelta(hours=12)
# A failed post is asked about now and then: its owner may send it again from Buffer.
ERROR_LOOK = timedelta(hours=3)
ERROR_LOOKS_FOR = timedelta(days=2)

AUTO_RETRY_SETTING = "buffer_autoretry:{}"
# Sent again by itself only soon after it failed; later, the person decides.
AUTO_RETRY_WITHIN = timedelta(hours=6)
RETRY_MINUTES = (4, 12)

KEEP_HOSTED = timedelta(hours=1)  # the bucket's copy, after the post went out
KEEP_PUB = timedelta(days=3)  # the file here, after the post went out
KEEP_OLD_PUB = timedelta(days=14)  # files from before posts were followed
KEEP_ERROR = timedelta(days=7)  # a failed post's video, for sending it again
KEEP_DRAFT = timedelta(days=30)
KEEP_UNKNOWN = timedelta(days=7)
HOSTED_MAX_AGE = timedelta(days=45)

# Buffer allows 600 requests a day per account: checks leave most of it to the person.
PER_KEY_ROUND = 40
DAY_BUDGET = 450

BACKFILLED = "buffer_posts:backfilled"
BACKFILL_EVERY = timedelta(hours=6)
PUBLIC_BASE = "public_base"  # the https address last used for links to /api/pub

# Meta's networks: a download of a video from one of them is Instagram fetching it.
META = ("2a03:2880:", "31.13.", "66.220.", "69.63.", "69.171.", "173.252.", "157.240.",
        "179.60.", "185.60.", "129.134.", "102.132.")  # fmt: skip

_retrying = threading.Lock()
_uploads = threading.BoundedSemaphore(2)
_pub_locks: dict[str, threading.Lock] = {}
_pub_locks_guard = threading.Lock()
_last_backfill: datetime | None = None


def aware(at: datetime | None) -> datetime | None:
    """SQLite gives times back without their zone; they are UTC."""
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def now_utc() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ keys and requests


def spent(row: BufferKey, count: buffer.Count) -> None:
    today = datetime.now().date().isoformat()
    if row.used_on != today:
        row.used_on, row.used = today, 0
    row.used += count.n


def used_today(row: BufferKey) -> int:
    return row.used if row.used_on == datetime.now().date().isoformat() else 0


def auto_retry_on(session: Session, owner_id: str) -> bool:
    found = session.get(Setting, AUTO_RETRY_SETTING.format(owner_id))
    return found is None or found.value != "0"


def set_auto_retry(session: Session, owner_id: str, on: bool) -> None:
    key = AUTO_RETRY_SETTING.format(owner_id)
    found = session.get(Setting, key)
    if found is None:
        session.add(Setting(key=key, value="1" if on else "0"))
    else:
        found.value = "1" if on else "0"


def key_for(session: Session, row: BufferPost) -> BufferKey | None:
    """The key the post was made with, or another of its owner's that shows the channel."""
    found = session.get(BufferKey, row.key_id) if row.key_id else None
    if found is not None:
        return found
    for key in session.scalars(select(BufferKey).where(BufferKey.owner_id == row.owner_id)):
        if any(c["id"] == row.channel_id for c in key.channels):
            return key
    return None


def channel_of(key: BufferKey, channel_id: str) -> buffer.Channel | None:
    found = next((c for c in key.channels if c["id"] == channel_id), None)
    return buffer.Channel(**found) if found else None


# ------------------------------------------------------------------ the video's links


def pub_dir(settings: Settings) -> Path:
    return settings.media_dir / "pub"


def make_pub(settings: Settings, source: Path) -> str:
    """A file for the video under an unguessable name: a hard link, so deleting the job
    keeps it. Its name."""
    folder = pub_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_urlsafe(24)}.mp4"
    try:
        (folder / name).hardlink_to(source)
    except OSError:
        shutil.copy2(source, folder / name)
    return name


def pub_link(base: str, pub: str) -> str:
    return f"{base.rstrip('/')}/api/pub/{pub}"


def remember_base(session: Session, base: str) -> None:
    found = session.get(Setting, PUBLIC_BASE)
    if found is None:
        session.add(Setting(key=PUBLIC_BASE, value=base))
    elif found.value != base:
        found.value = base


def base_for(session: Session, settings: Settings, row: BufferPost | None = None) -> str:
    """The https address for links to /api/pub when no request gives one."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    if row is not None and "/api/pub/" in row.link:
        return row.link.split("/api/pub/")[0]
    found = session.get(Setting, PUBLIC_BASE)
    return found.value if found else ""


def _lock(pub: str) -> threading.Lock:
    with _pub_locks_guard:
        return _pub_locks.setdefault(pub, threading.Lock())


def hosted_bytes(session: Session) -> int:
    query = select(func.coalesce(func.sum(HostedFile.size), 0)).where(
        HostedFile.deleted_at.is_(None)
    )
    return int(session.scalar(query) or 0)


def ensure_hosted(sessions: sessionmaker, settings: Settings, pub: str) -> tuple[str | None, str]:
    """The R2 key of the video's copy in the bucket, put there now if it isn't yet, and a
    note when it couldn't be (the editor's own link is used then). (None, "") when R2 isn't
    set up. Safe to call from several threads for the same file: it's uploaded once."""
    with sessions() as session:
        config = hosting.load(session)
    if not hosting.usable(config):
        return None, ""
    path = pub_dir(settings) / pub
    with _lock(pub):
        with sessions() as session:
            found = session.scalar(
                select(HostedFile).where(HostedFile.pub == pub, HostedFile.deleted_at.is_(None))
            )
            if found is not None:
                return found.key, ""
            try:
                size = path.stat().st_size
            except OSError:
                return None, "the video file is missing"
            if hosted_bytes(session) + size > hosting.CAP:
                return None, (
                    "Cloudflare R2 is nearly full, so it went out with the editor's own link"
                )
        key = f"{hosting.PREFIX}{pub}"
        try:
            with _uploads:
                hosting.upload(config, path, key)
        except hosting.HostingError as exc:
            log.warning("R2 upload of %s failed: %s", pub, exc)
            return None, f"putting it on Cloudflare R2 failed ({exc}); sent with the editor's link"
        with sessions() as session:
            row = session.get(HostedFile, key)
            if row is None:
                session.add(HostedFile(key=key, pub=pub, size=size))
            else:
                row.size, row.created_at, row.deleted_at = size, now_utc(), None
            session.commit()
        log.info("R2: put %s (%.1f MB)", key, size / 1e6)
        return key, ""


def link_of(session: Session, hosted: str | None, pub: str, base: str) -> str:
    if hosted:
        config = hosting.load(session)
        if config is not None:
            return config.link(hosted)
    if not base.startswith("https://"):
        raise buffer.BufferError(
            "Buffer needs an https link to fetch the video: open the editor at its https "
            "address, or set PUBLIC_URL in .env"
        )
    return pub_link(base, pub)


# ------------------------------------------------------------------ records and views


def record(
    session: Session,
    *,
    owner_id: str,
    key: BufferKey,
    channel: buffer.Channel,
    made: buffer.Posted,
    link: str,
    pub: str,
    hosted: str,
    text: str,
    first_comment: str,
    draft: bool,
    output_id: int | None,
    title: str,
    due_at: datetime | None = None,
    retry_of: str | None = None,
    auto: bool = False,
) -> BufferPost:
    status = made.status if made.status in KNOWN else ("draft" if draft else "scheduled")
    # Already followed (read from Buffer meanwhile): the same post, kept once.
    found = session.scalar(select(BufferPost).where(BufferPost.post_id == made.id))
    if found is not None:
        found.status, found.link, found.hosted = status, link, hosted
        found.due_at = made.due_at or due_at or found.due_at
        if output_id is not None:
            found.output_id = output_id
        return found
    row = BufferPost(
        post_id=made.id,
        owner_id=owner_id,
        key_id=key.id,
        channel_id=channel.id,
        channel=channel.name,
        service=channel.service,
        output_id=output_id,
        title=title,
        text=text,
        first_comment=first_comment,
        draft=draft,
        link=link,
        pub=pub,
        hosted=hosted,
        due_at=made.due_at or due_at,
        status=status,
        retry_of=retry_of,
        auto=auto,
    )
    session.add(row)
    return row


def instagram_cut(row: BufferPost) -> bool:
    """Instagram's last download of the video from here stopped before the end."""
    theirs = [f for f in row.fetches or [] if f.get("by") == "Instagram"]
    return bool(theirs) and not theirs[-1].get("whole")


def view(row: BufferPost) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "channel": row.channel,
        "service": row.service,
        "output_id": row.output_id,
        "title": row.title,
        "text": row.text,
        "status": row.status,
        "due_at": aware(row.due_at),
        "sent_at": aware(row.sent_at),
        "error": row.error,
        "raw_error": row.raw_error,
        "draft": row.draft,
        "hosted": bool(row.hosted),
        "auto": row.auto,
        "retry_of": row.retry_of,
        "instagram_cut": instagram_cut(row),
        "created_at": aware(row.created_at),
    }


def shown(session: Session, owner_id: str, days: int = 3) -> list[BufferPost]:
    """An account's posts for its Buffer page: every failed one still worth sending again,
    then the rest of the last `days` days, newest first; the ones sent again as another
    post aren't listed (the new one is)."""
    since = now_utc() - timedelta(days=days)
    rows = session.scalars(
        select(BufferPost)
        .where(BufferPost.owner_id == owner_id, BufferPost.status != "retried")
        .order_by(BufferPost.created_at.desc())
    )
    keep = []
    for row in rows:
        failed = row.status == "error" and holds(row, now_utc())
        if failed or aware(row.created_at) >= since:
            keep.append(row)
    keep.sort(key=lambda r: (r.status != "error", -aware(r.created_at).timestamp()))
    return keep


def failed_count(session: Session, owner_id: str) -> int:
    since = now_utc() - KEEP_ERROR
    query = select(func.count()).where(
        BufferPost.owner_id == owner_id,
        BufferPost.status == "error",
        BufferPost.created_at >= since.replace(tzinfo=None),
    )
    return int(session.scalar(query) or 0)


def for_outputs(session: Session, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    rows = session.scalars(
        select(BufferPost)
        .where(BufferPost.output_id.in_(ids), BufferPost.status != "retried")
        .order_by(BufferPost.created_at)
    )
    found: dict[int, list[dict]] = {}
    for row in rows:
        found.setdefault(row.output_id, []).append(view(row))
    return found


def fetched(sessions: sessionmaker, pub: str, entry: dict) -> None:
    """Notes a download of a /api/pub video on the posts that use it (last 10 kept)."""
    with sessions() as session:
        rows = session.scalars(select(BufferPost).where(BufferPost.pub == pub)).all()
        for row in rows:
            row.fetches = [*(row.fetches or []), entry][-10:]
        if rows:
            session.commit()


def who(ip: str) -> str:
    return "Instagram" if ip.startswith(META) else ip


# ------------------------------------------------------------------ asking Buffer


def activity(row: BufferPost) -> datetime:
    times = [row.created_at, row.due_at, row.sent_at, row.checked_at]
    return max(aware(t) for t in times if t is not None)


def holds(row: BufferPost, now: datetime) -> bool:
    """The post may still need its video."""
    if row.status in WAITING:
        return True
    if row.status == "unknown":
        return now < activity(row) + KEEP_UNKNOWN
    if row.status == "draft":
        return now < aware(row.created_at) + KEEP_DRAFT
    if row.status == "error":
        return now < activity(row) + KEEP_ERROR
    return False


def look(row: BufferPost, now: datetime) -> str:
    """ "look" (ask Buffer now), "wait", or "give_up" (stop asking often: unknown)."""
    due = aware(row.due_at) or aware(row.created_at)
    last = aware(row.checked_at)
    if row.status in WAITING:
        if now < due + FIRST_LOOK:
            return "wait"
        past = now - due
        if past > GIVE_UP:
            return "give_up"
        every = next((e for limit, e in LOOK_EVERY if past < limit), LOOK_LATE)
        fresh = last is None or last < due or now - last >= every
        return "look" if fresh else "wait"
    if row.status == "unknown":
        late = now < due + KEEP_UNKNOWN and (last is None or now - last >= SLOW_LOOK)
        return "look" if late else "wait"
    if row.status == "error":
        recent = now < (last or due) + ERROR_LOOKS_FOR and now < due + ERROR_LOOKS_FOR
        return "look" if recent and (last is None or now - last >= ERROR_LOOK) else "wait"
    if row.status == "draft":
        young = now < aware(row.created_at) + KEEP_DRAFT
        return "look" if young and (last is None or now - last >= SLOW_LOOK) else "wait"
    return "wait"


def update(row: BufferPost, found: buffer.Posted | None, now: datetime) -> bool:
    """Takes in what Buffer said; True when the post has just failed."""
    row.checked_at, row.checks = now, (row.checks or 0) + 1
    if found is None:
        row.status = "gone"
        return False
    before = row.status
    if found.status in KNOWN:
        row.status = found.status
    row.due_at = found.due_at or row.due_at
    row.sent_at = found.sent_at or row.sent_at
    if found.status == "error":
        row.error, row.raw_error = found.error, found.raw_error
    return row.status == "error" and before != "error"


def should_auto_retry(session: Session, settings: Settings, row: BufferPost, now: datetime) -> bool:
    due = aware(row.due_at) or aware(row.created_at)
    return (
        row.status == "error"
        and not row.draft
        and not row.auto
        and row.retry_of is None
        and buffer.media_problem(row.error, row.raw_error)
        and now - due < AUTO_RETRY_WITHIN
        and bool(row.pub)
        and (pub_dir(settings) / row.pub).is_file()
        and auto_retry_on(session, row.owner_id)
    )


def retry_time(now: datetime) -> datetime:
    """A few minutes from now, off the round minutes posts pile up on (:00, :15, :30, :45)."""
    at = now + timedelta(minutes=random.uniform(*RETRY_MINUTES))
    while at.minute % 15 in (14, 0, 1):
        at += timedelta(minutes=2)
    return at.replace(second=0, microsecond=0)


def retry(
    session: Session,
    sessions: sessionmaker,
    settings: Settings,
    row: BufferPost,
    due_at: datetime | None = None,
    auto: bool = False,
    now: datetime | None = None,
) -> BufferPost:
    """Sends a failed post again as a new post (now, at `due_at`, or, `auto`, in a few
    minutes) and deletes the failed one from Buffer. The new post."""
    now = now or now_utc()
    if row.status != "error":
        raise buffer.BufferError("only a failed post can be sent again")
    key = key_for(session, row)
    if key is None:
        raise buffer.BufferError("none of your Buffer keys shows that channel any more")
    channel = channel_of(key, row.channel_id)
    if channel is None:
        raise buffer.BufferError("none of your Buffer keys shows that channel any more")
    if not row.pub or not (pub_dir(settings) / row.pub).is_file():
        raise buffer.BufferError("the video isn't on the server any more")
    base = base_for(session, settings, row)
    hosted, note = ensure_hosted(sessions, settings, row.pub)
    link = link_of(session, hosted, row.pub, base)
    if auto:
        mode, due = "customScheduled", retry_time(now)
    elif due_at is not None:
        mode, due = "customScheduled", aware(due_at)
    else:
        mode, due = "shareNow", None
    data = buffer.post_input(channel, link, row.text, mode, due, row.first_comment)
    count = buffer.Count()
    try:
        made = buffer.create(key.key, data, None, count)
    finally:
        spent(key, count)
    new = record(
        session,
        owner_id=row.owner_id,
        key=key,
        channel=channel,
        made=made,
        link=link,
        pub=row.pub,
        hosted=hosted or "",
        text=row.text,
        first_comment=row.first_comment,
        draft=False,
        output_id=row.output_id,
        title=row.title,
        due_at=due or now,
        retry_of=row.id,
        auto=auto,
    )
    session.flush()
    row.status, row.retried_by = "retried", new.id
    gone = buffer.Count()
    try:
        buffer.delete_post(key.key, row.post_id, None, gone)
    except buffer.BufferError as exc:
        log.warning("buffer: the failed post %s stays in Buffer: %s", row.post_id, exc)
    finally:
        spent(key, gone)
    log.info(
        "buffer: post %s (%s) sent again as %s%s%s",
        row.post_id, row.channel, new.post_id, " by itself" if auto else "",
        f"; {note}" if note else "",
    )  # fmt: skip
    return new


def retry_by_id(
    sessions: sessionmaker,
    settings: Settings,
    row_id: str,
    owner_id: str,
    due_at: datetime | None = None,
) -> dict:
    """The person's "send again": the new post's view. Raises LookupError when the post
    isn't theirs, BufferError when Buffer refuses."""
    with _retrying, sessions() as session:
        row = session.get(BufferPost, row_id)
        if row is None or row.owner_id != owner_id:
            raise LookupError(row_id)
        new = retry(session, sessions, settings, row, due_at)
        session.commit()
        return view(new)


def dismiss(sessions: sessionmaker, row_id: str, owner_id: str) -> None:
    """Deletes a failed post from Buffer and stops showing it."""
    with sessions() as session:
        row = session.get(BufferPost, row_id)
        if row is None or row.owner_id != owner_id:
            raise LookupError(row_id)
        if row.status != "error":
            raise buffer.BufferError("only a failed post can be removed here")
        key = key_for(session, row)
        if key is not None:
            count = buffer.Count()
            try:
                buffer.delete_post(key.key, row.post_id, None, count)
            except buffer.BufferError as exc:
                if "not found" not in str(exc).lower():
                    raise
            finally:
                spent(key, count)
        row.status = "gone"
        session.commit()


def backfill(sessions: sessionmaker, settings: Settings, now: datetime | None = None) -> bool:
    """Reads, once, the posts still waiting in every Buffer account that carry a link to
    /api/pub, made before posts were followed. True once that is done for every key."""
    global _last_backfill
    now = now or now_utc()
    with sessions() as session:
        if session.get(Setting, BACKFILLED) is not None:
            return True
        if _last_backfill is not None and now - _last_backfill < BACKFILL_EVERY:
            return False
        _last_backfill = now
        known = set(session.scalars(select(BufferPost.post_id)))
        outputs = {}
        for output in session.scalars(select(Output)):
            with contextlib.suppress(OSError):
                outputs[os.stat(output.path).st_ino] = output.id
        done = True
        for key in session.scalars(select(BufferKey)):
            count = buffer.Count()
            try:
                waiting = buffer.pending(key.key, None, count)
            except buffer.BufferError as exc:
                log.warning("buffer: reading the waiting posts of %s failed: %s", key.label, exc)
                done = False
                continue
            finally:
                spent(key, count)
            for found in waiting:
                link = next((a for a in found.assets if "/api/pub/" in a), None)
                if link is None or found.id in known:
                    continue
                pub = link.rsplit("/", 1)[-1]
                channel = next((c for c in key.channels if c["id"] == found.channel_id), {})
                try:
                    inode = os.stat(pub_dir(settings) / pub).st_ino
                except OSError:
                    inode = None
                session.add(
                    BufferPost(
                        post_id=found.id,
                        owner_id=key.owner_id,
                        key_id=key.id,
                        channel_id=found.channel_id,
                        channel=channel.get("name", ""),
                        service=channel.get("service", ""),
                        output_id=outputs.get(inode),
                        title=found.text.split("\n")[0][:80],
                        text=found.text,
                        draft=found.status == "draft",
                        link=link,
                        pub=pub,
                        due_at=found.due_at,
                        status=found.status if found.status in KNOWN else "scheduled",
                        error=found.error,
                        raw_error=found.raw_error,
                    )
                )
                known.add(found.id)
        if done:
            session.add(Setting(key=BACKFILLED, value=now.isoformat()))
        session.commit()
        log.info("buffer: read the posts waiting in Buffer (%s)", "all keys" if done else "not all")
        return done


def watch(sessions: sessionmaker, settings: Settings, now: datetime | None = None) -> dict:
    """One round: asks Buffer about the posts due a look, sends again the ones whose video
    just failed, and deletes videos nothing waits for. What it did, by kind."""
    now = now or now_utc()
    done = {"asked": 0, "failed": 0, "retried": 0}
    backfill(sessions, settings, now)
    failed: list[str] = []
    with sessions() as session:
        rows = session.scalars(select(BufferPost).where(BufferPost.status.in_(FOLLOWED))).all()
        asked: dict[str, int] = {}
        for row in rows:
            decision = look(row, now)
            if decision == "give_up":
                row.status = "unknown"
                continue
            if decision != "look":
                continue
            key = key_for(session, row)
            if key is None or "refused" in (key.error or ""):
                continue
            if asked.get(key.id, 0) >= PER_KEY_ROUND or used_today(key) >= DAY_BUDGET:
                continue
            count = buffer.Count()
            try:
                found = buffer.post(key.key, row.post_id, None, count)
            except buffer.BufferError as exc:
                log.warning("buffer: asking about post %s failed: %s", row.post_id, exc)
                if "refused the API key" in str(exc):
                    key.error = str(exc)
                continue
            finally:
                spent(key, count)
                asked[key.id] = asked.get(key.id, 0) + count.n
            done["asked"] += 1
            if update(row, found, now):
                done["failed"] += 1
                failed.append(row.id)
                why = row.raw_error or row.error
                log.info("buffer: post %s to %s failed: %s", row.post_id, row.channel, why)
        session.commit()
        for row_id in failed:
            row = session.get(BufferPost, row_id)
            if row is None or not should_auto_retry(session, settings, row, now):
                continue
            with _retrying:
                session.refresh(row)
                if row.status != "error":
                    continue
                try:
                    retry(session, sessions, settings, row, auto=True, now=now)
                    done["retried"] += 1
                except (buffer.BufferError, hosting.HostingError) as exc:
                    row.error = f"{row.error} (sending it again by itself failed: {exc})"
                    log.warning("buffer: sending post %s again failed: %s", row.post_id, exc)
                session.commit()
    done |= sweep(sessions, settings, now)
    return done


# ------------------------------------------------------------------ cleaning up


def sweep(sessions: sessionmaker, settings: Settings, now: datetime | None = None) -> dict:
    """Deletes the bucket's copies and the files here that no post waits for any more."""
    now = now or now_utc()
    freed = {"hosted_deleted": 0, "pub_deleted": 0}
    with sessions() as session:
        rows = session.scalars(select(BufferPost)).all()
        by_hosted: dict[str, list[BufferPost]] = {}
        by_pub: dict[str, list[BufferPost]] = {}
        for row in rows:
            if row.hosted:
                by_hosted.setdefault(row.hosted, []).append(row)
            if row.pub:
                by_pub.setdefault(row.pub, []).append(row)
        config = hosting.load(session)
        if config is not None:
            hosted = session.scalars(select(HostedFile).where(HostedFile.deleted_at.is_(None)))
            for file in hosted.all():
                refs = by_hosted.get(file.key, [])
                newest = max([aware(file.created_at), *(activity(r) for r in refs)])
                idle = not any(holds(r, now) for r in refs) and now - newest > KEEP_HOSTED
                if idle or now - aware(file.created_at) > HOSTED_MAX_AGE:
                    try:
                        hosting.delete(config, file.key)
                    except hosting.HostingError as exc:
                        log.warning("R2: deleting %s failed: %s", file.key, exc)
                        continue
                    file.deleted_at = now
                    freed["hosted_deleted"] += 1
            session.commit()
        backfilled = session.get(Setting, BACKFILLED) is not None
    folder = pub_dir(settings)
    if not folder.is_dir():
        return freed
    for path in folder.glob("*.mp4"):
        refs = by_pub.get(path.name)
        try:
            changed = datetime.fromtimestamp(path.stat().st_ctime, UTC)
        except OSError:
            continue
        if refs:
            newest = max(activity(r) for r in refs)
            old = not any(holds(r, now) for r in refs) and now - newest > KEEP_PUB
        else:
            old = backfilled and now - changed > KEEP_OLD_PUB
        if old:
            path.unlink(missing_ok=True)
            freed["pub_deleted"] += 1
    if freed["pub_deleted"] or freed["hosted_deleted"]:
        log.info(
            "buffer: deleted %d video(s) here and %d on R2 that no post waits for",
            freed["pub_deleted"], freed["hosted_deleted"],
        )  # fmt: skip
    return freed
