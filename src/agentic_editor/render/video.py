"""Builds the ffmpeg video filtergraph for an EDL. Pure string building, so the
graph can be unit tested without running ffmpeg."""

from dataclasses import dataclass, field
from pathlib import Path

from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.edl import EDL, Clip, CropMode, Shot
from agentic_editor.ffmpeg import escape_filter_path
from agentic_editor.presets import Preset


@dataclass
class Layers:
    """Pre-rendered pieces the graph composites, each an input index or file."""

    text_overlay: int | None = None
    title_overlay: int | None = None
    title_duration: float = 0.0
    hook_overlay: int | None = None
    hook_duration: float = 0.0
    pulses: list[float] = field(default_factory=list)  # reel times of beat pulses
    correction: list[str] = field(default_factory=list)  # automatic colour fix
    look: list[str] = field(default_factory=list)  # look and LUT, mixed in at look_strength
    look_strength: float = 1.0
    background: str | None = None  # overrides the preset's, e.g. taken from the footage
    pulse_scale: float = 1.0
    line_overlay: int | None = None
    line_duration: float = 0.0  # 0 keeps it up for the whole reel
    logo: int | None = None
    captions: Path | None = None
    fonts_dir: Path | None = None
    crop_commands: dict[int, Path] = field(default_factory=dict)
    black_bars: tuple[int, int, int, int] | None = None
    watermarks: list[Watermark] = field(default_factory=list)
    # Smart framing: the picture's area without black bars, and a follow path per shot.
    content: tuple[int, int, int, int] | None = None
    shot_commands: dict[int, Path] = field(default_factory=dict)


def blur_chain(
    clip: Clip, index: int, marks: list[Watermark], strength: float
) -> tuple[list[str], str]:
    """Graph parts that blur each watermark while it is on screen during this clip, in
    source pixels and before any cropping. Returns the parts and the label they end on."""
    parts: list[str] = []
    current = f"{index}:v"
    for k, mark in enumerate(marks):
        visible = mark.visible(clip.start, clip.end)
        if not visible:
            continue
        # Inputs are seeked to the clip start, so their timestamps start at zero.
        enable = "+".join(
            f"between(t,{a - clip.start:.3f},{b - clip.start:.3f})" for a, b in visible
        )
        # Big lettering survives a blur sized for a small handle, so scale with the box.
        sigma = min(max(strength, 0.4 * mark.height), max(mark.width, mark.height) / 2)
        base, patch, done = f"w{index}_{k}b", f"w{index}_{k}p", f"w{index}_{k}"
        parts.append(f"[{current}]split=2[{base}][{patch}]")
        parts.append(
            f"[{patch}]crop={mark.width}:{mark.height}:{mark.x}:{mark.y},"
            f"gblur=sigma={sigma:g}:steps=3[{patch}b]"
        )
        parts.append(f"[{base}][{patch}b]overlay={mark.x}:{mark.y}:enable='{enable}'[{done}]")
        current = done
    return parts, current


def grade_chain(label: str, index: int, layers: Layers) -> tuple[list[str], str]:
    """Colour the picture of one clip: the correction, then the look mixed in at its
    strength. Returns graph parts and the label they end on."""
    parts = []
    if layers.correction:
        parts.append(f"[{label}]{','.join(layers.correction)}[g{index}c]")
        label = f"g{index}c"
    if layers.look:
        look = ",".join(layers.look)
        if layers.look_strength >= 1:
            parts.append(f"[{label}]{look}[g{index}l]")
        else:
            parts.append(f"[{label}]split=2[g{index}a][g{index}b]")
            parts.append(f"[g{index}b]{look}[g{index}x]")
            parts.append(
                f"[g{index}a][g{index}x]blend=all_mode=normal:"
                f"all_opacity={layers.look_strength:.2f}[g{index}l]"
            )
        label = f"g{index}l"
    return parts, label


def ffmpeg_color(hex_color: str) -> str:
    return "0x" + hex_color[1:].upper()


def frame_chain(
    mode: CropMode,
    source: tuple[int, int],
    frame: tuple[int, int],
    background: str,
    crop_label: str,
    commands: Path | None,
    black_bars: tuple[int, int, int, int] | None,
) -> list[str]:
    """Filters that turn one source clip into a frame-sized clip."""
    width, height = frame
    src_w, src_h = source
    chain = []
    if black_bars:
        bw, bh, bx, by = black_bars
        chain.append(f"crop={bw}:{bh}:{bx}:{by}")
        src_w, src_h = bw, bh

    if mode == "none":
        chain += [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={ffmpeg_color(background)}",
        ]
        return chain

    crop_w, crop_h = crop_size((src_w, src_h), frame)
    x = (src_w - crop_w) // 2
    y = (src_h - crop_h) // 2
    if mode == "face" and commands is not None:
        chain.append(f"sendcmd=f='{escape_filter_path(commands)}'")
        chain.append(f"crop@{crop_label}={crop_w}:{crop_h}:{x}:{y}")
    else:
        chain.append(f"crop={crop_w}:{crop_h}:{x}:{y}")
    chain.append(f"scale={width}:{height}:flags=lanczos")
    return chain


def shot_chain(
    shot: Shot,
    index: int,
    content: tuple[int, int, int, int],
    frame: tuple[int, int],
    background: str,
    commands: Path | None,
) -> list[str]:
    """Filters that frame one shot: crop its window at full picture height, then fill the
    frame, or, for a window wider than the frame's aspect, fit it and pad above and below."""
    width, height = frame
    _, top, _, picture_height = content
    crop = f"{shot.width}:{picture_height}:{shot.x}:{top}"
    if shot.follow and commands is not None:
        chain = [f"sendcmd=f='{escape_filter_path(commands)}'", f"crop@s{index}={crop}"]
    else:
        chain = [f"crop={crop}"]
    if shot.width * height <= width * picture_height + height:  # fills the frame
        return [*chain, f"scale={width}:{height}:flags=lanczos"]
    return [
        *chain,
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={ffmpeg_color(background)}",
    ]


def crop_size(source: tuple[int, int], frame: tuple[int, int]) -> tuple[int, int]:
    """Largest window with the frame's aspect ratio that fits inside the source."""
    src_w, src_h = source
    target = frame[0] / frame[1]
    if src_w / src_h > target:
        return _even(src_h * target), src_h
    return src_w, _even(src_w / target)


def _even(value: float) -> int:
    return int(value) // 2 * 2


PULSE_SECONDS = 0.3


def zoom_expression(edl: EDL, pulses: list[float] = (), pulse_scale: float = 1.0) -> str | None:
    """Scale over time: the planner's punch-ins, plus short pulses that ease back out."""
    terms = []
    for t in pulses:
        d = PULSE_SECONDS
        terms.append(f"{pulse_scale - 1:.3f}*between(t,{t:.3f},{t + d:.3f})*(1-(t-{t:.3f})/{d})")
    for zoom in edl.zooms:
        t = edl.to_output_time(zoom.time)
        if t is None:
            continue
        terms.append(f"{zoom.scale - 1:.3f}*between(t,{t:.3f},{t + zoom.duration:.3f})")
    return "1+" + "+".join(terms) if terms else None


def video_band(
    mode: CropMode, source: tuple[int, int], frame: tuple[int, int]
) -> tuple[float, float] | None:
    """Rows of the frame covered by the picture when it is letterboxed, else None."""
    if mode != "none":
        return None
    width, height = frame
    scaled = min(height, width * source[1] / source[0])
    if scaled >= height - 1:
        return None
    top = (height - scaled) / 2
    return top, top + scaled


def build_graph(
    edl: EDL, preset: Preset, source: tuple[int, int], layers: Layers
) -> tuple[str, str]:
    """Video filtergraph. Clip i is input i. Returns (graph, output label)."""
    frame = preset.size
    width, height = frame
    parts = []
    segments = 0
    background = layers.background or preset.framing.background
    for i, clip in enumerate(edl.clips):
        blurred, label = blur_chain(clip, i, layers.watermarks, preset.watermarks.strength)
        parts += blurred
        graded, label = grade_chain(label, i, layers)
        parts += graded
        if edl.crop_mode == "smart" and edl.shots:
            # One continuous clip, framed shot by shot and joined back in order.
            content = layers.content or (0, 0, *source)
            count = len(edl.shots)
            parts.append(f"[{label}]split={count}" + "".join(f"[s{k}]" for k in range(count)))
            for k, shot in enumerate(edl.shots):
                start, end = shot.start - clip.start, shot.end - clip.start
                chain = [f"trim=start={start:.3f}:end={end:.3f}", "setpts=PTS-STARTPTS"]
                chain += shot_chain(
                    shot,
                    k,
                    content,
                    frame,
                    background,
                    layers.shot_commands.get(k),
                )
                chain += ["setsar=1", f"fps={preset.fps}"]
                parts.append(f"[s{k}]{','.join(chain)}[v{segments}]")
                segments += 1
            continue
        chain = frame_chain(
            edl.crop_mode,
            source,
            frame,
            background,
            f"c{i}",
            layers.crop_commands.get(i),
            layers.black_bars,
        )
        chain += ["setsar=1", f"fps={preset.fps}"]
        parts.append(f"[{label}]{','.join(chain)}[v{segments}]")
        segments += 1
    joined = "".join(f"[v{i}]" for i in range(segments))
    parts.append(f"{joined}concat=n={segments}:v=1:a=0[base]")
    current = "base"

    def step(filters: str, label: str) -> None:
        nonlocal current
        parts.append(f"[{current}]{filters}[{label}]")
        current = label

    if (zoom := zoom_expression(edl, layers.pulses, layers.pulse_scale)) is not None:
        step(
            f"scale=w='trunc({width}*({zoom})/2)*2':h='trunc({height}*({zoom})/2)*2'"
            f":eval=frame:flags=bicubic,crop={width}:{height}",
            "zoomed",
        )

    def overlay(index: int, label: str, until: float | None = None) -> None:
        nonlocal current
        enable = f":enable='lt(t,{until:.3f})'" if until else ""
        parts.append(f"[{current}][{index}:v]overlay=0:0{enable}[{label}]")
        current = label

    if layers.text_overlay is not None:
        overlay(layers.text_overlay, "texted")
    if layers.captions is not None:
        fonts = f":fontsdir='{escape_filter_path(layers.fonts_dir)}'" if layers.fonts_dir else ""
        step(f"subtitles=filename='{escape_filter_path(layers.captions)}'{fonts}", "captioned")
    if layers.hook_overlay is not None:
        overlay(layers.hook_overlay, "hooked", layers.hook_duration)
    if layers.title_overlay is not None:
        overlay(layers.title_overlay, "titled", layers.title_duration)
    if layers.line_overlay is not None:
        overlay(layers.line_overlay, "lined", layers.line_duration or None)
    if layers.logo is not None:
        logo = preset.logo
        x = str(logo.margin) if logo.position.endswith("left") else f"W-w-{logo.margin}"
        y = str(logo.margin) if logo.position.startswith("top") else f"H-h-{logo.margin}"
        parts.append(
            f"[{layers.logo}:v]scale={logo.width}:-1,format=rgba,"
            f"colorchannelmixer=aa={logo.opacity:.2f}[logo]"
        )
        parts.append(f"[{current}][logo]overlay={x}:{y}[branded]")
        current = "branded"
    step("format=yuv420p", "out")
    return ";".join(parts), current
