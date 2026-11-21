"""The animation autopilot: an account's daily reels, made and posted by themselves.

Each day at its hour (and a few hours apart when it makes more than one), an autopilot
starts a reel: the next topic from its list, else a new one its niche suggests (chosen in
the draft, studio._choose_topic). The draft's facts are checked, the reel renders, and
then it waits for a person's approval, or posts by itself once `needs` reels were
approved and auto_post is on (never with a doubtful fact, never without a page).
"""

import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agentic_editor import buffer_posts, features, instagram
from agentic_editor.animate import studio
from agentic_editor.db import Animation, AnimationAutopilot, InstagramChannel, Job, Output, User
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

GAP_HOURS = 3  # between an autopilot's reels on one day
FEATURE = "animation_studio"


def due(pilot: AnimationAutopilot, now: datetime) -> bool:
    """Whether the autopilot's next reel of the day is due at `now` (local time)."""
    if not pilot.active or pilot.per_day < 1:
        return False
    today = now.date()
    made = pilot.made_on_last if pilot.last_on == today else 0
    return made < pilot.per_day and now.hour >= pilot.hour + GAP_HOURS * made


def new_animation(db, pilot: AnimationAutopilot, today: date) -> Animation:
    """The autopilot's next reel, queued for its draft: the first topic waiting, or none
    (the draft picks one in the niche)."""
    topics = list(pilot.topics or [])
    idea = topics.pop(0).strip() if topics else ""
    if idea:
        pilot.topics = topics
        pilot.used = [*(pilot.used or []), idea][-200:]
    pilot.made_on_last = (pilot.made_on_last if pilot.last_on == today else 0) + 1
    pilot.last_on = today
    found = Animation(
        owner_id=pilot.owner_id, idea=idea, kind=pilot.kind, style=pilot.style,
        voice=pilot.voice, length=pilot.length, images=pilot.images, autopilot_id=pilot.id,
        task={"op": "auto"},
        status="queued", message="waiting for a free slot", doubts=[],
    )  # fmt: skip
    db.add(found)
    db.flush()
    return found


def start_due(Session: sessionmaker, now: datetime | None = None, queue=None) -> list[str]:
    """Start every reel that is due, for accounts that still have the studio. Returns
    the animations started."""
    from agentic_editor import jobs

    queue = queue or jobs.animate
    now = now or datetime.now()
    started: list[str] = []
    with Session() as db:
        for pilot in db.scalars(select(AnimationAutopilot).where(AnimationAutopilot.active)):
            if not due(pilot, now):
                continue
            owner = db.get(User, pilot.owner_id) if pilot.owner_id else None
            if owner is None or not features.enabled(owner, FEATURE, db):
                continue
            if not pilot.topics and not pilot.niche.strip():
                continue  # nothing to make a reel about
            started.append(new_animation(db, pilot, now.date()).id)
        db.commit()
    for animation_id in started:
        log.info("autopilot: starting animation %s", animation_id)
        queue(animation_id)
    return started


def render(animation_id: str, Session: sessionmaker, process=None) -> str:
    """An autopilot reel's render, as an ordinary job."""
    from agentic_editor import jobs

    process = process or jobs.process
    with Session() as db:
        a = db.get(Animation, animation_id)
        job = Job(
            source=studio.SOURCE + a.id, title=(a.plan or {}).get("title", ""), presets=[],
            notes=None, trim=False, owner_id=a.owner_id,
        )  # fmt: skip
        db.add(job)
        db.flush()
        a.job_id, a.post_status, a.post_error = job.id, "rendering", None
        db.commit()
        job_id = job.id
    process(job_id)
    return job_id


def may_post_alone(pilot: AnimationAutopilot, a: Animation) -> bool:
    return bool(
        pilot.auto_post and pilot.approved >= pilot.needs and not a.doubts and pilot.channel_id
    )


def post(db, a: Animation, base_url: str, settings: Settings, send=instagram.post_reel) -> str:
    """Post the reel's latest render to its autopilot's page; the post's id."""
    pilot = db.get(AnimationAutopilot, a.autopilot_id) if a.autopilot_id else None
    channel = db.get(InstagramChannel, pilot.channel_id) if pilot and pilot.channel_id else None
    if channel is None:
        raise instagram.InstagramError("no Instagram page is chosen for the autopilot")
    output = db.scalar(select(Output).where(Output.job_id == a.job_id).order_by(Output.id.desc()))
    if output is None or not Path(output.path).is_file():
        raise instagram.InstagramError("the video isn't there; render it again")
    if not base_url:
        raise instagram.InstagramError("the site's public address isn't known yet")
    pub = buffer_posts.make_pub(settings, Path(output.path))
    link = f"{base_url.rstrip('/')}/api/pub/{pub}"
    chan = instagram.Channel(
        id=channel.id, username=channel.username, access_token=channel.access_token
    )
    post_id = send(chan, link, output.caption or (a.plan or {}).get("caption", ""), True)
    channel.last_post_at = datetime.now().replace(microsecond=0)
    log.info("autopilot: posted animation %s to @%s -> %s", a.id, channel.username, post_id)
    return post_id


def after_render(
    animation_id: str,
    Session: sessionmaker,
    settings: Settings,
    ok: bool,
    error: str = "",
    send=instagram.post_reel,
) -> None:
    """An autopilot reel's render ended: it posts by itself when allowed, else waits for
    approval; a failed render is noted."""  # fmt: skip
    with Session() as db:
        a = db.get(Animation, animation_id)
        if a is None or not a.autopilot_id:
            return
        if not ok:
            a.post_status, a.post_error = "failed", error or "the render failed"
            db.commit()
            return
        pilot = db.get(AnimationAutopilot, a.autopilot_id)
        if pilot is None or not may_post_alone(pilot, a):
            a.post_status = "awaiting"
            db.commit()
            return
        a.post_status = "posting"
        db.commit()
        try:
            a.post_id = post(db, a, pilot.base_url, settings, send)
            a.post_status, a.post_error = "posted", None
        except Exception as exc:  # it waits for a person instead
            log.warning("autopilot: posting animation %s failed: %s", animation_id, exc)
            a.post_status, a.post_error = "awaiting", f"posting by itself failed: {exc}"
        db.commit()


def approve(db, a: Animation, base_url: str, settings: Settings, send=instagram.post_reel) -> None:
    """A person approved an autopilot reel: it posts now (when the autopilot has a page),
    and counts towards posting by itself."""
    pilot = db.get(AnimationAutopilot, a.autopilot_id) if a.autopilot_id else None
    if pilot is None:
        raise ValueError("this reel wasn't made by the autopilot")
    if pilot.channel_id:
        a.post_id = post(db, a, base_url or pilot.base_url, settings, send)
        a.post_status = "posted"
    else:
        a.post_status = "approved"
    a.post_error = None
    pilot.approved += 1
