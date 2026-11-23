"""The Kick clipper's HTTP routes (feature "kick_clipper"), installed by api.create_app.

Streamers and hunts are their owner's; the primary account sees everyone's, as with
renders. A hunt is queued (jobs.hunt); the clips it makes are ordinary jobs, shown on the
Jobs page and in the hunt's folder on the piles.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_editor import features, jobs
from agentic_editor.clipper import daily, hunt, kick
from agentic_editor.db import Job, KickHunt, KickStreamer, Output

FEATURE = hunt.FEATURE
DEFAULT_PRESET = "kick-clips"
CLOCK = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+)$")


class StreamerRequest(BaseModel):
    name: str = Field(min_length=2, max_length=300)  # the streamer's name, or a Kick link
    live: bool = False
    preset: str = Field(default="", max_length=64)
    # Clips that aren't gameplay under a camera ("": kick-clips when there is one).
    irl_preset: str = Field(default="", max_length=64)
    per_hour: int = Field(default=4, ge=1, le=12)
    folder: str = Field(default="", max_length=80)


class StreamerChange(BaseModel):
    live: bool | None = None
    preset: str | None = Field(default=None, max_length=64)
    irl_preset: str | None = Field(default=None, max_length=64)
    per_hour: int | None = Field(default=None, ge=1, le=12)
    folder: str | None = Field(default=None, max_length=80)


class DailyRequest(BaseModel):
    on: bool = False
    hour: int = Field(default=8, ge=0, le=23)
    streamers: list[str] = Field(default_factory=list, max_length=20)  # names or links
    gta: int = Field(default=15, ge=0, le=60)
    footage: int = Field(default=20, ge=0, le=60)
    streams: int = Field(default=3, ge=1, le=5)


class HuntRequest(BaseModel):
    link: str = Field(min_length=2, max_length=300)  # a stream's link, or a streamer (live)
    start: str = Field(default="", max_length=12)  # h:mm:ss into the stream; "" its start
    end: str = Field(default="", max_length=12)  # "" its end (a live one: so far)
    count: int = Field(default=5, ge=1, le=10)
    preset: str = Field(default="", max_length=64)
    folder: str = Field(default="", max_length=80)
    # "auto": each clip's layout by what it shows (hunt.clip_preset); "fixed": `preset`.
    layout: Literal["auto", "fixed"] = "auto"
    irl_preset: str = Field(default="", max_length=64)


def seconds_of(text: str) -> float | None:
    """'1:02:03', '2:03' or '75' as seconds; None for nothing."""
    text = text.strip()
    if not text:
        return None
    found = CLOCK.match(text)
    if not found:
        raise HTTPException(422, f"{text!r} isn't a time in the stream (like 1:02:03 or 12:30)")
    parts = [int(p) for p in text.split(":")]
    if any(p >= 60 for p in parts[1:]):
        raise HTTPException(422, f"{text!r} isn't a time in the stream (like 1:02:03 or 12:30)")
    total = 0
    for part in parts:
        total = total * 60 + part
    return float(total)


def stream_view(s: kick.Stream, slug: str) -> dict:
    return {
        "id": s.id, "uuid": s.uuid, "title": s.title, "started": s.started,
        "duration": s.duration, "live": s.live, "thumbnail": s.thumbnail, "views": s.views,
        "link": f"https://kick.com/{slug}/videos/{s.uuid}" if s.uuid else f"https://kick.com/{slug}",
        "has_video": bool(s.source),
    }  # fmt: skip


def install(app, *, User, DB, renders_of, sees_every_render, presets_of, client=None) -> None:
    """`client` makes the Kick client (kick.Client when not given)."""

    def clipper_user(account, session: Session) -> None:
        if not features.enabled(account, FEATURE, session):
            raise HTTPException(403, "the Kick clipper isn't on for this account yet")

    def ask_kick(do):
        made = (client or kick.Client)()
        try:
            return do(made)
        except kick.KickError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            made.close()

    def preset_for(account, name: str) -> str:
        """The preset a clip is made with: the one named, else the Kick clips preset, else
        the first that edits; never a download-only one (a clip is cut)."""
        have = presets_of(account)
        editing = [n for n, p in have.items() if not p.download_only]
        if name:
            if name not in have:
                raise HTTPException(422, f"no preset {name!r}")
            if have[name].download_only:
                raise HTTPException(422, "a download-only preset can't cut clips; pick another")
            return name
        if DEFAULT_PRESET in editing:
            return DEFAULT_PRESET
        if not editing:
            raise HTTPException(422, "there is no preset to make the clips with")
        return editing[0]

    def irl_for(account, name: str) -> str:
        """The preset for clips that aren't gameplay: "" (kick-clips when there is one)."""
        return preset_for(account, name) if name.strip() else ""

    def own(query, model, account, session: Session):
        if sees_every_render(account, session):
            return query
        return query.where(model.owner_id == account.id)

    def find_streamer(streamer_id: str, account, session: Session) -> KickStreamer:
        clipper_user(account, session)
        found = session.get(KickStreamer, streamer_id)
        if found is None or not renders_of(account, found.owner_id, session):
            raise HTTPException(404, "no such streamer")
        return found

    def find_hunt(hunt_id: str, account, session: Session) -> KickHunt:
        clipper_user(account, session)
        found = session.get(KickHunt, hunt_id)
        if found is None or not renders_of(account, found.owner_id, session):
            raise HTTPException(404, "no such hunt")
        return found

    def streamer_view(s: KickStreamer) -> dict:
        return {
            "id": s.id, "slug": s.slug, "username": s.username or s.slug, "avatar": s.avatar,
            "live": s.live, "preset": s.preset, "irl_preset": s.irl_preset,
            "per_hour": s.per_hour, "folder": s.folder,
            "folder_shown": " ".join(s.folder.split()) or f"Kick {s.username or s.slug}",
            "is_live": s.is_live, "last_checked": s.last_checked, "last_result": s.last_result,
            "link": f"https://kick.com/{s.slug}", "created_at": s.created_at,
        }  # fmt: skip

    def job_view(job: Job | None, session: Session) -> dict | None:
        if job is None:
            return None
        outputs = session.scalars(
            select(Output).where(Output.job_id == job.id).order_by(Output.id)
        ).all()
        return {
            "id": job.id, "status": job.status, "stage": job.stage, "progress": job.progress,
            "error": job.error,
            "outputs": [
                {"id": o.id, "duration": o.duration, "title": o.title, "preset": o.preset}
                for o in outputs
            ],
        }  # fmt: skip

    def hunt_view(h: KickHunt, session: Session) -> dict:
        found = []
        for m in h.moments or []:
            job = session.get(Job, m["job_id"]) if m.get("job_id") else None
            link = ""
            if h.uuid:
                link = kick.MOMENT_LINK.format(slug=h.slug, uuid=h.uuid, seconds=int(m["start"]))
            found.append({**m, "link": link, "job": job_view(job, session)})
        return {
            "id": h.id, "slug": h.slug, "username": h.username or h.slug, "title": h.title,
            "uuid": h.uuid, "stream_id": h.stream_id, "start": h.start, "end": h.end,
            "count": h.count, "preset": h.preset, "live": h.live, "layout": h.layout,
            "irl_preset": h.irl_preset,
            "folder": hunt.folder_name(h), "status": h.status, "message": h.message,
            "error": h.error, "created_at": h.created_at, "streamer_id": h.streamer_id,
            "link": f"https://kick.com/{h.slug}/videos/{h.uuid}" if h.uuid else "",
            "moments": found,
        }  # fmt: skip

    @app.get("/api/kick/streamers")
    def list_streamers(account: User, session: DB) -> list[dict]:
        clipper_user(account, session)
        query = own(select(KickStreamer), KickStreamer, account, session)
        return [streamer_view(s) for s in session.scalars(query.order_by(KickStreamer.created_at))]

    @app.post("/api/kick/streamers")
    def add_streamer(body: StreamerRequest, account: User, session: DB) -> dict:
        clipper_user(account, session)
        try:
            link = kick.parse_link(body.name)
        except kick.KickError as exc:
            raise HTTPException(422, str(exc)) from exc
        slug = link.slug
        if not slug:
            slug = ask_kick(lambda c: c.video(link.uuid))[0]
        channel = ask_kick(lambda c: c.channel(slug))
        preset = preset_for(account, body.preset)
        found = KickStreamer(
            owner_id=account.id, slug=channel.slug.lower(), username=channel.username,
            channel_id=channel.id, avatar=channel.avatar, live=body.live, preset=preset,
            per_hour=body.per_hour, folder=" ".join(body.folder.split()),
            irl_preset=irl_for(account, body.irl_preset),
            is_live=channel.live is not None,
        )  # fmt: skip
        session.add(found)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, f"{channel.username} is already on the list") from exc
        return streamer_view(found)

    @app.patch("/api/kick/streamers/{streamer_id}")
    def change_streamer(streamer_id: str, body: StreamerChange, account: User, session: DB):
        found = find_streamer(streamer_id, account, session)
        if body.preset is not None:
            found.preset = preset_for(account, body.preset)
        if body.irl_preset is not None:
            found.irl_preset = irl_for(account, body.irl_preset)
        if body.per_hour is not None:
            found.per_hour = body.per_hour
        if body.folder is not None:
            found.folder = " ".join(body.folder.split())
        if body.live is not None and body.live != found.live:
            found.live = body.live
            # Following again starts from what is live then, not from where it stopped.
            found.stream_id, found.done_until = None, 0.0
        session.commit()
        return streamer_view(found)

    @app.delete("/api/kick/streamers/{streamer_id}")
    def remove_streamer(streamer_id: str, account: User, session: DB) -> dict:
        session.delete(find_streamer(streamer_id, account, session))
        session.commit()
        return {"ok": True}

    @app.get("/api/kick/streams")
    def list_streams(name: str, account: User, session: DB) -> dict:
        """A streamer's streams (the live one first), to pick one to clip."""
        clipper_user(account, session)
        try:
            link = kick.parse_link(name)
        except kick.KickError as exc:
            raise HTTPException(422, str(exc)) from exc

        def read(c: kick.Client):
            slug = link.slug or c.video(link.uuid)[0]
            return c.channel(slug), c.streams(slug)

        channel, streams = ask_kick(read)
        return {
            "slug": channel.slug, "username": channel.username, "avatar": channel.avatar,
            "is_live": channel.live is not None,
            "streams": [stream_view(s, channel.slug) for s in streams[:30]],
        }  # fmt: skip

    @app.get("/api/kick/hunts")
    def list_hunts(account: User, session: DB) -> list[dict]:
        clipper_user(account, session)
        query = own(select(KickHunt), KickHunt, account, session)
        query = query.order_by(KickHunt.created_at.desc()).limit(40)
        return [hunt_view(h, session) for h in session.scalars(query)]

    @app.get("/api/kick/hunts/{hunt_id}")
    def get_hunt(hunt_id: str, account: User, session: DB) -> dict:
        return hunt_view(find_hunt(hunt_id, account, session), session)

    @app.post("/api/kick/hunts")
    def start_hunt(body: HuntRequest, account: User, session: DB) -> dict:
        clipper_user(account, session)
        try:
            link = kick.parse_link(body.link)
        except kick.KickError as exc:
            raise HTTPException(422, str(exc)) from exc
        start, end = seconds_of(body.start), seconds_of(body.end)
        if start is not None and end is not None and end - start < hunt.SHORTEST_STRETCH:
            raise HTTPException(
                422, f"from {body.start} to {body.end} is too short for a clip; give a minute"
            )
        preset = preset_for(account, body.preset)

        def read(c: kick.Client):
            if link.kind == "stream":
                slug, stream = c.video(link.uuid, link.slug)
                slug = link.slug or slug
            else:
                slug = link.slug
                live = c.channel(slug).live
                if live is None:
                    raise kick.KickError(f"{slug} isn't live now; pick one of their streams")
                stream = live
            return c.channel(slug), stream

        channel, stream = ask_kick(read)
        if not stream.source and not channel.vods:
            raise HTTPException(
                422, f"{channel.username} has Kick's stream videos (VODs) off: nothing to clip from"
            )
        if not stream.source:
            raise HTTPException(422, "Kick hasn't made this stream's video yet")
        streamer = session.scalar(
            select(KickStreamer).where(
                KickStreamer.owner_id == account.id, KickStreamer.slug == channel.slug.lower()
            )
        )
        folder = " ".join(body.folder.split()) or (streamer.folder if streamer else "")
        found = hunt.new_hunt(
            session, account.id, channel.slug.lower(), channel.username, channel.id, stream,
            start or 0.0, end or 0.0, body.count, preset, folder=folder, layout=body.layout,
            irl_preset=irl_for(account, body.irl_preset)
            or (streamer.irl_preset if streamer else ""),
        )  # fmt: skip
        session.commit()
        jobs.hunt(found.id)
        session.refresh(found)
        return hunt_view(found, session)

    @app.post("/api/kick/hunts/{hunt_id}/retry")
    def retry_hunt(hunt_id: str, account: User, session: DB) -> dict:
        found = find_hunt(hunt_id, account, session)
        if found.status != "failed":
            raise HTTPException(409, "only a hunt that failed is tried again")
        found.status, found.error, found.message = "queued", None, "waiting for a free slot"
        found.updated_at = datetime.now(UTC)
        session.commit()
        jobs.hunt(found.id)
        session.refresh(found)
        return hunt_view(found, session)

    @app.delete("/api/kick/hunts/{hunt_id}")
    def remove_hunt(hunt_id: str, account: User, session: DB) -> dict:
        """The hunt goes from the list; the clips it made stay (they are jobs)."""
        found = find_hunt(hunt_id, account, session)
        if found.status in hunt.BUSY:
            raise HTTPException(409, "it is still looking; wait a moment")
        session.delete(found)
        session.commit()
        return {"ok": True}

    def daily_view(d: daily.Daily) -> dict:
        return {**d.model_dump(), "facecam_preset": daily.FACECAM, "full_preset": daily.FULL}

    @app.get("/api/kick/daily")
    def get_daily(account: User, session: DB) -> dict:
        """The account's morning clips (clipper/daily.py)."""
        clipper_user(account, session)
        return daily_view(daily.load(session, account.id))

    @app.put("/api/kick/daily")
    def set_daily(body: DailyRequest, account: User, session: DB) -> dict:
        clipper_user(account, session)
        names = []
        for given in body.streamers:
            if not given.strip():
                continue
            try:
                link = kick.parse_link(given)
            except kick.KickError as exc:
                raise HTTPException(422, str(exc)) from exc
            if not link.slug:
                raise HTTPException(422, f"{given!r} isn't a streamer's name or channel link")
            names.append(link.slug)
        found = daily.load(session, account.id)
        changed = found.model_copy(update={**body.model_dump(), "streamers": names})
        if body.on and not changed.streamers:
            raise HTTPException(422, "add the streamers to clip every morning")
        daily.save(session, account.id, daily.Daily.model_validate(changed.model_dump()))
        session.commit()
        return daily_view(daily.load(session, account.id))
