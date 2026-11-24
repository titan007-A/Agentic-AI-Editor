"""The channel autopilot's HTTP routes (feature "channel_autopilot"), installed by
api.create_app.

An autopilot belongs to an Instagram page the account connected (InstagramChannel), and
only that account sees and changes it. Saving one makes it (in dry run unless switched
off), linking the research account named, or one made for the page's own name.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_editor import features, jobs
from agentic_editor.channel import pilot as autopilot
from agentic_editor.db import (
    ChannelPilot,
    ChannelPost,
    InstagramChannel,
    Job,
    Output,
    Research,
    ResearchAccount,
)

FEATURE = autopilot.FEATURE
CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
NEW = "new"  # a research account made for the page's own name
SHOWN = 300  # videos a page lists, newest first
SHOWS = {
    "queue": ("queued", "checking", "editing", "reviewing", "awaiting", "ready", "due",
              "posting", "look"),
    "posted": ("posted", "failed"),
    "skipped": ("skipped", "unused"),
}  # fmt: skip


class PilotSettings(BaseModel):
    active: bool = True
    dry_run: bool = True
    # A research account's id; "new": one made for the page's own name; None: none yet.
    research_account_id: str | None = Field(default=None, max_length=12)
    preset: str = Field(default="", max_length=64)
    focus: str = Field(default="", max_length=600)
    research_hour: int = Field(default=7, ge=0, le=23)
    posts_per_day: int = Field(default=autopilot.DEFAULT_PER_DAY, ge=1, le=autopilot.MOST_PER_DAY)
    post_times: list[str] = Field(
        default_factory=lambda: list(autopilot.DEFAULT_TIMES), max_length=autopilot.MOST_PER_DAY
    )
    needs: int = Field(default=20, ge=0, le=500)

    @field_validator("post_times")
    @classmethod
    def _clock(cls, times: list[str]) -> list[str]:
        tidy = []
        for given in times:
            given = given.strip()
            if not CLOCK.match(given):
                raise ValueError(f"{given!r} isn't a time like 18:30")
            tidy.append(given)
        return sorted(set(tidy))

    @model_validator(mode="after")
    def _enough_times(self) -> "PilotSettings":
        if len(self.post_times) < self.posts_per_day:
            raise ValueError(
                f"give {self.posts_per_day} post times for {self.posts_per_day} posts a day"
            )
        return self


def install(app, *, User, DB, presets_of, public_base, settings) -> None:
    def pilot_user(account, session: Session) -> None:
        if not features.enabled(account, FEATURE, session):
            raise HTTPException(403, "the channel autopilot isn't on for this account yet")

    def my_channel(channel_id: str, account, session: Session) -> InstagramChannel:
        pilot_user(account, session)
        channel = session.get(InstagramChannel, channel_id)
        if channel is None or channel.owner_id != account.id:
            raise HTTPException(404, "that Instagram page isn't connected to this account")
        return channel

    def pilot_of(channel: InstagramChannel, session: Session) -> ChannelPilot | None:
        return session.scalar(select(ChannelPilot).where(ChannelPilot.channel_id == channel.id))

    def my_post(post_id: int, account, session: Session) -> ChannelPost:
        pilot_user(account, session)
        row = session.get(ChannelPost, post_id)
        pilot = session.get(ChannelPilot, row.pilot_id) if row else None
        if row is None or pilot is None or pilot.owner_id != account.id:
            raise HTTPException(404, "no such video")
        return row

    def pilot_view(pilot: ChannelPilot, channel: InstagramChannel, session: Session) -> dict:
        counts = dict(
            session.execute(
                select(ChannelPost.status, func.count())
                .where(ChannelPost.pilot_id == pilot.id)
                .group_by(ChannelPost.status)
            ).all()
        )
        found = session.get(Research, pilot.research_id) if pilot.research_id else None
        account = session.get(ResearchAccount, pilot.research_account_id or "")
        today = datetime.now().date()
        posted_today = sum(
            autopilot.local(at).date() == today
            for at in session.scalars(
                select(ChannelPost.posted_at).where(
                    ChannelPost.pilot_id == pilot.id,
                    ChannelPost.status == "posted",
                    ChannelPost.posted_at.is_not(None),
                )
            )
        )
        best = sorted(pilot.history or [], key=autopilot.reactions, reverse=True)[:5]
        return {
            **{k: getattr(pilot, k) for k in PilotSettings.model_fields},
            "id": pilot.id,
            "channel_id": channel.id,
            "username": channel.username,
            "approved": pilot.approved,
            "paused_reason": pilot.paused_reason,
            "note": pilot.note,
            "research_handle": account.handle if account else None,
            "research": None
            if found is None
            else {
                "id": found.id,
                "status": found.status,
                "message": found.message,
                "progress": found.progress,
                "found": len(found.items),
                "created_at": found.created_at.isoformat(),
            },
            "counts": counts,
            "posted_today": posted_today,
            "next_post": next_post(pilot),
            "learned_at": pilot.learned_at.isoformat() if pilot.learned_at else None,
            "learn_note": pilot.learn_note,
            "posts_read": len(pilot.history or []),
            "insights_allowed": pilot.insights_allowed,
            "best": [
                {
                    "caption": (h.get("caption") or "").strip().split("\n", 1)[0][:120],
                    "likes": h.get("likes"),
                    "comments": h.get("comments"),
                    "link": h.get("link", ""),
                }
                for h in best
            ],
        }

    def next_post(pilot: ChannelPilot) -> str | None:
        times = autopilot.times_of(pilot)
        if not pilot.active or not times:
            return None
        now = datetime.now()
        later = [t for t in times if t > now.time()]
        at = later[0] if later else times[0]
        return f"{'today' if later else 'tomorrow'} {at:%H:%M}"

    def post_view(row: ChannelPost, session: Session) -> dict:
        output = session.get(Output, row.output_id) if row.output_id else None
        job = session.get(Job, row.job_id) if row.job_id else None
        return {
            "id": row.id, "status": row.status, "reason": row.reason, "code": row.code,
            "url": row.url, "title": row.title[:300], "song": row.song, "artist": row.artist,
            "heard_song": row.heard_song, "heard_artist": row.heard_artist,
            "score": row.score, "relevance": row.relevance, "duration": row.duration,
            "checks": row.checks, "approved": row.approved, "job_id": row.job_id,
            "job_status": job.status if job else None,
            "output_id": output.id if output else None,
            "has_thumbnail": bool(output and output.thumbnail),
            "caption": row.caption or (output.caption if output else ""),
            "post_id": row.post_id, "permalink": row.permalink, "dry_run": row.dry_run,
            "posted_at": row.posted_at.isoformat() if row.posted_at else None,
            "insights": row.insights, "insights_at":
                row.insights_at.isoformat() if row.insights_at else None,
            "created_at": row.created_at.isoformat(),
        }  # fmt: skip

    def research_account(
        value: str | None, channel: InstagramChannel, preset: str, account, session: Session
    ) -> str | None:
        if not value:
            return None
        if value != NEW:
            found = session.get(ResearchAccount, value)
            if found is None or found.owner_id != account.id:
                raise HTTPException(404, "no such research account")
            if preset and preset not in found.presets:
                found.presets = [*found.presets, preset]
            return found.id
        handle = channel.username.lower()
        found = session.scalar(
            select(ResearchAccount).where(
                ResearchAccount.owner_id == account.id, ResearchAccount.handle == handle
            )
        )
        if found is None:
            found = ResearchAccount(owner_id=account.id, handle=handle, presets=[])
            session.add(found)
            session.flush()
        if preset and preset not in found.presets:
            found.presets = [*found.presets, preset]
        return found.id

    @app.get("/api/channel-autopilot")
    def list_pilots(account: User, session: DB) -> dict:
        pilot_user(account, session)
        channels = session.scalars(
            select(InstagramChannel)
            .where(InstagramChannel.owner_id == account.id)
            .order_by(InstagramChannel.username)
        ).all()
        presets = presets_of(account)
        accounts_ = session.scalars(
            select(ResearchAccount)
            .where(ResearchAccount.owner_id == account.id)
            .order_by(ResearchAccount.created_at)
        ).all()
        return {
            "channels": [
                {
                    "id": c.id,
                    "username": c.username,
                    "avatar": c.avatar,
                    "pilot": pilot_view(p, c, session) if (p := pilot_of(c, session)) else None,
                }
                for c in channels
            ],
            "presets": [
                {"name": n, "label": p.label, "split": p.split.enabled}
                for n, p in presets.items()
                if not p.download_only and not p.upscale.enabled
            ],
            "research_accounts": [{"id": a.id, "handle": a.handle} for a in accounts_],
            "defaults": PilotSettings().model_dump(),
        }

    @app.put("/api/channel-autopilot/{channel_id}")
    def save_pilot(
        channel_id: str, body: PilotSettings, account: User, session: DB, request: Request
    ) -> dict:
        channel = my_channel(channel_id, account, session)
        if body.preset and body.preset not in presets_of(account):
            raise HTTPException(422, f"no preset {body.preset!r}")
        if body.active and not body.preset:
            raise HTTPException(422, "choose the preset its reels are edited with")
        pilot = pilot_of(channel, session)
        if pilot is None:
            pilot = ChannelPilot(owner_id=account.id, channel_id=channel.id)
            session.add(pilot)
        resumed = body.active and not pilot.active
        for key, value in body.model_dump(exclude={"research_account_id"}).items():
            setattr(pilot, key, value)
        pilot.focus = " ".join(body.focus.split())
        pilot.research_account_id = research_account(
            body.research_account_id, channel, body.preset, account, session
        )
        if resumed or not body.active:
            pilot.paused_reason = ""
        pilot.base_url = public_base(request)
        session.commit()
        return pilot_view(pilot, channel, session)

    @app.post("/api/channel-autopilot/{channel_id}/find")
    def find_now(channel_id: str, account: User, session: DB) -> dict:
        """Today's research now, whatever the hour (the next one is tomorrow's)."""
        channel = my_channel(channel_id, account, session)
        pilot = pilot_of(channel, session)
        if pilot is None:
            raise HTTPException(409, "save the autopilot first")
        if not pilot.active:
            raise HTTPException(409, "resume the autopilot first")
        busy = session.get(Research, pilot.research_id) if pilot.research_id else None
        if busy is not None and busy.status in ("queued", "running"):
            raise HTTPException(409, "its research is still running")
        pilot.research_on = None
        research_id = autopilot.start_research(session, pilot, datetime.now(), fresh=True)
        session.commit()
        if research_id is None:
            raise HTTPException(409, pilot.note or "nothing to find now")
        jobs.channel_learn(pilot.id, research_id)
        return pilot_view(pilot, channel, session)

    @app.post("/api/channel-autopilot/{channel_id}/learn")
    def learn_now(channel_id: str, account: User, session: DB) -> dict:
        """Read the page's posts now (they are read before each research anyway)."""
        channel = my_channel(channel_id, account, session)
        pilot = pilot_of(channel, session)
        if pilot is None:
            raise HTTPException(409, "save the autopilot first")
        jobs.channel_learn(pilot.id)
        return pilot_view(pilot, channel, session)

    @app.get("/api/channel-autopilot/{channel_id}/posts")
    def list_posts(
        channel_id: str,
        account: User,
        session: DB,
        show: Literal["queue", "posted", "skipped"] = "queue",
    ) -> list[dict]:
        channel = my_channel(channel_id, account, session)
        pilot = pilot_of(channel, session)
        if pilot is None:
            return []
        order = ChannelPost.posted_at.desc() if show == "posted" else ChannelPost.id.desc()
        rows = session.scalars(
            select(ChannelPost)
            .where(ChannelPost.pilot_id == pilot.id, ChannelPost.status.in_(SHOWS[show]))
            .order_by(order)
            .limit(SHOWN)
        )
        return [post_view(r, session) for r in rows]

    @app.post("/api/channel-posts/{post_id}/approve")
    def approve_post(post_id: int, account: User, session: DB) -> dict:
        """It posts at the next post time, and counts towards posting alone."""
        row = my_post(post_id, account, session)
        try:
            autopilot.approve(session, row)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return post_view(row, session)

    @app.post("/api/channel-posts/{post_id}/skip")
    def skip_post(post_id: int, account: User, session: DB) -> dict:
        row = my_post(post_id, account, session)
        if row.status in ("posted", "posting", "due", "checking"):
            raise HTTPException(409, "it can't be skipped now")
        row.status, row.reason = "skipped", "skipped by a person"
        session.commit()
        return post_view(row, session)

    @app.post("/api/channel-posts/{post_id}/retry")
    def retry_post(post_id: int, account: User, session: DB) -> dict:
        """A reel whose post failed goes at the next post time once more (look on Instagram
        first: a post that broke off may have gone out)."""
        row = my_post(post_id, account, session)
        if row.status != "failed":
            raise HTTPException(409, "only a post that failed can be tried again")
        row.status, row.reason, row.posted_at = "ready", "", None
        row.updated_at = datetime.now(UTC)
        session.commit()
        return post_view(row, session)
