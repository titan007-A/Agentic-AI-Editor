"""HTTP API and static frontend, served as one process together with the job worker."""

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import signal
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from agentic_editor import accounts, autopilot, jobs, research
from agentic_editor.auth import LoginGuard
from agentic_editor.backends import get_backends
from agentic_editor.db import (
    AccountSnapshot,
    Event,
    Feed,
    FeedItem,
    HiddenAccount,
    Job,
    Output,
    Research,
    ResearchAccount,
    ResearchItem,
    ResearchMessage,
    Setting,
    StyleDraft,
)
from agentic_editor.db import User as Account
from agentic_editor.download import update_ytdlp
from agentic_editor.pipeline import UPLOAD_PREFIX
from agentic_editor.presets import Preset, load_presets, save_preset
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
NOTICE_KEY = "usage_notice_accepted"
ORIGINAL_NAME = ".name"  # next to an upload: the file name it was sent with
UPLOAD_REF = re.compile(r"[0-9a-f]{20}/\w[\w-]*\.[a-z0-9]+")
SCAN_SECONDS = 300  # how often the autopilot looks at its feeds
# How long a shutdown waits for running jobs; any still running are failed at the next start.
SHUTDOWN_SECONDS = 30
PILE_LIMIT = 100
FAILED_LOGIN_DELAY = 1.0


class LoginRequest(BaseModel):
    username: str = ""
    password: str


class AccountRequest(BaseModel):
    username: str
    password: str


class PasswordRequest(BaseModel):
    password: str


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
    # Where to search: Instagram reels, YouTube Shorts, or both.
    sites: list[Literal["instagram", "shorts"]] = Field(
        default=["instagram", "shorts"], min_length=1, max_length=2
    )


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


def job_view(job: Job, only: int | None = None, owner: str | None = None) -> JobView:
    outputs = [
        OutputView(
            id=o.id,
            preset=o.preset,
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

    def view(
        job: Job,
        account: Account,
        session: Session,
        only: int | None = None,
        names: dict[str | None, str] | None = None,
    ) -> JobView:
        """The job as its viewer sees it: the admin with who started it. A list passes
        `names` read once, so a long one doesn't look each owner up again."""
        if account.role != accounts.ADMIN:
            return job_view(job, only)
        return job_view(job, only, (names or owners(session)).get(job.owner_id))

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
        }

    @app.post("/api/notice")
    def accept_notice(account: User, session: DB) -> dict:
        key = f"{NOTICE_KEY}:{account.id}"
        session.merge(Setting(key=key, value=datetime.now().isoformat()))
        session.commit()
        return {"ok": True}

    @app.get("/api/system")
    def system(_: User) -> dict:
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

    @app.put("/api/presets/{name}")
    def put_preset(
        name: str, body: dict, account: User, session: DB, owner: str | None = None
    ) -> dict:
        target = whose(account, owner, session)
        try:
            preset = Preset.model_validate({**body, "name": name})
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
        save_preset(preset, presets_dir(target))
        return preset.model_dump(mode="json")

    @app.delete("/api/presets/{name}")
    def delete_preset(name: str, account: User, session: DB, owner: str | None = None) -> dict:
        target = whose(account, owner, session)
        if name not in presets(target):
            raise HTTPException(404, "no such preset")
        (presets_dir(target) / f"{name}.yaml").unlink()
        return {"ok": True}

    @app.post("/api/presets/{name}/share")
    def share_preset(name: str, me: Admin, session: DB) -> dict:
        """Copy one of the admin's presets to every employee, replacing theirs of that name:
        employees' presets are their own copies, so the admin's later changes don't reach
        them otherwise."""
        preset = presets(me).get(name)
        if preset is None:
            raise HTTPException(404, "no such preset")
        members = list(session.scalars(select(Account).where(Account.role == accounts.MEMBER)))
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
        fields = ResearchView.model_fields.keys() - {"found", "owner", "items", "messages"}
        view = ResearchView(
            **{k: getattr(found, k) for k in fields},
            found=len(found.items),
            owner=owners(session).get(found.owner_id) if account.role == accounts.ADMIN else None,
        )
        if items:
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

    @app.get("/api/jobs")
    def list_jobs(account: User, session: DB) -> list[JobView]:
        query = select(Job).options(selectinload(Job.outputs))
        if account.role != accounts.ADMIN:
            query = query.where(Job.owner_id == account.id)
        rows = session.scalars(query.order_by(Job.created_at.desc()).limit(200))
        names = owners(session) if account.role == accounts.ADMIN else None
        return [view(j, account, session, names=names) for j in rows]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, account: User, session: DB) -> JobView:
        return view(find_job(job_id, session, account), account, session)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, account: User, session: DB) -> dict:
        job = find_job(job_id, session, account)
        if job.status in ("queued", "running"):
            raise HTTPException(409, "the job is still running")
        session.delete(job)
        session.commit()
        return {"ok": True}

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
        return [view(o.job, account, session, only=o.id, names=names) for o in outputs]

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

    @app.post("/api/admin/users")
    def create_account(body: AccountRequest, _: Admin, session: DB) -> AccountView:
        # Only employees are made here: a stolen admin session can't make more admins.
        try:
            account = accounts.create(
                session, body.username, body.password, accounts.MEMBER, settings.presets_dir
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

    @app.post("/api/admin/users/{account_id}/password")
    def reset_password(account_id: str, body: PasswordRequest, me: Admin, session: DB) -> dict:
        target = find_account(account_id, session)
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
