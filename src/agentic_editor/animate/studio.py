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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from agentic_editor.animate import images, maps, mapwriter, planner, voice
from agentic_editor.animate.plan import Plan, tidy
from agentic_editor.animate.render import render
from agentic_editor.animate.timeline import Spoken, Timeline, build, player_data
from agentic_editor.db import Animation
from agentic_editor.edl import EDL, Clip
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)
_mixing = threading.Lock()

SOURCE = "animation:"  # a Job's source for rendering an animation
BUSY = ("drafting", "changing", "painting", "voicing")  # a task is working on it
HISTORY = 20
STYLE_NAMES = {
    "glow": "Neon Glow", "paper": "Clean Paper", "story": "Character story",
    "kinetic": "Bold kinetic text", "stick": "Stick figure", "map": "World map",
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
    if dest.exists():
        return dest
    # The browser asks for the sound more than once at a time (ranges, a second tab): one
    # mix, the others wait for it, instead of each speaking and mixing it all again.
    with _mixing:
        if dest.exists():
            return dest
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


def paint_plan(
    plan: Plan, animation_id: str, Session: sessionmaker, settings: Settings, paint=None
) -> tuple[Plan, list[str]]:
    """`plan` with each scene's picture painted (kept ones reused): the writer's `art`
    for it, nobody in it behind characters; a style drawn as itself gets none."""
    paint = paint or images.paint
    if plan.style in images.UNPAINTED:
        return plan, []
    if plan.style == "map":
        return _paint_cards(plan, animation_id, Session, settings, paint)
    wanted = [(i, s) for i, s in enumerate(plan.scenes) if s.visual.art.strip()]
    if not wanted:
        return plan, []
    if paint is images.paint and not images.available(settings):
        return plan, ["painted pictures need the image model (installed on the Mac)"]
    pictures = [
        images.Picture(
            s.visual.art, _seed_of(s.visual.art, s.visual.art_seed), s.visual.kind == "scene"
        )
        for _, s in wanted
    ]
    progress = lambda stage, message: _set(Session, animation_id, message=message)  # noqa: E731
    names = paint(pictures, plan.style, settings, progress)
    scenes = list(plan.scenes)
    for (i, scene), name in zip(wanted, names, strict=True):
        scenes[i] = scene.model_copy(
            update={"visual": scene.visual.model_copy(update={"image": name})}
        )
    missing = sum(1 for n in names if not n)
    note = [f"{missing} of {len(names)} pictures couldn't be painted"] if missing else []
    return plan.model_copy(update={"scenes": scenes}), note


def _paint_cards(
    plan: Plan, animation_id: str, Session: sessionmaker, settings: Settings, paint
) -> tuple[Plan, list[str]]:
    """A World map reel's card pictures, in its look: each thing alone on white (cut out
    of it for the neon look), or a dark photo for gold horror. A thing painted for an
    earlier reel (same look, country and name, any wording of its picture) is used again,
    never painted twice (images.CardIndex)."""
    wanted = [
        (si, ii, item, s.visual.text)
        for si, s in enumerate(plan.scenes)
        if s.visual.kind == "country"
        for ii, item in enumerate(s.visual.items)
        if item.art.strip()
    ]
    if not wanted:
        return plan, []
    style, size = images.card_style(plan.look)
    index = images.CardIndex(images.folder(settings))
    keys = [
        images.card_key(style, item.text, country, item.art_seed) for *_, item, country in wanted
    ]
    known = [index.get(key) for key in keys]
    todo = [n for n, name in enumerate(known) if not name]
    unpainted: list[str] = []
    if todo and paint is images.paint and not images.available(settings):
        # Only the ones painted before (for this look) are shown; the rest show emoji.
        unpainted = [
            "card pictures need the image model (installed on the Mac); the cards show emoji"
        ]
        todo = []
    pictures = [
        images.Picture(wanted[n][2].art, _seed_of(wanted[n][2].art, wanted[n][2].art_seed))
        for n in todo
    ]
    progress = lambda stage, message: _set(Session, animation_id, message=message)  # noqa: E731
    painted = paint(pictures, style, settings, progress, size=size) if pictures else []
    names = list(known)
    for n, name in zip(todo, painted, strict=True):
        names[n] = name
        if name:
            index.put(keys[n], name)
    index.save()
    if plan.look == "neon":  # the dish on the map itself: its white background cut away
        names = [images.cut_out(settings, name) if name else "" for name in names]
    if len(todo) < len(wanted):
        log.info("animation %s: %d of %d card pictures from earlier reels", animation_id,
                 len(wanted) - len(todo), len(wanted))  # fmt: skip
    scenes = list(plan.scenes)
    for (si, ii, *_), name in zip(wanted, names, strict=True):
        visual = scenes[si].visual
        items = list(visual.items)
        items[ii] = items[ii].model_copy(update={"image": name})
        scenes[si] = scenes[si].model_copy(
            update={"visual": visual.model_copy(update={"items": items})}
        )
    missing = sum(1 for n in names if not n)
    note = unpainted or (
        [f"{missing} of {len(names)} card pictures couldn't be painted"] if missing else []
    )
    return plan.model_copy(update={"scenes": scenes}), note


def without_pictures(plan: Plan) -> Plan:
    scenes = []
    for s in plan.scenes:
        items = [i.model_copy(update={"image": ""}) for i in s.visual.items]
        visual = s.visual.model_copy(update={"image": "", "items": items})
        scenes.append(s.model_copy(update={"visual": visual}))
    return plan.model_copy(update={"scenes": scenes})


def ensure_map(settings: Settings, say=None) -> list[str]:
    """The world map, fetched and made the first time a World map reel needs it; a note
    when it can't be (the reel then flies over a plain ocean)."""
    if maps.ready(settings.models_dir):
        return []
    try:
        maps.fetch(settings.models_dir, say)
    except Exception as exc:
        log.exception("no world map")
        return [f"the world map couldn't be fetched ({exc}); the reel shows a plain ocean"]
    return []


class Countries:
    """The map's countries for the writer: whether a name is one (called), and where its
    label sits (locate: lon, lat), to put a country added to a tour near its neighbours."""

    def __init__(self, table: dict):
        self.table = table

    def __call__(self, name: str) -> bool:
        return maps.find(name, self.table) is not None

    def code(self, name: str) -> str | None:
        """The map's code for a country however it is named ("England" is GBR)."""
        return maps.find(name, self.table)

    def locate(self, name: str) -> tuple[float, float] | None:
        code = maps.find(name, self.table)
        return tuple(self.table[code]["label"]) if code else None


def known_country(settings: Settings) -> Countries | None:
    """The map's countries (None when the map isn't here to ask)."""
    table = maps.table(settings.models_dir)
    return Countries(table) if table else None


def player_of(plan: Plan, timeline: Timeline, settings: Settings) -> dict:
    """What the player draws: the timeline's data, with the map a World map reel needs."""
    return maps.attach(player_data(plan, timeline), plan, settings.models_dir)


def _seed_of(art: str, again: int) -> int:
    return (int(hashlib.sha256(art.encode()).hexdigest()[:8], 16) + 7919 * again) % 2**31


def run_task(
    animation_id: str, Session: sessionmaker, settings: Settings, router=None, speak=voice.speak,
    paint=None,
) -> None:  # fmt: skip
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
            wants_pictures = bool(a.images)
            current = Plan.model_validate(a.plan) if a.plan else None
        op = task.get("op", "draft")
        notes: list[str] = []
        doubts: list[str] = []
        plan = current
        wants_map = (current.style if current else brief.style) == "map" or (
            op == "auto" and brief.style == "map"
        )
        if wants_map:  # before the writer holds the model: the first time takes a while
            say = lambda message: _set(Session, animation_id, message=message)  # noqa: E731
            notes += ensure_map(settings, say)
        known = known_country(settings) if wants_map else None
        checks = mapwriter.Checks(settings.cache_dir / "map-checks.json") if wants_map else None
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
                    plan, wrote = planner.draft(llm, brief, found, known, checks)
                    notes = [*notes, *wrote]
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
                    plan, wrote = planner.change(
                        llm, current, task.get("request", ""), brief, known, checks
                    )
                    notes = [*notes, *wrote]
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
                    plan = planner.redo_scene(
                        llm, current, index, task.get("request", ""), brief.idea, checks
                    )
        if plan is None:
            raise planner.PlannerError("there is no reel to voice")
        # Mended again every time: a reel made before a mend (a name's look, a prop too big
        # to hold) gets it when it is next voiced or restyled.
        plan = tidy(plan)
        if wants_pictures:
            # The voice is recorded while the pictures are painted: neither needs the other
            # (the voice is the narration's, the pictures the model's on the GPU).
            _set(
                Session, animation_id, status="painting",
                message="painting the pictures (and recording the voice)",
            )  # fmt: skip
            with ThreadPoolExecutor(1, thread_name_prefix="voice") as voicing:
                voiced = voicing.submit(timeline_of, plan, settings, speak)
                plan, painted = paint_plan(plan, animation_id, Session, settings, paint)
                _set(Session, animation_id, status="voicing", message="recording the voice-over")
                timeline, _ = voiced.result()
            notes = [*notes, *painted]
        else:  # pictures off: the scenes are drawn
            plan = without_pictures(plan)
            _set(Session, animation_id, status="voicing", message="recording the voice-over")
            timeline, _ = timeline_of(plan, settings, speak)
        data = player_of(plan, timeline, settings)
        lost = maps.missing(plan, settings.models_dir)
        if lost:
            notes = [*notes, "not on the map (shown without flying there): " + ", ".join(lost)]
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
        if pilot is None or (not pilot.niche.strip() and pilot.style != "map"):
            raise planner.PlannerError("the autopilot has no topics left and no niche to find more")
        niche, used, style = pilot.niche, list(pilot.used or []), pilot.style
    if style == "map":
        topic = mapwriter.next_topic(llm, niche, used)
    else:
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
        images_dir=images.folder(settings),
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
