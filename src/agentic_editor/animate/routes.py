"""The studio's HTTP routes (feature "animation_studio"), installed by api.create_app.

An animation is its owner's; the primary account sees everyone's, like renders. Work on
one (a draft, a change, the voice) is queued (jobs.animate); while it runs the animation
can't be changed again. Rendering makes an ordinary Job.
"""

from typing import Literal

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor import features, jobs
from agentic_editor.animate import render, studio, voice
from agentic_editor.animate.plan import Plan
from agentic_editor.db import Animation, Job, Output
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
    "Rubik-Bold", "Rubik-SemiBold",
}  # fmt: skip
# The player page only runs its own script and shows what it is sent as text.
PLAYER_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; img-src 'self' data: blob:; frame-ancestors 'self'"
)


class AnimationRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=300)
    kind: Literal["auto", "facts", "lesson", "story", "news"] = "auto"
    style: Literal["auto", "glow", "paper"] = "auto"
    voice: str = Field(default="auto", max_length=32)
    length: Literal["auto", "short", "medium", "long"] = "auto"


class PlanEdit(BaseModel):
    plan: dict


class ChangeRequest(BaseModel):
    request: str = Field(default="", max_length=500)
    scene: int | None = Field(default=None, ge=0, le=20)


class StudioChoice(BaseModel):
    """Changes the studio's own controls make at once (no writing): style and voice."""

    style: Literal["glow", "paper"] | None = None
    voice: str | None = Field(default=None, max_length=32)


def install(app, *, User, DB, renders_of, sees_every_render, enqueue, settings) -> None:
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
            "voice": found.voice, "length": found.length, "status": found.status,
            "message": found.message, "error": found.error, "notes": found.notes or [],
            "title": plan.get("title") or found.idea, "scenes": len(plan.get("scenes", [])),
            "duration": (found.player or {}).get("duration"), "can_undo": bool(found.history),
            "created_at": found.created_at, "updated_at": found.updated_at,
            "render": render_of(found, session),
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
            style=body.style, voice=body.voice, length=body.length,
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
        if not update:
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
    def player_file(name: str, account: User) -> FileResponse:
        if name in PLAYER_FILES:
            headers = {"Cache-Control": "no-cache"}
            if name.endswith(".html"):
                headers["Content-Security-Policy"] = PLAYER_CSP
            return FileResponse(
                render.PLAYER / name, media_type=PLAYER_FILES[name], headers=headers
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
