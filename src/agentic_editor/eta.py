"""How long a job still has, roughly, for the jobs list and the job page.

A running job is judged two ways and the two blended by how far it is: by how long jobs
with the same presets took lately (the median, so one stuck job doesn't skew it), and by
its own pace so far (time spent over the share done). A queued job waits for a free slot:
the running jobs' time left and the queued ones ahead of it are dealt out over the slots
in the order they were asked for, then its own usual time is added."""

from datetime import UTC, datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor.db import Job

HISTORY = 200  # finished jobs looked back at
PACE_FROM = 0.15  # the job's own pace is trusted from this share on
FLOOR = 5  # s: never say "no time left" while it still runs
OVERTIME = 30  # s: what's said when a job has run past its usual time and can't tell


def _aware(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def usual_times(session: Session) -> tuple[dict[tuple[str, ...], float], float | None]:
    """The median time recent jobs took, by their presets, and over all of them."""
    rows = session.execute(
        select(Job.presets, Job.started_at, Job.updated_at)
        .where(Job.status == "done", Job.started_at.is_not(None))
        .order_by(Job.updated_at.desc())
        .limit(HISTORY)
    ).all()
    by: dict[tuple[str, ...], list[float]] = {}
    every: list[float] = []
    for presets, started, ended in rows:
        took = (_aware(ended) - _aware(started)).total_seconds()
        if took <= 0:
            continue
        by.setdefault(tuple(presets), []).append(took)
        every.append(took)
    return {k: median(v) for k, v in by.items()}, (median(every) if every else None)


def left_running(job: Job, usual: float | None, now: datetime) -> int | None:
    started = _aware(job.started_at)
    elapsed = max(0.0, (now - started).total_seconds()) if started else 0.0
    done = max(0.0, min(1.0, job.progress or 0.0))
    by_history = max(0.0, usual - elapsed) if usual is not None else None
    by_pace = elapsed * (1 - done) / done if done >= PACE_FROM and elapsed > 0 else None
    if by_history is None and by_pace is None:
        return None
    if by_pace is None:
        left = by_history if by_history > 0 else OVERTIME
    elif by_history is None:
        left = by_pace
    else:
        left = done * by_pace + (1 - done) * by_history
    return max(FLOOR, round(left))


def estimates(session: Session, workers: int, now: datetime | None = None) -> dict[str, int]:
    """Seconds left for every queued or running job (those that can be told)."""
    now = now or datetime.now(UTC)
    by, overall = usual_times(session)
    active = list(
        session.scalars(
            select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.created_at)
        )
    )
    out: dict[str, int] = {}
    slots: list[float] = []
    for job in active:
        if job.status == "running":
            left = left_running(job, by.get(tuple(job.presets), overall), now)
            if left is not None:
                out[job.id] = left
            slots.append(left or 0)
    slots = sorted(slots)[:workers] + [0.0] * max(0, workers - len(slots))
    for job in active:
        if job.status != "queued":
            continue
        own = by.get(tuple(job.presets), overall)
        if own is None:
            continue
        slots.sort()
        start = slots[0]
        out[job.id] = round(start + own)
        slots[0] = start + own
    return out
