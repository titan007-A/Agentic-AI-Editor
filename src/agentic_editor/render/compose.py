"""Runs the render: soundtrack, overlays, captions and the final encode."""

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from agentic_editor import ffmpeg, framing, subtitles
from agentic_editor.analysis import onscreen
from agentic_editor.analysis.faces import Sample
from agentic_editor.analyze import Analysis
from agentic_editor.backends import Backends, encoder_args
from agentic_editor.edl import EDL, Cue
from agentic_editor.planner import voiced_regions
from agentic_editor.presets import CaptionStyle, Preset
from agentic_editor.render import audio, captions, grade, text
from agentic_editor.render.video import Layers, build_graph, crop_size, video_band
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


def picture_area(
    video: Path, start: float, end: float, size: tuple[int, int], probes: int = 4
) -> tuple[int, int, int, int]:
    """The part of the frame that holds picture, as x, y, width, height. Bars are measured
    at a few points and the widest picture kept, so a dark scene isn't taken for a bar."""
    found = []
    for i in range(probes):
        at = start + (end - start) * i / probes
        bars = detect_black_bars(video, at)
        if bars:
            w, h, x, y = bars
            found.append((x, y, x + w, y + h))
    if not found:
        return (0, 0, *size)
    x0, y0 = min(f[0] for f in found), min(f[1] for f in found)
    x1, y1 = max(f[2] for f in found), max(f[3] for f in found)
    return (x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2)


def timeline_words(edl: EDL, analysis: Analysis):
    words = []
    offset = 0.0
    for clip in edl.clips:
        for word in analysis.transcript.between(clip.start, clip.end):
            words.append(
                word.model_copy(
                    update={
                        "start": offset + max(word.start, clip.start) - clip.start,
                        "end": offset + min(word.end, clip.end) - clip.start,
                    }
                )
            )
        offset += clip.duration
    return words


def caption_cues(
    edl: EDL, analysis: Analysis, style: CaptionStyle, fits: Callable[[str], bool] | None = None
) -> list[Cue]:
    """The captions for this reel in the style's mode. Translated and written captions
    come from the models before rendering; without them, fall back to the speech."""
    words = timeline_words(edl, analysis)
    if style.mode in ("translated", "visual") and edl.cues:
        return edl.cues
    if style.mode in ("translated", "visual"):
        log.warning("no %s captions were made; showing the speech instead", style.mode)
    if style.mode == "lyrics":
        breaths = None
        if analysis.audio_source == "vocals" and analysis.silences:
            clip = edl.clips[0]
            breaths = [
                (max(a, clip.start) - clip.start, min(b, clip.end) - clip.start)
                for a, b in voiced_regions(analysis)
                if b > clip.start and a < clip.end
            ]
        return subtitles.lyric_cues(words, breaths=breaths, fits=fits)
    per_line = edl.caption_overrides.words_per_line or style.words_per_line
    return subtitles.speech_cues(words, per_line, fits)


def caption_scales(
    cues: list[Cue], measure: Callable[[str], float], room: float, pop: bool
) -> list[float]:
    """How much to shrink each caption so that none runs off the frame: one holding a
    word too long for the frame's width is drawn smaller rather than cut off."""
    scales = []
    for cue in cues:
        width = max(measure(line) for line in cue.text.split("\n"))
        if pop and cue.words:
            width += 0.12 * max(measure(w.text) for w in cue.words)  # the spoken word grows
        scales.append(min(1.0, room / max(1.0, width)))
    return scales


def banner_top(
    position: str,
    offset: int,
    height: int,
    frame_height: int,
    rows: list[tuple[float, list[tuple[float, float]]]],
    until: float | None = None,
    taken: list[tuple[float, float]] = (),
) -> int:
    """Top edge for a banner: its usual position if faces stay clear of it, else the other
    end of the frame, else stacked beside a banner already there. `taken` are the bands of
    banners placed before this one, which it must not cover."""
    gap = 24
    near = offset if position == "top" else frame_height - offset - height
    far = frame_height - offset - height if position == "top" else offset
    options = [near, far]
    for top, bottom in taken:
        options += [round(bottom + gap), round(top - gap - height)]
    options = [o for o in options if 0 <= o <= frame_height - height]
    seen = [bands for t, bands in rows if until is None or t <= until]

    def cost(top: int) -> tuple[float, int]:
        band = (top, top + height)
        faces = sum(subtitles.overlap(band, bands) > 0 for bands in seen) / max(1, len(seen))
        blocked = subtitles.overlap(band, list(taken)) > 0
        # Covering another banner is never acceptable; a face only for a small share of time.
        return (blocked, faces > 0.3, faces), options.index(top)

    return min(options, key=cost)


def clear_rows(
    edl: EDL,
    analysis: Analysis,
    preset: Preset,
    content: tuple[int, int, int, int] | None,
    faces: list[Sample],
    texts: list[tuple[float, list[onscreen.Box]]] = (),
) -> list[tuple[float, list[tuple[float, float]]]]:
    """The rows of the output frame to keep captions and banners off, at reel times:
    faces, and text burnt into the picture while it is on screen."""
    source = (analysis.width, analysis.height)
    picture = content or (0, 0, *source)
    shots = edl.shots if edl.crop_mode == "smart" else []
    # An inset picture is drawn smaller, as if a wider window were fitted to the frame.
    window = (
        source[0] / preset.framing.picture_width
        if edl.crop_mode == "none"
        else crop_size(source, preset.size)[0]
    )
    clip = edl.clips[0]
    return framing.screen_rows(
        faces, clip, shots, picture, source, preset.size, window
    ) + framing.box_rows(list(texts), clip, shots, picture, source, preset.size, window)


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
    faces: list[Sample] | None = None,
    texts: list[tuple[float, list[onscreen.Box]]] | None = None,
) -> Path:
    started = time.monotonic()
    work_dir.mkdir(parents=True, exist_ok=True)
    frame = preset.size
    music = asset_path(preset.music.file) if preset.music.file else None
    soundtrack = audio.render_soundtrack(edl, preset, analysis.audio_path, music, work_dir)

    inputs: list[str] = []
    for clip in edl.clips:
        inputs += ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video)]
    layers = Layers()
    if edl.crop_mode == "smart":
        clip = edl.clips[0]
        layers.content = edl.picture or picture_area(
            video, clip.start, clip.end, (analysis.width, analysis.height)
        )
        layers.shot_commands = crop_commands or {}
    else:
        layers.crop_commands = crop_commands or {}
    if preset.watermarks.blur:
        layers.watermarks = analysis.watermarks

    def add_input(path: Path) -> int:
        inputs.extend(["-i", str(path)])
        return inputs.count("-i") - 1

    source = (analysis.width, analysis.height)
    # Bars baked into the source would show against a background taken from the footage.
    crop_bars = preset.framing.crop_black_bars or preset.framing.auto_background
    if crop_bars and edl.crop_mode == "none":
        layers.black_bars = detect_black_bars(video, edl.clips[0].start)
        if layers.black_bars:
            source = layers.black_bars[:2]

    if preset.text_blocks:
        band = video_band(edl.crop_mode, source, frame, preset.framing.picture_width)
        placed = []
        for block in preset.text_blocks:
            gap = (
                preset.framing.text_gap_above
                if block.anchor == "top"
                else preset.framing.text_gap_below
            )
            placed.append(
                text.place(block, text.font_file(settings.fonts_dir, block.font), frame, band, gap)
            )
        layers.text_overlay = add_input(text.draw(placed, frame, work_dir / "text.png"))

    rows = (
        clear_rows(edl, analysis, preset, layers.content, faces or [], texts or [])
        if faces or texts
        else []
    )

    colour = preset.grade
    if colour.auto or preset.framing.auto_background:
        clip = edl.clips[0]
        bars = layers.content or (
            (layers.black_bars[2], layers.black_bars[3], *layers.black_bars[:2])
            if layers.black_bars
            else None
        )
        measured = grade.measure(video, clip.start, clip.end, settings.ffmpeg_bin, bars)
        if colour.auto:
            layers.correction = grade.correction(measured)
        if preset.framing.auto_background:
            text_colours = [b.color for b in preset.text_blocks] or [preset.captions.color]
            dark = all(grade.is_dark(c) for c in text_colours)
            layers.background = grade.background(measured, dark)
    if colour.look != "none" or colour.lut:
        lut = asset_path(colour.lut) if colour.lut else None
        layers.look = grade.look_filters(colour.look, lut)
        layers.look_strength = colour.strength

    sync = preset.beat_sync
    if sync.pulse and analysis.beats.beats:
        clip = edl.clips[0]
        times = analysis.beats.downbeats if sync.pulse_on == "bar" else analysis.beats.beats
        layers.pulses = [t - clip.start for t in times if clip.start <= t < clip.end]
        layers.pulse_scale = sync.pulse_scale

    # Banners keep off faces and the picture's own text; captions keep off those and banners.
    obstacles: list[tuple[float, float, float, float]] = []  # reel start, end, top, bottom

    def banner(
        words: str, font_name: str, size: int, color: str, box: str | None,
        position: str, offset: int, until: float, name: str,
    ) -> int:  # fmt: skip
        font = text.font_file(settings.fonts_dir, font_name)
        wrapped = text.balance(words, font, size, frame[0] - 160)
        height = round(size * 1.3 * (wrapped.count("\n") + 1))
        taken = [(top, bottom) for _, _, top, bottom in obstacles]
        top = banner_top(position, offset, height, frame[1], rows, until, taken)
        obstacles.append((0.0, until, top, top + height))
        png = text.draw_banner(wrapped, font, size, color, box, frame, top, work_dir / name)
        return add_input(png)

    title = preset.title_overlay
    if title.enabled and edl.title:
        layers.title_duration = min(title.duration, edl.duration)
        layers.title_overlay = banner(
            edl.title, title.font, title.size, title.color, title.box_color,
            "top", title.offset, layers.title_duration, "title.png",
        )  # fmt: skip

    if preset.hook.enabled and preset.hook.overlay and edl.hook:
        offset = title.offset + (round(title.size * 1.6) if layers.title_overlay is not None else 0)
        layers.hook_duration = min(3.0, edl.duration)
        layers.hook_overlay = banner(
            edl.hook, title.font, round(title.size * 0.8), title.color, title.box_color,
            "top", offset, layers.hook_duration, "hook.png",
        )  # fmt: skip

    line = preset.one_liner
    if line.enabled and edl.one_liner:
        layers.line_duration = line.duration or 0.0
        layers.line_overlay = banner(
            edl.one_liner, line.font, line.size, line.color, line.box_color,
            line.position, line.offset, line.duration or edl.duration, "line.png",
        )  # fmt: skip

    if preset.captions.enabled:
        style = preset.captions
        overrides = edl.caption_overrides
        font = text.font_file(settings.fonts_dir, style.font)
        sized = text.caption_font(font, style.size)
        room = frame[0] - 120 - 2 * style.outline

        def measure(line: str) -> float:
            return sized.getlength(line.upper() if style.uppercase else line)

        cues = caption_cues(edl, analysis, style, lambda line: measure(line) <= room)
        if any(not c.words for c in cues):
            cues = subtitles.fit_lines(cues, measure, room)
        scales = caption_scales(cues, measure, room, style.animation == "pop")
        position = overrides.position or style.position
        placements = None
        if style.avoid_faces and (rows or obstacles):
            blocked = rows + [
                (start + k * 0.5, [(top, bottom)])
                for start, end, top, bottom in obstacles
                for k in range(int((end - start) / 0.5) + 1)
            ]
            lines = max((c.text.count("\n") + 1 for c in cues), default=1)
            height = round(style.size * 1.25 * lines + 2 * style.outline)
            placements = subtitles.place(cues, position, style.margin, height, frame[1], blocked)
        family = text.font_family(font)
        ass = captions.build_ass(
            cues,
            style,
            frame,
            family,
            position=position,
            highlight_color=overrides.highlight_color,
            placements=placements,
            scales=scales,
        )
        layers.captions = work_dir / "captions.ass"
        layers.captions.write_text(ass, encoding="utf-8")
        layers.fonts_dir = settings.fonts_dir
        if style.export_srt:
            dest.with_suffix(".srt").write_text(subtitles.to_srt(cues), encoding="utf-8")

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
    log.info(
        "rendered %s with %s in %.1fs", dest.name, backends.encoder_name, time.monotonic() - started
    )
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
        parts.append(
            f"[{i}:a]aformat=sample_rates={audio.SAMPLE_RATE}:channel_layouts=stereo[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(pieces)}:v=1:a=1[v][a]")
    ffmpeg.run([
        "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
        *encoder_args(backends, preset.bitrate_mbps),
        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(dest),
    ])  # fmt: skip
