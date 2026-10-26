"""HTTP API and static frontend, served as one process together with the job worker."""

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import signal
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from agentic_editor import autopilot, jobs
from agentic_editor.auth import LoginGuard, verify_password
from agentic_editor.backends import get_backends
from agentic_editor.db import Event, Feed, FeedItem, Job, Output, Setting, StyleDraft
from agentic_editor.download import update_ytdlp
from agentic_editor.pipeline import UPLOAD_PREFIX
from agentic_editor.presets import Preset, load_presets, save_preset
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
NOTICE_KEY = "usage_notice_accepted"
UPLOAD_REF = re.compile(r"[0-9a-f]{20}/\w[\w-]*\.[a-z0-9]+")
SCAN_SECONDS = 300  # how often the autopilot looks at its feeds
# How long a shutdown waits for running jobs; any still running are failed at the next start.
SHUTDOWN_SECONDS = 30
PILE_LIMIT = 100
FAILED_LOGIN_DELAY = 1.0


class LoginRequest(BaseModel):
    password: str


class JobRequest(BaseModel):
    sources: list[str] = Field(min_length=1)
    presets: list[str] = Field(min_length=1)
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


class FeedRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["folder", "link"]
    location: str = Field(min_length=1)
    presets: list[str] = Field(min_length=1)
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


def job_view(job: Job, only: int | None = None) -> JobView:
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

    def user(request: Request) -> str:
        if request.session.get("user") != "owner":
            raise HTTPException(401, "log in first")
        return "owner"

    DB = Annotated[Session, Depends(db)]
    User = Annotated[str, Depends(user)]

    def find_job(job_id: str, session: Session) -> Job:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job

    def find_output(output_id: int, session: Session) -> Output:
        output = session.get(Output, output_id)
        if output is None:
            raise HTTPException(404, "no such output")
        return output

    def presets() -> dict[str, Preset]:
        return load_presets(settings.presets_dir)

    def enqueue(session: Session, **fields) -> Job:
        job = Job(**fields)
        session.add(job)
        session.commit()
        jobs.process(job.id)
        return job

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
        right = bool(settings.password_hash) and await asyncio.to_thread(
            verify_password, body.password, settings.password_hash
        )
        if not right:
            await asyncio.sleep(FAILED_LOGIN_DELAY)
            raise HTTPException(401, "wrong password")
        logins.succeeded(client)
        request.session["user"] = "owner"
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}

    @app.get("/api/me")
    def me(_: User, session: DB) -> dict:
        accepted = session.get(Setting, NOTICE_KEY) is not None
        return {"user": "owner", "notice_accepted": accepted}

    @app.post("/api/notice")
    def accept_notice(_: User, session: DB) -> dict:
        session.merge(Setting(key=NOTICE_KEY, value=datetime.now().isoformat()))
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
    async def ytdlp_update(_: User) -> dict:
        version = await asyncio.to_thread(update_ytdlp)
        return {"yt_dlp": version, "note": "restart the server to load the new version"}

    @app.get("/api/fonts")
    def fonts(_: User) -> list[str]:
        return sorted(p.stem for p in settings.fonts_dir.glob("*.[ot]tf"))

    @app.get("/api/presets")
    def list_presets(_: User) -> list[dict]:
        return [p.model_dump(mode="json") for p in presets().values()]

    @app.get("/api/presets/schema")
    def preset_schema(_: User) -> dict:
        return Preset.model_json_schema()

    @app.put("/api/presets/{name}")
    def put_preset(name: str, body: dict, _: User) -> dict:
        try:
            preset = Preset.model_validate({**body, "name": name})
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
        save_preset(preset, settings.presets_dir)
        return preset.model_dump(mode="json")

    @app.delete("/api/presets/{name}")
    def delete_preset(name: str, _: User) -> dict:
        path = settings.presets_dir / f"{name}.yaml"
        if name not in presets():
            raise HTTPException(404, "no such preset")
        path.unlink()
        return {"ok": True}

    @app.get("/api/presets/{name}/yaml")
    def preset_yaml(name: str, _: User) -> dict:
        preset = presets().get(name)
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
        return {"source": f"{UPLOAD_PREFIX}{name}", "name": file.filename}

    def check_source(source: str) -> None:
        if source.startswith(UPLOAD_PREFIX):
            if not UPLOAD_REF.fullmatch(source.removeprefix(UPLOAD_PREFIX)):
                raise HTTPException(422, "bad upload reference")
        elif not source.startswith(("http://", "https://")):
            raise HTTPException(422, f"not a link or upload: {source}")

    @app.post("/api/jobs")
    def create_jobs(body: JobRequest, _: User, session: DB) -> list[JobView]:
        unknown = set(body.presets) - presets().keys()
        if unknown:
            raise HTTPException(422, f"unknown preset(s): {', '.join(sorted(unknown))}")
        sources = list(dict.fromkeys(s.strip() for s in body.sources if s.strip()))
        for source in sources:  # all of them before any job starts
            check_source(source)
        created = []
        for source in sources:
            title = source if source.startswith("http") else ""
            created.append(
                enqueue(session, source=source, title=title, presets=body.presets, notes=body.notes)
            )
        return [job_view(j) for j in created]

    def draft_view(draft: StyleDraft) -> StyleDraftView:
        return StyleDraftView(**{k: getattr(draft, k) for k in StyleDraftView.model_fields})

    @app.post("/api/styles")
    def start_learning(body: LearnRequest, _: User, session: DB) -> StyleDraftView:
        source = body.source.strip()
        check_source(source)
        draft = StyleDraft(source=source, name=body.name)
        session.add(draft)
        session.commit()
        jobs.learn_style(draft.id)
        return draft_view(draft)

    @app.get("/api/styles/{draft_id}")
    def get_style(draft_id: str, _: User, session: DB) -> StyleDraftView:
        draft = session.get(StyleDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "no such draft")
        return draft_view(draft)

    @app.get("/api/jobs")
    def list_jobs(_: User, session: DB) -> list[JobView]:
        rows = session.scalars(
            select(Job)
            .options(selectinload(Job.outputs))
            .order_by(Job.created_at.desc())
            .limit(200)
        )
        return [job_view(j) for j in rows]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, _: User, session: DB) -> JobView:
        return job_view(find_job(job_id, session))

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, _: User, session: DB) -> dict:
        job = find_job(job_id, session)
        if job.status in ("queued", "running"):
            raise HTTPException(409, "the job is still running")
        session.delete(job)
        session.commit()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/regenerate")
    def regenerate(job_id: str, body: RegenerateRequest, _: User, session: DB) -> JobView:
        job = find_job(job_id, session)
        if body.preset not in presets():
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
        )
        return job_view(new)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, _: User):
        with make_session() as session:
            find_job(job_id, session)

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
    def video(output_id: int, _: User, session: DB, download: bool = False) -> FileResponse:
        output = find_output(output_id, session)
        name = download_name(output)
        return FileResponse(
            output.path, media_type="video/mp4", filename=f"{name}.mp4" if download else None
        )

    @app.get("/api/outputs/{output_id}/subtitles")
    def subtitles(output_id: int, _: User, session: DB) -> FileResponse:
        output = find_output(output_id, session)
        srt = Path(output.path).with_suffix(".srt")
        if not srt.exists():
            raise HTTPException(404, "no subtitles for this video")
        return FileResponse(
            srt, media_type="application/x-subrip", filename=f"{download_name(output)}.srt"
        )

    @app.get("/api/outputs/{output_id}/thumbnail")
    def thumbnail(
        output_id: int,
        _: User,
        session: DB,
        width: Annotated[int | None, Query(ge=120, le=1080)] = None,
    ) -> FileResponse:
        """The thumbnail, or a copy `width` px wide for a card, made once. Through a relay
        a dozen full 1080x1920 images is what makes a long list slow to appear, and asking
        again on every refresh only to hear they haven't changed costs a round trip each."""
        output = find_output(output_id, session)
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

    def find_feed(feed_id: int, session: Session) -> Feed:
        feed = session.get(Feed, feed_id)
        if feed is None:
            raise HTTPException(404, "no such feed")
        return feed

    def checked_feed(body: FeedRequest) -> FeedRequest:
        unknown = set(body.presets) - presets().keys()
        if unknown:
            raise HTTPException(422, f"unknown preset(s): {', '.join(sorted(unknown))}")
        location = body.location.strip()
        if body.kind == "folder":
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
    def list_feeds(_: User, session: DB) -> list[FeedView]:
        feeds = select(Feed).options(selectinload(Feed.items).selectinload(FeedItem.job))
        return [feed_view(f) for f in session.scalars(feeds.order_by(Feed.id))]

    @app.post("/api/feeds")
    def create_feed(body: FeedRequest, _: User, session: DB) -> FeedView:
        feed = Feed(**checked_feed(body).model_dump())
        session.add(feed)
        session.commit()
        # Look at once: what is already there is noted now, before anything new arrives.
        autopilot.check(feed.id, make_session, jobs.process)
        session.expire_all()
        return feed_view(find_feed(feed.id, session))

    @app.put("/api/feeds/{feed_id}")
    def update_feed(feed_id: int, body: FeedRequest, _: User, session: DB) -> FeedView:
        feed = find_feed(feed_id, session)
        updated = checked_feed(body)
        if (updated.kind, updated.location) != (feed.kind, feed.location):
            feed.looked = False  # somewhere new: what is already there is noted first
        for key, value in updated.model_dump().items():
            setattr(feed, key, value)
        session.commit()
        return feed_view(feed)

    @app.delete("/api/feeds/{feed_id}")
    def delete_feed(feed_id: int, _: User, session: DB) -> dict:
        session.delete(find_feed(feed_id, session))
        session.commit()
        return {"ok": True}

    @app.post("/api/feeds/{feed_id}/check")
    def check_feed(feed_id: int, _: User, session: DB) -> FeedView:
        find_feed(feed_id, session)
        autopilot.check(feed_id, make_session, jobs.process)
        session.expire_all()
        return feed_view(find_feed(feed_id, session))

    @app.get("/api/piles")
    def pile_counts(_: User, session: DB) -> dict[str, int]:
        counts = {"ready": 0, "look": 0}
        rows = session.execute(select(Output.pile, func.count()).group_by(Output.pile))
        counts.update({pile: n for pile, n in rows if pile})
        return counts

    @app.get("/api/piles/{pile}")
    def pile(pile: Literal["ready", "look"], _: User, session: DB) -> list[JobView]:
        """The outputs in a pile, newest first, each with its job."""
        outputs = session.scalars(
            select(Output)
            .where(Output.pile == pile)
            .options(selectinload(Output.job).selectinload(Job.outputs))
            .order_by(Output.id.desc())
            .limit(PILE_LIMIT)
        )
        return [job_view(o.job, only=o.id) for o in outputs]

    @app.post("/api/outputs/{output_id}/verdict")
    def verdict(output_id: int, body: VerdictRequest, _: User, session: DB) -> JobView:
        output = find_output(output_id, session)
        output.verdict = body.verdict
        session.commit()
        return job_view(output.job, only=output.id)

    if settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
