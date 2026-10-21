"""End-to-end run for one source video and a set of presets."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentic_editor import download
from agentic_editor.analyze import Analysis, analyze, file_hash, with_silences
from agentic_editor.backends import Backends
from agentic_editor.cuts import clean_edl
from agentic_editor.edl import EDL
from agentic_editor.heuristic import plan_by_energy
from agentic_editor.presets import Preset
from agentic_editor.render import compose
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]


@dataclass
class Output:
    preset: str
    path: Path
    edl: EDL
    notes: list[str] = field(default_factory=list)


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def resolve_source(value: str, settings: Settings) -> download.Source:
    if is_url(value):
        return download.fetch(value, settings.media_dir / "sources")
    return download.local(Path(value).expanduser())


def plan(analysis: Analysis, preset: Preset) -> EDL:
    edl = plan_by_energy(analysis, preset)
    return clean_edl(edl, analysis.transcript.words, analysis.silences, preset.silence.max_pause)


def run(
    source_ref: str,
    presets: list[Preset],
    settings: Settings,
    backends: Backends,
    progress: Progress = lambda stage, message: log.info("%s: %s", stage, message),
) -> list[Output]:
    progress("download", f"fetching {source_ref}")
    source = resolve_source(source_ref, settings)
    work_dir = settings.cache_dir / file_hash(source.path)
    out_dir = settings.media_dir / "outputs" / work_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    router = ModelRouter(settings, backends)

    outputs = []
    analyses: dict[str, Analysis] = {}
    for preset in presets:
        if preset.audio.source not in analyses:
            analyses[preset.audio.source] = analyze(
                source.path, work_dir, router, preset.audio.source, progress
            )
        analysis = with_silences(
            analyses[preset.audio.source], preset.silence.threshold_db, preset.silence.min_duration
        )
        progress("plan", f"planning {preset.name}")
        edl = plan(analysis, preset)
        progress("render", f"rendering {preset.name}")
        dest = out_dir / f"{preset.name}.mp4"
        compose.render(edl, preset, analysis, source.path, dest, work_dir / "render" / preset.name,
                       settings, backends)  # fmt: skip
        (out_dir / f"{preset.name}.edl.json").write_text(edl.model_dump_json(indent=2))
        outputs.append(Output(preset.name, dest, edl))
        progress("done", f"{preset.name} -> {dest}")

    (out_dir / "source.json").write_text(json.dumps({"title": source.title, "url": source.url}))
    return outputs
