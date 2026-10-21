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

from agentic_editor import download, review
from agentic_editor.analysis import faces
from agentic_editor.analyze import Analysis, analyze, file_hash, with_silences
from agentic_editor.backends import Backends
from agentic_editor.cuts import clean_edl
from agentic_editor.edl import EDL
from agentic_editor.heuristic import plan_by_energy
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.planner import build_lines, fit_duration, plan_with_llm
from agentic_editor.presets import Preset
from agentic_editor.render import compose
from agentic_editor.render.video import crop_size
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]
MAX_FIXES = 2


@dataclass
class Output:
    preset: str
    path: Path
    edl: EDL
    planner: str
    thumbnail: Path | None = None
    renders: int = 1
    issues: list[str] = field(default_factory=list)


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def resolve_source(value: str, settings: Settings) -> download.Source:
    if is_url(value):
        return download.fetch(value, settings.media_dir / "sources")
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
    edl = clean_edl(edl, analysis.transcript.words, analysis.silences, preset.silence.max_pause)
    return edl, planner


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


def fix(
    issues: list[review.Issue], edl: EDL, preset: Preset, analysis: Analysis, loudness_goal: float
) -> tuple[EDL, Preset, bool]:
    """Deterministic fixes for review issues. Returns the new plan and whether it changed."""
    changed = False
    for issue in issues:
        if issue.kind == "duration":
            clips = fit_duration(edl.clips, build_lines(analysis), preset)
            if clips != edl.clips:
                edl, changed = edl.model_copy(update={"clips": clips}), True
        elif issue.kind == "loudness" and issue.measured is not None:
            # Aim off-target by the error just measured; loudnorm falls back to dynamic
            # mode on some material and lands short of where it was asked to go.
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
        commands = face_commands(edl, target, analysis, video, settings, work_dir, detections)
        compose.render(edl, target, analysis, video, dest, work_dir, settings, backends, commands)
        progress("review", f"checking {preset.name}")
        captions = work_dir / "captions.ass" if target.captions.enabled else None
        issues = review.review(
            dest, edl, preset, analysis.duration, captions,
            compose.timeline_words(edl, analysis), vision, work_dir,
        )  # fmt: skip
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
) -> list[Output]:
    progress("download", f"fetching {source_ref}")
    source = resolve_source(source_ref, settings)
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
        for preset in presets:
            analysis = for_preset(preset)
            edl, planner = plans[preset.name]
            dest = out_dir / f"{preset.name}.mp4"
            render_dir = work_dir / "render" / preset.name
            progress("render", f"rendering {preset.name}")
            edl, renders, issues = produce(edl, preset, analysis, source.path, dest, render_dir,
                                           settings, backends, reviewer, progress)  # fmt: skip
            thumbnail = None
            if reviewer is not None:
                thumbnail = review.pick_thumbnail(reviewer, dest, edl.duration, render_dir)
            (out_dir / f"{preset.name}.edl.json").write_text(edl.model_dump_json(indent=2))
            outputs.append(Output(preset.name, dest, edl, planner, thumbnail, renders,
                                  [f"{i.kind}: {i.detail}" for i in issues]))  # fmt: skip
            progress("done", f"{preset.name} -> {dest}")

    meta = {"title": source.title, "url": source.url, "notes": notes}
    (out_dir / "source.json").write_text(json.dumps(meta))
    return outputs


def _reachable(client: OllamaClient) -> bool:
    try:
        client.http.get(f"{client.base_url}/api/version", timeout=3)
    except httpx.HTTPError:
        log.warning("Ollama is not reachable, skipping vision review and thumbnails")
        return False
    return True
