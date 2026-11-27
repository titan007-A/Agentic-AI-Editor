import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    case,
    create_engine,
    event,
    func,
    or_,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class User(Base):
    """An account: the owner as admin, who manages the others, or an employee (member)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin or member
    # The primary account: the one admin who makes, changes and removes the other admins
    # (accounts.primary; until one is marked, the first admin).
    is_primary: Mapped[bool] = mapped_column(default=False)
    # An admin whose presets are their own (presets/people/<id>, as an employee's), seen
    # only by them and the primary account, not by the other admins (accounts.presets_dir).
    private_presets: Mapped[bool] = mapped_column(default=False)
    # An employee whose presets only this admin (by id) and the primary account see, change
    # and send to; None: every admin (accounts.can_see_presets).
    presets_by: Mapped[str | None] = mapped_column(String(12), default=None)
    # Raised on a password change or removal, which ends every session signed in before.
    session_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_seen_at: Mapped[datetime | None]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    source: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    presets: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(String(12))
    # False: the person chose to keep the whole video, not cut to the preset's length.
    trim: Mapped[bool] = mapped_column(default=True)
    # When a worker took it up: whose turn is next goes by who was served last.
    started_at: Mapped[datetime | None]
    # Who started it; None for jobs from before accounts, which are the admin's.
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    # The song, once the edit or its caption knew it, with its year and label
    # (songfacts.Facts); None until then. Its warnings are worked out when shown (strikes.py).
    song_facts: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    outputs: Mapped[list["Output"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Output.id"
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Output(Base):
    __tablename__ = "outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    preset: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    thumbnail: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[float]
    title: Mapped[str] = mapped_column(Text, default="")
    planner: Mapped[str] = mapped_column(String(64))
    renders: Mapped[int] = mapped_column(default=1)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    edl: Mapped[dict] = mapped_column(JSON)
    # 0-1: how sure the agent is the video is ready to post as it is.
    confidence: Mapped[float] = mapped_column(default=1.0)
    doubts: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The editor's call, which overrides the agent's: approved, posted or dismissed.
    verdict: Mapped[str | None] = mapped_column(String(16))
    # The post's caption (see caption.py): queued, writing, done or failed; the articles it
    # was written from; and what to check before posting it, empty when it passed.
    caption: Mapped[str] = mapped_column(Text, default="")
    caption_status: Mapped[str | None] = mapped_column(String(16))
    # When the caption was asked for, and when a turn took it up: captions take turns
    # with jobs (see jobs.claim_turn).
    caption_queued_at: Mapped[datetime | None]
    caption_started_at: Mapped[datetime | None]
    caption_sources: Mapped[list[dict]] = mapped_column(JSON, default=list)
    caption_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The song it is about: as the model found it from the video, or as a person named it
    # when the video doesn't say or the model got it wrong.
    caption_song: Mapped[str] = mapped_column(Text, default="")
    caption_artist: Mapped[str] = mapped_column(Text, default="")
    # Or, for a post that isn't about a song, the topic a person named instead.
    caption_topic: Mapped[str] = mapped_column(Text, default="")
    # The folder it was put in inside its preset's folder on the Autopilot piles
    # (PileFolder); None: straight in the preset's folder.
    folder_id: Mapped[int | None]

    job: Mapped[Job] = relationship(back_populates="outputs")

    @hybrid_property
    def pile(self) -> str | None:
        """ready, look (needs a look) or None once posted or dismissed."""
        if self.verdict in ("posted", "dismissed"):
            return None
        if self.verdict == "approved" or not (self.doubts or self.issues):
            return "ready"
        return "look"

    @pile.inplace.expression
    @classmethod
    def _pile(cls):
        verdict = func.coalesce(cls.verdict, "")
        clean = (func.coalesce(func.json_array_length(cls.doubts), 0) == 0) & (
            func.coalesce(func.json_array_length(cls.issues), 0) == 0
        )
        return case(
            (verdict.in_(("posted", "dismissed")), None),
            (or_(verdict == "approved", clean), "ready"),
            else_="look",
        )


class PileFolder(Base):
    """A folder someone made inside a preset's folder on the Autopilot piles (or inside
    another such folder) to sort its finished videos (Output.folder_id). It holds videos
    in both piles: a video keeps its folder when it moves from "needs a look" to ready."""

    __tablename__ = "pile_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # who made it
    preset: Mapped[str] = mapped_column(String(64))
    # The folder it is in; None: straight in the preset's folder.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("pile_folders.id"))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ShareLink(Base):
    """A link that shows one pile folder's videos (and its folders' in it) to anyone who
    has it, without logging in: a client watches them, downloads them and copies their post
    captions. The folder's videos are read when the link is opened, so new ones show up;
    taking the link away stops it."""

    __tablename__ = "share_links"

    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # who shared it
    folder_id: Mapped[int] = mapped_column(ForeignKey("pile_folders.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Feed(Base):
    """A folder, playlist or channel the autopilot watches for new videos."""

    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # folder or link
    location: Mapped[str] = mapped_column(Text)
    presets: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Hours of the day (0-23) new videos may start, e.g. 23 to 7 for overnight; None for any.
    active_from: Mapped[int | None]
    active_to: Mapped[int | None]
    batch: Mapped[int] = mapped_column(default=3)  # videos in progress at once, at most
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # None: the admin's
    include_existing: Mapped[bool] = mapped_column(default=False)
    # Start from the latest videos: those already there are edited too, newest first, the
    # `skip_newest` newest of them passed over (start that many videos after the latest);
    # new ones still come first as they arrive. `restart` asks the next check to read what
    # is already there again (the setting was just turned on or changed).
    from_latest: Mapped[bool] = mapped_column(default=False)
    skip_newest: Mapped[int] = mapped_column(default=0)
    restart: Mapped[bool] = mapped_column(default=False)
    # Only videos about this are edited ("GTA 5"); the vision model looks at each new one
    # first, and passes over the rest (see autopilot.judge_topic). None: every video.
    topic: Mapped[str | None] = mapped_column(Text)
    # The folder its finished videos go in, inside their preset's folder on the piles
    # (PileFolder, made the first time); None: straight in the preset's folder.
    folder: Mapped[str | None] = mapped_column(Text)
    # Each day's videos in a folder of that day inside it ("23-NOV"), so days stay apart.
    dated: Mapped[bool] = mapped_column(default=True)
    # Whether the first look, which notes what is already there, has been made. A new feed
    # starts without it; feeds saved before this column existed had made it, so the column
    # is added to them as true.
    looked: Mapped[bool] = mapped_column(default=False, server_default=text("1"))
    last_checked: Mapped[datetime | None]
    last_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    items: Mapped[list["FeedItem"]] = relationship(
        back_populates="feed", cascade="all, delete-orphan", order_by="FeedItem.id.desc()"
    )


class FeedItem(Base):
    """A video a feed has seen, so it is never edited twice."""

    __tablename__ = "feed_items"
    __table_args__ = (UniqueConstraint("feed_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    skipped: Mapped[bool] = mapped_column(default=False)  # already there when the feed began
    # Why it was passed over without being edited: not about the feed's topic, or it couldn't
    # be looked at. Such an item is skipped too.
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    feed: Mapped[Feed] = relationship(back_populates="items")
    job: Mapped[Job | None] = relationship()


class StyleDraft(Base):
    """A preset being drafted from a reel that has the look wanted."""

    __tablename__ = "style_drafts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    source: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(64))
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # None: the admin's
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message: Mapped[str] = mapped_column(Text, default="waiting for a free slot")
    preset: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Animation(Base):
    """An animated, narrated reel being written in the studio (animate/): its brief, its
    Plan as it stands, earlier Plans for undo and what the player needs to show it."""

    __tablename__ = "animations"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    idea: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="auto")
    style: Mapped[str] = mapped_column(String(16), default="auto")
    voice: Mapped[str] = mapped_column(String(32), default="auto")
    length: Mapped[str] = mapped_column(String(16), default="auto")
    images: Mapped[bool] = mapped_column(default=True)  # painted pictures (animate/images.py)
    # queued -> drafting / changing / painting / voicing -> ready, or failed.
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message: Mapped[str] = mapped_column(Text, default="waiting for a free slot")
    error: Mapped[str | None] = mapped_column(Text)
    # What the queued task does: {"op": "draft"}, {"op": "change", "request", "scene"},
    # {"op": "voice"}.
    task: Mapped[dict | None] = mapped_column(JSON)
    plan: Mapped[dict | None] = mapped_column(JSON)
    history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    player: Mapped[dict | None] = mapped_column(JSON)  # timeline.player_data
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    job_id: Mapped[str | None] = mapped_column(String(12))  # its latest render
    # Made by an autopilot (AnimationAutopilot): what became of its post. None: made by hand;
    # rendering -> awaiting (a person approves) -> posting -> posted, or skipped / failed.
    autopilot_id: Mapped[str | None] = mapped_column(String(12), index=True)
    post_status: Mapped[str | None] = mapped_column(String(16))
    post_error: Mapped[str | None] = mapped_column(Text)
    post_id: Mapped[str | None] = mapped_column(String(64))
    # Claims the fact check found no source for; any of them keeps it from posting by itself.
    doubts: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class AnimationAutopilot(Base):
    """An account's daily animated reels (animate/schedule.py): topics from its list, else
    new ones from its niche; each drafted, checked and rendered by itself, then posted once
    a person approves, or by itself after `needs` approvals once auto_post is on."""

    __tablename__ = "animation_autopilots"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True, unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    niche: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)  # waiting, first goes next
    used: Mapped[list[str]] = mapped_column(JSON, default=list)  # made already
    hour: Mapped[int] = mapped_column(default=10)  # local time
    per_day: Mapped[int] = mapped_column(default=1)
    kind: Mapped[str] = mapped_column(String(16), default="auto")
    style: Mapped[str] = mapped_column(String(16), default="auto")
    voice: Mapped[str] = mapped_column(String(32), default="auto")
    length: Mapped[str] = mapped_column(String(16), default="auto")
    images: Mapped[bool] = mapped_column(default=True)
    channel_id: Mapped[str | None] = mapped_column(String(32))  # InstagramChannel to post to
    # The site's address as people reach it, for the link Instagram fetches the video from.
    base_url: Mapped[str] = mapped_column(Text, default="")
    auto_post: Mapped[bool] = mapped_column(default=False)
    needs: Mapped[int] = mapped_column(default=10)  # approvals before auto_post may go on
    approved: Mapped[int] = mapped_column(default=0)
    last_on: Mapped[date | None]
    made_on_last: Mapped[int] = mapped_column(default=0)  # reels started on last_on
    created_at: Mapped[datetime] = mapped_column(default=_now)


class McpToken(Base):
    """One way in for Claude to an account's Animations tab (mcp_access): a key the person
    made and copied into Claude Code, or a connection claude.ai made through the sign-in
    page. Only hashes are kept. It works as long as the account's password stays the same
    (`version`), like a signed-in session."""

    __tablename__ = "mcp_tokens"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(8), default="key")  # key or oauth
    client_id: Mapped[str | None] = mapped_column(String(64))  # oauth: the McpClient
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    tail: Mapped[str] = mapped_column(String(8), default="")  # a key's last letters, to tell it
    # May post reels (Instagram, an autopilot reel's approval); off unless the person said so.
    posting: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=0)  # the account's session_version
    expires_at: Mapped[datetime | None]  # oauth: the access token's end; refreshed
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_used_at: Mapped[datetime | None]


class McpClient(Base):
    """An app that registered itself to ask for a connection (OAuth dynamic client
    registration, as claude.ai does): it still needs a signed-in person to allow it."""

    __tablename__ = "mcp_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    info: Mapped[dict] = mapped_column(JSON)  # OAuthClientInformationFull
    created_at: Mapped[datetime] = mapped_column(default=_now)


class KickStreamer(Base):
    """A Kick streamer an account clips (clipper/), with the preset and folder its clips
    get; with `live` on it is clipped while it streams, one stretch at a time."""

    __tablename__ = "kick_streamers"
    __table_args__ = (UniqueConstraint("owner_id", "slug"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(Text, default="")
    channel_id: Mapped[int] = mapped_column(default=0)
    avatar: Mapped[str] = mapped_column(Text, default="")
    live: Mapped[bool] = mapped_column(default=False)  # clip it while it streams
    preset: Mapped[str] = mapped_column(String(64), default="")
    # A clip that isn't gameplay under a camera is made with this (clipper/hunt.clip_preset);
    # "": the account's kick-clips when it has it.
    irl_preset: Mapped[str] = mapped_column(String(64), default="")
    per_hour: Mapped[int] = mapped_column(default=4)  # clips an hour of live stream, at most
    folder: Mapped[str] = mapped_column(Text, default="")  # on the piles; "" is "Kick <name>"
    # The live stream being clipped, and how far into it the clipping has got (seconds).
    stream_id: Mapped[int | None]
    done_until: Mapped[float] = mapped_column(default=0.0)
    is_live: Mapped[bool] = mapped_column(default=False)  # when last looked at
    last_checked: Mapped[datetime | None]
    last_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class KickHunt(Base):
    """One look for clips in a stretch of a Kick stream (clipper/hunt.py): the moments it
    found, each made into a Job."""

    __tablename__ = "kick_hunts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    streamer_id: Mapped[str | None] = mapped_column(String(12), index=True)  # a live stretch's
    slug: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(Text, default="")
    channel_id: Mapped[int] = mapped_column(default=0)
    stream_id: Mapped[int] = mapped_column(default=0)
    uuid: Mapped[str] = mapped_column(String(64), default="")  # the stream's video id
    title: Mapped[str] = mapped_column(Text, default="")
    started: Mapped[datetime | None]  # when the stream began (UTC)
    source: Mapped[str] = mapped_column(Text, default="")  # its HLS master playlist
    start: Mapped[float] = mapped_column(default=0.0)  # the stretch, seconds into the stream
    end: Mapped[float] = mapped_column(default=0.0)  # 0: to the end
    count: Mapped[int] = mapped_column(default=5)  # clips wanted, at most
    preset: Mapped[str] = mapped_column(String(64), default="")
    # "auto": each clip's layout by what it shows (clipper/hunt.clip_preset: gameplay under a
    # camera -> the facecam preset, anything else -> irl_preset); "fixed": `preset` for all.
    layout: Mapped[str] = mapped_column(String(8), default="auto")
    irl_preset: Mapped[str] = mapped_column(String(64), default="")
    # Started by a schedule (the morning clips), not a person: its clips wait behind people's
    # own jobs, as a live follow's do (jobs.automatic_jobs).
    auto: Mapped[bool] = mapped_column(default=False)
    live: Mapped[bool] = mapped_column(default=False)  # a live stretch: strong moments only
    folder: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message: Mapped[str] = mapped_column(Text, default="waiting for a free slot")
    error: Mapped[str | None] = mapped_column(Text)
    moments: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ResearchAccount(Base):
    """An Instagram account a person runs, and the presets they edit its reels with: the
    researcher learns what it posts and finds reels like its best ones (see research.py)."""

    __tablename__ = "research_accounts"
    __table_args__ = (UniqueConstraint("owner_id", "handle"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    handle: Mapped[str] = mapped_column(String(64))
    presets: Mapped[list[str]] = mapped_column(JSON, default=list)
    # What the planning model read in the account's own reels, and when; empty when none
    # were found, and then a research goes by the presets.
    summary: Mapped[str] = mapped_column(Text, default="")
    learned_at: Mapped[datetime | None]
    own: Mapped[list[str]] = mapped_column(JSON, default=list)  # its own reels' codes
    # Pages the owner takes reels from, "instagram:name" or "youtube:@name": every research
    # for the account looks at their latest too.
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    best: Mapped[list[dict]] = mapped_column(JSON, default=list)  # its best liked
    # Learning runs as a task of its own: idle, queued, running, done or failed.
    learn_status: Mapped[str] = mapped_column(String(16), default="idle")
    learn_message: Mapped[str] = mapped_column(Text, default="")
    learn_error: Mapped[str | None] = mapped_column(Text)
    # A research for it every morning: the hour (server time) it starts at, None when off,
    # and the day the last one started.
    daily_hour: Mapped[int | None]
    daily_on: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AccountSnapshot(Base):
    """How an Instagram account's own reels were doing when it was learned: each reel's
    likes, comments and posting time, so the page's performance can be followed."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("research_accounts.id", ondelete="CASCADE"), index=True
    )
    taken_at: Mapped[datetime] = mapped_column(default=_now)
    reels: Mapped[list[dict]] = mapped_column(JSON, default=list)


class HiddenAccount(Base):
    """An Instagram account whose reels a person doesn't want: researches leave it out."""

    __tablename__ = "research_hidden"
    __table_args__ = (UniqueConstraint("owner_id", "handle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # None: the admin's
    handle: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Research(Base):
    """A search for Instagram reels that fit a description, or an account (see
    research.py)."""

    __tablename__ = "research"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    # What to find; for an account, an optional focus.
    description: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)  # None: the admin's
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_accounts.id", ondelete="SET NULL"), index=True
    )
    handle: Mapped[str] = mapped_column(Text, default="")  # kept if the account is removed
    # Where it searches: "instagram" reels and YouTube "shorts" (see research.SITES).
    sites: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["instagram"])
    daily: Mapped[bool] = mapped_column(default=False)  # started by the account's morning one
    # "search" (for a description or an account) or "pdf": a PDF of songs, each with the
    # words to write above and below its reel, read into entries [{"singer", "song", "top",
    # "bottom"}] and searched for one by one.
    mode: Mapped[str] = mapped_column(String(8), default="search")
    pdf_text: Mapped[str] = mapped_column(Text, default="")
    entries: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Every song the PDF lists (see songlist.Listed), for the person to choose from while
    # the research is "choosing"; the chosen ones become `entries`.
    listing: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # What a PDF lists ("songs", "films", "military aircraft"...): how its items are searched.
    kind: Mapped[str] = mapped_column(String(60), default="songs")
    # A follow-up asked for in an earlier research's chat ("more Italian").
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("research.id", ondelete="SET NULL"), index=True
    )
    # "asking": the model thinks of questions (none yet), then waits for the answers; a
    # PDF's is "reading" while the model reads it and "choosing" until the person picks its
    # songs; then queued, running, done or failed like a job.
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # When a turn took it up: whose research goes next is by who was served last.
    started_at: Mapped[datetime | None]
    # The questions asked before searching, [{"question", "choices"}], and the answers
    # given, [{"question", "answer"}]: they become part of what to find.
    questions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    answers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text, default="waiting for a free slot")
    progress: Mapped[float] = mapped_column(default=0.0)
    brief: Mapped[str] = mapped_column(Text, default="")  # the request as the model read it
    queries: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Reels found but left out because they were edited already.
    used: Mapped[int] = mapped_column(default=0)
    considered: Mapped[int] = mapped_column(default=0)  # reels looked up and rated
    # Reels found but not given, by why ("shown before", "didn't fit"...).
    left_out: Mapped[dict] = mapped_column(JSON, default=dict)
    cutoff: Mapped[float | None]  # the best score of the rated reels not given
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    items: Mapped[list["ResearchItem"]] = relationship(
        back_populates="research", cascade="all, delete-orphan", order_by="ResearchItem.rank"
    )
    messages: Mapped[list["ResearchMessage"]] = relationship(
        back_populates="research", cascade="all, delete-orphan", order_by="ResearchMessage.id"
    )


class ResearchMessage(Base):
    """One line of the chat on a finished research: the person's, or the researcher's
    answer, which may start a follow-up research."""

    __tablename__ = "research_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    research_id: Mapped[str] = mapped_column(
        ForeignKey("research.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" or "researcher"
    text: Mapped[str] = mapped_column(Text)
    answered: Mapped[bool] = mapped_column(default=False)  # a person's line, once answered
    follow_up_id: Mapped[str | None] = mapped_column(String(12))  # the research it started
    created_at: Mapped[datetime] = mapped_column(default=_now)

    research: Mapped[Research] = relationship(back_populates="messages")


class ResearchItem(Base):
    """A reel a research found, in its place in the ranking."""

    __tablename__ = "research_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    research_id: Mapped[str] = mapped_column(
        ForeignKey("research.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int]
    code: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, default="")
    account: Mapped[str] = mapped_column(Text, default="")  # its display name
    handle: Mapped[str] = mapped_column(Text, default="")  # its Instagram name, @handle
    caption: Mapped[str] = mapped_column(Text, default="")
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    views: Mapped[int | None]  # a YouTube Short's; Instagram doesn't show them
    # The best version the site offers: its picture, kbit/s and (YouTube's) seconds.
    width: Mapped[int | None]
    height: Mapped[int | None]
    bitrate: Mapped[float | None]
    duration: Mapped[float | None]
    posted_at: Mapped[datetime | None]
    thumbnail: Mapped[str] = mapped_column(Text, default="")
    # 0-1 each: how well it fits (None when the model couldn't rate it), how far it spread
    # among the reels found, and the two together.
    relevance: Mapped[float | None]
    why: Mapped[str] = mapped_column(Text, default="")
    # The rest of the model's thinking: the song, what it matches, the preset it suits.
    song: Mapped[str] = mapped_column(Text, default="")
    matches: Mapped[str] = mapped_column(Text, default="")
    preset: Mapped[str] = mapped_column(Text, default="")
    preset_why: Mapped[str] = mapped_column(Text, default="")
    # The singer or band the model named (`song` is "Title - Artist" when it named both).
    artist: Mapped[str] = mapped_column(Text, default="")
    per_day: Mapped[float | None]  # likes and comments a day since it was posted
    virality: Mapped[float] = mapped_column(default=0.0)
    score: Mapped[float] = mapped_column(default=0.0)
    seen: Mapped[bool] = mapped_column(default=False)  # shown by an earlier research
    entry: Mapped[int | None]  # in a PDF research, the number of the song it is for
    # The person's feedback on it, which later researches learn from: "good", "bad" or
    # None, and why it wasn't for them ("Wrong music", their own words...).
    verdict: Mapped[str | None] = mapped_column(String(8))
    verdict_reason: Mapped[str] = mapped_column(Text, default="")
    # Its song's year and label as songfacts found them; "" when not known.
    year: Mapped[str] = mapped_column(String(4), default="")
    label: Mapped[str] = mapped_column(Text, default="")
    label_group: Mapped[str] = mapped_column(String(8), default="")

    research: Mapped[Research] = relationship(back_populates="items")


class InstagramChannel(Base):
    """An Instagram professional account connected through Meta's Unified Login
    (instagram.py), with the token that covers it, and how its comments are answered
    (comments.py)."""

    __tablename__ = "instagram_channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # IG user ID
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), default="")
    avatar: Mapped[str | None] = mapped_column(Text)
    # A Business Integration System User token: one per login, shared by the accounts it
    # covers; None expiry: it doesn't expire.
    access_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None]
    # The client's business portfolio the token belongs to.
    business_id: Mapped[str] = mapped_column(String(32), default="")
    connected_at: Mapped[datetime] = mapped_column(default=_now)
    # Last post sent via our app.
    last_post_at: Mapped[datetime | None]
    # Answering comments: "off", "review" (replies are drafted for a person to send) or
    # "auto" (sent by themselves, up to reply_limit an hour; doubtful ones wait for a
    # person); in the words of reply_style; only comments made after reply_since.
    reply_mode: Mapped[str] = mapped_column(String(8), default="off")
    reply_style: Mapped[str] = mapped_column(Text, default="")
    reply_limit: Mapped[int] = mapped_column(default=15)
    reply_since: Mapped[datetime | None]
    # A reply goes out at a random time this many minutes after it was drafted, not at once.
    reply_wait_min: Mapped[int] = mapped_column(default=5)
    reply_wait_max: Mapped[int] = mapped_column(default=30)
    # While Meta refuses the app (blocked, token gone, too many calls), no turns until then.
    paused_until: Mapped[datetime | None]
    # The last look at its comments: when, and what came of it (or what went wrong).
    checked_at: Mapped[datetime | None]
    check_note: Mapped[str] = mapped_column(Text, default="")


class InstagramComment(Base):
    """A comment on one of a connected account's posts, and its reply: drafted by the model,
    then sent (by a person or by itself) or skipped."""

    __tablename__ = "instagram_comments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # Instagram's comment ID
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("instagram_channels.id", ondelete="CASCADE"), index=True
    )
    post_id: Mapped[str] = mapped_column(String(40))
    post_caption: Mapped[str] = mapped_column(Text, default="")
    post_url: Mapped[str] = mapped_column(Text, default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    commented_at: Mapped[datetime | None]
    # new (not drafted yet), drafted (a reply waits to be sent), look (a person should
    # answer it), sending, sent, skipped, failed
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)
    draft: Mapped[str] = mapped_column(Text, default="")
    # When a drafted reply may go out by itself (a random wait after drafting).
    send_after: Mapped[datetime | None]
    why: Mapped[str] = mapped_column(Text, default="")  # the model's note on it
    reply_id: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str] = mapped_column(Text, default="")
    sent_by: Mapped[str] = mapped_column(String(8), default="")  # "auto" or "person"
    created_at: Mapped[datetime] = mapped_column(default=_now)
    sent_at: Mapped[datetime | None]


class ChannelPilot(Base):
    """A connected Instagram account that runs itself (channel/pilot.py): each day its
    research account's research finds videos for it, the best are checked, edited with
    its preset, checked again and posted at its post times, through the app's own
    Instagram connection. The first `needs` reels wait for a person's approval; in dry run
    nothing is really posted."""

    __tablename__ = "channel_pilots"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    # The InstagramChannel; no link to it: a page disconnected and connected again keeps
    # its autopilot and what it posted (nothing runs while the page is missing).
    channel_id: Mapped[str] = mapped_column(String(32), unique=True)
    active: Mapped[bool] = mapped_column(default=True)  # off: paused, by a person or itself
    # Everything but the post itself: what would be posted is marked so, nothing goes out.
    dry_run: Mapped[bool] = mapped_column(default=True)
    research_account_id: Mapped[str | None] = mapped_column(String(12))
    preset: Mapped[str] = mapped_column(String(64), default="")
    # What its researches look for besides what the account's best posts show.
    focus: Mapped[str] = mapped_column(Text, default="")
    # Its researches rate a recognisable but less obvious song over an artist's most famous
    # hit (the owner's channel data: surprising picks did best).
    surprise: Mapped[bool] = mapped_column(default=True)
    research_hour: Mapped[int] = mapped_column(default=7)  # local time
    posts_per_day: Mapped[int] = mapped_column(default=1)
    post_times: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["19:00"])  # "HH:MM"
    needs: Mapped[int] = mapped_column(default=20)  # reels a person approves first
    approved: Mapped[int] = mapped_column(default=0)
    # The site's address as people reach it, for the link Instagram fetches a video from.
    base_url: Mapped[str] = mapped_column(Text, default="")
    # Why it paused itself (a post that failed), shown on its page until it is resumed.
    paused_reason: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")  # what it did last
    research_on: Mapped[date | None]  # the day its last research started
    researches_on: Mapped[int] = mapped_column(default=0)  # researches started that day
    research_id: Mapped[str | None] = mapped_column(String(12))  # that research
    picked_id: Mapped[str | None] = mapped_column(String(12))  # the research it picked from
    # The account's own posts as Instagram gave them when last read: id, caption, likes,
    # comments, time, link and whatever insights its login allows (channel/pilot.learn).
    history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    learned_at: Mapped[datetime | None]
    learn_note: Mapped[str] = mapped_column(Text, default="")
    # Whether Meta gives the account's insights (views, reach, saves) to the app's login;
    # None until asked. Without them, likes and comments are the measure.
    insights_allowed: Mapped[bool | None]
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ChannelPost(Base):
    """One video a channel autopilot found, through its checks, edit and post."""

    __tablename__ = "channel_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    pilot_id: Mapped[str] = mapped_column(
        ForeignKey("channel_pilots.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    research_id: Mapped[str | None] = mapped_column(String(12))
    code: Mapped[str] = mapped_column(String(64), index=True)  # research.code_of
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")  # the video's caption or title
    # The song as the research named it, then as Shazam heard it.
    song: Mapped[str] = mapped_column(Text, default="")
    artist: Mapped[str] = mapped_column(Text, default="")
    heard_song: Mapped[str] = mapped_column(Text, default="")
    heard_artist: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(default=0.0)  # the research's
    relevance: Mapped[float | None]
    duration: Mapped[float | None]  # the source's seconds
    work_dir: Mapped[str] = mapped_column(Text, default="")  # its analysis cache folder
    # Another copy of a song picked in the same batch: checked only if the better-scored
    # copy fails its checks.
    spare: Mapped[bool] = mapped_column(default=False)
    retry_at: Mapped[datetime | None]  # UTC: not checked before then (Shazam asked to wait)
    # queued -> checking -> skipped, or editing -> reviewing -> look (needs a look), or
    # awaiting (a person approves) / ready -> due (its post time came) -> posting -> posted,
    # or failed; unused: picked but not needed, once enough others passed.
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")  # why skipped, failed or looked at
    checks: Mapped[list[dict]] = mapped_column(JSON, default=list)  # {name, ok, detail}
    approved: Mapped[bool] = mapped_column(default=False)  # by a person
    job_id: Mapped[str | None] = mapped_column(String(12), index=True)
    output_id: Mapped[int | None]
    caption: Mapped[str] = mapped_column(Text, default="")  # as posted
    post_id: Mapped[str | None] = mapped_column(String(64))  # Instagram's media id
    permalink: Mapped[str] = mapped_column(Text, default="")
    dry_run: Mapped[bool] = mapped_column(default=False)  # "posted" in dry run: never sent
    posted_at: Mapped[datetime | None]  # UTC: when its post time came, then when it went out
    insights: Mapped[dict] = mapped_column(JSON, default=dict)
    insights_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class BufferKey(Base):
    """A Buffer API key someone added, named by them, and the channels Buffer showed through
    it when last asked: kept so posting doesn't ask Buffer again each time."""

    __tablename__ = "buffer_keys"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(Text)
    channels: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Why the last request with it failed, empty when it worked.
    error: Mapped[str] = mapped_column(Text, default="")
    refreshed_at: Mapped[datetime | None]
    # Requests sent with it on `used_on` (a date): Buffer limits each account per day.
    used_on: Mapped[str] = mapped_column(String(10), default="")
    used: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class PresetBuffer(Base):
    """The Buffer channels one person's preset posts to. Channels, not keys: a key replaced
    after it expired still reaches the same channels, so the presets keep working."""

    __tablename__ = "preset_buffer"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    preset: Mapped[str] = mapped_column(String(64), primary_key=True)
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)


class BufferPost(Base):
    """A post the editor put in someone's Buffer, followed until it went out (or didn't):
    buffer_posts asks Buffer how it went, sends a failed one again and cleans up its video
    after. Kept apart from the reel (output_id is no link), which may be deleted first."""

    __tablename__ = "buffer_posts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    post_id: Mapped[str] = mapped_column(String(40), unique=True)  # Buffer's
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key_id: Mapped[str | None] = mapped_column(String(12))  # the key it was made with
    channel_id: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(Text, default="")  # its name, as shown then
    service: Mapped[str] = mapped_column(String(20), default="")
    output_id: Mapped[int | None] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    first_comment: Mapped[str] = mapped_column(Text, default="")
    # Kept in Buffer's drafts (a trial reel): goes out when the person sends it there.
    draft: Mapped[bool] = mapped_column(default=False)
    link: Mapped[str] = mapped_column(Text)  # the video link Buffer was given
    pub: Mapped[str] = mapped_column(String(64), default="")  # its file in media/pub
    hosted: Mapped[str] = mapped_column(String(120), default="")  # its R2 key; "": ours
    due_at: Mapped[datetime | None]
    # scheduled, sending, needs_approval, draft, sent, error, gone (deleted in Buffer),
    # retried (sent again as another post), unknown (still not out long after its time)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    raw_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    checks: Mapped[int] = mapped_column(default=0)
    # The last downloads of its video from this machine: {at, by, bytes, of, seconds, whole}.
    fetches: Mapped[list[dict]] = mapped_column(JSON, default=list)
    retry_of: Mapped[str | None] = mapped_column(String(12))
    retried_by: Mapped[str | None] = mapped_column(String(12))
    auto: Mapped[bool] = mapped_column(default=False)  # sent again by the editor itself
    created_at: Mapped[datetime] = mapped_column(default=_now)


class HostedFile(Base):
    """A video put on Cloudflare R2 for Buffer (hosting.py), until buffer_posts deletes it."""

    __tablename__ = "hosted_files"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    pub: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    deleted_at: Mapped[datetime | None]


class NicheSearch(Base):
    """One run of the niche account finder (niche.py) for one person."""

    __tablename__ = "niche_searches"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    niche: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[float] = mapped_column(default=0.0)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    # The schedule that started it; None when started by hand.
    schedule_id: Mapped[str | None] = mapped_column(String(12))
    accounts: Mapped[list["NicheAccount"]] = relationship(back_populates="search")


class NicheSchedule(Base):
    """A niche search that runs by itself at an hour (the server's local time) on chosen
    weekdays (Monday 0; none: every day), each run bringing accounts not shown before."""

    __tablename__ = "niche_schedules"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    niche: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    hour: Mapped[int] = mapped_column(default=7)
    days: Mapped[list[int]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)
    last_on: Mapped[date | None]  # the day its last run started
    created_at: Mapped[datetime] = mapped_column(default=_now)


class NicheAccount(Base):
    """An account the finder showed someone, once per person: it stays when its search is
    deleted (without a search). Shown again by a later search (niche.REPEAT_AFTER), it moves
    to that search. Not shown to the person again for a while once they opened a message to
    it."""

    __tablename__ = "niche_accounts"
    __table_args__ = (UniqueConstraint("owner_id", "handle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[str | None] = mapped_column(
        ForeignKey("niche_searches.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    handle: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text, default="")
    followers: Mapped[int | None]
    posts: Mapped[int | None]
    about: Mapped[str] = mapped_column(Text, default="")
    fit: Mapped[float | None]
    why: Mapped[str] = mapped_column(Text, default="")
    # "page", "person" or "business", as the model judged it; empty for older finds.
    kind: Mapped[str] = mapped_column(String(16), default="")
    vote: Mapped[int] = mapped_column(default=0)  # 1 liked, -1 not wanted
    reason: Mapped[str] = mapped_column(Text, default="")
    # Times this person opened a message to it from the finder, and the last time.
    dm_opens: Mapped[int] = mapped_column(default=0)
    dm_opened_at: Mapped[datetime | None]
    # Searches that showed it to this person, and when the last one did (None: when made).
    times_shown: Mapped[int] = mapped_column(default=1)
    shown_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=_now)
    search: Mapped[NicheSearch | None] = relationship(back_populates="accounts")


class ReelLog(Base):
    """One line per reel made, kept when its job is cleaned up: what the leaderboard, the
    streaks and the badges count. No link to the output, which may be gone."""

    __tablename__ = "reel_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(unique=True)
    owner_id: Mapped[str | None] = mapped_column(String(12), index=True)
    preset: Mapped[str] = mapped_column(String(64))
    song: Mapped[str] = mapped_column(Text, default="")
    artist: Mapped[str] = mapped_column(Text, default="")
    made_at: Mapped[datetime] = mapped_column(default=_now, index=True)
    captioned: Mapped[bool] = mapped_column(default=False)
    posted: Mapped[int] = mapped_column(default=0)


class Strike(Base):
    """A song a label blocked or took down on one of the pages: the strike list, one for
    every account (strikes.py). Russia-only mutes and blocks never go on it."""

    __tablename__ = "strikes"

    id: Mapped[int] = mapped_column(primary_key=True)
    song: Mapped[str] = mapped_column(Text)
    artist: Mapped[str] = mapped_column(Text, default="")
    label_group: Mapped[str] = mapped_column(String(8), default="other")  # songfacts.GROUPS
    what: Mapped[str] = mapped_column(String(16), default="blocked")  # strikes.WHATS
    note: Mapped[str] = mapped_column(Text, default="")  # "228 countries"
    # Who put it there (they and an admin may take it off); None: the list's first entry.
    added_by: Mapped[str | None] = mapped_column(String(12), index=True)
    job_id: Mapped[str | None] = mapped_column(String(12))  # the reel it happened to
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def make_sessionmaker(path: Path) -> sessionmaker:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record):
        cursor = connection.cursor()
        # WAL lets the SSE readers poll while the worker thread writes progress.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return sessionmaker(engine, expire_on_commit=False)


def add_missing_columns(engine) -> None:
    """create_all makes new tables but never touches existing ones; add the columns later
    versions introduced so an older database keeps working."""
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            have = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table.name})")
            }
            for column in table.columns:
                if column.name in have:
                    continue
                literal = "NULL"
                if column.server_default is not None:
                    literal = column.server_default.arg.text
                elif column.default is not None:
                    value = column.default.arg
                    # Callable defaults (list, dict) are wrapped to take a context argument.
                    value = value(None) if callable(value) else value
                    if isinstance(value, bool | int | float):
                        literal = str(int(value) if isinstance(value, bool) else value)
                    elif isinstance(value, list | dict | str):
                        text = value if isinstance(value, str) else json.dumps(value)
                        literal = "'" + text.replace("'", "''") + "'"
                kind = column.type.compile(engine.dialect)
                connection.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind} DEFAULT {literal}"
                )
