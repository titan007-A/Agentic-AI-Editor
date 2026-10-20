"""Runs the render: soundtrack, overlays, captions and the final encode."""

import logging
import re
import time
from pathlib import Path

from agentic_editor import ffmpeg
from agentic_editor.analyze import Analysis
from agentic_editor.backends import Backends, encoder_args
from agentic_editor.edl import EDL
from agentic_editor.presets import Preset
from agentic_editor.render import audio, captions, text
from agentic_editor.render.video import Layers, build_graph, video_band
from agentic_editor.settings import ROOT, Settings

log = logging.getLogger(__name__)
_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def asset_path(name: str) -> Path:
    path = Path(name).expanduser()
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"preset asset not found: {path}")
    return path


def detect_black_bars(video: Path, start: float) -> tuple[int, int, int, int] | None:
    result = ffmpeg.run(["-ss", f"{start:.3f}", "-t", "5", "-i", str(video),
                         "-vf", "cropdetect=24:2:0", "-f", "null", "-"])  # fmt: skip
    matches = _CROP.findall(result.stderr)
    return tuple(int(v) for v in matches[-1]) if matches else None


def timeline_words(edl: EDL, analysis: Analysis):
    words = []
    offset = 0.0
    for clip in edl.clips:
        for word in analysis.transcript.between(clip.start, clip.end):
            words.append(word.model_copy(update={
                "start": offset + max(word.start, clip.start) - clip.start,
                "end": offset + min(word.end, clip.end) - clip.start,
            }))  # fmt: skip
        offset += clip.duration
    return words


def render(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    dest: Path,
    work_dir: Path,
    settings: Settings,
    backends: Backends,
    crop_commands: dict[int, Path] | None = None,
) -> Path:
    started = time.monotonic()
    work_dir.mkdir(parents=True, exist_ok=True)
    frame = preset.size
    music = asset_path(preset.music.file) if preset.music.file else None
    soundtrack = audio.render_soundtrack(edl, preset, analysis.audio_path, music, work_dir)

    inputs: list[str] = []
    for clip in edl.clips:
        inputs += ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video)]
    layers = Layers(crop_commands=crop_commands or {})

    def add_input(path: Path) -> int:
        inputs.extend(["-i", str(path)])
        return inputs.count("-i") - 1

    source = (analysis.width, analysis.height)
    if preset.framing.crop_black_bars and edl.crop_mode == "none":
        layers.black_bars = detect_black_bars(video, edl.clips[0].start)
        if layers.black_bars:
            source = layers.black_bars[:2]

    if preset.text_blocks:
        band = video_band(edl.crop_mode, source, frame)
        placed = []
        for block in preset.text_blocks:
            gap = preset.framing.text_gap_above if block.anchor == "top" else preset.framing.text_gap_below
            placed.append(text.place(block, text.font_file(settings.fonts_dir, block.font),
                                     frame, band, gap))  # fmt: skip
        layers.text_overlay = add_input(text.draw(placed, frame, work_dir / "text.png"))

    title = preset.title_overlay
    if title.enabled and edl.title:
        png = text.draw_banner(edl.title, text.font_file(settings.fonts_dir, title.font),
                               title.size, title.color, title.box_color, frame, title.offset,
                               work_dir / "title.png")  # fmt: skip
        layers.title_overlay = add_input(png)
        layers.title_duration = title.duration

    if preset.hook.enabled and preset.hook.overlay and edl.hook:
        top = title.offset + (round(title.size * 1.6) if layers.title_overlay is not None else 0)
        png = text.draw_banner(edl.hook, text.font_file(settings.fonts_dir, title.font),
                               round(title.size * 0.8), title.color, title.box_color, frame,
                               top, work_dir / "hook.png")  # fmt: skip
        layers.hook_overlay = add_input(png)
        layers.hook_duration = min(3.0, edl.duration)

    if preset.captions.enabled:
        style = preset.captions
        overrides = edl.caption_overrides
        family = text.font_family(text.font_file(settings.fonts_dir, style.font))
        ass = captions.build_ass(
            timeline_words(edl, analysis), style, frame, family,
            position=overrides.position, words_per_line=overrides.words_per_line,
            highlight_color=overrides.highlight_color,
        )  # fmt: skip
        layers.captions = work_dir / "captions.ass"
        layers.captions.write_text(ass, encoding="utf-8")
        layers.fonts_dir = settings.fonts_dir

    if preset.logo.file:
        layers.logo = add_input(asset_path(preset.logo.file))

    audio_index = add_input(soundtrack)
    graph, out = build_graph(edl, preset, source, layers)
    main = dest if not (preset.intro or preset.outro) else work_dir / "main.mp4"
    ffmpeg.run([
        "-y", *inputs, "-filter_complex", graph,
        "-map", f"[{out}]", "-map", f"{audio_index}:a",
        *encoder_args(backends, preset.bitrate_mbps),
        "-r", str(preset.fps),
        "-c:a", "aac", "-b:a", "256k", "-ar", str(audio.SAMPLE_RATE),
        "-movflags", "+faststart", str(main),
    ])  # fmt: skip

    if main != dest:
        add_bookends(main, preset, dest, backends)
    log.info("rendered %s with %s in %.1fs", dest.name, backends.encoder_name,
             time.monotonic() - started)  # fmt: skip
    return dest


def add_bookends(main: Path, preset: Preset, dest: Path, backends: Backends) -> None:
    width, height = preset.size
    pieces = [asset_path(p) for p in (preset.intro,) if p] + [main]
    pieces += [asset_path(p) for p in (preset.outro,) if p]
    inputs, parts, labels = [], [], []
    for i, piece in enumerate(pieces):
        inputs += ["-i", str(piece)]
        parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={preset.fps}[v{i}]"
        )
        parts.append(f"[{i}:a]aformat=sample_rates={audio.SAMPLE_RATE}:channel_layouts=stereo[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(pieces)}:v=1:a=1[v][a]")
    ffmpeg.run([
        "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
        *encoder_args(backends, preset.bitrate_mbps),
        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(dest),
    ])  # fmt: skip
