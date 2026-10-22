"""End-to-end run for one source video and a set of presets.

Stages run one model at a time: separation and whisper (child processes), then the
planning LLM for every preset, then rendering with the vision model reviewing results.
Each model is unloaded before the next stage starts."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agentic_editor import download, framing, review, trim
from agentic_editor.analysis import faces, subjects, watermarks
from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.analyze import Analysis, analyze, file_hash, with_silences
from agentic_editor.backends import Backends
from agentic_editor.cuts import clean_edl
from agentic_editor.edl import EDL, Shot
from agentic_editor.heuristic import plan_by_energy
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.planner import plan_with_llm
from agentic_editor.presets import Preset
from agentic_editor.render import compose
from agentic_editor.render.video import crop_size
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]
MAX_FIXES = 2
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


@dataclass
class Result:
    title: str
    outputs: list[Output]


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def resolve_source(value: str, settings: Settings) -> download.Source:
    if is_url(value):
        return download.fetch(value, settings.media_dir / "sources")
    if value.startswith(UPLOAD_PREFIX):
        return download.local(settings.media_dir / "uploads" / value.removeprefix(UPLOAD_PREFIX))
    return download.local(Path(value).expanduser())


def plan(
    llm: OllamaClient, analysis: Analysis, preset: Preset, notes: str | None
) -> tuple[EDL, str]:
    """Plan with the LLM, falling back to the energy heuristic if it can't deliver."""
    try:
        edl, planner = plan_with_llm(llm, analysis, preset, notes), llm.model
    except (LLMError, httpx.HTTPError) as exc:
        log.warning("%s: LLM planning failed (%s), using the energy heuristic", preset.name, exc)
        edl, planner = plan_by_energy(analysis, preset), "heuristic"
    edl = clean_edl(edl, analysis.transcript.words, analysis.silences)
    return trim.apply(edl, analysis, preset), planner


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
            known[f"{t:.3f}"] = [s.model_dump() for s in items]
        cache.write_text(json.dumps(known))
    return [(t, [subjects.Subject.model_validate(s) for s in known[f"{t:.3f}"]]) for t in times]


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
        shots = framing.frame_shots(
            clip, analysis.scenes, samples, face_samples, content, analysis.width, preset.size
        )
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


def fix(
    issues: list[review.Issue], edl: EDL, preset: Preset, analysis: Analysis, loudness_goal: float
) -> tuple[EDL, Preset, bool]:
    """Deterministic fixes for review issues. Returns the new plan and whether it changed."""
    changed = False
    for issue in issues:
        if issue.kind == "duration":
            trimmed = trim.apply(edl, analysis, preset)
            if trimmed.clips != edl.clips:
                edl, changed = trimmed, True
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
) -> tuple[EDL, int, list[review.Issue]]:
    """Render, review, fix and re-render up to MAX_FIXES times."""
    work_dir.mkdir(parents=True, exist_ok=True)
    detections: dict[tuple[float, float], list[faces.Sample]] = {}
    target = preset
    renders = 1
    while True:
        if edl.crop_mode == "smart":
            edl, commands = smart_framing(
                edl, target, analysis, video, settings, work_dir, detections, vision
            )
        else:
            commands = face_commands(edl, target, analysis, video, settings, work_dir, detections)
        compose.render(edl, target, analysis, video, dest, work_dir, settings, backends, commands)
        progress("review", f"checking {preset.name}")
        captions = work_dir / "captions.ass" if target.captions.enabled else None
        issues = review.review(
            dest,
            edl,
            preset,
            analysis.duration,
            captions,
            compose.timeline_words(edl, analysis),
            vision,
            work_dir,
        )
        if not issues or renders > MAX_FIXES:
            return edl, renders, issues
        edl, target, changed = fix(issues, edl, target, analysis, preset.audio.loudness_lufs)
        if not changed:
            return edl, renders, issues
        renders += 1
        progress("render", f"re-rendering {preset.name}: {issues[0].detail}")


def run(
    source_ref: str,
    presets: list[Preset],
    settings: Settings,
    backends: Backends,
    notes: str | None = None,
    progress: Progress = lambda stage, message: log.info("%s: %s", stage, message),
) -> Result:
    progress("download", f"fetching {source_ref}")
    source = resolve_source(source_ref, settings)
    progress("analyze", f"analyzing {source.title}")
    work_dir = settings.cache_dir / file_hash(source.path)
    out_dir = settings.media_dir / "outputs" / work_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    router = ModelRouter(settings, backends)

    analyses: dict[str, Analysis] = {}
    for preset in presets:
        if preset.audio.source not in analyses:
            analyses[preset.audio.source] = analyze(
                source.path, work_dir, router, preset.audio.source, progress
            )

    def for_preset(preset: Preset) -> Analysis:
        base = analyses[preset.audio.source]
        return with_silences(base, preset.silence.threshold_db, preset.silence.min_duration)

    plans = {}
    with router.planner() as llm:
        for preset in presets:
            progress("plan", f"planning {preset.name}")
            plans[preset.name] = plan(llm, for_preset(preset), preset, notes)

    outputs = []
    with router.vision() as vision:
        reviewer = vision if _reachable(vision) else None
        candidates = next(iter(analyses.values())).watermarks
        marks = confirmed_watermarks(
            candidates, reviewer, source.path, settings, work_dir, progress
        )
        for preset in presets:
            analysis = for_preset(preset).model_copy(update={"watermarks": marks})
            edl, planner = plans[preset.name]
            dest = out_dir / f"{preset.name}.mp4"
            render_dir = work_dir / "render" / preset.name
            progress("render", f"rendering {preset.name}")
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
            )
            thumbnail = None
            if reviewer is not None:
                try:
                    thumbnail = review.pick_thumbnail(reviewer, dest, edl.duration, render_dir)
                except LLMError as exc:
                    log.warning("no thumbnail for %s: %s", preset.name, exc)
            (out_dir / f"{preset.name}.edl.json").write_text(edl.model_dump_json(indent=2))
            outputs.append(
                Output(
                    preset.name,
                    dest,
                    edl,
                    planner,
                    thumbnail,
                    renders,
                    [f"{i.kind}: {i.detail}" for i in issues],
                )
            )
            progress("done", f"{preset.name} -> {dest}")

    meta = {"title": source.title, "url": source.url, "notes": notes}
    (out_dir / "source.json").write_text(json.dumps(meta))
    return Result(source.title, outputs)


def confirmed_watermarks(
    candidates: list[Watermark],
    vision: OllamaClient | None,
    video: Path,
    settings: Settings,
    work_dir: Path,
    progress: Progress,
) -> list[Watermark]:
    """Candidates the vision model agrees are overlays. Without it every candidate is
    blurred: a smudge on some scenery is a smaller miss than a watermark left in."""
    if not candidates:
        return []
    if vision is None:
        log.warning("no vision model; blurring %d unconfirmed watermark(s)", len(candidates))
        return candidates
    cache = work_dir / f"watermarks-{vision.model.replace(':', '_').replace('/', '_')}.json"
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
