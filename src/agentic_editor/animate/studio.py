"""The studio's work on stored animations: drafting, changes, voicing and rendering.

A queued task (jobs.animate) runs one of an animation's operations: write a draft,
change it in plain words, write one scene again, or voice an edited Plan. Every Plan
that is replaced is kept for undo. After each, the voice for every scene is made (cached
by text, so only what changed is spoken) and the player's data is stored, so the studio
shows the reel at once. Rendering is an ordinary Job (source "animation:<id>"): see
render_job.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from agentic_editor.animate import planner, voice
from agentic_editor.animate.plan import Plan, tidy
from agentic_editor.animate.render import render
from agentic_editor.animate.timeline import Spoken, Timeline, build, player_data
from agentic_editor.db import Animation
from agentic_editor.edl import EDL, Clip
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

SOURCE = "animation:"  # a Job's source for rendering an animation
BUSY = ("drafting", "changing", "voicing")  # a task is working on it
HISTORY = 20
STYLE_NAMES = {
    "glow": "Neon Glow", "paper": "Clean Paper", "story": "Character story",
    "kinetic": "Bold kinetic text", "stick": "Stick figure",
}  # fmt: skip


def label(preset: str) -> str:
    """How a render's preset is shown: an animation's by its style."""
    style = preset.removeprefix("animation-")
    return (
        f"Animation \u00b7 {STYLE_NAMES[style]}"
        if preset != style and style in STYLE_NAMES
        else preset
    )


def voice_dir(settings: Settings) -> Path:
    return settings.cache_dir / "animation-voices"


def brief_of(a: Animation) -> planner.Brief:
    return planner.Brief(a.idea, a.kind, a.style, a.voice, a.length)


def speak_all(plan: Plan, settings: Settings, speak=voice.speak) -> list[Spoken]:
    """Every scene's voice and the closing line's, in order (timeline.build's `voices`)."""
    lines = [s.narration for s in plan.scenes] + [plan.cta]
    return [speak(line, plan.voice, voice_dir(settings)) for line in lines]


def timeline_of(plan: Plan, settings: Settings, speak=voice.speak) -> tuple[Timeline, list[Spoken]]:
    spoken = speak_all(plan, settings, speak)
    return build(plan, spoken), spoken


def preview_sound(plan: Plan, player: dict, settings: Settings, speak=voice.speak) -> Path:
    """The preview's mixed sound (voice, music, effects), made once per version of the
    reel and kept: the studio plays it the moment the reel is shown."""
    from agentic_editor.animate import render, sound

    raw = json.dumps([player, plan.mood, plan.voice, 1], sort_keys=True, default=str)
    key = hashlib.sha256(raw.encode()).hexdigest()[:24]
    dest = settings.cache_dir / "animation-sound" / f"{key}.wav"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeline, spoken = timeline_of(plan, settings, speak)
        partial = dest.with_suffix(".part.wav")
        sound.mix(
            timeline, [Path(s.path) for s in spoken], plan.mood, partial,
            seed=render.seed_of(plan.title),
        )  # fmt: skip
        partial.replace(dest)
    return dest


def _keep(a: Animation, plan: Plan, notes: list[str]) -> None:
    if a.plan:
        a.history = [a.plan, *(a.history or [])][:HISTORY]
    a.plan = plan.model_dump(mode="json")
    a.notes = notes


def _set(Session: sessionmaker, animation_id: str, **values) -> Animation | None:
    with Session() as db:
        a = db.get(Animation, animation_id)
        if a is None:
            return None
        for k, v in values.items():
            setattr(a, k, v)
        db.commit()
        return a


def run_task(
    animation_id: str, Session: sessionmaker, settings: Settings, router=None, speak=voice.speak
) -> None:
    """The animation's queued operation (Animation.task). Like a job, it never stays
    busy: whatever happens is stored."""
    from agentic_editor.backends import get_backends
    from agentic_editor.router import ModelRouter

    op = "draft"
    try:
        with Session() as db:
            a = db.get(Animation, animation_id)
            if a is None:
                return
            task = dict(a.task or {"op": "draft"})
            brief = brief_of(a)
            current = Plan.model_validate(a.plan) if a.plan else None
        op = task.get("op", "draft")
        notes: list[str] = []
        doubts: list[str] = []
        plan = current
        if op in ("draft", "auto", "change", "scene"):
            router = router or ModelRouter(settings, get_backends(), lambda *args: None)
            if op in ("draft", "auto"):
                _set(
                    Session,
                    animation_id,
                    status="drafting",
                    message="working out the subject",
                    error=None,
                )
                # One session for all of it: closing it unloads the model, and loading it
                # again costs more than the searches it would wait through.
                with router.planner() as llm:
                    planner.roomy(llm)
                    if op == "auto" and not brief.idea:
                        _set(Session, animation_id, message="choosing today's topic")
                        brief.idea = _choose_topic(animation_id, Session, llm)
                    topic, query = (
                        ("", "") if brief.kind == "lesson" else planner.subject(llm, brief.idea)
                    )
                    _set(Session, animation_id, message="finding sources")
                    found = planner.gather(
                        brief.idea,
                        brief.kind,
                        settings.cache_dir / "captions",
                        topic=topic,
                        query=query,
                    )
                    _set(Session, animation_id, message=f"writing the reel ({len(found)} sources)")
                    plan, notes = planner.draft(llm, brief, found)
                    if op == "auto":  # nobody reads it before it may post: check its facts
                        _set(Session, animation_id, message="checking the facts")
                        doubts = planner.fact_check(llm, plan, found)
            elif current is None:
                raise planner.PlannerError("there is no draft to change yet")
            elif op == "change":
                _set(
                    Session,
                    animation_id,
                    status="changing",
                    message="changing it as asked",
                    error=None,
                )
                with router.planner() as llm:
                    planner.roomy(llm)
                    plan, notes = planner.change(llm, current, task.get("request", ""), brief)
            else:
                index = int(task.get("scene", 0))
                _set(
                    Session,
                    animation_id,
                    status="changing",
                    message=f"writing scene {index + 1} again",
                    error=None,
                )
                with router.planner() as llm:
                    planner.roomy(llm)
                    plan = planner.redo_scene(llm, current, index, task.get("request", ""))
        if plan is None:
            raise planner.PlannerError("there is no reel to voice")
        # Mended again every time: a reel made before a mend (a name's look, a prop too big
        # to hold) gets it when it is next voiced or restyled.
        plan = tidy(plan)
        _set(Session, animation_id, status="voicing", message="recording the voice-over")
        timeline, _ = timeline_of(plan, settings, speak)
        data = player_data(plan, timeline)
        _set(Session, animation_id, message="mixing the sound")
        try:
            preview_sound(plan, data, settings, speak)
        except Exception:  # the studio makes it when asked instead
            log.exception("animation %s: no preview sound", animation_id)
        notes = [*notes, f"{timeline.duration:.0f} s, {planner.words_note(plan, brief.length)}"]
        with Session() as db:
            a = db.get(Animation, animation_id)
            if a is None:
                return
            if op != "voice":
                _keep(a, plan, notes)
            else:
                a.plan, a.notes = plan.model_dump(mode="json"), notes
            a.player, a.status, a.message, a.task, a.error = data, "ready", "ready", None, None
            if op == "auto":
                a.doubts = doubts
            db.commit()
        if op == "auto":  # an autopilot's reel goes on to its render by itself
            from agentic_editor.animate import schedule

            schedule.render(animation_id, Session)
    except Exception as exc:
        log.exception("animation %s failed", animation_id)
        if op == "auto":  # an autopilot's reel that never got made
            _set(
                Session,
                animation_id,
                post_status="failed",
                post_error=f"{type(exc).__name__}: {exc}",
            )
        _set(
            Session, animation_id, status="failed", task=None, message="failed",
            error=str(exc)
            if isinstance(exc, planner.PlannerError)
            else f"{type(exc).__name__}: {exc}",
        )  # fmt: skip


def _choose_topic(animation_id: str, Session: sessionmaker, llm) -> str:
    """Today's topic for an autopilot's reel that had none waiting in its list: a new one in
    its niche, noted as made so it isn't chosen again."""
    from agentic_editor.db import AnimationAutopilot

    with Session() as db:
        a = db.get(Animation, animation_id)
        pilot = db.get(AnimationAutopilot, a.autopilot_id) if a and a.autopilot_id else None
        if pilot is None or not pilot.niche.strip():
            raise planner.PlannerError("the autopilot has no topics left and no niche to find more")
        niche, used = pilot.niche, list(pilot.used or [])
    topic = planner.next_topic(llm, niche, used)
    with Session() as db:
        a = db.get(Animation, animation_id)
        a.idea = topic
        pilot = db.get(AnimationAutopilot, a.autopilot_id)
        pilot.used = [*(pilot.used or []), topic][-200:]
        db.commit()
    return topic


def edited(a: Animation, plan: Plan) -> Plan:
    """A Plan a person edited by hand, mended, keeping what only the studio sets."""
    plan = tidy(plan)
    if not plan.scenes:
        raise ValueError("a reel needs at least one scene")
    return plan


def undo(a: Animation) -> bool:
    """Back to the Plan before the last change; False when there is none."""
    if not a.history:
        return False
    a.plan, a.history = a.history[0], list(a.history[1:])
    a.updated_at = datetime.now(UTC)
    return True


def render_job(
    animation_id: str,
    Session: sessionmaker,
    settings: Settings,
    progress,
    run_id: str,
    speak=voice.speak,
    draw=render,
):
    """A Job's render of an animation: its Plan as it stands, as a pipeline.Result."""
    from agentic_editor import pipeline

    with Session() as db:
        a = db.get(Animation, animation_id)
        if a is None or not a.plan:
            raise RuntimeError("the animation is gone or has no draft")
        plan = Plan.model_validate(a.plan)
    progress("title", plan.title or "Animation")
    progress("animate", "recording the voice-over")
    timeline, spoken = timeline_of(plan, settings, speak)
    out_dir = settings.media_dir / "outputs" / f"animation-{animation_id}" / run_id
    video, thumb, problems = draw(
        plan,
        timeline,
        [Path(s.path) for s in spoken],
        out_dir,
        settings.fonts_dir,
        settings.models_dir,
        progress,
    )
    edl = EDL(
        clips=[Clip(start=0, end=round(timeline.duration, 3))],
        title=plan.title,
        crop_mode="none",
        decisions=[
            f"animation: {STYLE_NAMES.get(plan.style, plan.style)}, voice {plan.voice}, "
            f"{len(plan.scenes)} scenes"
        ],
    )
    issues = [f"text shrunk to fit: {p}" for p in problems]
    output = pipeline.Output(
        f"animation-{plan.style}", video, edl, "animation", thumbnail=thumb, issues=issues
    )
    progress("done", f"animation -> {video}")
    return pipeline.Result(plan.title or "Animation", [output]), plan


def caption_of(animation_id: str, Session: sessionmaker):
    """The post caption of an animation's render: its Plan's, as it stands now."""
    from agentic_editor import caption

    with Session() as db:
        a = db.get(Animation, animation_id)
        plan = Plan.model_validate(a.plan) if a is not None and a.plan else None
    if plan is None or not plan.caption.strip():
        raise caption.CaptionError("the animation has no caption; write one in the studio")
    return caption.Written(plan.caption, [s.model_dump() for s in plan.sources], [])
