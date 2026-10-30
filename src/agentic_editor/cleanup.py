"""Keeping the server's disk free. A deleted job takes its videos with it, and jobs that
finished more than the keep time ago (an admin setting, 4 days unless changed; 0 keeps
everything) are deleted by themselves, with the downloads, uploads and analysis caches no
kept job needs. Only files inside the media and cache folders are ever touched: a folder
an autopilot feed reads from is the owner's own."""

import logging
import re
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentic_editor import download
from agentic_editor.db import Job, Setting
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

KEEP_DAYS = "keep_days"
DEFAULT_KEEP_DAYS = 4
MAX_KEEP_DAYS = 365
UPLOAD_PREFIX = "upload:"
FINISHED = ("done", "failed")
# An analysis cache folder: the video's file hash (analyze.file_hash).
CACHE_NAME = re.compile(r"^[0-9a-f]{20}$")


def keep_days(session: Session) -> int:
    found = session.get(Setting, KEEP_DAYS)
    try:
        return int(found.value) if found else DEFAULT_KEEP_DAYS
    except ValueError:
        return DEFAULT_KEEP_DAYS


def set_keep_days(session: Session, days: int) -> None:
    found = session.get(Setting, KEEP_DAYS)
    if found:
        found.value = str(days)
    else:
        session.add(Setting(key=KEEP_DAYS, value=str(days)))
    session.commit()


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _remove(path: Path) -> int:
    """Bytes freed by removing a file or folder; 0 when it was already gone."""
    if not path.exists():
        return 0
    if path.is_dir():
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path, ignore_errors=True)
    else:
        size = path.stat().st_size
        path.unlink(missing_ok=True)
    return size


def job_folders(job: Job, media_dir: Path) -> set[Path]:
    """The folders a job's videos were written to: media/outputs/<video>/<job id>, found by
    its outputs and by name, as a failed job has none but may have left part of one."""
    outputs = media_dir / "outputs"
    folders = {
        Path(output.path).parent
        for output in job.outputs
        if Path(output.path).parent.name == job.id
    }
    if outputs.is_dir():
        folders |= set(outputs.glob(f"*/{job.id}"))
    return {f for f in folders if f.is_dir() and _inside(f, outputs)}


def delete_jobs(session: Session, jobs: Iterable[Job], media_dir: Path) -> tuple[int, int]:
    """Deletes finished jobs with their videos: (jobs deleted, bytes freed). One still
    queued or running is left alone."""
    count = freed = 0
    for job in jobs:
        if job.status not in FINISHED:
            continue
        for folder in job_folders(job, media_dir):
            freed += _remove(folder)
            if folder.parent.exists() and not any(folder.parent.iterdir()):
                folder.parent.rmdir()
        session.delete(job)
        count += 1
    session.commit()
    return count, freed


def _older(path: Path, cutoff: float) -> bool:
    """Whether nothing in the folder (or the file) changed since `cutoff`."""
    try:
        times = [path.stat().st_mtime]
        if path.is_dir():
            times += [p.stat().st_mtime for p in path.iterdir()]
        return max(times) < cutoff
    except FileNotFoundError:
        return False


def sweep(Session: sessionmaker, settings: Settings, now: datetime | None = None) -> dict:
    """Deletes what is older than the keep time (see the module): what went, by kind."""
    now = now or datetime.now(UTC)
    with Session() as session:
        days = keep_days(session)
        if days <= 0:
            return {}
        cutoff = now - timedelta(days=days)
        old = session.scalars(
            select(Job).where(
                Job.status.in_(FINISHED), Job.updated_at < cutoff.replace(tzinfo=None)
            )
        ).all()
        jobs, freed = delete_jobs(session, old, settings.media_dir)
        kept = session.scalars(select(Job.source)).all()
        busy = session.scalars(select(Job.id).where(Job.status.in_(("queued", "running")))).all()
    stamp = cutoff.timestamp()
    gone = {"jobs": jobs}
    media = settings.media_dir

    # Downloads: a link kept by a job keeps its folder; the rest go once untouched.
    sources = media / "sources"
    links = sources / "links"
    wanted_links = {download._link_file(s, sources) for s in kept if "://" in s}
    wanted_folders = {
        sources / link.read_text(encoding="utf-8").strip() for link in wanted_links if link.exists()
    }
    count = 0
    if links.is_dir():
        for link in links.glob("*.txt"):
            if link not in wanted_links and _older(link, stamp):
                freed += _remove(link)
    if sources.is_dir():
        for folder in sources.iterdir():
            unused = folder.is_dir() and folder != links and folder not in wanted_folders
            if unused and _older(folder, stamp):
                freed += _remove(folder)
                count += 1
    gone["downloads"] = count

    # Uploads, by the folder each was saved in.
    uploads = media / "uploads"
    wanted_uploads = {
        s.removeprefix(UPLOAD_PREFIX).split("/", 1)[0] for s in kept if s.startswith(UPLOAD_PREFIX)
    }
    count = 0
    if uploads.is_dir():
        for folder in uploads.iterdir():
            if folder.is_dir() and folder.name not in wanted_uploads and _older(folder, stamp):
                freed += _remove(folder)
                count += 1
    gone["uploads"] = count

    # Videos of jobs deleted before their files went with them.
    outputs = media / "outputs"
    count = 0
    if outputs.is_dir():
        with Session() as session:
            ids = set(session.scalars(select(Job.id)).all())
        for video in outputs.iterdir():
            if not video.is_dir():
                continue
            for run in video.iterdir():
                if run.is_dir() and run.name not in ids and _older(run, stamp):
                    freed += _remove(run)
                    count += 1
            if not any(video.iterdir()):
                video.rmdir()
    gone["orphan outputs"] = count

    # Analysis caches only speed a video's next edit up; one in use is left alone.
    count = 0
    if not busy and settings.cache_dir.is_dir():
        for folder in settings.cache_dir.iterdir():
            if folder.is_dir() and CACHE_NAME.match(folder.name) and _older(folder, stamp):
                freed += _remove(folder)
                count += 1
    gone["caches"] = count
    gone["bytes"] = freed
    if any(v for k, v in gone.items() if k != "bytes"):
        log.info("kept %d days: deleted %s, %.1f MB freed", days, gone, freed / 1e6)
    return gone
