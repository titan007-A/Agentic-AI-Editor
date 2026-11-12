"""The playful side for accounts trying the "fun" feature: a team leaderboard, day streaks
and badges, all counted from the reel log (db.ReelLog), which outlives cleaned-up jobs.
Days are India's (the team's), so a reel at 00:30 counts for the new day."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor.db import Job, Output, ReelLog
from agentic_editor.db import User as Account

ZONE = ZoneInfo("Asia/Kolkata")
# Reels from before accounts existed (no owner) were made by the owner's account.
OWNER = "siddhant"


@dataclass(frozen=True)
class Badge:
    id: str
    name: str
    description: str
    goal: int  # what `progress` counts up to
    # reels, day (most in one day), streak (days in a row), night, early (reels at those
    # hours), captions, posts, presets (different ones used)
    measure: str


BADGES = (
    Badge("first_reel", "Lights, camera", "Make your first reel", 1, "reels"),
    Badge("reels_10", "Warming up", "Make 10 reels", 10, "reels"),
    Badge("reels_100", "Century", "Make 100 reels", 100, "reels"),
    Badge("reels_500", "Reel machine", "Make 500 reels", 500, "reels"),
    Badge("reels_1000", "Legend", "Make 1000 reels", 1000, "reels"),
    Badge("day_10", "Busy day", "Make 10 reels in one day", 10, "day"),
    Badge("day_25", "Factory floor", "Make 25 reels in one day", 25, "day"),
    Badge("streak_3", "On a roll", "Make reels 3 days in a row", 3, "streak"),
    Badge("streak_7", "Week of fire", "Make reels 7 days in a row", 7, "streak"),
    Badge("streak_30", "Unstoppable", "Make reels 30 days in a row", 30, "streak"),
    Badge("night_owl", "Night owl", "Make a reel between midnight and 5 am", 1, "night"),
    Badge("early_bird", "Early bird", "Make a reel between 5 and 7 am", 1, "early"),
    Badge("captions_25", "Caption master", "Get 25 post captions written", 25, "captions"),
    Badge("posts_10", "Shipping it", "Send 10 reels to Buffer", 10, "posts"),
    Badge("variety_5", "All-rounder", "Make reels with 5 different presets", 5, "presets"),
)


def local(at: datetime) -> datetime:
    """A stored time (UTC, naive from SQLite) in India."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(ZONE)


def record(session: Session, outputs: list[Output], owner_id: str | None) -> None:
    """Log reels just made (their ids must be known: flush first)."""
    known = set(
        session.scalars(
            select(ReelLog.output_id).where(ReelLog.output_id.in_([o.id for o in outputs]))
        )
    )
    for output in outputs:
        if output.id in known:
            continue
        session.add(
            ReelLog(
                output_id=output.id,
                owner_id=owner_id,
                preset=output.preset,
                song=output.caption_song or "",
                artist=output.caption_artist or "",
                made_at=datetime.now(UTC),
            )
        )


def mark(session: Session, output_id: int, *, captioned: bool = False, posted: int = 0) -> None:
    row = session.scalar(select(ReelLog).where(ReelLog.output_id == output_id))
    if row is None:
        return
    if captioned:
        row.captioned = True
    row.posted += posted


def backfill(session: Session) -> int:
    """Log the reels still on the server from before the log was kept, once each, at their
    job's time. How many were added."""
    logged = select(ReelLog.output_id)
    missing = session.execute(
        select(Output, Job).join(Job, Output.job_id == Job.id).where(Output.id.not_in(logged))
    ).all()
    for output, job in missing:
        session.add(
            ReelLog(
                output_id=output.id,
                owner_id=job.owner_id,
                preset=output.preset,
                song=output.caption_song or "",
                artist=output.caption_artist or "",
                made_at=job.updated_at or job.created_at,
                captioned=output.caption_status == "done",
            )
        )
    return len(missing)


def streaks(days: set[date], today: date) -> tuple[int, int]:
    """The run of days in a row up to today (or yesterday, today not over yet) and the
    longest run ever."""
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return current, best


def badges(rows: list[ReelLog]) -> list[dict]:
    """Every badge, with how far along the person is and when it was won (None: not yet).
    Won at the reel that first made it true."""
    won: dict[str, datetime] = {}
    progress: Counter = Counter()
    per_day: Counter = Counter()
    days: set[date] = set()
    presets: set[str] = set()
    for row in sorted(rows, key=lambda r: r.made_at):
        at = local(row.made_at)
        per_day[at.date()] += 1
        days.add(at.date())
        presets.add(row.preset)
        progress["reels"] += 1
        progress["day"] = max(progress["day"], per_day[at.date()])
        progress["streak"] = max(progress["streak"], streaks(days, at.date())[0])
        progress["night"] += at.hour < 5
        progress["early"] += 5 <= at.hour < 7
        progress["captions"] += row.captioned
        progress["posts"] += row.posted
        progress["presets"] = len(presets)
        for badge in BADGES:
            if badge.id not in won and progress[badge.measure] >= badge.goal:
                won[badge.id] = row.made_at
    return [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "goal": b.goal,
            "progress": min(progress[b.measure], b.goal),
            "won_at": local(won[b.id]).isoformat() if b.id in won else None,
        }
        for b in BADGES
    ]


def stats(session: Session, me: Account, now: datetime | None = None) -> dict:
    """The person's counts, streak and badges, and the team's leaderboard for today, this
    week (from Monday) and this month."""
    now = local(now or datetime.now(UTC))
    today = now.date()
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    people = {a.id: a.username for a in session.scalars(select(Account))}
    owner = next((i for i, name in people.items() if name == OWNER), None)

    def who(owner_id: str | None) -> str | None:
        return people.get(owner_id or owner) if (owner_id or owner) else None

    rows = list(session.scalars(select(ReelLog)))
    mine = [r for r in rows if (r.owner_id or owner) == me.id]
    board: dict[str, Counter] = {"today": Counter(), "week": Counter(), "month": Counter()}
    for row in rows:
        name = who(row.owner_id)
        if name is None:
            continue
        day = local(row.made_at).date()
        if day == today:
            board["today"][name] += 1
        if day >= week:
            board["week"][name] += 1
        if day >= month:
            board["month"][name] += 1
    days = {local(r.made_at).date() for r in mine}
    current, best = streaks(days, today)
    return {
        "me": {
            "user": me.username,
            "today": sum(1 for r in mine if local(r.made_at).date() == today),
            "week": sum(1 for r in mine if local(r.made_at).date() >= week),
            "month": sum(1 for r in mine if local(r.made_at).date() >= month),
            "total": len(mine),
            "streak": current,
            "best_streak": best,
            "made_today": today in days,
            "captions": sum(r.captioned for r in mine),
            "posts": sum(r.posted for r in mine),
        },
        "leaderboard": {
            span: [{"user": u, "reels": n} for u, n in counts.most_common(10)]
            for span, counts in board.items()
        },
        "badges": badges(mine),
    }
