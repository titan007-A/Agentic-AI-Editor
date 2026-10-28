"""Background jobs: one job is one source video rendered with one or more presets."""

import json
import logging
from functools import lru_cache
from pathlib import Path

from huey import SqliteHuey
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agentic_editor import accounts, caption, pipeline, style
from agentic_editor.backends import get_backends
from agentic_editor.db import Event, Job, Output, StyleDraft, User, make_sessionmaker
from agentic_editor.presets import PostCaption, load_presets
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings, get_settings

log = logging.getLogger(__name__)

STAGE_PROGRESS = {
    "download": 0.02,
    "analyze": 0.05,
    "separate": 0.10,
    "transcribe": 0.25,
    "plan": 0.45,
}
RENDER_START = 0.55
INTERRUPTED = "stopped when the server shut down; regenerate to run it again"

_queue_file = get_settings().queue_database
_queue_file.parent.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey("agentic-editor", filename=str(_queue_file), results=False)


@lru_cache
def sessions() -> sessionmaker:
    return make_sessionmaker(get_settings().database)


class Tracker:
    """Turns pipeline progress callbacks into stored events and a rising progress value."""

    def __init__(self, Session: sessionmaker, job_id: str, outputs: int):
        self.Session = Session
        self.job_id = job_id
        self.outputs = outputs  # videos the job is expected to make
        self.finished = 0
        self.value = 0.0
        self.stage = "download"

    def __call__(self, stage: str, message: str) -> None:
        if stage == "title":  # the source's own title, known once it is downloaded
            with self.Session() as db:
                db.get(Job, self.job_id).title = message
                db.commit()
            return
        # A job that got the GPU or encoder it waited for is back at the stage it was at.
        if stage == "resume":
            stage = self.stage
        elif stage != "wait":
            self.stage = stage
        share = (1 - RENDER_START) / self.outputs
        if stage == "done":
            self.finished += 1
            value = RENDER_START + share * self.finished
        elif stage in ("render", "review"):
            value = RENDER_START + share * (self.finished + (0.3 if stage == "render" else 0.7))
        else:
            value = STAGE_PROGRESS.get(stage, self.value)
        self.value = max(self.value, min(value, 0.99))
        with self.Session() as db:
            db.add(Event(job_id=self.job_id, stage=stage, message=message, progress=self.value))
            job = db.get(Job, self.job_id)
            job.stage, job.progress = stage, self.value
            db.commit()


def run_job(job_id: str, Session: sessionmaker, settings: Settings, run=pipeline.run) -> None:
    track = Tracker(Session, job_id, 1)
    # Everything is inside the try, the final write too: a job must never stay "running".
    try:
        with Session() as db:
            job = db.get(Job, job_id)
            job.status = "running"
            db.commit()
            source, preset_names, notes = job.source, list(job.presets), job.notes
            owner = db.get(User, job.owner_id) if job.owner_id else None
            folder = accounts.presets_dir(owner, settings.presets_dir)

        available = load_presets(folder)
        missing = [name for name in preset_names if name not in available]
        if missing:
            raise ValueError(f"unknown preset(s): {', '.join(missing)}")
        presets = [available[name] for name in preset_names]
        track.outputs = max(1, sum(p.hook.variants for p in presets))
        result = run(source, presets, settings, get_backends(), notes, track, run_id=job_id)

        with Session() as db:
            job = db.get(Job, job_id)
            job.title = result.title
            for out in result.outputs:
                job.outputs.append(
                    Output(
                        preset=out.preset,
                        path=str(out.path),
                        thumbnail=str(out.thumbnail) if out.thumbnail else None,
                        duration=round(out.edl.duration, 2),
                        title=out.edl.title,
                        planner=out.planner,
                        renders=out.renders,
                        issues=out.issues,
                        edl=out.edl.model_dump(mode="json"),
                        confidence=out.confidence,
                        doubts=out.doubts,
                    )
                )
            job.status, job.stage, job.progress = "done", "done", 1.0
            # Some versions made and some not is still a finished job; the rest is in its error.
            job.error = "; ".join(result.failures) or None
            message = "all presets rendered" if not result.failures else "rendered, with failures"
            db.add(Event(job_id=job_id, stage="finished", message=message, progress=1.0))
            captioned = [o for o in job.outputs if available[o.preset].post_caption.enabled]
            for output in captioned:
                output.caption_status = "queued"
            db.commit()
            for output in captioned:
                write_caption(output.id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with Session() as db:
            job = db.get(Job, job_id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            db.add(Event(job_id=job_id, stage="failed", message=job.error, progress=track.value))
            db.commit()


def source_text(output_path: Path, media_dir: Path) -> tuple[str, str]:
    """The title and description of the video an output was made from. Older outputs
    kept only the title next to them; the download's own record has the description."""
    meta_file = output_path.parent / "source.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    if not meta.get("description") and meta.get("url"):
        for other in (media_dir / "sources").glob("*/source.json"):
            found = json.loads(other.read_text(encoding="utf-8"))
            if found.get("url") == meta["url"]:
                meta = {**found, **{k: v for k, v in meta.items() if v}}
                break
    return meta.get("title", ""), meta.get("description", "")


def run_caption(output_id: int, Session: sessionmaker, settings: Settings, router=None) -> None:
    """Write an output's post caption (see caption.py). Like a job, it never stays
    "writing": whatever happens is stored."""
    try:
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption_status = "writing"
            db.commit()
            owner = db.get(User, output.job.owner_id) if output.job.owner_id else None
            available = load_presets(accounts.presets_dir(owner, settings.presets_dir))
            preset = available.get(output.preset)
            wanted = preset.post_caption if preset else PostCaption()
            decisions = output.edl.get("decisions", [])
            path = Path(output.path)
            named = output.caption_song, output.caption_artist
        headline = next((d.split(": ", 1)[1] for d in decisions if d.startswith("headline ")), "")
        title, description = source_text(path, settings.media_dir)
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        with router.planner() as llm:
            llm.context = caption.CONTEXT
            song, artist = (
                named if named[0] else (caption.identify(llm, title, description, headline))
            )
            with Session() as db:
                output = db.get(Output, output_id)
                output.caption_song, output.caption_artist = song, artist
                db.commit()
            sources = caption.gather(song, artist, wanted.language, settings.cache_dir / "captions")
            written = caption.write(
                llm, song, artist, sources, wanted.language, headline, wanted.style,
                (wanted.min_words, wanted.max_words),
            )  # fmt: skip
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption, output.caption_sources = written.text, written.sources
            output.caption_notes, output.caption_status = written.notes, "done"
            db.commit()
    except Exception as exc:
        log.exception("caption for output %s failed", output_id)
        reason = (
            str(exc) if isinstance(exc, caption.CaptionError) else f"{type(exc).__name__}: {exc}"
        )
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption_status, output.caption_notes = "failed", [reason]
            db.commit()


def unused_name(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def run_learning(
    draft_id: str, Session: sessionmaker, settings: Settings, learn=style.learn
) -> None:
    """Draft a preset from a reference reel. It runs on the job queue, so it never has
    the GPU at the same time as an edit."""

    def progress(stage: str, message: str) -> None:
        with Session() as db:
            db.get(StyleDraft, draft_id).message = message
            db.commit()

    try:
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.status = "running"
            db.commit()
            source, name = draft.source, draft.name
            owner = db.get(User, draft.owner_id) if draft.owner_id else None
            folder = accounts.presets_dir(owner, settings.presets_dir)

        learned = learn(source, settings, get_backends(), name, progress)
        preset = learned.preset
        if not name:
            taken = set(load_presets(folder)) if folder.is_dir() else set()
            preset = preset.model_copy(update={"name": unused_name(preset.name, taken)})
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.preset, draft.notes = preset.model_dump(mode="json"), learned.notes
            draft.status, draft.message = "done", "draft ready"
            db.commit()
    except Exception as exc:
        log.exception("learning style draft %s failed", draft_id)
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.status, draft.error = "failed", f"{type(exc).__name__}: {exc}"
            db.commit()


@huey.task()
def process(job_id: str) -> None:
    run_job(job_id, sessions(), get_settings())


@huey.task()
def learn_style(draft_id: str) -> None:
    run_learning(draft_id, sessions(), get_settings())


@huey.task()
def write_caption(output_id: int) -> None:
    run_caption(output_id, sessions(), get_settings())


PROCESS, LEARN = process.task_class.__name__, learn_style.task_class.__name__
CAPTION = write_caption.task_class.__name__


def recover(Session: sessionmaker) -> None:
    """Settle what a server that stopped mid-job left behind; run it before the worker
    starts. The queue drops a task once a worker takes it, so a job or draft still marked
    running will never finish: it is marked failed. One still queued whose task is gone
    (it never reached the queue) is queued again."""
    pending = {(task.name, task.args[0]) for task in huey.pending() if task.args}
    with Session() as db:
        for job in db.scalars(select(Job).where(Job.status == "running")).all():
            job.status, job.error = "failed", INTERRUPTED
            db.add(Event(job_id=job.id, stage="failed", message=INTERRUPTED, progress=job.progress))
        for draft in db.scalars(select(StyleDraft).where(StyleDraft.status == "running")).all():
            draft.status, draft.error = "failed", INTERRUPTED
        for output in db.scalars(select(Output).where(Output.caption_status == "writing")).all():
            output.caption_status, output.caption_notes = "failed", [INTERRUPTED]
        lost_captions = [
            output_id
            for output_id in db.scalars(select(Output.id).where(Output.caption_status == "queued"))
            if (CAPTION, output_id) not in pending
        ]
        lost_jobs = [
            job_id
            for job_id in db.scalars(select(Job.id).where(Job.status == "queued"))
            if (PROCESS, job_id) not in pending
        ]
        lost_drafts = [
            draft_id
            for draft_id in db.scalars(select(StyleDraft.id).where(StyleDraft.status == "queued"))
            if (LEARN, draft_id) not in pending
        ]
        db.commit()
    for job_id in lost_jobs:
        log.info("queueing job %s again", job_id)
        process(job_id)
    for draft_id in lost_drafts:
        log.info("queueing style draft %s again", draft_id)
        learn_style(draft_id)
    for output_id in lost_captions:
        log.info("queueing the caption of output %s again", output_id)
        write_caption(output_id)
