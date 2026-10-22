"""HTTP API and static frontend, served as one process together with the job worker."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import signal
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from agentic_editor import jobs
from agentic_editor.auth import verify_password
from agentic_editor.backends import get_backends
from agentic_editor.db import Event, Job, Output, Setting
from agentic_editor.download import update_ytdlp
from agentic_editor.pipeline import UPLOAD_PREFIX
from agentic_editor.presets import Preset, load_presets, save_preset
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
NOTICE_KEY = "usage_notice_accepted"
UPLOAD_REF = re.compile(r"[0-9a-f]{20}/\w[\w-]*\.[a-z0-9]+")


class LoginRequest(BaseModel):
    password: str


class JobRequest(BaseModel):
    sources: list[str] = Field(min_length=1)
    presets: list[str] = Field(min_length=1)
    notes: str | None = None


class RegenerateRequest(BaseModel):
    preset: str
    notes: str | None = None


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


def job_view(job: Job) -> JobView:
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
            edl=o.edl,
        )
        for o in job.outputs
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


def create_app(
    settings: Settings, session_factory: sessionmaker | None = None, start_worker: bool = True
) -> FastAPI:
    make_session = session_factory or jobs.sessions()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        consumer = None
        if start_worker:
            consumer = jobs.huey.create_consumer(
                workers=settings.max_concurrent_jobs, worker_type="thread", periodic=False
            )
            # Consumer.start() installs its own SIGINT/SIGTERM/SIGHUP handlers, which would
            # stop uvicorn from ever seeing a shutdown request. Put uvicorn's back.
            handled = [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)]
            saved = {sig: signal.getsignal(sig) for sig in handled if sig is not None}
            consumer.start()
            for sig, handler in saved.items():
                signal.signal(sig, handler)
            log.info("job worker started with %d slot(s)", settings.max_concurrent_jobs)
        yield
        if consumer is not None:
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

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request) -> dict:
        if not settings.password_hash or not verify_password(body.password, settings.password_hash):
            await asyncio.sleep(1)
            raise HTTPException(401, "wrong password")
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

    @app.post("/api/jobs")
    def create_jobs(body: JobRequest, _: User, session: DB) -> list[JobView]:
        unknown = set(body.presets) - presets().keys()
        if unknown:
            raise HTTPException(422, f"unknown preset(s): {', '.join(sorted(unknown))}")
        created = []
        for source in dict.fromkeys(s.strip() for s in body.sources if s.strip()):
            if source.startswith(UPLOAD_PREFIX):
                if not UPLOAD_REF.fullmatch(source.removeprefix(UPLOAD_PREFIX)):
                    raise HTTPException(422, "bad upload reference")
            elif not source.startswith(("http://", "https://")):
                raise HTTPException(422, f"not a link or upload: {source}")
            title = source if source.startswith("http") else ""
            created.append(
                enqueue(session, source=source, title=title, presets=body.presets, notes=body.notes)
            )
        return [job_view(j) for j in created]

    @app.get("/api/jobs")
    def list_jobs(_: User, session: DB) -> list[JobView]:
        rows = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(200))
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
        new = enqueue(
            session,
            source=job.source,
            title=job.title,
            presets=[body.preset],
            notes=body.notes,
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
    def thumbnail(output_id: int, _: User, session: DB) -> FileResponse:
        output = find_output(output_id, session)
        if not output.thumbnail:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(output.thumbnail, media_type="image/jpeg")

    if settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
