"""Builds the ffmpeg video filtergraph for an EDL. Pure string building, so the
graph can be unit tested without running ffmpeg."""

from dataclasses import dataclass, field
from pathlib import Path

from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.edl import EDL, Clip, CropMode
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
    logo: int | None = None
    captions: Path | None = None
    fonts_dir: Path | None = None
    crop_commands: dict[int, Path] = field(default_factory=dict)
    black_bars: tuple[int, int, int, int] | None = None
    watermarks: list[Watermark] = field(default_factory=list)


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


def crop_size(source: tuple[int, int], frame: tuple[int, int]) -> tuple[int, int]:
    """Largest window with the frame's aspect ratio that fits inside the source."""
    src_w, src_h = source
    target = frame[0] / frame[1]
    if src_w / src_h > target:
        return _even(src_h * target), src_h
    return src_w, _even(src_w / target)


def _even(value: float) -> int:
    return int(value) // 2 * 2


def zoom_expression(edl: EDL) -> str | None:
    terms = []
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
    for i, clip in enumerate(edl.clips):
        blurred, label = blur_chain(clip, i, layers.watermarks, preset.watermarks.strength)
        parts += blurred
        chain = frame_chain(
            edl.crop_mode,
            source,
            frame,
            preset.framing.background,
            f"c{i}",
            layers.crop_commands.get(i),
            layers.black_bars,
        )
        chain += ["setsar=1", f"fps={preset.fps}"]
        parts.append(f"[{label}]{','.join(chain)}[v{i}]")
    joined = "".join(f"[v{i}]" for i in range(len(edl.clips)))
    parts.append(f"{joined}concat=n={len(edl.clips)}:v=1:a=0[base]")
    current = "base"

    def step(filters: str, label: str) -> None:
        nonlocal current
        parts.append(f"[{current}]{filters}[{label}]")
        current = label

    if (zoom := zoom_expression(edl)) is not None:
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
