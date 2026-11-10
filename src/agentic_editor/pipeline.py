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
from PIL import Image

from agentic_editor import (
    agent,
    caption,
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
from agentic_editor.agent import LANGUAGES
from agentic_editor.analysis import faces, ocr, onscreen, subjects, watermarks
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
    """Subjects at each time, cached so a re-render doesn't ask again: heads, instruments
    and the rest from the vision model, on-screen text from OCR (the vision model's own text
    boxes only where OCR can't run here)."""
    if not times:
        return []
    found: dict[float, list[subjects.Subject]] = {t: [] for t in times}
    if vision is not None:
        cache = work_dir / f"subjects-{vision.model.replace(':', '_').replace('/', '_')}.json"
        known = json.loads(cache.read_text()) if cache.exists() else {}
        missing = [t for t in times if f"{t:.3f}" not in known]
        if missing:
            size = (analysis.width, analysis.height)
            asked = subjects.find(
                vision, video, missing, size, settings.ffmpeg_bin, work_dir / "views"
            )
            for t, items in zip(missing, asked, strict=True):
                if items is not None:  # a failed answer is asked again next time
                    known[f"{t:.3f}"] = [s.model_dump() for s in items]
            cache.write_text(json.dumps(known))
        found = {
            t: [subjects.Subject.model_validate(s) for s in known.get(f"{t:.3f}", [])]
            for t in times
        }
    texts = text_lines(video, times, analysis, settings, work_dir)
    if texts is not None:
        found = {t: [s for s in found[t] if s.kind != "text"] + texts[t] for t in times}
    return [(t, found[t]) for t in times]


OCR_VERSION = 2
TEXT_EVERY = 2.0  # seconds between OCR looks for the picture band's edge text


def text_lines(
    video: Path, times: list[float], analysis: Analysis, settings: Settings, work_dir: Path
) -> dict[float, list[subjects.Subject]] | None:
    """The text OCR reads at each time, as subjects in source pixels, cached; None where
    OCR can't run (not installed, its models can't be fetched)."""
    if not ocr.available():
        return None
    cache = work_dir / f"texts-ocr-v{OCR_VERSION}.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    missing = [t for t in times if f"{t:.3f}" not in known]
    if missing:
        views = work_dir / "views-ocr"
        views.mkdir(parents=True, exist_ok=True)
        try:
            for t in missing:
                frame = subjects.grab(video, t, views / f"{t:.3f}.jpg", settings.ffmpeg_bin)
                with Image.open(frame) as picture:
                    scale = analysis.width / picture.width
                    lines = ocr.read(picture, settings.models_dir, whole_frame=True)
                known[f"{t:.3f}"] = [
                    subjects.Subject(
                        kind="text",
                        label=line.text[:80],
                        x0=round(line.x0 * scale),
                        y0=round(line.y0 * scale),
                        x1=round(line.x1 * scale),
                        y1=round(line.y1 * scale),
                        keep=True,
                    ).model_dump()
                    for line in lines
                ]
        except Exception as exc:  # OCR is an improvement, never a reason to fail a job
            log.warning("no OCR here (%s); the vision model's text boxes are used", exc)
            return None
        finally:
            cache.write_text(json.dumps(known))
    return {
        t: [subjects.Subject.model_validate(s) for s in known.get(f"{t:.3f}", [])] for t in times
    }


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


def line_language(preset: Preset) -> str | None:
    """The language the one-liner is written in, by name: its own, else the captions' when
    they are translated, else None (the model's choice)."""
    language = preset.one_liner.language or (
        preset.captions.language if preset.captions.mode == "translated" else None
    )
    if not language or not language.strip():
        return None
    return LANGUAGES.get(language.strip().lower(), language.strip())


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
            language = line_language(preset)
            update["one_liner"] = subtitles.one_line(
                vision, frames(4, "line"), line.max_words, line.hint, context, notes, language
            )
            decisions.append(f'one-liner: "{update["one_liner"]}"')
        except LLMError as exc:
            doubts.append(f"one-liner failed ({exc})")
            decisions.append(doubts[-1])
    return edl.model_copy(update={**update, "decisions": decisions, "doubts": doubts})


def without_text(items: list[subjects.Subject]) -> list[subjects.Subject]:
    """Must-keep items but text: for footage whose point is the action (keep_action), text
    burned into it is another page's caption or name, better cropped away than kept."""
    return [s for s in items if s.kind != "text"]


def drops_text(preset: Preset) -> bool:
    """Whether smart framing leaves text out of what must stay in view: footage whose
    point is the action, or a preset that crops a video's own text away."""
    return preset.framing.keep_action or preset.framing.only_with_text


# A video's own text, for only_with_text: a line of at least two words this wide (of the
# picture), seen at two looks or more (one, when there is only one), and not a watermark
# (those get blurred). A sign or a username in a corner is not the video's text.
OWN_TEXT_WIDTH = 0.15
OWN_TEXT_PAD = 0.01  # of the picture's height, around each line
OWN_TEXT_LEAST = 0.4  # the share of the picture's rows the crop must keep, or none is cut
# Text is cropped away only along the picture's top or bottom, within this share of its
# height: text across the middle (sung lyrics over a face) would take the subject with it.
OWN_TEXT_EDGE = 0.35


def own_text(
    times: list[float],
    found: dict[float, list[subjects.Subject]],
    size: tuple[int, int],
    marks: list[Watermark],
) -> list[tuple[int, int]]:
    """The rows of the frame that hold the video's own text, merged, from what was read at
    `times`; empty when it has none."""

    def counts(s: subjects.Subject) -> bool:
        words = s.label.split()
        if len(words) < 2 or sum(c.isalpha() for c in s.label) < 6:
            return False
        if s.x1 - s.x0 < OWN_TEXT_WIDTH * size[0]:
            return False
        middle = ((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2)
        return not any(
            m.x <= middle[0] <= m.x + m.width and m.y <= middle[1] <= m.y + m.height for m in marks
        )

    seen = [[s for s in found.get(t, []) if s.kind == "text" and counts(s)] for t in times]
    looks = sum(bool(lines) for lines in seen)
    if looks == 0 or (looks < 2 and len(times) > 1):
        return []
    pad = OWN_TEXT_PAD * size[1]
    rows = sorted((max(0, s.y0 - pad), min(size[1], s.y1 + pad)) for lines in seen for s in lines)
    merged: list[list[float]] = []
    for top, bottom in rows:
        if merged and top <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], bottom)
        else:
            merged.append([top, bottom])
    return [(round(a), round(b)) for a, b in merged]


def clear_band(
    content: tuple[int, int, int, int], rows: list[tuple[int, int]]
) -> tuple[int, int, int, int] | None:
    """The tallest stretch of the picture's rows without text, at its full width, or None
    when text crosses its middle or leaves too little of it (OWN_TEXT_EDGE, _LEAST)."""
    x, y, width, height = content
    edges, top = [], y
    for a, b in rows:
        if b <= y or a >= y + height:
            continue
        if b > y + OWN_TEXT_EDGE * height and a < y + (1 - OWN_TEXT_EDGE) * height:
            return None
        if a > top:
            edges.append((top, a))
        top = max(top, b)
    if top < y + height:
        edges.append((top, y + height))
    if not edges:
        return None
    a, b = max(edges, key=lambda e: e[1] - e[0])
    a, b = (a + 1) // 2 * 2, b // 2 * 2
    if b - a < OWN_TEXT_LEAST * height:
        return None
    return x, a, width, b - a


def crop_or_whole(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    settings: Settings,
    work_dir: Path,
    vision: OllamaClient | None,
) -> EDL:
    """For only_with_text: a video with text of its own keeps smart framing, inside the
    rows its text leaves; one without is shown whole (mode none)."""
    if edl.crop_mode != "smart" or not preset.framing.only_with_text:
        return edl
    clip = edl.clips[0]
    size = (analysis.width, analysis.height)
    times = framing.view_times(clip, analysis.scenes, every=TEXT_EVERY)
    found = text_lines(video, times, analysis, settings, work_dir)
    if found is None:
        if vision is None:
            return edl.noted("couldn't look for the video's own text (no OCR, no vision model)")
        found = dict(subject_samples(vision, video, times, analysis, settings, work_dir))
    rows = own_text(times, found, size, analysis.watermarks)
    if not rows:
        return edl.model_copy(
            update={
                "crop_mode": "none",
                "picture": None,
                "shots": [],
                "decisions": [*edl.decisions, "no text of its own on the video: shown whole"],
            }
        )
    content = edl.picture or compose.picture_area(video, clip.start, clip.end, size)
    seen = ", ".join(f"{a}-{b}" for a, b in rows)
    band = clear_band(content, rows)
    if band is None:
        note = (
            f"text on the video (rows {seen}) crosses the middle of the picture or covers too "
            "much of it to crop away; framed smartly on the whole picture"
        )
        return edl.model_copy(update={"picture": content, "decisions": [*edl.decisions, note]})
    note = (
        f"text on the video (rows {seen}): cropped away, framed smartly inside rows "
        f"{band[1]}-{band[1] + band[3]}"
    )
    return edl.model_copy(update={"picture": band, "decisions": [*edl.decisions, note]})


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
        items = without_text(found[t]) if drops_text(preset) else found[t]
        spans = framing.keep_spans(items, face_samples, t, analysis.width)
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
    shared = not preset.framing.keep_action
    return framing.harmonize(updated, width, content, shared), [note]


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
    """Cut a picture shown whole to fit the band between the preset's text (or its
    picture_rows), at the frame's full width: top and bottom go, around the faces, and
    with them any text the picture brings along its edges."""
    if not preset.text_blocks and preset.framing.picture_rows is None:
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
    # Burnt-in lyrics come and go line by line, so one look a shot misses them: OCR (cheap,
    # off the GPU) looks every few seconds, and a line along an edge at any moment goes.
    every = framing.view_times(clip, analysis.scenes, every=TEXT_EVERY)
    if (texts := text_lines(video, every, analysis, settings, work_dir)) is not None:
        marks += [(s.y0, s.y1) for items in texts.values() for s in items]
    if not preset.watermarks.cut_text:
        # The video's own text stays in view: nothing is cut for it.
        samples = [(t, [s for s in items if s.kind != "text"]) for t, items in samples]
        marks = []
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
        note += (
            f"; framed on the moving part, rows {moving.top}-{moving.bottom}, not the still image"
        )
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
    if preset.framing.only_with_text:
        samples = [(t, without_text(items)) for t, items in samples]
    if preset.framing.keep_action:
        size = (analysis.width, analysis.height)
        actions = compose.action_subjects(video, clip.start, clip.end, analysis.scenes, size,
                                          content)  # fmt: skip
        samples = [
            (t, [*without_text(items), *(box for a, b, box in actions if a <= t < b)])
            for t, items in samples
        ]
    seen = framing.views(samples, face_samples, content, analysis.width)
    if not edl.shots:
        # Cropped top and bottom: a still image above or below the clip counts less.
        tall = width >= content[2] and framing.crop_height(content, preset.size) < content[3]
        size = (analysis.width, analysis.height)
        moving = compose.moving_part(video, clip.start, clip.end, size, content) if tall else None
        shots = framing.frame_shots(
            clip, analysis.scenes, samples, face_samples, content, analysis.width, preset.size,
            analysis.height, moving, shared=not preset.framing.keep_action,
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
    edl = crop_or_whole(edl, preset, analysis, video, settings, work_dir, vision)
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
    """Every preset's version of the video. A preset that only upscales (preset.upscale)
    is made from the whole video by itself; the others are edited together (edit_presets),
    and one side failing doesn't lose what the other made."""
    upscaling = [p for p in presets if p.upscale.enabled]
    if not upscaling:
        return edit_presets(source, work_dir, presets, settings, backends, notes, progress,
                            run_id)  # fmt: skip
    out_dir = settings.media_dir / "outputs" / work_dir.name / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Output] = []
    failures: list[str] = []
    errors: list[Exception] = []
    for preset in upscaling:
        try:
            outputs.append(upscaled(source, preset, out_dir, settings, backends, progress))
        except Exception as exc:
            log.exception("upscaling %s failed", preset.name)
            failures.append(f"{preset.name}: {type(exc).__name__}: {exc}")
            errors.append(exc)
    rest = [p for p in presets if not p.upscale.enabled]
    if rest:
        try:
            edited = edit_presets(source, work_dir, rest, settings, backends, notes, progress,
                                  run_id)  # fmt: skip
            outputs += edited.outputs
            failures += edited.failures
        except Exception as exc:
            if not outputs:
                raise
            log.exception("editing failed")
            failures.append(f"{type(exc).__name__}: {exc}")
    else:
        meta = {"title": source.title, "url": source.url, "notes": notes,
                "uploader": source.uploader, "description": source.description}  # fmt: skip
        (out_dir / "source.json").write_text(json.dumps(meta))
    if not outputs:
        if len(errors) == 1:
            raise errors[0]
        raise RuntimeError("; ".join(failures)) from errors[-1]
    return Result(source.title, outputs, failures)


def upscaled(
    source: download.Source,
    preset: Preset,
    out_dir: Path,
    settings: Settings,
    backends: Backends,
    progress: Progress,
) -> Output:
    """The whole video, bigger and sharper and otherwise as it was (see upscale/)."""
    from agentic_editor import upscale

    dest = out_dir / f"{preset.name}.mp4"
    wanted = preset.upscale
    progress("render", f"upscaling for {preset.name}")
    sizes = upscale.upscale(source.path, dest, settings.models_dir, backends, wanted.size,
                            wanted.faces, wanted.denoise, progress)  # fmt: skip
    length = ffmpeg.probe(dest).duration
    thumbnail = out_dir / f"{preset.name}-thumb.jpg"
    try:
        ffmpeg.run(["-y", "-ss", f"{length / 3:.2f}", "-i", str(dest), "-frames:v", "1",
                    "-vf", "scale=720:-2", "-q:v", "3", str(thumbnail)])  # fmt: skip
    except ffmpeg.FFmpegError as exc:
        log.warning("no thumbnail for %s: %s", preset.name, exc)
        thumbnail = None
    (w, h), (ow, oh) = sizes.source, sizes.out
    faces = f", faces {wanted.faces:.0%}" if wanted.faces else ", faces left as they were"
    edl = EDL(
        clips=[Clip(start=0.0, end=max(length, 0.1), reason="the whole video, upscaled")],
        title=source.title,
        crop_mode="none",
        decisions=[f"upscaled {w}x{h} to {ow}x{oh}{faces}, denoise {wanted.denoise:.0%}"],
    )
    (out_dir / f"{preset.name}.edl.json").write_text(edl.model_dump_json(indent=2))
    progress("done", f"{preset.name} -> {dest}")
    return Output(preset.name, dest, edl, "upscale", thumbnail)


def edit_presets(
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
    texts, notes = names.texts_from_notes(notes)
    given_headline, notes = headline.from_notes(notes)
    given_headline = given_headline or names.headline_from_texts(presets, texts)
    presets = [names.with_texts(p, texts) for p in presets]
    # Text that moves with the picture, where the preset moves it (framing.text_follows_video).
    presets = [compose.moved_layout(p) for p in presets]
    # "song:" and "artist:" lines are always taken, so the instruction reader never sees
    # them; a preset that doesn't name the song still gets them for its reel's song.
    wanted = set().union({"song", "artist"}, *(names.wanted(p) for p in presets))
    given, notes = names.from_notes(notes, wanted)
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
    # A preset with auto_name falls back to the sound, heard once for them all, and when
    # Shazam doesn't know it (a live cover on a TV show), to what the post's caption says.
    named, songs = [], {}

    @functools.cache
    def heard() -> names.Song | None:
        progress("analyze", "recognising the song from its sound")
        if found := shazam.song(source.path, work_dir):
            return found
        if not source.description.strip():
            return None
        progress("analyze", "reading the song from the post's caption")
        try:
            with router.planner() as llm:
                return caption.performed_in(llm, source.title, source.description)
        except (LLMError, httpx.HTTPError) as exc:
            log.warning("couldn't read the song from the caption: %s", exc)
            return None

    @functools.cache
    def came_out(song: str, artist: str) -> str | None:
        """The song's year for {year}: the post's caption says it, else MusicBrainz."""
        if found := names.year_in(f"{source.title}\n{source.description}", song):
            return found
        progress("analyze", f"looking up when {song} came out")
        return shazam.first_year(song, artist)

    for preset in presets:
        hear = heard if preset.auto_name else None
        try:
            if names.names_song(preset):
                songs[preset.name] = names.song_and_artist(source.title, given, hear)
            elif given.get("song") and given.get("artist"):
                songs[preset.name] = (given["song"], given["artist"])
            named.append(names.fill(preset, source.title, given, hear, came_out))
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
        # The language a preset says is sung or said there; the first one's, if they differ.
        told = next((p.audio.language for p in presets
                     if p.audio.source == kind and p.audio.language), None)  # fmt: skip
        try:
            analyses[kind] = analyze(source.path, work_dir, router, kind, progress, told)
        except Exception as exc:
            # Vocals that can't be isolated sink the presets that need them, not the others.
            failed(", ".join(p.name for p in presets if p.audio.source == kind), exc)
    if not analyses:
        raise errors[0]  # the video itself can't be read: say why, not where it broke later
    presets = [p for p in presets if p.audio.source in analyses]
    # A headline written by watching the clip waits for what it says.
    if not given_headline and any(headline.wanted(p) and p.headline.research for p in presets):
        presets = headlined(router, source, presets, None, instructed, failed, settings,
                            progress, said=lambda p: said_in(analyses[p.audio.source]),
                            notes_for=notes_for)  # fmt: skip
        if not presets:
            raise RuntimeError("; ".join(failures)) from errors[-1]

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
    said: Callable[[Preset], str] | None = None,
    notes_for: dict[str, str | None] | None = None,
) -> list[Preset]:
    """The presets with {headline} filled in: the one the notes give, or one the planner
    writes for each preset from the video's title and description. A preset that writes it
    by watching the clip (Headline.research) waits for `said`, what the clip says: it is
    passed over until then, and only it is written once `said` is given."""
    face = compose.text_face(settings, None)
    done = []
    with ExitStack() as stack:
        llm = None
        for preset in presets:
            researched = preset.headline.research and not given
            if not headline.wanted(preset) or researched != (said is not None):
                done.append(preset)
                continue
            doubts: list[str] = []
            try:
                if given:
                    line, how = given, "from your notes"
                else:
                    if llm is None:
                        progress("plan", "writing the headline")
                        llm = stack.enter_context(router.planner())
                    if said is not None:
                        cache = settings.cache_dir / "captions"
                        line, doubts, spoken = headline.write_researched(
                            llm, source, preset, said(preset), cache
                        )
                        how = "written from the clip and Wikipedia"
                        if spoken and notes_for is not None:
                            keep = f'Keep the line "{spoken}" in the reel.'
                            notes_for[preset.name] = " ".join(
                                filter(None, [notes_for.get(preset.name), keep])
                            )
                    else:
                        line, how = headline.write(llm, source, preset), "written from the title"
            except (LLMError, httpx.HTTPError, headline.NoHeadline) as exc:
                failed(preset.name, exc)
                continue
            decisions[preset.name][0].append(f"headline {how}: {line}")
            decisions[preset.name][1].extend(doubts)
            done.append(headline.fill(preset, line, face))
    return done


def said_in(analysis: Analysis) -> str:
    return " ".join(w.text for w in analysis.transcript.words)


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
    reads = ocr.available()  # decided with OCR's reading or the vision model's: kept apart
    cache = work_dir / f"watermarks-v{watermarks.VERSION}-{model}{'-ocr' if reads else ''}.json"
    if cache.exists():
        checked = [Watermark.model_validate(m) for m in json.loads(cache.read_text())]
    else:
        progress("analyze", f"checking {len(candidates)} possible watermark(s)")

        def read(close_up: Path) -> str:
            return " ".join(line.text for line in ocr.read(close_up, settings.models_dir))

        checked = watermarks.confirm(
            vision, video, candidates, settings.ffmpeg_bin, work_dir / "watermark-checks",
            read if reads else None,
        )  # fmt: skip
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
