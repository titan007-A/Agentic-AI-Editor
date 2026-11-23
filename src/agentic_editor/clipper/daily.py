"""Kick clips every morning (the Clipper tab's "Every morning"; owner, 2026-11-23: "daily
morning 8 ka timer", 15 GTA clips and 20 of the rest from the streamers' latest streams).

At its hour, once a day, an account's schedule takes its streamers' newest finished streams
it hasn't clipped yet (the newest one again when a streamer had none new: a hunt keeps away
from the clips already made of a stream, see hunt.clipped_before), looks at each (a few
seconds from four places in it): the vision model says whether it is GTA (as a topic feed
does, autopilot.judge_topic). `gta` clips are shared out over the GTA streams and
`footage` over the others, a streamer at a time in turn, and filed into the day's folders
"DD-MON-GTA" and "DD-MON-FOOTAGE" on the piles. Each clip gets its layout by what it shows
(hunt.clip_preset): gameplay under a camera the facecam band, anything else the whole
picture. The hunts run one after another in one worker, so the
others stay free for people's edits; each clip is an ordinary job in the fair queue.
"""

import json
import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentic_editor.clipper import hunt, kick
from agentic_editor.db import KickHunt, Setting, User

KEY = "kick_daily:"  # Setting key prefix, then the account's id
FACECAM = "kick-facecam-9x16"  # a camera box over a game: camera on top, the band, game below
FULL = "kick-clips"  # IRL, or the whole picture: full width, the band under it
SHORTEST_STREAM = 600  # seconds: a shorter stream is passed over
MOST_PER_HUNT = 8  # clips asked of one stream at most
SAMPLE_AT = (0.2, 0.4, 0.6, 0.8)  # where in a stream it is looked at
SAMPLE_SECONDS = 4.0
SAMPLE_HEIGHT = 720
TOPIC = "GTA"

log = logging.getLogger(__name__)


class Daily(BaseModel):
    """An account's morning Kick clips, kept as a Setting."""

    on: bool = False
    hour: int = Field(default=8, ge=0, le=23)  # local time
    streamers: list[str] = Field(default_factory=list, max_length=20)  # Kick names
    gta: int = Field(default=15, ge=0, le=60)
    footage: int = Field(default=20, ge=0, le=60)
    streams: int = Field(default=3, ge=1, le=5)  # newest finished streams looked at each
    last_on: str = ""  # the day it last ran (ISO date)
    last_result: str = ""

    @field_validator("streamers")
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        names = [" ".join(v.split()).lower().lstrip("@") for v in value]
        return list(dict.fromkeys(n for n in names if n))


def load(db: Session, owner_id: str) -> Daily:
    row = db.get(Setting, KEY + owner_id)
    if row is None:
        return Daily()
    try:
        return Daily.model_validate(json.loads(row.value))
    except ValueError:
        return Daily()


def save(db: Session, owner_id: str, daily: Daily) -> None:
    row = db.get(Setting, KEY + owner_id)
    value = daily.model_dump_json()
    if row is None:
        db.add(Setting(key=KEY + owner_id, value=value))
    else:
        row.value = value


def due(daily: Daily, now: datetime) -> bool:
    return (
        daily.on and bool(daily.streamers) and now.hour >= daily.hour
        and daily.last_on != now.date().isoformat()
    )  # fmt: skip


def start_due(Session: sessionmaker, now: datetime | None = None, queue=None) -> list[str]:
    """Start the morning clips of each account whose hour has come and that had none yet
    today (a server that was off then starts them once it is back): the accounts started."""
    if queue is None:
        from agentic_editor import jobs

        queue = jobs.kick_daily
    now = now or datetime.now()
    started = []
    with Session() as db:
        for row in db.scalars(select(Setting).where(Setting.key.like(KEY + "%"))).all():
            owner_id = row.key.removeprefix(KEY)
            daily = load(db, owner_id)
            if not due(daily, now):
                continue
            daily.last_on, daily.last_result = now.date().isoformat(), "starting"
            save(db, owner_id, daily)
            started.append(owner_id)
        db.commit()
    for owner_id in started:
        log.info("starting the morning Kick clips of %s", owner_id)
        queue(owner_id)
    return started


@dataclass
class Picked:
    """A stream chosen for today, as looked at."""

    slug: str
    username: str
    channel_id: int
    stream: kick.Stream
    gta: bool


def share(picked: list[Picked], total: int) -> list[tuple[Picked, int]]:
    """`total` clips over the streams, a streamer at a time in turn (each one's newest
    stream first, then its next), at most MOST_PER_HUNT from one stream."""
    by_streamer: dict[str, list[Picked]] = {}
    for p in picked:
        by_streamer.setdefault(p.slug, []).append(p)
    counts: dict[int, int] = {}
    turn = {slug: 0 for slug in by_streamer}
    left = total
    while left > 0:
        gave = False
        for slug, streams in by_streamer.items():
            if left == 0:
                break
            for _ in range(len(streams)):  # this streamer's next stream with room
                p = streams[turn[slug] % len(streams)]
                turn[slug] += 1
                if counts.get(id(p), 0) < MOST_PER_HUNT:
                    counts[id(p)] = counts.get(id(p), 0) + 1
                    left -= 1
                    gave = True
                    break
        if not gave:
            break  # every stream is full
    return [(p, counts[id(p)]) for p in picked if counts.get(id(p))]


def look(client, stream: kick.Stream, settings, router=None) -> bool | None:
    """Whether the stream is GTA, from a few seconds of it at SAMPLE_AT; None when it
    couldn't be told."""
    from agentic_editor import autopilot

    playlist = client.playlist(kick.best(client.variants(stream.source), SAMPLE_HEIGHT))
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for n, at in enumerate(SAMPLE_AT):
            start = playlist.duration * at
            part = Path(tmp) / f"part{n}.mp4"
            try:
                hunt.cut(playlist, start, start + SAMPLE_SECONDS, part)
                parts.append(part)
            except RuntimeError as exc:
                log.info("couldn't cut %s at %.0f s: %s", stream.uuid, start, exc)
        if not parts:
            return None
        sample = Path(tmp) / "sample.mp4"
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(
            [settings.ffmpeg_bin, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i",
             str(listing), "-c", "copy", str(sample)],
            check=True, capture_output=True, timeout=120,
        )  # fmt: skip
        entry = autopilot.Entry(f"kick-{stream.id}", stream.title, str(sample))
        try:
            return autopilot.judge_topic(TOPIC, [entry], settings, router)[0].fits
        except Exception as exc:  # the vision model away: run() goes by the title
            log.warning("couldn't look at %s's picture: %s", stream.uuid, exc)
            return None


def run(
    owner_id: str,
    Session: sessionmaker,
    settings,
    client=None,
    looker: Callable | None = None,
    hunter: Callable[[str], None] | None = None,
    now: datetime | None = None,
) -> str:
    """Today's clips for the account: streams picked and looked at, hunts made and run one
    after another (`hunter`, hunt.run by default). What was done, as said on the tab."""
    from agentic_editor import accounts, autopilot
    from agentic_editor.presets import load_presets

    now = now or datetime.now()
    own_client = client is None
    client = client or kick.Client()
    looker = looker or (lambda c, s: look(c, s, settings))
    hunter = hunter or (lambda hunt_id: hunt.run(hunt_id, Session, settings, client=client))
    try:
        with Session() as db:
            daily = load(db, owner_id)
            user = db.get(User, owner_id)
            have = load_presets(accounts.presets_dir(user, settings.presets_dir))
            hunted = {
                (h.slug, h.stream_id)
                for h in db.scalars(select(KickHunt).where(KickHunt.owner_id == owner_id))
            }
        editing = [n for n, p in have.items() if not p.download_only] or list(have)
        full = FULL if FULL in have else editing[0]
        chosen = FACECAM if FACECAM in have else full  # each clip picks its layout
        picked: list[Picked] = []
        notes: list[str] = []
        for slug in daily.streamers:
            try:
                channel = client.channel(slug)
                streams = [
                    s for s in client.streams(slug)
                    if s.source and not s.live and (s.duration or 0) >= SHORTEST_STREAM
                ][: daily.streams]  # fmt: skip
            except kick.KickError as exc:
                notes.append(f"{slug}: {exc}")
                continue
            new = [s for s in streams if (slug, s.id) not in hunted]
            for stream in new or streams[:1]:
                try:
                    gta = looker(client, stream)
                except Exception as exc:
                    log.warning("couldn't look at %s's stream %s: %s", slug, stream.uuid, exc)
                    notes.append(f"{slug}: a stream couldn't be looked at")
                    continue
                if gta is None:
                    gta = "gta" in stream.title.lower()  # the title, when the frames can't say
                picked.append(Picked(slug, channel.username, channel.id, stream, gta))
        day = autopilot.day_tag(now)
        plan = [
            (p, n, f"{day}-GTA") for p, n in share([p for p in picked if p.gta], daily.gta)
        ] + [
            (p, n, f"{day}-FOOTAGE") for p, n in share([p for p in picked if not p.gta],
                                                       daily.footage)
        ]  # fmt: skip
        made = []
        with Session() as db:
            for p, count, folder in plan:
                found = hunt.new_hunt(
                    db, owner_id, p.slug, p.username, p.channel_id, p.stream, 0.0, 0.0,
                    count, chosen, folder=folder, irl_preset=full,
                )  # fmt: skip
                made.append(found.id)
            db.commit()
        gta_n = sum(n for _, n, f in plan if f.endswith("GTA"))
        result = (
            f"{now:%d %b}: {gta_n} GTA clips and {sum(n for _, n, _ in plan) - gta_n} others "
            f"asked of {len(plan)} streams ({len({p.slug for p, _, _ in plan})} streamers)"
        )
        if notes:
            result += "; " + "; ".join(notes)
        _said(Session, owner_id, result + "; finding the moments")
        for hunt_id in made:
            hunter(hunt_id)
        _said(Session, owner_id, result)
        return result
    except Exception as exc:
        log.exception("the morning Kick clips of %s failed", owner_id)
        _said(Session, owner_id, f"failed: {exc}"[:300])
        raise
    finally:
        if own_client:
            client.close()


def _said(Session: sessionmaker, owner_id: str, message: str) -> None:
    with Session() as db:
        daily = load(db, owner_id)
        daily.last_result = message
        save(db, owner_id, daily)
        db.commit()
