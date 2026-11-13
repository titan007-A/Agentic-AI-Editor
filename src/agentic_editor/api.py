"""HTTP API and static frontend, served as one process together with the job worker."""

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import shutil
import signal
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import httpx
import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from agentic_editor import (
    accounts,
    assets,
    autopilot,
    buffer,
    cleanup,
    eta,
    features,
    fun,
    instagram,
    jobs,
    research,
    songlist,
)
from agentic_editor.auth import LoginGuard, verify_password
from agentic_editor.backends import get_backends
from agentic_editor.db import (
    AccountSnapshot,
    Event,
    Feed,
    FeedItem,
    HiddenAccount,
    InstagramChannel,
    Job,
    NicheAccount,
    NicheSchedule,
    NicheSearch,
    Output,
    PresetBuffer,
    Research,
    ResearchAccount,
    ResearchItem,
    ResearchMessage,
    Setting,
    StyleDraft,
)
from agentic_editor.db import BufferKey as BufferKeyRow
from agentic_editor.db import User as Account
from agentic_editor.download import update_ytdlp
from agentic_editor.ffmpeg import probe as ffmpeg_probe
from agentic_editor.llm import LLMError
from agentic_editor.pipeline import UPLOAD_PREFIX
from agentic_editor.presets import Preset, load_presets, save_preset
from agentic_editor.render import preview
from agentic_editor.render.fonts import MissingGlyphs
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
# Files a preset uses (logo, intro/outro, music, LUT), by what they are for.
ASSET_SUFFIXES = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": VIDEO_SUFFIXES,
    "audio": {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"},
    "lut": {".cube"},
}
ASSET_LIMIT = 300 * 1024 * 1024
PUB_NAME = re.compile(r"[\w-]{32}\.mp4")
ASSET_NAME = re.compile(r"[0-9a-f]{12}-\w[\w-]*\.[a-z0-9]+")
NOTICE_KEY = "usage_notice_accepted"
ORIGINAL_NAME = ".name"  # next to an upload: the file name it was sent with
UPLOAD_REF = re.compile(r"[0-9a-f]{20}/\w[\w-]*\.[a-z0-9]+")
SCAN_SECONDS = 300  # how often the autopilot looks at its feeds
PDF_BYTES = 10 * 1024 * 1024  # the largest PDF of songs a research takes
# How long a shutdown waits for running jobs; any still running are failed at the next start.
SHUTDOWN_SECONDS = 30
PILE_LIMIT = 100
FAILED_LOGIN_DELAY = 1.0


class LoginRequest(BaseModel):
    username: str = ""
    password: str


class BufferKeyRequest(BaseModel):
    label: str = Field(default="", max_length=80)
    key: str = Field(min_length=8, max_length=4000)


class BufferLabelRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class PresetChannelsRequest(BaseModel):
    channels: list[str] = Field(max_length=100)


class BufferPostRequest(BaseModel):
    channels: list[str] = Field(min_length=1)
    text: str = ""
    mode: buffer.Mode = "addToQueue"
    due_at: datetime | None = None
    first_comment: str = ""
    # Keep these channels as the reel's preset's from now on.
    remember: bool = False
    # An Instagram trial reel: Buffer's API can't say so, so it goes in as a draft tagged
    # buffer.TRIAL_TAG, for the person to turn Trial on in Buffer (feature trial_reel).
    trial: bool = False


class ShareRequest(BaseModel):
    to: list[str] | None = None  # employees' account ids; none given: every employee


class AssignRequest(BaseModel):
    names: list[str]  # the admin's presets the employee should have
    # True: the ones they already have are replaced by the admin's current version too.
    replace: bool = False


class ChooseWordsRequest(BaseModel):
    words: str = Field(min_length=3, max_length=1000)


class ChooseRequest(BaseModel):
    keep: list[int] = Field(max_length=5000)  # positions in the research's listing
    swap: bool = False
    line: Literal["top", "bottom", "none"] = "top"
    presets: dict[str, str] = {}  # section -> preset
    per_research: int = Field(default=30, ge=1, le=60)
    kind: str = Field(default="", max_length=60)  # what the list is of; "" keeps the guess


class AccountRequest(BaseModel):
    username: str
    password: str
    role: Literal["member", "admin"] = "member"
    # The admin's own password, typed again to make another admin.
    my_password: str = ""


class PasswordRequest(BaseModel):
    password: str
    # The admin's own password, typed again to change another admin's.
    my_password: str = ""


class AccountView(BaseModel):
    id: str
    username: str
    role: str
    created_at: datetime
    last_seen_at: datetime | None
    jobs: int
    presets: int


# One preset a job (and a feed): the owner wants each video edited one way at a time.
class JobRequest(BaseModel):
    sources: list[str] = Field(min_length=1)
    presets: list[str] = Field(min_length=1, max_length=1)
    notes: str | None = None


class RegenerateRequest(BaseModel):
    preset: str
    notes: str | None = None


class LabelsRequest(BaseModel):
    # Preset (file) name -> the name shown for it, for the admin and every employee.
    labels: dict[str, str]


class KeepRequest(BaseModel):
    # Days a finished job and its videos are kept; 0 keeps them until deleted by hand.
    keep_days: int = Field(ge=0, le=cleanup.MAX_KEEP_DAYS)


class LearnRequest(BaseModel):
    source: str = Field(min_length=1)
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")


class StyleDraftView(BaseModel):
    id: str
    source: str
    status: str
    message: str
    preset: dict | None
    notes: list[str]
    error: str | None
    created_at: datetime


class ResearchRequest(BaseModel):
    # What to find; for an account, an optional focus.
    description: str = Field(default="", max_length=1000)
    account_id: str | None = None
    # Search at once, without the questions the researcher asks first.
    skip_questions: bool = False
    # Where to search: Instagram reels, YouTube Shorts, posts on X with a video; any of them.
    sites: list[Literal["instagram", "shorts", "x"]] = Field(
        default=["instagram", "shorts"], min_length=1, max_length=3
    )


class NicheRequest(BaseModel):
    """A niche account search: who to find, and how big they must be."""

    niche: str = Field(min_length=3, max_length=1000)
    min_followers: int = Field(default=0, ge=0, le=1_000_000_000)
    max_followers: int | None = Field(default=None, ge=0, le=1_000_000_000)
    language: str = Field(default="", max_length=60)
    country: str = Field(default="", max_length=60)


class NicheScheduleRequest(NicheRequest):
    hour: int = Field(ge=0, le=23)
    days: list[int] = Field(default=[], max_length=7)  # Monday 0; none: every day

    @model_validator(mode="after")
    def _days(self):
        if any(d < 0 or d > 6 for d in self.days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        self.days = sorted(set(self.days))
        return self


class NicheScheduleChange(BaseModel):
    active: bool | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    days: list[int] | None = Field(default=None, max_length=7)


class NicheVote(BaseModel):
    vote: Literal[-1, 0, 1]
    reason: str = Field(default="", max_length=300)


class Answer(BaseModel):
    question: str = Field(max_length=300)
    answer: str = Field(default="", max_length=500)


class AnswersRequest(BaseModel):
    answers: list[Answer] = Field(default=[], max_length=12)  # none: the questions skipped


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ResearchMessageView(BaseModel):
    id: int
    role: str
    text: str
    answered: bool
    follow_up_id: str | None
    created_at: datetime


class ResearchAccountRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=200)  # "@name", "name" or its link
    presets: list[str] = Field(min_length=1, max_length=50)


class ResearchAccountUpdate(BaseModel):
    presets: list[str] = Field(min_length=1, max_length=50)


class SourcesRequest(BaseModel):
    # Instagram @names or profile links, YouTube channel links.
    sources: list[str] = Field(max_length=research.MAX_SOURCES)


class DailyRequest(BaseModel):
    # The hour of the morning research, server time; None turns it off.
    hour: int | None = Field(ge=0, le=23)


class ResearchAccountView(BaseModel):
    id: str
    handle: str
    presets: list[str]
    summary: str
    learned_at: datetime | None
    own: int  # its own reels found
    best: list[dict]
    learn_status: str
    learn_message: str
    learn_error: str | None
    sources: list[str]  # pages it takes reels from: "instagram:name", "youtube:@name"
    daily_hour: int | None  # a research every day at this hour; None when off
    daily_on: date | None  # the day the last one started
    created_at: datetime


class ResearchItemView(BaseModel):
    id: int
    rank: int
    code: str
    url: str
    query: str
    account: str  # its display name
    handle: str  # its Instagram name
    caption: str
    likes: int | None
    comments: int | None
    views: int | None  # a YouTube Short's
    width: int | None  # the best version the site offers
    height: int | None
    bitrate: float | None  # kbit/s
    duration: float | None  # seconds, when the site says (YouTube does)
    posted_at: datetime | None
    thumbnail: str
    relevance: float | None
    why: str
    song: str
    matches: str
    preset: str
    preset_why: str
    per_day: float | None
    virality: float
    score: float
    seen: bool  # shown by an earlier research
    entry: int | None  # in a PDF research, the number of the song it is for
    used: bool  # edited since, by a job of the research's owner
    verdict: str | None  # the person's feedback: "good", "bad" or none yet
    verdict_reason: str
    hidden: bool  # its account is one the research's owner hid


class FeedbackRequest(BaseModel):
    verdict: Literal["good", "bad"] | None  # None takes the feedback back
    reason: str = Field(default="", max_length=300)
    # Leave its account out of later researches too.
    hide_account: bool = False


class OwnReelView(BaseModel):
    code: str
    url: str
    caption: str
    likes: int | None
    comments: int | None
    posted_at: datetime | None
    per_day: float | None  # likes and comments a day since it was posted
    gained: int | None  # likes since the snapshot before; None when it wasn't in that one


class PerformanceView(BaseModel):
    # How the account's own reels found were doing when it was last learned, and before.
    taken_at: datetime | None
    previous_at: datetime | None
    reels: list[OwnReelView]  # most liked first
    average: float | None  # likes a reel
    previous_average: float | None


class SongTrendView(BaseModel):
    song: str
    reels: int
    accounts: int
    per_day: float


class TrendsView(BaseModel):
    # Reels posted in the last two weeks, spreading fastest first, and songs several
    # accounts posted lately: from what the person's researches found.
    reels: list[ResearchItemView]
    songs: list[SongTrendView]


class HiddenAccountView(BaseModel):
    handle: str
    created_at: datetime


class ResearchView(BaseModel):
    id: str
    description: str
    account_id: str | None
    handle: str
    sites: list[str]
    daily: bool  # the account's morning research started it
    mode: str  # "search", or "pdf": songs from a PDF, each with its words above and below
    entries: list[dict]  # a PDF's songs: {"singer", "song", "top", "bottom"}
    # Every song the PDF lists, to choose from: shown with the research alone, not in lists.
    listing: list[dict] = []
    kind: str = "songs"  # what a PDF lists
    parent_id: str | None
    status: str
    message: str
    progress: float
    questions: list[dict]
    answers: list[dict]
    brief: str
    queries: list[str]
    used: int
    considered: int
    left_out: dict[str, int]
    cutoff: float | None
    error: str | None
    created_at: datetime
    found: int
    # Shown to the admin: the employee who asked for it.
    owner: str | None = None
    items: list[ResearchItemView] = []
    messages: list[ResearchMessageView] = []


class FeedRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["folder", "link"]
    location: str = Field(min_length=1)
    presets: list[str] = Field(min_length=1, max_length=1)
    notes: str | None = None
    enabled: bool = True
    active_from: int | None = Field(default=None, ge=0, le=23)
    active_to: int | None = Field(default=None, ge=0, le=23)
    batch: int = Field(default=3, ge=1, le=20)
    include_existing: bool = False


class FeedItemView(BaseModel):
    title: str
    source: str
    job_id: str | None
    status: str  # the job's, or skipped
    created_at: datetime


class FeedView(FeedRequest):
    id: int
    last_checked: datetime | None
    last_result: str | None
    counts: dict[str, int]
    recent: list[FeedItemView]


class CaptionRequest(BaseModel):
    # The song, when the video doesn't say which it is or the model named the wrong one;
    # or a topic, for a post that isn't about a song.
    song: str = Field(default="", max_length=200)
    artist: str = Field(default="", max_length=200)
    topic: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def one_subject(self) -> "CaptionRequest":
        if self.song.strip() and self.topic.strip():
            raise ValueError("name a song or a topic, not both")
        # Many songs share a name; the artist tells Wikipedia's articles apart.
        if self.song.strip() and not self.artist.strip():
            raise ValueError("name the artist too")
        return self


class VerdictRequest(BaseModel):
    verdict: Literal["approved", "posted", "dismissed"] | None


class OutputView(BaseModel):
    id: int
    preset: str
    # The preset's name as its owner sees it ("Iconic Spanish"), not its file name.
    preset_label: str = ""
    duration: float
    title: str
    planner: str
    renders: int
    issues: list[str]
    has_thumbnail: bool
    has_subtitles: bool
    confidence: float
    doubts: list[str]
    verdict: str | None = None
    pile: str | None = None
    edl: dict
    caption: str = ""
    caption_status: str | None = None
    caption_sources: list[dict] = []
    caption_notes: list[str] = []
    caption_song: str = ""
    caption_artist: str = ""
    caption_topic: str = ""


# A failure line in a job's error (pipeline.edit): "<preset>: UnnamedSong: ...".
UNNAMED = re.compile(r"([a-z0-9][a-z0-9-]*): UnnamedSong:")


class JobView(BaseModel):
    id: str
    source: str
    title: str
    presets: list[str]
    notes: str | None
    status: str
    stage: str
    progress: float
    error: str | None
    parent_id: str | None
    created_at: datetime
    outputs: list[OutputView] = []
    # Presets that couldn't name the song: the job page asks for it, then regenerates.
    unnamed: list[str] = []
    # The employee who started it, shown to the admin; None on a member's own list.
    owner: str | None = None
    # Roughly how long a queued or running job still has, in seconds (eta.py); None when
    # it can't be told or the job is over.
    eta_seconds: int | None = None


def job_view(
    job: Job,
    only: int | None = None,
    owner: str | None = None,
    labels: dict[str, str] | None = None,
    eta_seconds: int | None = None,
) -> JobView:
    outputs = [
        OutputView(
            id=o.id,
            preset=o.preset,
            preset_label=(labels or {}).get(o.preset, o.preset),
            duration=o.duration,
            title=o.title,
            planner=o.planner,
            renders=o.renders,
            issues=o.issues,
            has_thumbnail=bool(o.thumbnail),
            has_subtitles=Path(o.path).with_suffix(".srt").exists(),
            confidence=o.confidence,
            doubts=o.doubts,
            verdict=o.verdict,
            pile=o.pile,
            edl=o.edl,
            caption=o.caption or "",
            caption_status=o.caption_status,
            caption_sources=o.caption_sources or [],
            caption_notes=o.caption_notes or [],
            caption_song=o.caption_song or "",
            caption_artist=o.caption_artist or "",
            caption_topic=o.caption_topic or "",
        )
        for o in job.outputs
        if only is None or o.id == only
    ]
    return JobView(
        id=job.id,
        source=job.source,
        title=job.title,
        presets=job.presets,
        notes=job.notes,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        error=job.error,
        parent_id=job.parent_id,
        created_at=job.created_at,
        outputs=outputs,
        owner=owner,
        unnamed=UNNAMED.findall(job.error or ""),
        eta_seconds=eta_seconds,
    )


def feed_view(feed: Feed) -> FeedView:
    def status(item: FeedItem) -> str:
        if item.skipped:
            return "skipped"
        return item.job.status if item.job else "removed"

    counts: dict[str, int] = {}
    for item in feed.items:
        counts[status(item)] = counts.get(status(item), 0) + 1
    recent = [
        FeedItemView(
            title=i.title,
            source=i.source,
            job_id=i.job_id,
            status=status(i),
            created_at=i.created_at,
        )
        for i in feed.items
        if not i.skipped
    ][:10]
    fields = FeedRequest.model_fields.keys()
    return FeedView(
        **{k: getattr(feed, k) for k in fields},
        id=feed.id,
        last_checked=feed.last_checked,
        last_result=feed.last_result,
        counts=counts,
        recent=recent,
    )


def create_app(
    settings: Settings, session_factory: sessionmaker | None = None, start_worker: bool = True
) -> FastAPI:
    make_session = session_factory or jobs.sessions()

    async def scan_feeds() -> None:
        while True:
            try:
                await asyncio.to_thread(autopilot.check_all, make_session, jobs.process)
            except Exception:
                log.exception("checking the feeds failed")
            try:
                await asyncio.to_thread(jobs.start_daily, make_session)
            except Exception:
                log.exception("starting the morning researches failed")
            try:
                await asyncio.to_thread(jobs.start_niche_schedules, make_session)
            except Exception:
                log.exception("starting the scheduled niche searches failed")
            try:
                await asyncio.to_thread(cleanup.sweep, make_session, settings)
            except Exception:
                log.exception("deleting old videos failed")
            await asyncio.sleep(SCAN_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        consumer = None
        if start_worker:
            await asyncio.to_thread(jobs.recover, make_session)
            consumer = jobs.huey.create_consumer(
                workers=settings.max_concurrent_jobs,
                worker_type="thread",
                periodic=False,
                shutdown_timeout=SHUTDOWN_SECONDS,
            )
            # Consumer.start() installs its own SIGINT/SIGTERM/SIGHUP handlers, which would
            # stop uvicorn from ever seeing a shutdown request. Put uvicorn's back.
            handled = [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)]
            saved = {sig: signal.getsignal(sig) for sig in handled if sig is not None}
            consumer.start()
            for sig, handler in saved.items():
                signal.signal(sig, handler)
            log.info("job worker started with %d slot(s)", settings.max_concurrent_jobs)
            scanner = asyncio.create_task(scan_feeds())
        yield
        if consumer is not None:
            scanner.cancel()
            consumer.stop(graceful=True)

    app = FastAPI(title="Agentic AI Editor", lifespan=lifespan)
    secret = settings.secret_key or secrets.token_hex(32)
    app.add_middleware(
        SessionMiddleware, secret_key=secret, same_site="strict", max_age=30 * 24 * 3600
    )

    def db() -> Iterator[Session]:
        with make_session() as session:
            yield session

    def user(request: Request) -> Account:
        uid, version = request.session.get("uid"), request.session.get("v")
        if not uid:
            raise HTTPException(401, "log in first")
        with make_session() as session:
            account = session.get(Account, uid)
        # A changed password or a removed account ends the sessions signed in before it.
        if account is None or account.session_version != version:
            request.session.clear()
            raise HTTPException(401, "log in first")
        return account

    def admin(account: Annotated[Account, Depends(user)]) -> Account:
        if account.role != accounts.ADMIN:
            raise HTTPException(403, "only the admin can do that")
        return account

    DB = Annotated[Session, Depends(db)]
    User = Annotated[Account, Depends(user)]
    Admin = Annotated[Account, Depends(admin)]

    def mine(account: Account, owner_id: str | None) -> bool:
        """The admin sees everything; an employee only their own."""
        return account.role == accounts.ADMIN or owner_id == account.id

    def owners(session: Session) -> dict[str | None, str]:
        names: dict[str | None, str] = {a.id: a.username for a in session.scalars(select(Account))}
        first = next((a for a in accounts.admins(session)), None)
        names[None] = first.username if first else "admin"
        return names

    def preset_labels(
        owner_id: str | None, session: Session, cache: dict | None = None
    ) -> dict[str, str]:
        """The names the job's owner gave their presets, by file name. A list passes
        `cache`, so each owner's presets are read once."""
        if cache is not None and owner_id in cache:
            return cache[owner_id]
        owner = session.get(Account, owner_id) if owner_id else None
        found = {name: p.label for name, p in presets(owner).items()}
        if cache is not None:
            cache[owner_id] = found
        return found

    def view(
        job: Job,
        account: Account,
        session: Session,
        only: int | None = None,
        names: dict[str | None, str] | None = None,
        labels: dict | None = None,
        etas: dict[str, int] | None = None,
    ) -> JobView:
        """The job as its viewer sees it: the admin with who started it. A list passes
        `names`, `labels` (a cache) and `etas` worked out once, so a long one doesn't look
        each owner up again."""
        shown = preset_labels(job.owner_id, session, labels)
        left = None
        if job.status in ("queued", "running"):
            if etas is None:
                etas = eta.estimates(session, settings.max_concurrent_jobs)
            left = etas.get(job.id)
        if account.role != accounts.ADMIN:
            return job_view(job, only, labels=shown, eta_seconds=left)
        return job_view(job, only, (names or owners(session)).get(job.owner_id), shown, left)

    def find_job(job_id: str, session: Session, account: Account) -> Job:
        job = session.get(Job, job_id)
        if job is None or not mine(account, job.owner_id):
            raise HTTPException(404, "no such job")
        return job

    def find_output(output_id: int, session: Session, account: Account) -> Output:
        output = session.get(Output, output_id)
        if output is None or not mine(account, output.job.owner_id):
            raise HTTPException(404, "no such output")
        return output

    def whose(account: Account, owner: str | None, session: Session) -> Account | None:
        """The account whose presets a request is about: the caller's own, or, for the
        admin, any employee's named by id."""
        if not owner or owner == account.id:
            return account
        if account.role != accounts.ADMIN:
            raise HTTPException(403, "only the admin can see someone else's presets")
        target = session.get(Account, owner)
        if target is None:
            raise HTTPException(404, "no such account")
        return target

    def presets_dir(account: Account | None) -> Path:
        folder = accounts.presets_dir(account, settings.presets_dir)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def presets(account: Account | None) -> dict[str, Preset]:
        return load_presets(presets_dir(account))

    def bootstrap() -> None:
        """Before any account exists, the password in .env signs in as the admin, so
        nobody is locked out by the upgrade to accounts."""
        with make_session() as session:
            if session.scalar(select(func.count()).select_from(Account)):
                return
            if settings.password_hash:
                accounts.create(
                    session, "admin", None, accounts.ADMIN, settings.presets_dir,
                    password_hash=settings.password_hash,
                )  # fmt: skip

    def enqueue(session: Session, **fields) -> Job:
        job = Job(**fields)
        session.add(job)
        session.commit()
        jobs.process(job.id)
        return job

    bootstrap()
    logins = app.state.logins = LoginGuard()

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request) -> dict:
        client = request.client.host if request.client else ""
        wait = math.ceil(logins.wait(client))
        if wait:
            raise HTTPException(
                429,
                f"too many wrong passwords; try again in {wait} s",
                headers={"Retry-After": str(wait)},
            )
        # Counted before the check, so that many guesses sent at once are limited too.
        logins.failed(client)

        def check() -> Account | None:
            with make_session() as session:
                name = body.username
                if not name.strip():  # the old login form, with only a password
                    first = next(iter(accounts.admins(session)), None)
                    name = first.username if first else ""
                account = accounts.authenticate(session, name, body.password)
                if account is not None:
                    accounts.seen(session, account)
                return account

        account = await asyncio.to_thread(check)
        if account is None:
            await asyncio.sleep(FAILED_LOGIN_DELAY)
            raise HTTPException(401, "wrong username or password")
        logins.succeeded(client)
        request.session.clear()
        request.session.update({"uid": account.id, "v": account.session_version})
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}

    @app.get("/api/me")
    def me(account: User, session: DB) -> dict:
        accepted = session.get(Setting, f"{NOTICE_KEY}:{account.id}") is not None
        return {
            "user": account.username,
            "id": account.id,
            "role": account.role,
            "notice_accepted": accepted,
            "features": features.of(account, session),
        }

    @app.get("/api/fun")
    def fun_stats(me: User, session: DB) -> dict:
        """The leaderboard, streak and badges, for accounts trying the fun feature."""
        if not features.enabled(me, "fun", session):
            raise HTTPException(403, "not on for this account yet")
        if fun.backfill(session):
            session.commit()
        return fun.stats(session, me)

    @app.get("/api/fun/wrapped")
    def fun_wrapped(
        me: User,
        session: DB,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
    ) -> dict:
        """The person's month in numbers, for the Wrapped slides (fun feature)."""
        if not features.enabled(me, "fun", session):
            raise HTTPException(403, "not on for this account yet")
        return fun.wrapped(session, me, month)

    @app.post("/api/notice")
    def accept_notice(account: User, session: DB) -> dict:
        key = f"{NOTICE_KEY}:{account.id}"
        session.merge(Setting(key=key, value=datetime.now().isoformat()))
        session.commit()
        return {"ok": True}

    @app.get("/api/system")
    def system(_: Admin) -> dict:
        import yt_dlp.version

        backends = get_backends()
        return {
            "accelerator": backends.accelerator,
            "transcriber": backends.transcriber,
            "encoder": backends.encoder_name,
            "llm_model": settings.llm_model,
            "vision_model": settings.vision_model,
            "whisper_model": settings.whisper_model,
            "yt_dlp": yt_dlp.version.__version__,
        }

    @app.post("/api/system/update-ytdlp")
    async def ytdlp_update(_: Admin) -> dict:
        version = await asyncio.to_thread(update_ytdlp)
        return {"yt_dlp": version, "note": "restart the server to load the new version"}

    @app.get("/api/fonts")
    def fonts(_: User) -> list[str]:
        return sorted(p.stem for p in settings.fonts_dir.glob("*.[ot]tf"))

    @app.get("/api/presets")
    def list_presets(account: User, session: DB, owner: str | None = None) -> list[dict]:
        return [p.model_dump(mode="json") for p in presets(whose(account, owner, session)).values()]

    @app.get("/api/presets/schema")
    def preset_schema(_: User) -> dict:
        return Preset.model_json_schema()

    def everyone(session: Session) -> list[Account]:
        """The admins and every employee: whose preset folders a rename reaches."""
        return list(session.scalars(select(Account).order_by(Account.created_at)))

    def relabel(session: Session, name: str, label: str) -> list[str]:
        """Shows the preset `name` as `label` for everyone who has it: who that is. Only the
        name shown changes; each one's copy keeps its own settings."""
        renamed, folders = [], set()
        for account in everyone(session):
            folder = presets_dir(account)
            if folder in folders:
                continue  # every admin shares one folder
            folders.add(folder)
            found = load_presets(folder).get(name)
            if found is not None and found.label != label:
                save_preset(found.model_copy(update={"label": label}), folder)
                renamed.append(account.username)
        return renamed

    @app.get("/api/presets/names")
    def preset_names(_: Admin, session: DB) -> list[dict]:
        """Every preset anyone has, once: its name shown (the admin's, else the first
        employee's) and who has it."""
        found: dict[str, dict] = {}
        seen = set()
        for account in everyone(session):
            folder = presets_dir(account)
            if folder in seen:
                continue
            seen.add(folder)
            for name, preset in load_presets(folder).items():
                entry = found.setdefault(name, {"name": name, "label": preset.label, "who": []})
                entry["who"].append(account.username)
        return sorted(found.values(), key=lambda e: e["label"].casefold())

    @app.put("/api/presets/names")
    def rename_presets(body: LabelsRequest, _: Admin, session: DB) -> list[dict]:
        labels = {name: " ".join(label.split()) for name, label in body.labels.items()}
        if any(not label or len(label) > 120 for label in labels.values()):
            raise HTTPException(422, "a preset's name can't be empty or over 120 letters")
        for name, label in labels.items():
            relabel(session, name, label)
        return preset_names(_, session)

    @app.put("/api/presets/{name}")
    def put_preset(
        name: str, body: dict, account: User, session: DB, owner: str | None = None
    ) -> dict:
        target = whose(account, owner, session)
        try:
            preset = Preset.model_validate({**body, "name": name})
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
        before = presets(target).get(name)
        save_preset(preset, presets_dir(target))
        # The admin renaming one of their own presets renames it for every employee too.
        own = target is not None and target.role == accounts.ADMIN
        if own and before is not None and before.label != preset.label:
            relabel(session, name, preset.label)
        return preset.model_dump(mode="json")

    @app.post("/api/presets/preview")
    async def preview_preset(body: dict, account: User, shape: preview.Shape = "16:9") -> Response:
        """A still of what the preset being edited makes, unsaved changes and all."""
        try:
            preset = Preset.model_validate({"label": "preview", **body, "name": "preview"})
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
        try:
            image, notes = await asyncio.to_thread(preview.render_checked, preset, settings, shape)
        except MissingGlyphs as exc:
            raise HTTPException(422, f"The text has letters no font here can draw: {exc}") from exc
        return Response(
            image,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                # ASCII JSON, so it fits in a header.
                "X-Preview-Notes": json.dumps(notes),
                "Access-Control-Expose-Headers": "X-Preview-Notes",
            },
        )

    @app.delete("/api/presets/{name}")
    def delete_preset(name: str, account: User, session: DB, owner: str | None = None) -> dict:
        target = whose(account, owner, session)
        if name not in presets(target):
            raise HTTPException(404, "no such preset")
        folder = presets_dir(target)
        (folder / f"{name}.yaml").unlink()
        # Its Buffer channels go with it, for everyone who had it (admins share one folder).
        for account in everyone(session):
            if presets_dir(account) == folder:
                found = session.get(PresetBuffer, (account.id, name))
                if found is not None:
                    session.delete(found)
        session.commit()
        return {"ok": True}

    @app.post("/api/presets/{name}/share")
    def share_preset(name: str, me: Admin, session: DB, body: ShareRequest | None = None) -> dict:
        """Copy one of the admin's presets to the chosen employees (all of them when none are
        named), replacing theirs of that name: employees' presets are their own copies, so
        the admin's later changes don't reach them otherwise."""
        preset = presets(me).get(name)
        if preset is None:
            raise HTTPException(404, "no such preset")
        query = select(Account).where(Account.role == accounts.MEMBER)
        if body is not None and body.to is not None:
            wanted = set(body.to)
            if not wanted:
                raise HTTPException(422, "choose who gets it")
            query = query.where(Account.id.in_(wanted))
        members = list(session.scalars(query.order_by(Account.username)))
        if body is not None and body.to is not None and len(members) != len(set(body.to)):
            raise HTTPException(422, "send presets only to employees")
        for member in members:
            save_preset(preset, presets_dir(member))
        return {"sent_to": [m.username for m in members]}

    @app.get("/api/presets/{name}/yaml")
    def preset_yaml(name: str, account: User, session: DB, owner: str | None = None) -> dict:
        preset = presets(whose(account, owner, session)).get(name)
        if preset is None:
            raise HTTPException(404, "no such preset")
        return {
            "yaml": yaml.safe_dump(
                preset.model_dump(mode="json"), sort_keys=False, allow_unicode=True
            )
        }

    @app.post("/api/uploads")
    async def upload(file: UploadFile, _: User) -> dict:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(400, f"unsupported file type {suffix or '(none)'}")
        uploads = settings.media_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        partial = uploads / f".{secrets.token_hex(8)}.part"
        digest = hashlib.sha256()
        with partial.open("wb") as out:
            while chunk := await file.read(4 * 1024 * 1024):
                digest.update(chunk)
                out.write(chunk)
        # The folder is the content hash, so re-uploading a file overwrites it in place;
        # the original name is kept because it becomes the job title.
        stem = re.sub(r"[^\w-]+", "-", Path(file.filename or "").stem).strip("-") or "video"
        name = f"{digest.hexdigest()[:20]}/{stem[:80]}{suffix}"
        (uploads / name).parent.mkdir(exist_ok=True)
        partial.replace(uploads / name)
        # The stored name is made safe for a path; the title, which can carry a song and its
        # artist ("Song — Artist"), is read from the name as it was.
        (uploads / name).parent.joinpath(ORIGINAL_NAME).write_text(
            Path(file.filename or name).name[:200], encoding="utf-8"
        )
        return {"source": f"{UPLOAD_PREFIX}{name}", "name": file.filename}

    assets_dir = assets.folder(settings.media_dir)

    def check_asset(path: Path, kind: str) -> None:
        if kind == "image":
            try:
                with Image.open(path) as image:
                    image.verify()
            except Exception as exc:
                raise ValueError("this isn't a picture the editor can read") from exc
        elif kind == "lut":
            head = path.read_text(encoding="utf-8", errors="replace")[:20000]
            if "LUT_3D_SIZE" not in head:
                raise ValueError("this isn't a 3D .cube LUT (no LUT_3D_SIZE line)")
        else:
            try:
                info = ffmpeg_probe(path)
            except Exception as exc:
                raise ValueError("this file can't be read as video or sound") from exc
            if kind == "video" and not info.has_video:
                raise ValueError("this file has no picture")
            if kind == "audio" and not info.has_audio:
                raise ValueError("this file has no sound")

    @app.post("/api/assets")
    async def upload_asset(
        file: UploadFile, _: User, kind: Literal["image", "video", "audio", "lut"] = Form()
    ) -> dict:
        """A logo, intro/outro clip, music or LUT for a preset, kept under media/assets by
        its content, so the same file sent twice is stored once."""
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ASSET_SUFFIXES[kind]:
            allowed = ", ".join(sorted(ASSET_SUFFIXES[kind]))
            raise HTTPException(400, f"unsupported file type {suffix or '(none)'}; use {allowed}")
        assets_dir.mkdir(parents=True, exist_ok=True)
        partial = assets_dir / f".{secrets.token_hex(8)}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with partial.open("wb") as out:
                while chunk := await file.read(4 * 1024 * 1024):
                    size += len(chunk)
                    if size > ASSET_LIMIT:
                        raise HTTPException(413, "files for presets can be up to 300 MB")
                    digest.update(chunk)
                    out.write(chunk)
            try:
                await asyncio.to_thread(check_asset, partial, kind)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            dest = assets_dir / assets.name_for(digest.hexdigest(), file.filename or "", kind)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
        return {"path": assets.preset_value(dest), "name": file.filename}

    @app.get("/api/assets/{name}")
    def asset_file(name: str, _: User) -> FileResponse:
        """An uploaded preset file, for the editor to show."""
        path = assets_dir / name
        if not ASSET_NAME.fullmatch(name) or not path.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    def check_source(source: str) -> None:
        if source.startswith(UPLOAD_PREFIX):
            if not UPLOAD_REF.fullmatch(source.removeprefix(UPLOAD_PREFIX)):
                raise HTTPException(422, "bad upload reference")
        elif not source.startswith(("http://", "https://")):
            raise HTTPException(422, f"not a link or upload: {source}")

    @app.post("/api/jobs")
    def create_jobs(body: JobRequest, account: User, session: DB) -> list[JobView]:
        unknown = set(body.presets) - presets(account).keys()
        if unknown:
            raise HTTPException(422, f"unknown preset(s): {', '.join(sorted(unknown))}")
        sources = list(dict.fromkeys(s.strip() for s in body.sources if s.strip()))
        for source in sources:  # all of them before any job starts
            check_source(source)
        created = []
        for source in sources:
            title = source if source.startswith("http") else ""
            created.append(
                enqueue(
                    session,
                    source=source,
                    title=title,
                    presets=body.presets,
                    notes=body.notes,
                    owner_id=account.id,
                )
            )
        return [view(j, account, session) for j in created]

    def draft_view(draft: StyleDraft) -> StyleDraftView:
        return StyleDraftView(**{k: getattr(draft, k) for k in StyleDraftView.model_fields})

    @app.post("/api/styles")
    def start_learning(body: LearnRequest, account: User, session: DB) -> StyleDraftView:
        source = body.source.strip()
        check_source(source)
        draft = StyleDraft(source=source, name=body.name, owner_id=account.id)
        session.add(draft)
        session.commit()
        jobs.learn_style(draft.id)
        return draft_view(draft)

    @app.get("/api/styles/{draft_id}")
    def get_style(draft_id: str, account: User, session: DB) -> StyleDraftView:
        draft = session.get(StyleDraft, draft_id)
        if draft is None or not mine(account, draft.owner_id):
            raise HTTPException(404, "no such draft")
        return draft_view(draft)

    def find_research(research_id: str, session: Session, account: Account) -> Research:
        found = session.get(Research, research_id)
        if found is None or not mine(account, found.owner_id):
            raise HTTPException(404, "no such research")
        return found

    def item_views(
        items: list[ResearchItem], owner: str | None, account: Account, session: Session
    ) -> list[ResearchItemView]:
        """Reels researches gave, each marked when `owner` edited it since or hid its
        account."""
        used = jobs.reel_codes(session.scalars(select(Job.source).where(Job.owner_id == owner)))
        hidden = set(
            session.scalars(
                select(HiddenAccount.handle).where(HiddenAccount.owner_id == (owner or account.id))
            )
        )
        names = ResearchItemView.model_fields.keys() - {"used", "hidden"}
        return [
            ResearchItemView(
                **{k: getattr(i, k) for k in names},
                used=i.code in used,
                hidden=bool(i.handle) and i.handle in hidden,
            )
            for i in items
        ]

    def research_view(
        found: Research, account: Account, session: Session, items: bool = False
    ) -> ResearchView:
        fields = ResearchView.model_fields.keys() - {
            "found",
            "owner",
            "items",
            "messages",
            "listing",
        }
        view = ResearchView(
            **{k: getattr(found, k) for k in fields},
            found=len(found.items),
            owner=owners(session).get(found.owner_id) if account.role == accounts.ADMIN else None,
        )
        if items:
            view.listing = list(found.listing or []) if found.status == "choosing" else []
            view.items = item_views(found.items, found.owner_id, account, session)
            view.messages = [
                ResearchMessageView(**{k: getattr(m, k) for k in ResearchMessageView.model_fields})
                for m in found.messages
            ]
        return view

    # A person's Instagram accounts are theirs alone, the admin's too: each has its own
    # researcher, knowing their presets.
    def find_instagram(account_id: str, session: Session, account: Account) -> ResearchAccount:
        found = session.get(ResearchAccount, account_id)
        if found is None or found.owner_id != account.id:
            raise HTTPException(404, "no such Instagram account")
        return found

    def instagram_view(found: ResearchAccount) -> ResearchAccountView:
        fields = ResearchAccountView.model_fields.keys() - {"own"}
        return ResearchAccountView(**{k: getattr(found, k) for k in fields}, own=len(found.own))

    def checked_presets(names: list[str], account: Account) -> list[str]:
        mine_ = presets(account)
        if unknown := [n for n in names if n not in mine_]:
            raise HTTPException(422, f"unknown preset {', '.join(unknown)}")
        return list(dict.fromkeys(names))

    def learn_again(found: ResearchAccount, session: Session) -> None:
        found.learn_status, found.learn_message, found.learn_error = "queued", "", None
        session.commit()
        jobs.read_account(found.id)

    @app.get("/api/research/accounts")
    def list_instagram(account: User, session: DB) -> list[ResearchAccountView]:
        rows = session.scalars(
            select(ResearchAccount)
            .where(ResearchAccount.owner_id == account.id)
            .order_by(ResearchAccount.created_at)
        )
        return [instagram_view(a) for a in rows]

    @app.post("/api/research/accounts")
    def add_instagram(
        body: ResearchAccountRequest, account: User, session: DB
    ) -> ResearchAccountView:
        handle = research.handle_of(body.handle)
        if handle is None:
            raise HTTPException(422, "give the account as @name or its Instagram link")
        taken = select(ResearchAccount).where(
            ResearchAccount.owner_id == account.id, ResearchAccount.handle == handle
        )
        if session.scalars(taken).first() is not None:
            raise HTTPException(409, f"@{handle} is already one of your accounts")
        found = ResearchAccount(
            owner_id=account.id, handle=handle, presets=checked_presets(body.presets, account)
        )
        session.add(found)
        learn_again(found, session)
        return instagram_view(found)

    @app.put("/api/research/accounts/{account_id}")
    def update_instagram(
        account_id: str, body: ResearchAccountUpdate, account: User, session: DB
    ) -> ResearchAccountView:
        found = find_instagram(account_id, session, account)
        found.presets = checked_presets(body.presets, account)
        session.commit()
        return instagram_view(found)

    @app.get("/api/research/accounts/{account_id}/performance")
    def performance(account_id: str, account: User, session: DB) -> PerformanceView:
        found = find_instagram(account_id, session, account)
        snapshots = session.scalars(
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == found.id)
            .order_by(AccountSnapshot.taken_at.desc(), AccountSnapshot.id.desc())
            .limit(2)
        ).all()
        latest, previous = [*snapshots, None, None][:2]
        if latest is None:
            return PerformanceView(
                taken_at=None, previous_at=None, reels=[], average=None, previous_average=None
            )

        def average(reels: list[dict]) -> float | None:
            likes = [r["likes"] for r in reels if r.get("likes") is not None]
            return round(sum(likes) / len(likes), 1) if likes else None

        before = {r["code"]: r.get("likes") for r in previous.reels} if previous else {}
        now = datetime.now(UTC)
        reels = []
        for r in latest.reels:
            posted = datetime.fromisoformat(r["posted"]) if r.get("posted") else None
            reactions = (r.get("likes") or 0) + 2 * (r.get("comments") or 0)
            days = max(1.0, (now - posted).total_seconds() / 86400) if posted else None
            old = before.get(r["code"])
            reels.append(
                OwnReelView(
                    code=r["code"],
                    url=research.reel_url(r["code"]),
                    caption=r.get("caption") or "",
                    likes=r.get("likes"),
                    comments=r.get("comments"),
                    posted_at=posted,
                    per_day=round(reactions / days, 1) if days and reactions else None,
                    gained=r["likes"] - old
                    if old is not None and r.get("likes") is not None
                    else None,
                )
            )
        reels.sort(key=lambda r: r.likes or 0, reverse=True)
        return PerformanceView(
            taken_at=latest.taken_at,
            previous_at=previous.taken_at if previous else None,
            reels=reels,
            average=average(latest.reels),
            previous_average=average(previous.reels) if previous else None,
        )

    @app.put("/api/research/accounts/{account_id}/sources")
    def set_sources(
        account_id: str, body: SourcesRequest, account: User, session: DB
    ) -> ResearchAccountView:
        found = find_instagram(account_id, session, account)
        stored = []
        for given in body.sources:
            source = research.source_of(given)
            if source is None:
                raise HTTPException(
                    422, f"{given!r} isn't an Instagram @name or a YouTube channel link"
                )
            stored.append(source)
        found.sources = list(dict.fromkeys(stored))
        session.commit()
        return instagram_view(found)

    @app.put("/api/research/accounts/{account_id}/daily")
    def set_daily(
        account_id: str, body: DailyRequest, account: User, session: DB
    ) -> ResearchAccountView:
        found = find_instagram(account_id, session, account)
        found.daily_hour = body.hour
        session.commit()
        return instagram_view(found)

    @app.post("/api/research/accounts/{account_id}/learn")
    def relearn_instagram(account_id: str, account: User, session: DB) -> ResearchAccountView:
        found = find_instagram(account_id, session, account)
        if found.learn_status in ("queued", "running"):
            raise HTTPException(409, "it is being learned already")
        learn_again(found, session)
        return instagram_view(found)

    @app.delete("/api/research/accounts/{account_id}")
    def remove_instagram(account_id: str, account: User, session: DB) -> dict:
        found = find_instagram(account_id, session, account)
        if found.learn_status in ("queued", "running"):
            raise HTTPException(409, "it is being learned; try again in a minute")
        session.delete(found)
        session.commit()
        return {"ok": True}

    @app.post("/api/research")
    def start_research(body: ResearchRequest, account: User, session: DB) -> ResearchView:
        description = body.description.strip()
        handle = ""
        if body.account_id:
            handle = find_instagram(body.account_id, session, account).handle
        elif len(description) < 3:
            raise HTTPException(422, "say what to find, or choose one of your accounts")
        found = Research(
            description=description,
            owner_id=account.id,
            account_id=body.account_id or None,
            handle=handle,
            sites=list(dict.fromkeys(body.sites)),
        )
        if not body.skip_questions:
            found.status, found.message = "asking", "thinking of a few questions"
        session.add(found)
        session.commit()
        if body.skip_questions:
            jobs.find_reels(found.id)
        else:
            jobs.ask_questions(found.id)
        return research_view(found, account, session)

    async def pdf_text(file: UploadFile) -> str:
        if Path(file.filename or "").suffix.lower() != ".pdf":
            raise HTTPException(400, "give a PDF file")
        data = await file.read(PDF_BYTES + 1)
        if len(data) > PDF_BYTES:
            raise HTTPException(413, "the PDF is larger than 10 MB")
        try:
            text = await asyncio.to_thread(research.read_pdf, data)
        except Exception as exc:
            raise HTTPException(422, f"couldn't read the PDF: {exc}") from exc
        if len(text) < 3:
            raise HTTPException(422, "the PDF has no text to read (a scanned page is a picture)")
        return text

    @app.post("/api/research/pdf")
    async def research_pdf(
        file: UploadFile,
        account: User,
        session: DB,
        sites: Annotated[str, Form()] = "instagram,shorts",
    ) -> ResearchView:
        """A research of the songs a PDF lists, laid out any way: a numbered list is read
        by rule at once (see songlist.py), anything else by the model in the background
        ("reading"). Then it waits, "choosing", for the person to pick the songs and how
        (POST .../choose, or .../choose-by-words first), and only then searches."""
        chosen = [s for s in dict.fromkeys(sites.split(",")) if s in research.SITES]
        if not chosen:
            raise HTTPException(422, "search Instagram, YouTube Shorts, X or more of them")
        text = await pdf_text(file)
        listing = [e.as_dict() for e in research.listed(text)]
        found = Research(
            description=f"PDF: {Path(file.filename or 'songs.pdf').name[:120]}",
            owner_id=account.id, mode="pdf", pdf_text=text, sites=chosen, listing=listing,
            kind=research.guess_kind(text),
            status="choosing" if listing else "reading",
            message=(f"{len(listing)} songs found: choose which to research" if listing
                     else "reading the PDF"),
        )  # fmt: skip
        session.add(found)
        session.commit()
        if not listing:
            jobs.read_pdf_list(found.id)
        return research_view(found, account, session, items=True)

    def choosing(research_id: str, session: Session, account: Account) -> Research:
        found = find_research(research_id, session, account)
        if found.status != "choosing":
            raise HTTPException(409, "the research isn't waiting for songs to be chosen")
        return found

    @app.post("/api/research/{research_id}/choose-by-words")
    async def choose_by_words(
        research_id: str, body: ChooseWordsRequest, account: User, session: DB
    ) -> dict:
        """The songs and choices the person's words ask for (research.pdf_choose), for them
        to check and change before starting; nothing is started."""
        found = choosing(research_id, session, account)
        listing = list(found.listing)
        own = {name: p.label for name, p in presets(account).items()}

        def read() -> dict:
            router = ModelRouter(settings, get_backends(), lambda *args: None)
            with router.planner() as llm:
                return research.pdf_choose(llm, body.words, listing, own, found.kind)

        try:
            return await asyncio.to_thread(read)
        except (LLMError, httpx.HTTPError) as exc:
            raise HTTPException(
                503, f"the planning model couldn't read your words ({exc}); tick the songs"
            ) from exc

    @app.post("/api/research/{research_id}/choose")
    def choose_songs(
        research_id: str, body: ChooseRequest, account: User, session: DB
    ) -> ResearchView:
        """Start the research of the songs chosen (`keep`, positions in its listing), with
        the words under each song going `line`, song and singer swapped if `swap`, and a
        preset for each section: `per_research` songs a research, the first in this one,
        the rest in new ones, run one after another."""
        found = choosing(research_id, session, account)
        listing = list(found.listing)
        keep = list(dict.fromkeys(body.keep))
        if not keep:
            raise HTTPException(422, "choose at least one song")
        if any(not 0 <= i < len(listing) for i in keep):
            raise HTTPException(422, "a chosen song isn't in the PDF")
        known = presets(account)
        unknown = sorted({p for p in body.presets.values() if p and p not in known})
        if unknown:
            raise HTTPException(422, f"no such preset: {', '.join(unknown)}")
        kind = " ".join(body.kind.split()) or found.kind
        picked = songlist.entries_for(listing, keep, body.swap, body.line, body.presets)
        for e in picked:
            e["kind"] = kind
        parts = [
            picked[i : i + body.per_research] for i in range(0, len(picked), body.per_research)
        ]
        name = found.description
        researches = [found] + [
            Research(
                owner_id=found.owner_id,
                mode="pdf",
                pdf_text=found.pdf_text,
                sites=list(found.sites),
                description=name,
                kind=kind,
            )
            for _ in parts[1:]
        ]
        for one, part in zip(researches, parts, strict=True):
            spans: dict[str, tuple[int, int]] = {}
            for e in part:
                a, b = spans.get(e["section"], (e["number"], e["number"]))
                spans[e["section"]] = (min(a, e["number"]), max(b, e["number"]))
            what = ", ".join(f"{s} {a}-{b}" for s, (a, b) in spans.items())
            one.description = f"{name} · {what}"[:1000]
            one.entries, one.status, one.message = part, "queued", "waiting for a free slot"
            one.kind = kind
            session.add(one)
        session.commit()
        for one in researches:
            jobs.find_reels(one.id)
        return research_view(found, account, session, items=True)

    @app.post("/api/research/{research_id}/answers")
    def answer_questions(
        research_id: str, body: AnswersRequest, account: User, session: DB
    ) -> ResearchView:
        found = find_research(research_id, session, account)
        if found.status != "asking" or not found.questions:
            raise HTTPException(409, "the research isn't waiting for answers")
        asked = {q["question"] for q in found.questions}
        found.answers = [
            a.model_dump() for a in body.answers if a.question in asked and a.answer.strip()
        ]
        found.status, found.message = "queued", "waiting for a free slot"
        session.commit()
        jobs.find_reels(found.id)
        return research_view(found, account, session)

    @app.post("/api/research/{research_id}/chat")
    def chat_on_research(
        research_id: str, body: ChatRequest, account: User, session: DB
    ) -> ResearchView:
        found = find_research(research_id, session, account)
        if found.status != "done":
            raise HTTPException(409, "chat opens once the research is done")
        if any(m.role == "user" and not m.answered for m in found.messages):
            raise HTTPException(409, "the researcher is still answering")
        said = ResearchMessage(research_id=found.id, role="user", text=body.text.strip())
        session.add(said)
        session.commit()
        jobs.answer_chat(said.id)
        session.refresh(found)
        return research_view(found, account, session, items=True)

    @app.post("/api/research/items/{item_id}/feedback")
    def give_feedback(
        item_id: int, body: FeedbackRequest, account: User, session: DB
    ) -> ResearchView:
        """A thumbs up or down on a reel a research gave, which later researches of its
        owner learn from (see jobs.taste)."""
        item = session.get(ResearchItem, item_id)
        if item is None:
            raise HTTPException(404, "no such reel")
        found = find_research(item.research_id, session, account)
        item.verdict = body.verdict
        item.verdict_reason = body.reason.strip() if body.verdict == "bad" else ""
        if body.hide_account:
            if not item.handle:
                raise HTTPException(422, "its account isn't known, so it can't be hidden")
            owner = found.owner_id or account.id  # a research from before accounts: the admin's
            hidden = select(HiddenAccount).where(
                HiddenAccount.owner_id == owner, HiddenAccount.handle == item.handle
            )
            if session.scalars(hidden).first() is None:
                session.add(HiddenAccount(owner_id=owner, handle=item.handle))
        session.commit()
        session.refresh(found)
        return research_view(found, account, session, items=True)

    @app.get("/api/research/hidden")
    def list_hidden(account: User, session: DB) -> list[HiddenAccountView]:
        rows = session.scalars(
            select(HiddenAccount)
            .where(HiddenAccount.owner_id == account.id)
            .order_by(HiddenAccount.handle)
        )
        return [HiddenAccountView(handle=h.handle, created_at=h.created_at) for h in rows]

    @app.delete("/api/research/hidden/{handle}")
    def unhide(handle: str, account: User, session: DB) -> dict:
        found = session.scalars(
            select(HiddenAccount).where(
                HiddenAccount.owner_id == account.id, HiddenAccount.handle == handle
            )
        ).first()
        if found is None:
            raise HTTPException(404, "that account isn't hidden")
        session.delete(found)
        session.commit()
        return {"ok": True}

    @app.get("/api/research/trends")
    def research_trends(account: User, session: DB) -> TrendsView:
        since = datetime.now(UTC) - timedelta(days=research.TREND_DAYS)
        items = session.scalars(
            select(ResearchItem)
            .join(Research)
            .where(Research.owner_id == account.id, Research.created_at >= since)
            .order_by(Research.created_at.desc(), ResearchItem.rank)
        ).all()
        reels, songs = research.trends(items)
        return TrendsView(
            reels=item_views(reels, account.id, account, session),
            songs=[SongTrendView(**vars(t)) for t in songs],
        )

    @app.get("/api/research")
    def list_research(account: User, session: DB) -> list[ResearchView]:
        query = select(Research).options(selectinload(Research.items))
        if account.role != accounts.ADMIN:
            query = query.where(Research.owner_id == account.id)
        rows = session.scalars(query.order_by(Research.created_at.desc()).limit(50))
        return [research_view(r, account, session) for r in rows]

    @app.get("/api/research/{research_id}")
    def get_research(research_id: str, account: User, session: DB) -> ResearchView:
        found = find_research(research_id, session, account)
        return research_view(found, account, session, items=True)

    @app.delete("/api/research/{research_id}")
    def delete_research(research_id: str, account: User, session: DB) -> dict:
        found = find_research(research_id, session, account)
        if found.status in ("queued", "running"):
            raise HTTPException(409, "the research is still running")
        session.delete(found)
        session.commit()
        return {"ok": True}

    def niche_user(account: Account, session: Session) -> None:
        if not features.enabled(account, "niche_finder", session):
            raise HTTPException(403, "not on for this account yet")

    def niche_account_view(a: NicheAccount) -> dict:
        return {
            "id": a.id, "handle": a.handle, "name": a.name, "followers": a.followers,
            "posts": a.posts, "about": a.about, "fit": a.fit, "why": a.why, "vote": a.vote,
            "reason": a.reason, "url": f"https://www.instagram.com/{a.handle}/",
            "message_url": f"https://ig.me/m/{a.handle}",
        }  # fmt: skip

    def niche_view(found: NicheSearch, with_accounts: bool = False) -> dict:
        view = {
            "id": found.id, "niche": found.niche, "filters": found.filters,
            "status": found.status, "message": found.message, "progress": found.progress,
            "notes": found.notes or [], "error": found.error, "created_at": found.created_at,
            "count": len(found.accounts), "schedule_id": found.schedule_id,
        }  # fmt: skip
        if with_accounts:
            ordered = sorted(found.accounts, key=lambda a: (-(a.fit or 0), -(a.followers or 0)))
            view["accounts"] = [niche_account_view(a) for a in ordered]
        return view

    def find_niche(search_id: str, session: Session, account: Account) -> NicheSearch:
        found = session.get(NicheSearch, search_id)
        if found is None or found.owner_id != account.id:
            raise HTTPException(404, "no such search")
        return found

    @app.get("/api/niche")
    def list_niche(account: User, session: DB) -> list[dict]:
        niche_user(account, session)
        rows = session.scalars(
            select(NicheSearch)
            .where(NicheSearch.owner_id == account.id)
            .options(selectinload(NicheSearch.accounts))
            .order_by(NicheSearch.created_at.desc())
            .limit(50)
        )
        return [niche_view(r) for r in rows]

    @app.post("/api/niche")
    def start_niche(body: NicheRequest, account: User, session: DB) -> dict:
        niche_user(account, session)
        if body.max_followers is not None and body.max_followers < body.min_followers:
            raise HTTPException(422, "the most followers is below the fewest")
        found = NicheSearch(
            owner_id=account.id, niche=body.niche.strip(),
            filters=body.model_dump(exclude={"niche"}),
        )  # fmt: skip
        session.add(found)
        session.commit()
        jobs.find_accounts(found.id)
        return niche_view(found)

    def schedule_view(found: NicheSchedule) -> dict:
        return {
            "id": found.id, "niche": found.niche, "filters": found.filters, "hour": found.hour,
            "days": found.days or [], "active": found.active, "last_on": found.last_on,
            "created_at": found.created_at,
        }  # fmt: skip

    def find_schedule(schedule_id: str, session: Session, account: Account) -> NicheSchedule:
        found = session.get(NicheSchedule, schedule_id)
        if found is None or found.owner_id != account.id:
            raise HTTPException(404, "no such schedule")
        return found

    @app.get("/api/niche/schedules")
    def list_schedules(account: User, session: DB) -> list[dict]:
        niche_user(account, session)
        rows = session.scalars(
            select(NicheSchedule)
            .where(NicheSchedule.owner_id == account.id)
            .order_by(NicheSchedule.created_at.desc())
        )
        return [schedule_view(r) for r in rows]

    @app.post("/api/niche/schedules")
    def add_schedule(body: NicheScheduleRequest, account: User, session: DB) -> dict:
        niche_user(account, session)
        if body.max_followers is not None and body.max_followers < body.min_followers:
            raise HTTPException(422, "the most followers is below the fewest")
        found = NicheSchedule(
            owner_id=account.id, niche=body.niche.strip(), hour=body.hour, days=body.days,
            filters=body.model_dump(exclude={"niche", "hour", "days"}),
        )  # fmt: skip
        session.add(found)
        session.commit()
        return schedule_view(found)

    @app.patch("/api/niche/schedules/{schedule_id}")
    def change_schedule(
        schedule_id: str, body: NicheScheduleChange, account: User, session: DB
    ) -> dict:
        niche_user(account, session)
        found = find_schedule(schedule_id, session, account)
        if body.active is not None:
            found.active = body.active
        if body.hour is not None:
            found.hour = body.hour
        if body.days is not None:
            if any(d < 0 or d > 6 for d in body.days):
                raise HTTPException(422, "days are 0 (Monday) to 6 (Sunday)")
            found.days = sorted(set(body.days))
        session.commit()
        return schedule_view(found)

    @app.delete("/api/niche/schedules/{schedule_id}")
    def delete_schedule(schedule_id: str, account: User, session: DB) -> dict:
        niche_user(account, session)
        session.delete(find_schedule(schedule_id, session, account))
        session.commit()
        return {"ok": True}

    @app.get("/api/niche/{search_id}")
    def get_niche(search_id: str, account: User, session: DB) -> dict:
        niche_user(account, session)
        return niche_view(find_niche(search_id, session, account), with_accounts=True)

    @app.delete("/api/niche/{search_id}")
    def delete_niche(search_id: str, account: User, session: DB) -> dict:
        """The search goes; its accounts stay known, so they are never shown again."""
        niche_user(account, session)
        found = find_niche(search_id, session, account)
        if found.status in ("queued", "running"):
            raise HTTPException(409, "the search is still running")
        for a in found.accounts:
            a.search_id = None
        session.delete(found)
        session.commit()
        return {"ok": True}

    @app.post("/api/niche/accounts/{account_id}/vote")
    def vote_niche(account_id: int, body: NicheVote, account: User, session: DB) -> dict:
        niche_user(account, session)
        found = session.get(NicheAccount, account_id)
        if found is None or found.owner_id != account.id:
            raise HTTPException(404, "no such account")
        found.vote, found.reason = body.vote, body.reason.strip()
        session.commit()
        return niche_account_view(found)

    @app.get("/api/jobs")
    def list_jobs(account: User, session: DB) -> list[JobView]:
        query = select(Job).options(selectinload(Job.outputs))
        if account.role != accounts.ADMIN:
            query = query.where(Job.owner_id == account.id)
        rows = session.scalars(query.order_by(Job.created_at.desc()).limit(200))
        names = owners(session) if account.role == accounts.ADMIN else None
        labels: dict = {}
        etas = eta.estimates(session, settings.max_concurrent_jobs)
        return [view(j, account, session, names=names, labels=labels, etas=etas) for j in rows]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, account: User, session: DB) -> JobView:
        return view(find_job(job_id, session, account), account, session)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, account: User, session: DB) -> dict:
        job = find_job(job_id, session, account)
        if job.status in ("queued", "running"):
            raise HTTPException(409, "the job is still running")
        cleanup.delete_jobs(session, [job], settings.media_dir)
        return {"ok": True}

    @app.post("/api/jobs/clear")
    def clear_jobs(account: User, session: DB) -> dict:
        """Every finished job the account sees (the admin: everyone's) with its videos;
        ones still being edited stay."""
        query = select(Job).options(selectinload(Job.outputs))
        if account.role != accounts.ADMIN:
            query = query.where(Job.owner_id == account.id)
        found = session.scalars(query.where(Job.status.in_(cleanup.FINISHED))).all()
        deleted, freed = cleanup.delete_jobs(session, found, settings.media_dir)
        return {"deleted": deleted, "freed_mb": round(freed / 1e6, 1)}

    @app.get("/api/system/storage")
    def storage(_: Admin, session: DB) -> dict:
        return {"keep_days": cleanup.keep_days(session)}

    @app.put("/api/system/storage")
    def set_storage(body: KeepRequest, _: Admin, session: DB) -> dict:
        cleanup.set_keep_days(session, body.keep_days)
        return {"keep_days": body.keep_days}

    @app.post("/api/jobs/{job_id}/regenerate")
    def regenerate(job_id: str, body: RegenerateRequest, account: User, session: DB) -> JobView:
        job = find_job(job_id, session, account)
        # The job's owner's presets: an admin regenerating an employee's job uses theirs.
        owner = session.get(Account, job.owner_id) if job.owner_id else None
        if body.preset not in presets(owner):
            raise HTTPException(422, f"unknown preset {body.preset}")
        # The new notes say what should change; the earlier instruction still stands.
        notes = "\n".join(n.strip() for n in (job.notes, body.notes) if n and n.strip())
        new = enqueue(
            session,
            source=job.source,
            title=job.title,
            presets=[body.preset],
            notes=notes or None,
            parent_id=job.id,
            owner_id=job.owner_id,
        )
        return view(new, account, session)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, account: User):
        with make_session() as session:
            find_job(job_id, session, account)

        async def stream() -> AsyncIterator[str]:
            last = 0
            while not await request.is_disconnected():
                with make_session() as session:
                    events = session.scalars(
                        select(Event)
                        .where(Event.job_id == job_id, Event.id > last)
                        .order_by(Event.id)
                    ).all()
                    status = session.get(Job, job_id).status
                for e in events:
                    last = e.id
                    payload = {
                        "stage": e.stage,
                        "message": e.message,
                        "progress": e.progress,
                        "time": e.created_at.isoformat(),
                    }
                    yield f"id: {e.id}\ndata: {json.dumps(payload)}\n\n"
                if status in ("done", "failed"):
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    def download_name(output) -> str:
        return re.sub(r"[^\w.-]+", "-", f"{output.job.title or 'video'}-{output.preset}")[:100]

    @app.get("/api/outputs/{output_id}/video")
    def video(output_id: int, account: User, session: DB, download: bool = False) -> FileResponse:
        output = find_output(output_id, session, account)
        name = download_name(output)
        return FileResponse(
            output.path, media_type="video/mp4", filename=f"{name}.mp4" if download else None
        )

    # Posting to Buffer: everyone adds their own Buffer API keys, named, and links each preset
    # to Buffer channels. Buffer's limits are per Buffer account, so the editor asks it as
    # little as it can: channels are kept from when a key was added or refreshed.
    def buffer_rows(account: Account, session: Session) -> list[BufferKeyRow]:
        old = session.get(Setting, f"buffer_key:{account.id}")
        if old is not None:  # the one key of before, kept as a named key
            session.add(BufferKeyRow(owner_id=account.id, label="My Buffer", key=old.value))
            session.delete(old)
            session.commit()
        query = select(BufferKeyRow).where(BufferKeyRow.owner_id == account.id)
        return list(session.scalars(query.order_by(BufferKeyRow.created_at)))

    def spent(row: BufferKeyRow, count: buffer.Count) -> None:
        today = date.today().isoformat()
        if row.used_on != today:
            row.used_on, row.used = today, 0
        row.used += count.n

    async def refresh_key(row: BufferKeyRow) -> None:
        count = buffer.Count()
        try:
            found = await asyncio.to_thread(buffer.channels, row.key, None, count)
        except buffer.BufferError as exc:
            row.error = str(exc)
        else:
            row.channels = [vars(c) for c in found]
            row.error = ""
            row.refreshed_at = datetime.now(UTC)
        spent(row, count)

    def key_view(row: BufferKeyRow) -> dict:
        today = date.today().isoformat()
        return {
            "id": row.id,
            "label": row.label,
            "tail": row.key[-4:],
            "channels": [c | {"personal": buffer.personal(c)} for c in row.channels],
            "error": row.error,
            "refreshed_at": row.refreshed_at,
            "used_today": row.used if row.used_on == today else 0,
        }

    def find_key(key_id: str, account: Account, session: Session) -> BufferKeyRow:
        row = session.get(BufferKeyRow, key_id)
        if row is None or row.owner_id != account.id:
            raise HTTPException(404, "no such key")
        return row

    def preset_links(account: Account, session: Session) -> dict[str, list[str]]:
        query = select(PresetBuffer).where(PresetBuffer.owner_id == account.id)
        return {link.preset: link.channels for link in session.scalars(query)}

    def link_preset(account: Account, name: str, channels: list[str], session: Session) -> None:
        found = session.get(PresetBuffer, (account.id, name))
        if not channels:
            if found is not None:
                session.delete(found)
        elif found is None:
            session.add(PresetBuffer(owner_id=account.id, preset=name, channels=channels))
        else:
            found.channels = channels
        session.commit()

    def reachable(rows: list[BufferKeyRow]) -> dict[str, BufferKeyRow]:
        """Each channel and the key to post to it with: a working key first, else the one
        added first."""
        found: dict[str, BufferKeyRow] = {}
        for row in sorted(rows, key=lambda r: bool(r.error)):
            for channel in row.channels:
                found.setdefault(channel["id"], row)
        return found

    async def buffer_state(account: Account, session: Session) -> dict:
        rows = buffer_rows(account, session)
        for row in rows:
            if row.refreshed_at is None and not row.error:  # a key from before, never asked
                await refresh_key(row)
        session.commit()
        links = preset_links(account, session)
        return {
            "keys": [key_view(row) for row in rows],
            "presets": [
                {"name": p.name, "label": p.label, "channels": links.get(p.name, [])}
                for p in sorted(presets(account).values(), key=lambda p: p.label.casefold())
            ],
        }

    @app.get("/api/buffer")
    async def buffer_status(me: User, session: DB) -> dict:
        return await buffer_state(me, session)

    @app.post("/api/buffer/keys")
    async def add_buffer_key(body: BufferKeyRequest, me: User, session: DB) -> dict:
        key = body.key.strip()
        rows = buffer_rows(me, session)
        same = next((r for r in rows if r.key == key), None)
        if same is not None:
            raise HTTPException(422, f"you added this key already, as {same.label}")
        label = " ".join(body.label.split()) or f"Buffer {len(rows) + 1}"
        row = BufferKeyRow(owner_id=me.id, label=label, key=key)
        await refresh_key(row)
        if row.error:
            raise HTTPException(422, row.error)
        session.add(row)
        session.commit()
        return await buffer_state(me, session)

    @app.patch("/api/buffer/keys/{key_id}")
    async def rename_buffer_key(
        key_id: str, body: BufferLabelRequest, me: User, session: DB
    ) -> dict:
        label = " ".join(body.label.split())
        if not label:
            raise HTTPException(422, "give the key a name")
        find_key(key_id, me, session).label = label
        session.commit()
        return await buffer_state(me, session)

    @app.post("/api/buffer/keys/{key_id}/refresh")
    async def refresh_buffer_key(key_id: str, me: User, session: DB) -> dict:
        await refresh_key(find_key(key_id, me, session))
        session.commit()
        return await buffer_state(me, session)

    @app.delete("/api/buffer/keys/{key_id}")
    async def remove_buffer_key(key_id: str, me: User, session: DB) -> dict:
        session.delete(find_key(key_id, me, session))
        session.commit()
        return await buffer_state(me, session)

    @app.put("/api/buffer/presets/{name}")
    async def link_preset_channels(
        name: str, body: PresetChannelsRequest, me: User, session: DB
    ) -> dict:
        if name not in presets(me):
            raise HTTPException(404, "no such preset")
        wanted = list(dict.fromkeys(body.channels))
        known = reachable(buffer_rows(me, session))
        if any(c not in known for c in wanted):
            raise HTTPException(422, "none of your keys shows that channel; refresh the keys")
        link_preset(me, name, wanted, session)
        return await buffer_state(me, session)

    def poster(output: Output, me: Account, session: Session) -> Account:
        """Whose keys and preset links a reel is posted with: its owner's, also when an
        admin posts it for them."""
        owner = output.job.owner_id
        found = session.get(Account, owner) if owner else None
        return found or me

    @app.get("/api/outputs/{output_id}/buffer")
    async def output_buffer(output_id: int, me: User, session: DB) -> dict:
        output = find_output(output_id, session, me)
        who = poster(output, me, session)
        state = await buffer_state(who, session)
        preset = presets(who).get(output.preset)
        return {
            "keys": state["keys"],
            "preset": output.preset,
            "preset_label": preset.label if preset else output.preset,
            "linked": preset_links(who, session).get(output.preset, []),
            "owner": None if who.id == me.id else who.username,
        }

    pub_dir = settings.media_dir / "pub"

    def public_link(output: Output, request: Request) -> str:
        """A link anyone can open, for Buffer to fetch the video from when it posts: an
        unguessable name for a hard link to the file, so deleting the job keeps it."""
        base = (settings.public_url or str(request.base_url)).rstrip("/")
        if not base.startswith("https://"):
            raise HTTPException(
                422,
                "Buffer needs an https link to fetch the video: open the editor at its "
                "https address, or set PUBLIC_URL in .env",
            )
        pub_dir.mkdir(parents=True, exist_ok=True)
        name = f"{secrets.token_urlsafe(24)}.mp4"
        try:
            (pub_dir / name).hardlink_to(output.path)
        except OSError:
            shutil.copy2(output.path, pub_dir / name)
        return f"{base}/api/pub/{name}"

    @app.get("/api/pub/{name}")
    def public_video(name: str) -> FileResponse:
        """A video handed to another service; open to anyone with the link."""
        path = pub_dir / name
        if not PUB_NAME.fullmatch(name) or not path.is_file():
            raise HTTPException(404, "no such video")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/outputs/{output_id}/buffer")
    async def post_to_buffer(
        output_id: int, body: BufferPostRequest, request: Request, me: User, session: DB
    ) -> dict:
        output = find_output(output_id, session, me)
        who = poster(output, me, session)
        if body.mode == "customScheduled" and body.due_at is None:
            raise HTTPException(422, "pick a time to post it")
        if body.trial and not features.enabled(me, "trial_reel", session):
            raise HTTPException(403, "trial reels aren't on for this account yet")
        rows = buffer_rows(who, session)
        if not rows:
            raise HTTPException(409, "add a Buffer key on the Buffer page first")
        keys = reachable(rows)
        wanted = list(dict.fromkeys(body.channels))
        if any(c not in keys for c in wanted):
            raise HTTPException(
                422, "none of the keys shows that channel any more; refresh them on the Buffer page"
            )
        link = public_link(output, request)
        due = body.due_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        results = []
        for channel_id in wanted:
            row = keys[channel_id]
            channel = buffer.Channel(**next(c for c in row.channels if c["id"] == channel_id))
            count = buffer.Count()
            # A trial is an Instagram reel's; other channels get the post as asked.
            trial = body.trial and channel.service == "instagram"
            try:
                tags, note = [], ""
                if trial:
                    try:
                        tag = await asyncio.to_thread(
                            buffer.trial_tag, row.key, channel, None, count
                        )
                        tags = [tag]
                    except buffer.BufferError as exc:
                        if "refused the API key" in str(exc):
                            raise
                        note = f"saved without the {buffer.TRIAL_TAG} tag ({exc})"
                data = buffer.post_input(
                    channel, link, body.text, body.mode, due, body.first_comment,
                    draft=trial, tags=tags,
                )  # fmt: skip
                post = await asyncio.to_thread(buffer.create_post, row.key, data, None, count)
                result = {"channel": channel.name, "ok": True, "post": post}
                if trial:
                    result |= {"draft": True, "tagged": bool(tags), "note": note}
                results.append(result)
            except buffer.BufferError as exc:
                if "refused the API key" in str(exc):
                    row.error = str(exc)
                results.append({"channel": channel.name, "ok": False, "error": str(exc)})
            spent(row, count)
        if any(r["ok"] for r in results):
            fun.mark(session, output.id, posted=1)
        # Only the preset's own person changes its links, not an admin posting for them.
        mine_now = who.id == me.id
        if body.remember and mine_now and presets(who).get(output.preset) is not None:
            link_preset(who, output.preset, wanted, session)
        session.commit()
        log.info(
            "buffer: output %s by %s -> %s",
            output_id,
            who.username,
            [r | {"key": keys[c].label} for r, c in zip(results, wanted, strict=True)],
        )
        return {"results": results, "link": link}

    # ------------------------------------------------------------------ Instagram direct
    # OAuth: redirect to Instagram, handle callback, manage channels, post reels.
    # App must be configured: META_APP_ID + META_APP_SECRET in .env, and
    # https://<host>/auth/instagram/callback in the Meta App's Instagram product
    # "Valid OAuth redirect URIs".

    def _ig_redirect_uri(request: Request) -> str:
        base = (
            settings.public_url.rstrip("/")
            if settings.public_url
            else str(request.base_url).rstrip("/")
        )
        return f"{base}/auth/instagram/callback"

    def _ig_channel_view(ch: InstagramChannel) -> dict:
        return {
            "id": ch.id,
            "username": ch.username,
            "name": ch.name,
            "avatar": ch.avatar,
            "connected_at": ch.connected_at.isoformat() if ch.connected_at else None,
            "token_expires_at": ch.token_expires_at.isoformat() if ch.token_expires_at else None,
        }

    @app.get("/auth/instagram")
    def instagram_auth(request: Request, account: User, session: DB) -> RedirectResponse:
        if not settings.meta_app_id:
            raise HTTPException(503, "Meta App not configured (META_APP_ID missing in .env)")
        from secrets import token_urlsafe

        state = token_urlsafe(16)
        key = f"ig_oauth_state:{account.id}"
        existing = session.get(Setting, key)
        if existing:
            existing.value = state
        else:
            session.add(Setting(key=key, value=state))
        session.commit()
        url = instagram.auth_url(
            settings.meta_app_id, _ig_redirect_uri(request), state, settings.meta_config_id
        )
        return RedirectResponse(url)

    @app.get("/auth/instagram/callback")
    async def instagram_callback(
        request: Request,
        session: DB,
        code: str = "",
        state: str = "",
        error: str = "",
    ) -> RedirectResponse:
        if error:
            return RedirectResponse("/settings?ig_error=" + error)
        # Find the user by state token (no logged-in session at callback time)
        from sqlalchemy import select as sa_select

        rows = session.exec(  # type: ignore[call-overload]
            sa_select(Setting).where(Setting.key.like("ig_oauth_state:%"))  # type: ignore[union-attr]
        ).all()
        matched = next((r for r in rows if r.value == state), None)
        if not matched:
            return RedirectResponse("/settings?ig_error=invalid_state")
        owner_id = matched.key.split(":", 2)[2]
        session.delete(matched)
        try:
            redirect_uri = _ig_redirect_uri(request)
            short = instagram.exchange_code(
                settings.meta_app_id, settings.meta_app_secret, redirect_uri, code
            )
            token, expires_at = instagram.long_lived_token(settings.meta_app_secret, short)
            channel = instagram.get_account(token)
        except instagram.InstagramError as exc:
            log.warning("instagram oauth failed: %s", exc)
            return RedirectResponse(f"/settings?ig_error={str(exc)[:80]}")
        row = session.get(InstagramChannel, channel.id)
        if row is None:
            row = InstagramChannel(id=channel.id, owner_id=owner_id)
            session.add(row)
        row.username = channel.username
        row.name = channel.name
        row.avatar = channel.avatar
        row.access_token = token
        row.token_expires_at = expires_at
        session.commit()
        log.info("instagram: connected @%s for user %s", channel.username, owner_id)
        return RedirectResponse("/settings?ig_connected=1")

    @app.get("/api/instagram/channels")
    def list_ig_channels(account: User, session: DB) -> list[dict]:
        from sqlalchemy import select as sa_select

        rows = session.exec(  # type: ignore[call-overload]
            sa_select(InstagramChannel).where(InstagramChannel.owner_id == account.id)
        ).all()
        return [_ig_channel_view(ch) for ch in rows]

    @app.delete("/api/instagram/channels/{channel_id}")
    def remove_ig_channel(channel_id: str, account: User, session: DB) -> dict:
        ch = session.get(InstagramChannel, channel_id)
        if ch is None or ch.owner_id != account.id:
            raise HTTPException(404)
        session.delete(ch)
        session.commit()
        return {"ok": True}

    class IgPostRequest(BaseModel):
        channel_id: str
        caption: str = ""
        share_to_feed: bool = True

    @app.post("/api/outputs/{output_id}/instagram")
    async def post_to_instagram(
        output_id: int, body: IgPostRequest, account: User, session: DB, request: Request
    ) -> dict:
        output = find_output(output_id, session, account)
        ch = session.get(InstagramChannel, body.channel_id)
        if ch is None or ch.owner_id != account.id:
            raise HTTPException(404, "Instagram channel not found")
        # Build public video URL
        base = (
            settings.public_url.rstrip("/")
            if settings.public_url
            else str(request.base_url).rstrip("/")
        )
        vid_path = Path(output.path)
        vid_rel = vid_path.relative_to(settings.media_dir)
        video_url = f"{base}/media/{vid_rel.as_posix()}"
        chan = instagram.Channel(
            id=ch.id,
            username=ch.username,
            access_token=ch.access_token,
        )
        try:
            loop = asyncio.get_event_loop()
            post_id = await loop.run_in_executor(
                None,
                lambda: instagram.post_reel(chan, video_url, body.caption, body.share_to_feed),
            )
        except instagram.InstagramError as exc:
            raise HTTPException(502, str(exc)) from exc
        ch.last_post_at = datetime.now(UTC).replace(tzinfo=None)
        session.commit()
        log.info("instagram: posted output %s to @%s -> %s", output_id, ch.username, post_id)
        return {"post_id": post_id, "channel": ch.username}

    @app.get("/api/outputs/{output_id}/subtitles")
    def subtitles(output_id: int, account: User, session: DB) -> FileResponse:
        output = find_output(output_id, session, account)
        srt = Path(output.path).with_suffix(".srt")
        if not srt.exists():
            raise HTTPException(404, "no subtitles for this video")
        return FileResponse(
            srt, media_type="application/x-subrip", filename=f"{download_name(output)}.srt"
        )

    @app.get("/api/outputs/{output_id}/thumbnail")
    def thumbnail(
        output_id: int,
        account: User,
        session: DB,
        width: Annotated[int | None, Query(ge=120, le=1080)] = None,
    ) -> FileResponse:
        """The thumbnail, or a copy `width` px wide for a card, made once. Through a relay
        a dozen full 1080x1920 images is what makes a long list slow to appear, and asking
        again on every refresh only to hear they haven't changed costs a round trip each."""
        output = find_output(output_id, session, account)
        if not output.thumbnail:
            raise HTTPException(404, "no thumbnail")
        path = Path(output.thumbnail)
        if width:
            small = path.with_name(f"{path.stem}-{width}w.jpg")
            if not small.exists() or small.stat().st_mtime < path.stat().st_mtime:
                with Image.open(path) as image:
                    image.thumbnail((width, 4 * width))
                    image.convert("RGB").save(small, quality=82)
            path = small
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
        )

    def find_feed(feed_id: int, session: Session, account: Account) -> Feed:
        feed = session.get(Feed, feed_id)
        if feed is None or not mine(account, feed.owner_id):
            raise HTTPException(404, "no such feed")
        return feed

    def checked_feed(body: FeedRequest, owner: Account | None) -> FeedRequest:
        unknown = set(body.presets) - presets(owner).keys()
        if unknown:
            raise HTTPException(422, f"unknown preset(s): {', '.join(sorted(unknown))}")
        location = body.location.strip()
        if body.kind == "folder":
            # A folder feed reads this computer's disk, which only the admin may.
            if owner is not None and owner.role != accounts.ADMIN:
                raise HTTPException(403, "only the admin can watch a folder on this computer")
            folder = Path(location).expanduser()
            if not folder.is_dir():
                raise HTTPException(422, f"no such folder on this computer: {location}")
            location = str(folder.resolve())
        elif not location.startswith(("http://", "https://")):
            raise HTTPException(422, "a playlist or channel feed needs a link")
        if (body.active_from is None) != (body.active_to is None):
            raise HTTPException(422, "give both hours, or neither for any time")
        if body.active_from is not None and body.active_from == body.active_to:
            raise HTTPException(
                422, "the hours start and end at the same time; leave both out for any time"
            )
        notes = body.notes.strip() if body.notes else None
        return body.model_copy(update={"location": location, "notes": notes or None})

    @app.get("/api/feeds")
    def list_feeds(account: User, session: DB) -> list[FeedView]:
        feeds = select(Feed).options(selectinload(Feed.items).selectinload(FeedItem.job))
        if account.role != accounts.ADMIN:
            feeds = feeds.where(Feed.owner_id == account.id)
        return [feed_view(f) for f in session.scalars(feeds.order_by(Feed.id))]

    def look_now(feed: Feed, session: Session) -> None:
        """Check a feed now. An Instagram account takes a minute to look through (see
        autopilot.instagram_entries), so its check runs on its own while the page polls."""
        if feed.kind == "link" and autopilot.instagram_handle(feed.location):
            feed.last_result = "looking for the account's reels; this takes a minute"
            session.commit()
            threading.Thread(
                target=autopilot.check, args=(feed.id, make_session, jobs.process), daemon=True
            ).start()
        else:
            autopilot.check(feed.id, make_session, jobs.process)
        session.expire_all()

    @app.post("/api/feeds")
    def create_feed(body: FeedRequest, account: User, session: DB) -> FeedView:
        feed = Feed(**checked_feed(body, account).model_dump(), owner_id=account.id)
        session.add(feed)
        session.commit()
        # Look at once: what is already there is noted now, before anything new arrives.
        look_now(feed, session)
        return feed_view(find_feed(feed.id, session, account))

    @app.put("/api/feeds/{feed_id}")
    def update_feed(feed_id: int, body: FeedRequest, account: User, session: DB) -> FeedView:
        feed = find_feed(feed_id, session, account)
        owner = session.get(Account, feed.owner_id) if feed.owner_id else None
        updated = checked_feed(body, owner)
        if (updated.kind, updated.location) != (feed.kind, feed.location):
            feed.looked = False  # somewhere new: what is already there is noted first
        for key, value in updated.model_dump().items():
            setattr(feed, key, value)
        session.commit()
        return feed_view(feed)

    @app.delete("/api/feeds/{feed_id}")
    def delete_feed(feed_id: int, account: User, session: DB) -> dict:
        session.delete(find_feed(feed_id, session, account))
        session.commit()
        return {"ok": True}

    @app.post("/api/feeds/{feed_id}/check")
    def check_feed(feed_id: int, account: User, session: DB) -> FeedView:
        look_now(find_feed(feed_id, session, account), session)
        return feed_view(find_feed(feed_id, session, account))

    @app.get("/api/piles")
    def pile_counts(account: User, session: DB) -> dict[str, int]:
        counts = {"ready": 0, "look": 0}
        query = select(Output.pile, func.count()).join(Output.job)
        if account.role != accounts.ADMIN:
            query = query.where(Job.owner_id == account.id)
        rows = session.execute(query.group_by(Output.pile))
        counts.update({pile: n for pile, n in rows if pile})
        return counts

    @app.get("/api/piles/{pile}")
    def pile(pile: Literal["ready", "look"], account: User, session: DB) -> list[JobView]:
        """The outputs in a pile, newest first, each with its job."""
        query = select(Output).join(Output.job).where(Output.pile == pile)
        if account.role != accounts.ADMIN:
            query = query.where(Job.owner_id == account.id)
        outputs = session.scalars(
            query.options(selectinload(Output.job).selectinload(Job.outputs))
            .order_by(Output.id.desc())
            .limit(PILE_LIMIT)
        )
        names = owners(session) if account.role == accounts.ADMIN else None
        labels: dict = {}
        return [
            view(o.job, account, session, only=o.id, names=names, labels=labels) for o in outputs
        ]

    @app.get("/api/outputs/{output_id}")
    def output_detail(output_id: int, account: User, session: DB) -> JobView:
        output = find_output(output_id, session, account)
        return view(output.job, account, session, only=output.id)

    @app.post("/api/outputs/{output_id}/caption")
    def write_caption(
        output_id: int, account: User, session: DB, body: CaptionRequest | None = None
    ) -> JobView:
        """Write the post's caption in the background; the output shows how it goes. A
        song named here is the one it is about from now on."""
        output = find_output(output_id, session, account)
        if output.caption_status not in ("queued", "writing"):
            if body and body.topic.strip():
                output.caption_topic = body.topic.strip()
                output.caption_song = output.caption_artist = ""
            elif body and body.song.strip():
                output.caption_song = body.song.strip(" \"'“”")
                output.caption_artist = body.artist.strip()
                output.caption_topic = ""
            output.caption_status, output.caption_notes = "queued", []
            output.caption_queued_at = datetime.now(UTC)
            session.commit()
            jobs.write_caption(output.id)
        return view(output.job, account, session, only=output.id)

    @app.post("/api/outputs/{output_id}/verdict")
    def verdict(output_id: int, body: VerdictRequest, account: User, session: DB) -> JobView:
        output = find_output(output_id, session, account)
        output.verdict = body.verdict
        session.commit()
        return view(output.job, account, session, only=output.id)

    @app.get("/api/admin/users")
    def list_accounts(_: Admin, session: DB) -> list[AccountView]:
        counts = dict(
            session.execute(select(Job.owner_id, func.count()).group_by(Job.owner_id)).all()
        )
        first = next(iter(accounts.admins(session)), None)
        rows = session.scalars(select(Account).order_by(Account.created_at))
        return [
            AccountView(
                id=a.id,
                username=a.username,
                role=a.role,
                created_at=a.created_at,
                last_seen_at=a.last_seen_at,
                jobs=counts.get(a.id, 0) + (counts.get(None, 0) if a is first else 0),
                presets=len(list(presets_dir(a).glob("*.yaml"))),
            )
            for a in rows
        ]

    def confirm(me: Account, password: str, request: Request) -> None:
        """Making an admin, or changing an admin's password, takes the admin's own password
        typed again, so a session left open or stolen can't hand out the keys. Wrong ones
        count with wrong logins from the same address and wait the same way."""
        client = request.client.host if request.client else ""
        wait = math.ceil(logins.wait(client))
        if wait:
            raise HTTPException(
                429,
                f"too many wrong passwords; try again in {wait} s",
                headers={"Retry-After": str(wait)},
            )
        logins.failed(client)
        if not password or not verify_password(password, me.password_hash):
            time.sleep(FAILED_LOGIN_DELAY)
            # 403, not 401: the session is fine, and a 401 sends the page to the login.
            raise HTTPException(403, "that isn't your password; type your own to do this")
        logins.succeeded(client)

    @app.post("/api/admin/users")
    def create_account(
        body: AccountRequest, me: Admin, session: DB, request: Request
    ) -> AccountView:
        if body.role == accounts.ADMIN:
            confirm(me, body.my_password, request)
        try:
            account = accounts.create(
                session, body.username, body.password, body.role, settings.presets_dir
            )
        except accounts.AccountError as exc:
            raise HTTPException(422, str(exc)) from exc
        return AccountView(
            id=account.id, username=account.username, role=account.role,
            created_at=account.created_at, last_seen_at=None, jobs=0,
            presets=len(list(presets_dir(account).glob("*.yaml"))),
        )  # fmt: skip

    def find_account(account_id: str, session: Session) -> Account:
        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(404, "no such account")
        return account

    def assigned(target: Account, me: Account) -> dict:
        """The admin's presets, each marked if the employee has it, and the presets only
        the employee has (theirs alone; assigning never touches them)."""
        theirs = presets(target)
        mine = presets(me)
        return {
            "presets": [{"name": n, "label": p.label, "has": n in theirs} for n, p in mine.items()],
            "own": [{"name": n, "label": p.label} for n, p in theirs.items() if n not in mine],
        }

    def employee(account_id: str, session: Session) -> Account:
        target = find_account(account_id, session)
        if target.role != accounts.MEMBER:
            raise HTTPException(422, "admins share one set of presets: assign to employees")
        return target

    @app.get("/api/admin/users/{account_id}/presets")
    def assigned_presets(account_id: str, me: Admin, session: DB) -> dict:
        return assigned(employee(account_id, session), me)

    @app.put("/api/admin/users/{account_id}/presets")
    def assign_presets(account_id: str, body: AssignRequest, me: Admin, session: DB) -> dict:
        """Set which of the admin's presets one employee has: the ticked ones they lack are
        copied to them (and, with replace, the ones they have are refreshed), and the
        unticked ones they have are removed. Presets only they have are left alone."""
        target = employee(account_id, session)
        mine = presets(me)
        unknown = sorted(set(body.names) - set(mine))
        if unknown:
            raise HTTPException(422, f"no such preset: {', '.join(unknown)}")
        folder = presets_dir(target)
        theirs = presets(target)
        for name, preset in mine.items():
            if name in body.names and (body.replace or name not in theirs):
                save_preset(preset, folder)
            elif name not in body.names and name in theirs:
                (folder / f"{name}.yaml").unlink(missing_ok=True)
        return assigned(target, me)

    @app.post("/api/admin/users/{account_id}/password")
    def reset_password(
        account_id: str, body: PasswordRequest, me: Admin, session: DB, request: Request
    ) -> dict:
        target = find_account(account_id, session)
        if target.role == accounts.ADMIN:
            confirm(me, body.my_password, request)
        try:
            accounts.set_password(session, target, body.password)
        except accounts.AccountError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/admin/users/{account_id}")
    def remove_account(account_id: str, me: Admin, session: DB, delete_work: bool = False) -> dict:
        target = find_account(account_id, session)
        if target.id == me.id:
            raise HTTPException(422, "you can't remove your own account")
        if target.role == accounts.ADMIN:
            raise HTTPException(422, "an admin is removed from the Mac: aiedit user remove")
        busy = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.owner_id == target.id, Job.status.in_(("queued", "running")))
        )
        if busy:
            raise HTTPException(409, "their jobs are still running; wait or delete them first")
        keeper = None if delete_work else session.get(Account, me.id)
        accounts.remove(session, target, settings.presets_dir, keep_work_for=keeper)
        return {"ok": True}

    if settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
