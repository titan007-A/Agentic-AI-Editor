"""End-to-end run for one source video and a set of presets.

Stages run one model at a time: separation and whisper (child processes), then the
planning LLM for every preset, then rendering with the vision model reviewing results.
Each model is unloaded before the next stage starts."""

import functools
import json
import logging
import secrets
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agentic_editor import (
    agent,
    download,
    ffmpeg,
    framing,
    headline,
    hooks,
    names,
    review,
    shazam,
    silence,
    slots,
    subtitles,
    trim,
)
from agentic_editor.analysis import faces, onscreen, subjects, watermarks
from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.analyze import Analysis, analyze, file_hash, with_silences
from agentic_editor.backends import Backends
from agentic_editor.cuts import clean_edl
from agentic_editor.edl import EDL, Clip, Cue, Shot
from agentic_editor.heuristic import plan_by_energy
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.planner import overlap, pick_emphasis, plan_variants
from agentic_editor.presets import Preset
from agentic_editor.render import audio, compose
from agentic_editor.render.audio import measure_loudness
from agentic_editor.render.video import crop_size
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]
MAX_FIXES = 2
# Without the vision model to tell, only a box this share of the frame can be an overlay.
# A watermark (a handle, an app or channel logo) is small: no box bigger than this is ever
# blurred, whatever the vision model says, so a blur never covers a piece of the scene.
BLUR_AREA = 0.03  # of the frame
BLUR_HEIGHT = 0.10
BLUR_WIDTH = 0.5
UPLOAD_PREFIX = "upload:"


@dataclass
class Output:
    preset: str
    path: Path
    edl: EDL
    planner: str
    thumbnail: Path | None = None
    renders: int = 1
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0
    doubts: list[str] = field(default_factory=list)
    # (song, artist) for a preset that names them, so its post caption is about that song.
    song: tuple[str, str] | None = None


@dataclass
class Result:
    title: str
    outputs: list[Output]
    # One line per preset or version that couldn't be made; the others still are.
    failures: list[str] = field(default_factory=list)


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def resolve_source(value: str, settings: Settings) -> download.Source:
    if is_url(value):
        return download.fetch(value, settings.media_dir / "sources")
    if value.startswith(UPLOAD_PREFIX):
        return download.local(settings.media_dir / "uploads" / value.removeprefix(UPLOAD_PREFIX))
    return download.local(Path(value).expanduser())


def plan(
    llm: OllamaClient,
    analysis: Analysis,
    preset: Preset,
    notes: str | None,
    start_at: str | None = None,
) -> list[tuple[EDL, str]]:
    """Plan each version with the LLM, falling back to the energy heuristic if it can't
    deliver; then trim each one to the preset and choose its opening inside what it keeps.
    A version that trims down onto one already made is left out."""
    try:
        edls = plan_variants(llm, analysis, preset, notes, preset.hook.variants, start_at)
        planner = llm.model
    except (LLMError, httpx.HTTPError) as exc:
        log.warning("%s: LLM planning failed (%s), using the energy heuristic", preset.name, exc)
        edls, planner = [plan_by_energy(analysis, preset)], "heuristic"
    planned: list[tuple[EDL, str]] = []
    for edl in edls:
        edl = clean_edl(edl, analysis.transcript.words, analysis.silences)
        edl = trim.apply(edl, analysis, preset)
        edl = hooks.sharpen(llm, edl, analysis, preset, notes)
        span = (edl.clips[0].start, edl.clips[0].end)
        if any(overlap(span, (e.clips[0].start, e.clips[0].end)) > 0.5 for e, _ in planned):
            log.info("%s: a version trimmed down onto one already planned; left out", preset.name)
            continue
        if preset.zoom_punch_ins and planner != "heuristic":
            try:
                edl = edl.model_copy(update={"zooms": pick_emphasis(llm, edl, analysis)})
            except LLMError as exc:
                edl = edl.noted(f"no punch-in zooms ({exc})")
        planned.append((edl.model_copy(update={"variant": len(planned)}), planner))
    return planned


def needs_faces(edl: EDL, preset: Preset, analysis: Analysis) -> bool:
    """Whether making this plan looks for faces: to frame it, or to keep captions off them."""
    if edl.crop_mode == "smart" or (preset.captions.enabled and preset.captions.avoid_faces):
        return True
    source = (analysis.width, analysis.height)
    return edl.crop_mode == "face" and crop_size(source, preset.size)[0] < source[0]


def face_commands(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    detections: dict[tuple[float, float], list[faces.Sample]],
) -> dict[int, Path]:
    source = (analysis.width, analysis.height)
    crop_width = crop_size(source, preset.size)[0]
    if edl.crop_mode != "face" or crop_width >= source[0]:
        return {}
    commands = {}
    for i, clip in enumerate(edl.clips):
        key = (clip.start, clip.end)
        if key not in detections:
            detections[key] = faces.detect(video, clip.start, clip.end, settings.models_dir)
        path = faces.crop_path(detections[key], preset.framing.smoothing, source[0], crop_width)
        commands[i] = faces.write_commands(path, f"crop@c{i}", work_dir / f"crop-{i}.cmd")
    return commands


def subject_samples(
    vision: OllamaClient | None,
    video: Path,
    times: list[float],
    analysis: Analysis,
    settings: Settings,
    work_dir: Path,
) -> list[tuple[float, list[subjects.Subject]]]:
    """Vision-model subjects at each time, cached so a re-render doesn't ask again."""
    if vision is None or not times:
        return [(t, []) for t in times]
    cache = work_dir / f"subjects-{vision.model.replace(':', '_').replace('/', '_')}.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    missing = [t for t in times if f"{t:.3f}" not in known]
    if missing:
        size = (analysis.width, analysis.height)
        found = subjects.find(vision, video, missing, size, settings.ffmpeg_bin, work_dir / "views")
        for t, items in zip(missing, found, strict=True):
            if items is not None:  # a failed answer is asked again next time
                known[f"{t:.3f}"] = [s.model_dump() for s in items]
        cache.write_text(json.dumps(known))
    return [
        (t, [subjects.Subject.model_validate(s) for s in known.get(f"{t:.3f}", [])]) for t in times
    ]


def translated(
    llm: OllamaClient, edl: EDL, analysis: Analysis, preset: Preset, notes: str | None
) -> EDL:
    """Captions in another language, made while the planning model is loaded."""
    style = preset.captions
    if not (style.enabled and style.mode == "translated"):
        return edl
    if not style.language:
        note = "translated captions asked for without a language; showing the speech"
        return edl.model_copy(update={"decisions": [*edl.decisions, note]})
    words = compose.timeline_words(edl, analysis)
    update: dict = {}
    failed = False
    try:
        if analysis.audio_source == "vocals":
            # Sung lines are translated line by line, so the song keeps its lines.
            lines = subtitles.lyric_cues(words, subtitles.TRANSLATE_WORDS)
            update["cues"] = subtitles.translate(llm, lines, style.language, notes)
        else:
            update["cues"] = subtitles.translate_speech(llm, words, style.language, notes)
        note = f"captions translated into {style.language} ({len(update['cues'])} lines)"
    except (LLMError, httpx.HTTPError) as exc:
        note, failed = f"translation failed ({exc}); showing the speech", True
    # The on-screen hook and title follow the captions into the same language.
    overlays = {k: v for k, v in (("hook", edl.hook), ("title", edl.title)) if v}
    if overlays:
        wrapped = [Cue(start=0, end=1, text=v) for v in overlays.values()]
        try:
            done = subtitles.translate(llm, wrapped, style.language, notes)
            update |= {k: c.text for k, c in zip(overlays, done, strict=True)}
        except (LLMError, httpx.HTTPError):
            note, failed = note + "; hook and title left untranslated", True
    return edl.model_copy(update=update).noted(note, doubt=failed)


def written_text(
    vision: OllamaClient | None,
    edl: EDL,
    analysis: Analysis,
    preset: Preset,
    video: Path,
    settings: Settings,
    work_dir: Path,
    notes: str | None,
) -> EDL:
    """Captions and the one-line overlay the vision model writes from the picture."""
    want_cues = preset.captions.enabled and preset.captions.mode == "visual" and not edl.cues
    want_line = preset.one_liner.enabled and not edl.one_liner
    if not (want_cues or want_line):
        return edl
    if vision is None:
        return edl.noted("no vision model to write captions or the one-liner", doubt=True)
    clip = edl.clips[0]
    frames_dir = work_dir / "written"
    frames_dir.mkdir(parents=True, exist_ok=True)

    def frames(count: int, name: str) -> list[Path]:
        times = [clip.start + clip.duration * (i + 0.5) / count for i in range(count)]
        return [
            subjects.grab(video, t, frames_dir / f"{name}-{i}.jpg", settings.ffmpeg_bin)
            for i, t in enumerate(times)
        ]

    # What is said gives the model the subject; frames alone only show a person talking.
    said = " ".join(w.text for w in analysis.transcript.between(clip.start, clip.end))
    context = " ".join(said.split()[:120])
    update: dict = {}
    decisions, doubts = list(edl.decisions), list(edl.doubts)
    if want_cues:
        count = min(8, max(1, round(clip.duration / subtitles.VISUAL_EVERY)))
        try:
            update["cues"] = subtitles.visual_cues(
                vision, frames(count, "caption"), clip.duration, context, notes
            )
            decisions.append(f"wrote {len(update['cues'])} caption(s) from the picture")
        except LLMError as exc:
            doubts.append(f"captions from the picture failed ({exc}); showing the speech")
            decisions.append(doubts[-1])
    if want_line:
        line = preset.one_liner
        try:
            language = preset.captions.language if preset.captions.mode == "translated" else None
            update["one_liner"] = subtitles.one_line(
                vision, frames(4, "line"), line.max_words, line.hint, context, notes, language
            )
            decisions.append(f'one-liner: "{update["one_liner"]}"')
        except LLMError as exc:
            doubts.append(f"one-liner failed ({exc})")
            decisions.append(doubts[-1])
    return edl.model_copy(update={**update, "decisions": decisions, "doubts": doubts})


def recheck_framing(
    shots: list[Shot],
    sampled: list[float],
    seen: list[framing.View],
    face_samples: list[faces.Sample],
    content: tuple[int, int, int, int],
    width: int,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    vision: OllamaClient | None,
) -> tuple[list[Shot], list[str]]:
    """Look again at fresh moments in each cropped shot and widen any whose window would
    cut through something there. Geometry decides, from boxes: small vision models are
    unreliable at judging a crop directly, but good at saying where things are."""
    checks = [(k, t) for k, t in framing.check_times(shots, sampled) if shots[k].width < content[2]]
    if not checks:
        return shots, []
    times = [t for _, t in checks]
    found = dict(subject_samples(vision, video, times, analysis, settings, work_dir))
    needed: dict[int, framing.Span] = {}
    for k, t in checks:
        shot = shots[k]
        x = shot.x
        if shot.follow:
            path = framing.follow_path(
                shot, seen, face_samples, content, analysis.width, preset.framing.smoothing
            )
            x = min(path, key=lambda p: abs(p[0] - (t - shot.start)))[1]
        spans = framing.keep_spans(found[t], face_samples, t, analysis.width)
        cut = framing.crossing(spans, x, shot.width)
        if cut:
            needed[k] = framing.union([*cut, *([needed[k]] if k in needed else [])])
    if not needed:
        return shots, [f"re-checked {len(checks)} moment(s); nothing at the edges"]
    updated = list(shots)
    for k, span in needed.items():
        updated[k] = framing.widen_to(updated[k], span, content, width)
    widened = ", ".join(f"{shots[k].start:.1f}-{shots[k].end:.1f}s" for k in sorted(needed))
    note = f"re-checked {len(checks)} moment(s); widened {widened} where an edge cut something"
    return framing.harmonize(updated, width, content), [note]


def band_framing(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    detections: dict[tuple[float, float], list[faces.Sample]],
    vision: OllamaClient | None,
) -> EDL:
    """Cut a picture shown whole to fit the band between the preset's text, at the frame's
    full width: top and bottom go, around the faces, and with them any text the picture
    brings along its edges."""
    if not preset.text_blocks:
        return edl.model_copy(update={"shots": []})
    face = compose.text_face(settings, compose.text_language(preset, analysis))
    band = compose.picture_band(preset, face)
    if band is None:
        return edl.model_copy(update={"shots": []})
    clip = edl.clips[0]
    key = (clip.start, clip.end)
    size = (analysis.width, analysis.height)
    picture = (0, 0, *size)
    if preset.framing.crop_black_bars or preset.framing.auto_background:
        picture = compose.picture_area(video, clip.start, clip.end, size)
    if key not in detections:
        detections[key] = faces.detect(video, clip.start, clip.end, settings.models_dir)
    face_samples = [(clip.start + t, found) for t, found in detections[key]]
    # Once a shot is enough: the face detector follows the faces through it, and the window
    # only moves at a shot's start.
    times = framing.view_times(clip, analysis.scenes, every=None)
    samples = subject_samples(vision, video, times, analysis, settings, work_dir)
    marks = [(m.y, m.y + m.height) for m in analysis.watermarks]
    rows = compose.band_rows(preset, band, picture[2])
    moving = compose.moving_part(video, clip.start, clip.end, size, picture)
    shots = framing.band_shots(
        clip, analysis.scenes, samples, face_samples, picture, rows, analysis.height, marks,
        moving,
    )  # fmt: skip
    cut = sorted({s.height for s in shots if s.height and s.height < picture[3]})
    note = (
        f"cut the picture's top and bottom to {', '.join(map(str, cut))} of its "
        f"{picture[3]} rows, at its full width, to fit between the text"
        if cut
        else "the whole picture fits between the text"
    )
    if moving is not None:
        note += f"; framed on the moving part, rows {moving[0]}-{moving[1]}, not the still image"
    decisions = [
        d
        for d in edl.decisions
        if not d.startswith(("cut the picture's", "the whole picture fits"))
    ]
    return edl.model_copy(
        update={"shots": shots, "picture": picture, "decisions": [*decisions, note]}
    )


def smart_framing(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    detections: dict[tuple[float, float], list[faces.Sample]],
    vision: OllamaClient | None,
) -> tuple[EDL, dict[int, Path]]:
    """Frame each shot so nothing that matters is cut, and write the follow paths."""
    clip = edl.clips[0]
    key = (clip.start, clip.end)
    if key not in detections:
        detections[key] = faces.detect(video, clip.start, clip.end, settings.models_dir)
    face_samples = [(clip.start + t, found) for t, found in detections[key]]
    content = edl.picture or compose.picture_area(
        video, clip.start, clip.end, (analysis.width, analysis.height)
    )
    width = framing.crop_width(content, preset.size)
    times = framing.view_times(clip, analysis.scenes)
    samples = subject_samples(vision, video, times, analysis, settings, work_dir)
    seen = framing.views(samples, face_samples, content, analysis.width)
    if not edl.shots:
        # Cropped top and bottom: a still image above or below the clip counts less.
        tall = width >= content[2] and framing.crop_height(content, preset.size) < content[3]
        size = (analysis.width, analysis.height)
        moving = compose.moving_part(video, clip.start, clip.end, size, content) if tall else None
        shots = framing.frame_shots(
            clip, analysis.scenes, samples, face_samples, content, analysis.width, preset.size,
            analysis.height, moving,
        )  # fmt: skip
        shots, recheck = recheck_framing(
            shots, times, seen, face_samples, content, width, preset, analysis, video,
            settings, work_dir, vision,
        )  # fmt: skip
        decisions = [*edl.decisions, framing.summary(shots, width), *recheck]
        edl = edl.model_copy(update={"shots": shots, "picture": content, "decisions": decisions})
    commands = {}
    for k, shot in enumerate(edl.shots):
        if shot.follow:
            path = framing.follow_path(
                shot, seen, face_samples, content, analysis.width, preset.framing.smoothing
            )
            commands[k] = faces.write_commands(path, f"crop@s{k}", work_dir / f"shot-{k}.cmd")
    return edl, commands


def onscreen_text(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    vision: OllamaClient | None,
    tracked: dict[tuple[float, float], list[tuple[float, list[onscreen.Box]]]],
) -> list[tuple[float, list[onscreen.Box]]]:
    """When the picture's own text (a name banner, a title) is on screen during the reel,
    so captions and banners can keep off it."""
    overlays = preset.captions.enabled or edl.one_liner or (preset.hook.overlay and edl.hook)
    if vision is None or not (overlays or preset.title_overlay.enabled):
        return []
    clip = edl.clips[0]
    key = (clip.start, clip.end)
    if key not in tracked:
        times = framing.view_times(clip, analysis.scenes)
        found = [
            (t, (s.x0, s.y0, s.x1, s.y1))
            for t, items in subject_samples(vision, video, times, analysis, settings, work_dir)
            for s in items
            if s.kind == "text"
        ]
        tracked[key] = []
        if found:
            stamps, frames = onscreen.grab(video, clip.start, clip.end, settings.ffmpeg_bin)
            tracked[key] = onscreen.track(stamps, frames, found, analysis.width)
    return tracked[key]


def moved(edl: EDL, old: Clip) -> EDL:
    """The plan after its clip moved from `old`: the shots and the picture area are worked
    out again for the new stretch, and the models' captions, timed on the reel, shift with
    its start and drop what fell outside."""
    clip = edl.clips[0]
    shift = clip.start - old.start

    def shifted(cue: Cue) -> Cue:
        words = [
            w.model_copy(update={"start": w.start - shift, "end": w.end - shift}) for w in cue.words
        ]
        return cue.model_copy(
            update={
                "start": round(max(0.0, cue.start - shift), 3),
                "end": round(min(clip.duration, cue.end - shift), 3),
                "words": [w for w in words if w.end > 0 and w.start < clip.duration],
            }
        )

    cues = [shifted(c) for c in edl.cues if c.end - shift > 0 and c.start - shift < clip.duration]
    return edl.model_copy(update={"shots": [], "picture": None, "cues": cues})


def fix(
    issues: list[review.Issue], edl: EDL, preset: Preset, analysis: Analysis, loudness_goal: float
) -> tuple[EDL, Preset, bool]:
    """Deterministic fixes for review issues. Returns the new plan and whether it changed."""
    changed = False
    for issue in issues:
        if issue.kind == "duration":
            trimmed = trim.apply(edl, analysis, preset)
            if trimmed.clips != edl.clips:
                edl, changed = moved(trimmed, edl.clips[0]), True
        elif issue.kind == "loudness" and issue.measured is not None:
            # Aim off-target by the error just measured, which covers what the final
            # encode shifts.
            corrected = preset.audio.loudness_lufs + loudness_goal - issue.measured
            audio = preset.audio.model_copy(update={"loudness_lufs": corrected})
            preset, changed = preset.model_copy(update={"audio": audio}), True
        elif issue.kind == "framing":
            if edl.crop_mode == "center":
                edl = edl.model_copy(update={"crop_mode": "face"})
            elif edl.crop_mode == "face" and preset.framing.smoothing > 0.5:
                framing = preset.framing.model_copy(update={"smoothing": 0.5})
                preset = preset.model_copy(update={"framing": framing})
            else:
                edl = edl.model_copy(update={"crop_mode": "none"})
            changed = True
    return edl, preset, changed


def remux_audio(edl: EDL, preset: Preset, analysis: Analysis, dest: Path, work_dir: Path) -> None:
    """Lay a new soundtrack under a reel already rendered, copying its picture as it is: a
    new loudness target needs no new encode of the video."""
    music = compose.asset_path(preset.music.file) if preset.music.file else None
    track = audio.render_soundtrack(edl, preset, analysis.audio_path, music, work_dir)
    remuxed = dest.with_name(f".{dest.stem}.remux{dest.suffix}")
    ffmpeg.run([
        "-y", "-i", str(dest), "-i", str(track), "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", str(audio.SAMPLE_RATE),
        "-movflags", "+faststart", str(remuxed),
    ])  # fmt: skip
    remuxed.replace(dest)


def produce(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    dest: Path,
    work_dir: Path,
    settings: Settings,
    backends: Backends,
    vision: OllamaClient | None,
    progress: Progress,
    detections: dict[tuple[float, float], list[faces.Sample]] | None = None,
) -> tuple[EDL, int, list[review.Issue]]:
    """Render, review, fix and re-render up to MAX_FIXES times. `detections` are the faces
    already found, by clip."""
    work_dir.mkdir(parents=True, exist_ok=True)
    detections = {} if detections is None else detections
    tracked: dict[tuple[float, float], list[tuple[float, list[onscreen.Box]]]] = {}
    target = preset
    renders = 1
    remux = False
    while True:
        if not remux:
            if edl.crop_mode == "smart":
                edl, commands = smart_framing(
                    edl, target, analysis, video, settings, work_dir, detections, vision
                )
            elif edl.crop_mode == "none":
                edl = band_framing(
                    edl, target, analysis, video, settings, work_dir, detections, vision
                )
                commands = {}
            else:
                commands = face_commands(
                    edl, target, analysis, video, settings, work_dir, detections
                )
            clip = edl.clips[0]
            key = (clip.start, clip.end)
            if target.captions.enabled and target.captions.avoid_faces and key not in detections:
                detections[key] = faces.detect(video, clip.start, clip.end, settings.models_dir)
            face_samples = [(clip.start + t, found) for t, found in detections.get(key, [])]
            texts = onscreen_text(edl, target, analysis, video, settings, work_dir, vision, tracked)
        with slots.encoding(vision, progress=progress):
            if remux:
                remux_audio(edl, target, analysis, dest, work_dir)
            else:
                compose.render(
                    edl, target, analysis, video, dest, work_dir, settings, backends, commands,
                    face_samples, texts,
                )  # fmt: skip
            # Measured while another job may have the GPU: these only need the CPU.
            info, loudness = ffmpeg.probe(dest), measure_loudness(dest)
        progress("review", f"checking {preset.name}")
        # Caption timing is checked against the spoken words, so only for captions of them.
        spoken = target.captions.enabled and target.captions.mode in ("speech", "lyrics")
        captions = work_dir / "captions.ass" if spoken else None
        issues = review.review(
            dest,
            edl,
            preset,
            analysis.duration,
            captions,
            compose.timeline_words(edl, analysis),
            vision,
            work_dir,
            info=info,
            loudness=loudness,
        )
        if not issues or renders > MAX_FIXES:
            return edl, renders, issues
        fixed, retargeted, changed = fix(issues, edl, target, analysis, preset.audio.loudness_lufs)
        if not changed:
            return edl, renders, issues
        # When only the loudness target moved, the picture already rendered stays as it is.
        remux = (
            fixed == edl
            and retargeted.model_copy(update={"audio": target.audio}) == target
            and not (target.intro or target.outro)
        )
        edl, target = fixed, retargeted
        renders += 1
        progress("render", f"re-rendering {preset.name}: {issues[0].detail}")


def run(
    source_ref: str,
    presets: list[Preset],
    settings: Settings,
    backends: Backends,
    notes: str | None = None,
    progress: Progress = lambda stage, message: log.info("%s: %s", stage, message),
    run_id: str | None = None,
) -> Result:
    """Each run's outputs go to a folder of their own, named by `run_id` (a new one when
    not given), so a later run of the same video and preset doesn't replace them."""
    progress("download", f"fetching {source_ref}")
    source = resolve_source(source_ref, settings)
    progress("title", source.title)
    work_dir = settings.cache_dir / file_hash(source.path)
    work_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_id or secrets.token_hex(4)
    # Two jobs on one video would write the same cache files.
    with slots.video(work_dir).turn("edit it", progress):
        return edit(source, work_dir, presets, settings, backends, notes, progress, run_id)


def edit(
    source: download.Source,
    work_dir: Path,
    presets: list[Preset],
    settings: Settings,
    backends: Backends,
    notes: str | None,
    progress: Progress,
    run_id: str,
) -> Result:
    progress("analyze", f"analyzing {source.title}")
    out_dir = settings.media_dir / "outputs" / work_dir.name / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    router = ModelRouter(settings, backends, progress)
    given_headline, notes = headline.from_notes(notes)
    given, notes = names.from_notes(notes, set().union(*(names.wanted(p) for p in presets)))
    presets, instructed, notes_for, start_at = understand(router, presets, notes, progress)
    failures: list[str] = []
    errors: list[Exception] = []

    def failed(what: str, exc: Exception) -> None:
        log.exception("%s failed", what)
        failures.append(f"{what}: {type(exc).__name__}: {exc}")
        errors.append(exc)

    # A preset that names the song and artist takes them from the notes or the video's
    # name, and so do its reel's title and post caption, rather than the planner's guess,
    # which can name the wrong artist; its own fields come from the notes or the name.
    # A preset with auto_name falls back to the sound, heard once for them all.
    named, songs = [], {}

    @functools.cache
    def heard() -> names.Song | None:
        progress("analyze", "recognising the song from its sound")
        return shazam.song(source.path, work_dir)

    for preset in presets:
        hear = heard if preset.auto_name else None
        try:
            if names.names_song(preset):
                songs[preset.name] = names.song_and_artist(source.title, given, hear)
            named.append(names.fill(preset, source.title, given, hear))
        except (names.UnnamedSong, names.MissingField) as exc:
            failed(preset.name, exc)
    presets = named
    if any(headline.wanted(p) for p in presets):
        presets = headlined(router, source, presets, given_headline, instructed, failed,
                            settings, progress)  # fmt: skip
    if not presets:
        raise RuntimeError("; ".join(failures)) from errors[-1]

    analyses: dict[str, Analysis] = {}
    for kind in dict.fromkeys(p.audio.source for p in presets):
        try:
            analyses[kind] = analyze(source.path, work_dir, router, kind, progress)
        except Exception as exc:
            # Vocals that can't be isolated sink the presets that need them, not the others.
            failed(", ".join(p.name for p in presets if p.audio.source == kind), exc)
    if not analyses:
        raise errors[0]  # the video itself can't be read: say why, not where it broke later
    presets = [p for p in presets if p.audio.source in analyses]

    silenced: dict[tuple[str, float, float], Analysis] = {}

    def for_preset(preset: Preset) -> Analysis:
        """The analysis with silences as this preset counts them, each way found once."""
        key = (preset.audio.source, preset.silence.threshold_db, preset.silence.min_duration)
        if key not in silenced:
            silenced[key] = with_silences(analyses[key[0]], *key[1:])
        return silenced[key]

    # Silences are read from the audio before the planner takes the GPU.
    for preset in list(presets):
        try:
            for_preset(preset)
        except Exception as exc:
            failed(preset.name, exc)
            presets.remove(preset)

    plans = {}
    with router.planner() as llm:
        for preset in presets:
            progress("plan", f"planning {preset.name}")
            planning_notes = notes_for[preset.name]
            applied, missed = instructed[preset.name]
            try:
                plans[preset.name] = [
                    (
                        translated(
                            llm,
                            edl.model_copy(
                                update={
                                    "decisions": [*applied, *edl.decisions],
                                    "doubts": [*missed, *edl.doubts],
                                }
                            ),
                            for_preset(preset),
                            preset,
                            planning_notes,
                        ),
                        planner,
                    )
                    for edl, planner in plan(
                        llm, for_preset(preset), preset, planning_notes, start_at
                    )
                ]
            except Exception as exc:
                failed(preset.name, exc)

    for name, (song, artist) in songs.items():
        title = f"{song} - {artist}"
        plans[name] = [(e.model_copy(update={"title": title}), p) for e, p in plans.get(name, [])]

    # Faces and the picture's black bars need only the CPU, so they are found before the
    # vision model takes the GPU. One that fails here is tried again by the render.
    detections: dict[tuple[float, float], list[faces.Sample]] = {}
    for preset in presets:
        analysis, prepared = for_preset(preset), []
        for edl, planner in plans.get(preset.name, []):
            clip = edl.clips[0]
            key = (clip.start, clip.end)
            try:
                if key not in detections and needs_faces(edl, preset, analysis):
                    detections[key] = faces.detect(
                        source.path, clip.start, clip.end, settings.models_dir
                    )
                if edl.crop_mode == "smart" and edl.picture is None:
                    size = (analysis.width, analysis.height)
                    picture = compose.picture_area(source.path, clip.start, clip.end, size)
                    edl = edl.model_copy(update={"picture": picture})
            except Exception as exc:
                log.warning("%s: faces or picture area left for the render: %s", preset.name, exc)
            prepared.append((edl, planner))
        plans[preset.name] = prepared

    outputs = []
    with router.vision() as vision:
        reviewer = vision if _reachable(vision) else None
        first = next(iter(analyses.values()))
        marks = confirmed_watermarks(
            first.watermarks, reviewer, source.path, settings, work_dir, progress,
            (first.width, first.height),
        )  # fmt: skip
        for preset, (edl, planner) in [(p, v) for p in presets for v in plans.get(p.name, [])]:
            analysis = for_preset(preset).model_copy(update={"watermarks": marks})
            name = preset.name + (f"-{'abcde'[edl.variant]}" if edl.variant else "")
            dest = out_dir / f"{name}.mp4"
            render_dir = work_dir / "render" / name
            try:
                edl = written_text(
                    reviewer,
                    edl,
                    analysis,
                    preset,
                    source.path,
                    settings,
                    render_dir,
                    notes_for[preset.name],
                )
                progress("render", f"rendering {name}")
                edl, renders, issues = produce(
                    edl,
                    preset,
                    analysis,
                    source.path,
                    dest,
                    render_dir,
                    settings,
                    backends,
                    reviewer,
                    progress,
                    detections,
                )
                thumbnail = None
                if reviewer is not None:
                    try:
                        thumbnail = review.pick_thumbnail(reviewer, dest, edl.duration, render_dir)
                    except LLMError as exc:
                        log.warning("no thumbnail for %s: %s", preset.name, exc)
                (out_dir / f"{name}.edl.json").write_text(edl.model_dump_json(indent=2))
                unresolved = [f"{i.kind}: {i.detail}" for i in issues]
                clip = edl.clips[0]
                quiet = silence.long_silences(clip.start, clip.end, analysis)
                score, doubts = agent.confidence(edl, unresolved, planner, preset, quiet)
                outputs.append(
                    Output(
                        preset.name,
                        dest,
                        edl,
                        planner,
                        thumbnail,
                        renders,
                        unresolved,
                        score,
                        doubts,
                        songs.get(preset.name),
                    )
                )
                progress("done", f"{name} -> {dest}")
            except Exception as exc:
                # One version that can't be made doesn't lose the ones already rendered.
                failed(name, exc)

    if errors and not outputs:
        if len(errors) == 1:
            raise errors[0]
        raise RuntimeError("; ".join(failures)) from errors[-1]
    meta = {"title": source.title, "url": source.url, "notes": notes,
            "uploader": source.uploader, "description": source.description}  # fmt: skip
    (out_dir / "source.json").write_text(json.dumps(meta))
    return Result(source.title, outputs, failures)


def understand(
    router: ModelRouter, presets: list[Preset], notes: str | None, progress: Progress
) -> tuple[list[Preset], dict[str, tuple[list[str], list[str]]], dict[str, str | None], str | None]:
    """Read the editor's instruction into each preset: the presets to use, what was changed
    in each (and what couldn't be), the notes left for the planner, and where the
    instruction says to start."""
    decisions: dict[str, tuple[list[str], list[str]]] = {p.name: ([], []) for p in presets}
    planning: dict[str, str | None] = {p.name: notes for p in presets}
    if not notes or not notes.strip():
        return presets, decisions, planning, None
    progress("plan", "reading your instruction")
    try:
        with router.planner() as llm:
            wanted = agent.interpret(llm, notes)
    except (LLMError, httpx.HTTPError) as exc:
        note = f"couldn't read the instruction ({exc}); kept the preset"
        return presets, {p.name: ([note], [note]) for p in presets}, planning, None
    updated = []
    for preset in presets:
        changed, applied, failed = agent.apply(preset, wanted.requests, notes)
        decisions[preset.name] = (applied, failed)
        # The planner gets what the instruction said about the content; when the model
        # didn't separate that out, it gets the whole instruction rather than nothing.
        if wanted.content_notes.strip():
            planning[preset.name] = wanted.content_notes.strip()
        updated.append(changed)
    return updated, decisions, planning, agent.start_point(wanted.requests, notes)


def headlined(
    router: ModelRouter,
    source: download.Source,
    presets: list[Preset],
    given: str | None,
    decisions: dict[str, tuple[list[str], list[str]]],
    failed: Callable[[str, Exception], None],
    settings: Settings,
    progress: Progress,
) -> list[Preset]:
    """The presets with {headline} filled in: the one the notes give, or one the planner
    writes for each preset from the video's title and description."""
    face = compose.text_face(settings, None)
    done = []
    with ExitStack() as stack:
        llm = None
        for preset in presets:
            if not headline.wanted(preset):
                done.append(preset)
                continue
            try:
                if given:
                    line, said = given, "from your notes"
                else:
                    if llm is None:
                        progress("plan", "writing the headline")
                        llm = stack.enter_context(router.planner())
                    line, said = headline.write(llm, source, preset), "written from the title"
            except (LLMError, httpx.HTTPError, headline.NoHeadline) as exc:
                failed(preset.name, exc)
                continue
            decisions[preset.name][0].append(f"headline {said}: {line}")
            done.append(headline.fill(preset, line, face))
    return done


def confirmed_watermarks(
    candidates: list[Watermark],
    vision: OllamaClient | None,
    video: Path,
    settings: Settings,
    work_dir: Path,
    progress: Progress,
    frame: tuple[int, int],
) -> list[Watermark]:
    """Candidates the vision model agrees are overlays, and only those the size of one. A
    blur over a piece of the scene spoils a video more than a watermark left in, so without
    the vision model nothing is blurred."""
    width, height = frame
    candidates = [
        m
        for m in candidates
        if m.width * m.height <= BLUR_AREA * width * height
        and m.height <= BLUR_HEIGHT * height
        and m.width <= BLUR_WIDTH * width
    ]
    if not candidates:
        return []
    if vision is None:
        log.warning("no vision model; leaving %d possible watermark(s) unblurred", len(candidates))
        return []
    model = vision.model.replace(":", "_").replace("/", "_")
    cache = work_dir / f"watermarks-v{watermarks.VERSION}-{model}.json"
    if cache.exists():
        checked = [Watermark.model_validate(m) for m in json.loads(cache.read_text())]
    else:
        progress("analyze", f"checking {len(candidates)} possible watermark(s)")
        checked = watermarks.confirm(
            vision, video, candidates, settings.ffmpeg_bin, work_dir / "watermark-checks"
        )
        cache.write_text(json.dumps([m.model_dump() for m in checked]))
    kept = [m for m in checked if m.confirmed]
    log.info("%d of %d watermark candidate(s) confirmed", len(kept), len(checked))
    return kept


def _reachable(client: OllamaClient) -> bool:
    try:
        client.http.get(f"{client.base_url}/api/version", timeout=3)
    except httpx.HTTPError:
        log.warning("Ollama is not reachable, skipping vision review and thumbnails")
        return False
    return True
