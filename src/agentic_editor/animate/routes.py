"""The studio's HTTP routes (feature "animation_studio"), installed by api.create_app.

An animation is its owner's; the primary account sees everyone's, like renders. Work on
one (a draft, a change, the voice) is queued (jobs.animate); while it runs the animation
can't be changed again. Rendering makes an ordinary Job.
"""

import hashlib
import re
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor import features, instagram, jobs
from agentic_editor.animate import images, render, schedule, studio, voice
from agentic_editor.animate.plan import Plan
from agentic_editor.db import Animation, AnimationAutopilot, InstagramChannel, Job, Output
from agentic_editor.render import fonts

FEATURE = "animation_studio"
PLAYER_FILES = {
    "player.html": "text/html",
    "player.js": "text/javascript",
    "player.css": "text/css",
}
FONT_NAMES = {
    "Montserrat-ExtraBold", "Montserrat-Bold", "Inter-SemiBold", "Inter-Bold", "Inter-Medium",
    "Anton-Regular", "SourceSerif4-Bold", "SourceSerif4-Semibold", "Rubik-ExtraBold",
    "Rubik-Bold", "Rubik-SemiBold", "PatrickHand-Regular",
}  # fmt: skip
# The player page only runs its own script and shows what it is sent as text.
PLAYER_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; img-src 'self' data: blob:; frame-ancestors 'self'"
)


class AnimationRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=300)
    kind: Literal["auto", "facts", "lesson", "story", "news"] = "auto"
    style: Literal["auto", "glow", "paper", "story", "kinetic", "stick"] = "auto"
    voice: str = Field(default="auto", max_length=32)
    length: Literal["auto", "short", "medium", "long"] = "auto"
    images: bool = True  # painted pictures behind the scenes


class PlanEdit(BaseModel):
    plan: dict


class ChangeRequest(BaseModel):
    request: str = Field(default="", max_length=500)
    scene: int | None = Field(default=None, ge=0, le=20)


class StudioChoice(BaseModel):
    """Changes the studio's own controls make at once (no writing): style and voice."""

    style: Literal["glow", "paper", "story", "kinetic", "stick"] | None = None
    voice: str | None = Field(default=None, max_length=32)
    images: bool | None = None


class AutopilotSettings(BaseModel):
    active: bool = True
    niche: str = Field(default="", max_length=300)
    topics: list[str] = Field(default_factory=list, max_length=200)
    hour: int = Field(default=10, ge=0, le=23)
    per_day: int = Field(default=1, ge=1, le=3)
    kind: Literal["auto", "facts", "lesson", "story", "news"] = "auto"
    style: Literal["auto", "glow", "paper", "story", "kinetic", "stick"] = "auto"
    voice: str = Field(default="auto", max_length=32)
    length: Literal["auto", "short", "medium", "long"] = "auto"
    channel_id: str | None = Field(default=None, max_length=32)
    auto_post: bool = False
    images: bool = True


_versions: dict[str, tuple[float, int, str]] = {}


def asset_version(name: str) -> str:
    """A short hash of a player file's content, worked out again only when it changes."""
    path = render.PLAYER / name
    stat = path.stat()
    known = _versions.get(name)
    if known and known[:2] == (stat.st_mtime, stat.st_size):
        return known[2]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    _versions[name] = (stat.st_mtime, stat.st_size, digest)
    return digest


def install(
    app, *, User, DB, renders_of, sees_every_render, enqueue, settings, public_base
) -> None:
    def studio_user(account, session: Session) -> None:
        if not features.enabled(account, FEATURE, session):
            raise HTTPException(403, "the animation studio isn't on for this account yet")

    def find(animation_id: str, session: Session, account) -> Animation:
        studio_user(account, session)
        found = session.get(Animation, animation_id)
        if found is None or not renders_of(account, found.owner_id, session):
            raise HTTPException(404, "no such animation")
        return found

    def idle(found: Animation) -> None:
        if found.status in ("queued", *studio.BUSY):
            raise HTTPException(409, "it is still being worked on; wait a moment")

    def render_of(found: Animation, session: Session) -> dict | None:
        if not found.job_id:
            return None
        job = session.get(Job, found.job_id)
        if job is None:
            return None
        output = session.scalar(
            select(Output).where(Output.job_id == job.id).order_by(Output.id.desc())
        )
        return {
            "job_id": job.id, "status": job.status, "stage": job.stage, "progress": job.progress,
            "error": job.error, "output_id": output.id if output else None,
        }  # fmt: skip

    def view(found: Animation, session: Session, full: bool = False) -> dict:
        plan = found.plan or {}
        out = {
            "id": found.id, "idea": found.idea, "kind": found.kind, "style": found.style,
            "voice": found.voice, "length": found.length, "images": found.images,
            "status": found.status,
            "message": found.message, "error": found.error, "notes": found.notes or [],
            "title": plan.get("title") or found.idea, "scenes": len(plan.get("scenes", [])),
            "duration": (found.player or {}).get("duration"), "can_undo": bool(found.history),
            "created_at": found.created_at, "updated_at": found.updated_at,
            "render": render_of(found, session),
            "autopilot_id": found.autopilot_id, "post_status": found.post_status,
            "post_error": found.post_error, "post_id": found.post_id, "doubts": found.doubts or [],
        }  # fmt: skip
        if full:
            out["plan"] = found.plan
            out["player"] = found.player
        return out

    def queue(found: Animation, session: Session, task: dict, message: str) -> None:
        found.task, found.status, found.message, found.error = task, "queued", message, None
        session.commit()
        jobs.animate(found.id)
        session.refresh(found)  # as it is now (the queue may have taken it up already)

    @app.get("/api/animations")
    def list_animations(account: User, session: DB) -> list[dict]:
        studio_user(account, session)
        query = select(Animation).order_by(Animation.created_at.desc()).limit(100)
        if not sees_every_render(account, session):
            query = query.where(Animation.owner_id == account.id)
        return [view(a, session) for a in session.scalars(query)]

    @app.post("/api/animations")
    def create_animation(body: AnimationRequest, account: User, session: DB) -> dict:
        studio_user(account, session)
        if body.voice not in ("auto", *voice.GROUPS, *voice.BY_ID):
            raise HTTPException(422, f"no voice {body.voice!r}")
        found = Animation(
            owner_id=account.id, idea=" ".join(body.idea.split()), kind=body.kind,
            style=body.style, voice=body.voice, length=body.length, images=body.images,
        )  # fmt: skip
        session.add(found)
        session.flush()
        queue(found, session, {"op": "draft"}, "waiting for a free slot")
        return view(found, session, full=True)

    @app.get("/api/animations/{animation_id}")
    def get_animation(animation_id: str, account: User, session: DB) -> dict:
        return view(find(animation_id, session, account), session, full=True)

    @app.put("/api/animations/{animation_id}")
    def edit_animation(animation_id: str, body: PlanEdit, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        idle(found)
        try:
            plan = studio.edited(found, Plan.model_validate(body.plan))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, f"that reel isn't valid: {exc}") from exc
        if found.plan:
            found.history = [found.plan, *(found.history or [])][: studio.HISTORY]
        found.plan = plan.model_dump(mode="json")
        queue(found, session, {"op": "voice"}, "recording the voice-over")
        return view(found, session, full=True)

    @app.patch("/api/animations/{animation_id}")
    def set_animation(animation_id: str, body: StudioChoice, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        idle(found)
        if not found.plan:
            raise HTTPException(409, "there is no draft yet")
        plan = Plan.model_validate(found.plan)
        update: dict = {}
        if body.style:
            update["style"] = found.style = body.style
        if body.voice:
            if body.voice not in (*voice.GROUPS, *voice.BY_ID):
                raise HTTPException(422, f"no voice {body.voice!r}")
            update["voice"] = voice.resolve(body.voice, plan.kind, plan.mood)
            found.voice = body.voice
        changed_pictures = body.images is not None and body.images != found.images
        if changed_pictures:
            found.images = body.images
        if not update and not changed_pictures:
            return view(found, session, full=True)
        found.history = [found.plan, *(found.history or [])][: studio.HISTORY]
        found.plan = plan.model_copy(update=update).model_dump(mode="json")
        queue(found, session, {"op": "voice"}, "recording the voice-over")
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/change")
    def change_animation(
        animation_id: str, body: ChangeRequest, account: User, session: DB
    ) -> dict:
        found = find(animation_id, session, account)
        idle(found)
        if not found.plan:
            raise HTTPException(409, "there is no draft to change yet")
        if body.scene is None:
            if not body.request.strip():
                raise HTTPException(422, "say what to change")
            task = {"op": "change", "request": body.request.strip()}
        else:
            if body.scene >= len(found.plan.get("scenes", [])):
                raise HTTPException(422, f"there is no scene {body.scene + 1}")
            task = {"op": "scene", "scene": body.scene, "request": body.request.strip()}
        queue(found, session, task, "waiting for a free slot")
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/retry")
    def retry_animation(animation_id: str, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        if found.status != "failed":
            raise HTTPException(409, "only a failed draft can be tried again")
        queue(found, session, {"op": "voice" if found.plan else "draft"}, "waiting for a free slot")
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/undo")
    def undo_animation(animation_id: str, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        idle(found)
        if not studio.undo(found):
            raise HTTPException(409, "nothing to undo")
        plan = found.plan or {}
        found.style = plan.get("style", found.style)
        queue(found, session, {"op": "voice"}, "going back")
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/render")
    def render_animation(animation_id: str, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        if found.status != "ready" or not found.plan:
            raise HTTPException(409, "it isn't ready to render yet")
        current = render_of(found, session)
        if current and current["status"] in ("queued", "running"):
            raise HTTPException(409, "it is already rendering")
        job = enqueue(
            session, source=studio.SOURCE + found.id, title=(found.plan or {}).get("title", ""),
            presets=[], notes=None, trim=False, owner_id=found.owner_id or account.id,
        )  # fmt: skip
        found.job_id = job.id
        session.commit()
        session.expire_all()  # the job as it is now (the queue may have taken it up already)
        return view(found, session, full=True)

    @app.delete("/api/animations/{animation_id}")
    def delete_animation(animation_id: str, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        idle(found)
        session.delete(found)
        session.commit()
        return {"ok": True}

    @app.get("/api/animations/{animation_id}/sound")
    def animation_sound(animation_id: str, account: User, session: DB) -> FileResponse:
        found = find(animation_id, session, account)
        if not found.plan or not found.player:
            raise HTTPException(409, "there is no draft to hear yet")
        return FileResponse(
            studio.preview_sound(Plan.model_validate(found.plan), found.player, settings),
            media_type="audio/wav",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/animation-voices")
    def animation_voices(account: User, session: DB) -> list[dict]:
        studio_user(account, session)
        return [
            {"id": v.id, "name": v.name, "group": v.group, "about": v.about} for v in voice.VOICES
        ]

    @app.get("/api/animation-voices/{voice_id}/sample")
    def voice_sample(voice_id: str, account: User, session: DB) -> FileResponse:
        studio_user(account, session)
        chosen = voice.BY_ID.get(voice_id)
        if chosen is None:
            raise HTTPException(404, "no such voice")
        spoken = voice.speak(voice.sample_text(chosen), chosen.id, studio.voice_dir(settings))
        return FileResponse(
            spoken.path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"}
        )

    @app.get("/api/animation-player/{name}")
    def player_file(name: str, account: User, v: str = "") -> Response:
        """The player and its fonts. The page names its script and style with their
        content's hash (?v=), so a new player is never taken from a cache: Cloudflare
        gives static files four hours in the browser whatever the server says, and the
        old player without the newer styles drew them all as Neon Glow."""
        if name == "player.html":
            html = (render.PLAYER / name).read_text(encoding="utf-8")
            for asset in ("player.js", "player.css"):
                html = html.replace(f'"{asset}"', f'"{asset}?v={asset_version(asset)}"')
            headers = {"Cache-Control": "no-store", "Content-Security-Policy": PLAYER_CSP}
            return Response(html, media_type="text/html", headers=headers)
        if name in PLAYER_FILES:
            current = v == asset_version(name)
            cache = "private, max-age=31536000, immutable" if current else "no-store"
            return FileResponse(
                render.PLAYER / name,
                media_type=PLAYER_FILES[name],
                headers={"Cache-Control": cache},
            )
        stem = name.removesuffix(".ttf")
        if name.endswith(".ttf") and stem in FONT_NAMES:
            path = settings.fonts_dir / name
        elif name == fonts.COLOR_EMOJI:
            path = render.emoji_font(settings.models_dir)
        else:
            raise HTTPException(404, "no such file")
        if not path.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(
            path, media_type="font/ttf", headers={"Cache-Control": "private, max-age=604800"}
        )

    # ---- the autopilot -------------------------------------------------------------------

    def pilot_view(pilot: AnimationAutopilot | None, account, session: Session) -> dict:
        channels = session.scalars(
            select(InstagramChannel).where(InstagramChannel.owner_id == account.id)
        ).all()
        base = AutopilotSettings().model_dump()
        if pilot is not None:
            base |= {k: getattr(pilot, k) for k in AutopilotSettings.model_fields}
        waiting = 0
        if pilot is not None:
            waiting = len(session.scalars(
                select(Animation.id).where(
                    Animation.autopilot_id == pilot.id, Animation.post_status == "awaiting"
                )
            ).all())  # fmt: skip
        return base | {
            "exists": pilot is not None,
            "approved": pilot.approved if pilot else 0,
            "needs": pilot.needs if pilot else 10,
            "used": (pilot.used or [])[-20:] if pilot else [],
            "last_on": pilot.last_on if pilot else None,
            "waiting": waiting,
            "channels": [{"id": c.id, "username": c.username} for c in channels],
        }

    def pilot_of(account, session: Session) -> AnimationAutopilot | None:
        return session.scalar(
            select(AnimationAutopilot).where(AnimationAutopilot.owner_id == account.id)
        )

    @app.get("/api/animation-autopilot")
    def get_autopilot(account: User, session: DB) -> dict:
        studio_user(account, session)
        return pilot_view(pilot_of(account, session), account, session)

    @app.put("/api/animation-autopilot")
    def set_autopilot(
        body: AutopilotSettings, account: User, session: DB, request: Request
    ) -> dict:
        studio_user(account, session)
        if body.voice not in ("auto", *voice.GROUPS, *voice.BY_ID):
            raise HTTPException(422, f"no voice {body.voice!r}")
        if body.channel_id:
            channel = session.get(InstagramChannel, body.channel_id)
            if channel is None or channel.owner_id != account.id:
                raise HTTPException(404, "that Instagram page isn't connected to this account")
        pilot = pilot_of(account, session)
        if pilot is None:
            pilot = AnimationAutopilot(owner_id=account.id)
            session.add(pilot)
        if body.auto_post and not pilot.auto_post and (pilot.approved or 0) < (pilot.needs or 10):
            raise HTTPException(
                409,
                f"approve {pilot.needs or 10} of its reels first ({pilot.approved or 0} so far)",
            )
        for key, value in body.model_dump().items():
            setattr(pilot, key, value)
        pilot.topics = [" ".join(t.split())[:200] for t in body.topics if t.strip()]
        pilot.niche = " ".join(body.niche.split())
        pilot.base_url = public_base(request)
        session.commit()
        return pilot_view(pilot, account, session)

    @app.post("/api/animation-autopilot/now")
    def autopilot_now(account: User, session: DB) -> dict:
        """One of the autopilot's reels now, whatever the hour."""
        studio_user(account, session)
        pilot = pilot_of(account, session)
        if pilot is None or (not pilot.topics and not pilot.niche.strip()):
            raise HTTPException(409, "give the autopilot a niche or some topics first")
        found = schedule.new_animation(session, pilot, datetime.now().date())
        session.commit()
        jobs.animate(found.id)
        session.refresh(found)
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/approve")
    def approve_animation(animation_id: str, account: User, session: DB, request: Request) -> dict:
        found = find(animation_id, session, account)
        if found.post_status not in ("awaiting", "failed") or not found.job_id:
            raise HTTPException(409, "it isn't waiting for approval")
        try:
            schedule.approve(session, found, public_base(request), settings)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except instagram.InstagramError as exc:
            found.post_error = str(exc)
            session.commit()
            raise HTTPException(502, str(exc)) from exc
        session.commit()
        return view(found, session, full=True)

    @app.post("/api/animations/{animation_id}/skip")
    def skip_animation(animation_id: str, account: User, session: DB) -> dict:
        found = find(animation_id, session, account)
        if found.post_status not in ("awaiting", "failed"):
            raise HTTPException(409, "it isn't waiting for approval")
        found.post_status = "skipped"
        session.commit()
        return view(found, session, full=True)

    @app.get("/api/animation-images/{name}")
    def animation_image(name: str, account: User, session: DB) -> FileResponse:
        """A painted picture (images.name_of: named by what it shows, so kept for good)."""
        studio_user(account, session)
        if not re.fullmatch(r"[0-9a-f]{24}\.png", name):
            raise HTTPException(404, "no such picture")
        path = images.folder(settings) / name
        if not path.is_file():
            raise HTTPException(404, "no such picture")
        kept = {"Cache-Control": "private, max-age=31536000, immutable"}
        return FileResponse(path, media_type="image/png", headers=kept)
