"""Background jobs: one job is one source video rendered with one or more presets."""

import logging
from functools import lru_cache

from huey import SqliteHuey
from sqlalchemy.orm import sessionmaker

from agentic_editor import pipeline
from agentic_editor.backends import get_backends
from agentic_editor.db import Event, Job, Output, make_sessionmaker
from agentic_editor.presets import load_presets
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

_queue_file = get_settings().queue_database
_queue_file.parent.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey("agentic-editor", filename=str(_queue_file), results=False)


@lru_cache
def sessions() -> sessionmaker:
    return make_sessionmaker(get_settings().database)


class Tracker:
    """Turns pipeline progress callbacks into stored events and a rising progress value."""

    def __init__(self, Session: sessionmaker, job_id: str, preset_count: int):
        self.Session = Session
        self.job_id = job_id
        self.preset_count = preset_count
        self.finished = 0
        self.value = 0.0

    def __call__(self, stage: str, message: str) -> None:
        share = (1 - RENDER_START) / self.preset_count
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
    with Session() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        db.commit()
        source, preset_names, notes = job.source, list(job.presets), job.notes

    track = Tracker(Session, job_id, len(preset_names))
    try:
        available = load_presets(settings.presets_dir)
        missing = [name for name in preset_names if name not in available]
        if missing:
            raise ValueError(f"unknown preset(s): {', '.join(missing)}")
        presets = [available[name] for name in preset_names]
        result = run(source, presets, settings, get_backends(), notes, track)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with Session() as db:
            job = db.get(Job, job_id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            db.add(Event(job_id=job_id, stage="failed", message=job.error, progress=track.value))
            db.commit()
        return

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
        db.add(Event(job_id=job_id, stage="finished", message="all presets rendered", progress=1.0))
        db.commit()


@huey.task()
def process(job_id: str) -> None:
    run_job(job_id, sessions(), get_settings())
