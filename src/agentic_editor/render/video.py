"""Builds the ffmpeg video filtergraph for an EDL. Pure string building, so the
graph can be unit tested without running ffmpeg."""

import itertools
import math
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
    # Rows of the frame a picture shown whole goes in, between the preset's text.
    picture_band: tuple[int, int] | None = None
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
    if layers.look and layers.look_strength > 0:
        look = ",".join(layers.look)
        if layers.look_strength >= 1:
            parts.append(f"[{label}]{look}[g{index}l]")
        else:
            parts.append(f"[{label}]split=2[g{index}a][g{index}b]")
            parts.append(f"[g{index}b]{look}[g{index}x]")
            # blend's normal mode keeps `opacity` of its first input, so the look goes first.
            parts.append(
                f"[g{index}x][g{index}a]blend=all_mode=normal:"
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
    picture_width: float = 1.0,
    picture_band: tuple[int, int] | None = None,
) -> list[str]:
    """Filters that turn one source clip into a frame-sized clip."""
    crop, fit = frame_parts(
        mode, source, frame, background, crop_label, commands, black_bars, picture_width,
        picture_band,
    )  # fmt: skip
    return crop + fit


def frame_parts(
    mode: CropMode,
    source: tuple[int, int],
    frame: tuple[int, int],
    background: str,
    crop_label: str,
    commands: Path | None,
    black_bars: tuple[int, int, int, int] | None,
    picture_width: float = 1.0,
    picture_band: tuple[int, int] | None = None,
) -> tuple[list[str], list[str]]:
    """frame_chain in two parts: what cuts the window out, and what fits it to the frame."""
    src_w, src_h = source
    crop = []
    if black_bars:
        bw, bh, bx, by = black_bars
        crop.append(f"crop={bw}:{bh}:{bx}:{by}")
        src_w, src_h = bw, bh

    if mode == "none":
        return crop, band_fit(frame, background, picture_width, picture_band)

    crop_w, crop_h = crop_size((src_w, src_h), frame)
    x = (src_w - crop_w) // 2
    y = (src_h - crop_h) // 2
    if mode == "face" and commands is not None:
        crop.append(f"sendcmd=f='{escape_filter_path(commands)}'")
        crop.append(f"crop@{crop_label}={crop_w}:{crop_h}:{x}:{y}")
    else:
        crop.append(f"crop={crop_w}:{crop_h}:{x}:{y}")
    return crop, fit_chain((crop_w, crop_h), frame, background)


def fit_chain(size: tuple[int, int], frame: tuple[int, int], background: str) -> list[str]:
    """Scale a window to the frame without stretching it: one at the frame's aspect, give
    or take the two pixels of rounding to even sizes, fills it and loses that rounding at
    the edges; any other is fitted inside and padded."""
    w, h = size
    width, height = frame
    if abs(w * height - width * h) <= 2 * max(width, height):
        return [
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={width}:{height}",
        ]
    return [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={ffmpeg_color(background)}",
    ]


def shot_chain(
    shot: Shot,
    index: int,
    content: tuple[int, int, int, int],
    frame: tuple[int, int],
    background: str,
    commands: Path | None,
) -> list[str]:
    """Filters that frame one shot: crop its window, at full picture height unless the
    shot has its own, then fill the frame, or, for a window of another shape, fit it and
    pad the rest."""
    crop, fit = shot_parts(shot, index, content, frame, background, commands)
    return crop + fit


def band_fit(
    frame: tuple[int, int],
    background: str,
    picture_width: float = 1.0,
    band: tuple[int, int] | None = None,
) -> list[str]:
    """Scale a picture shown whole into `band` (rows of the frame, all of it by default)
    without stretching it, and pad the rest of the frame."""
    width, height = frame
    top, bottom = band or (0, height)
    y = f"{top}+({bottom - top}-ih)/2" if band else "(oh-ih)/2"
    return [
        f"scale={_even(width * picture_width)}:{bottom - top}"
        ":force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:{y}:color={ffmpeg_color(background)}",
    ]


def shot_parts(
    shot: Shot,
    index: int,
    content: tuple[int, int, int, int],
    frame: tuple[int, int],
    background: str,
    commands: Path | None,
) -> tuple[list[str], list[str]]:
    _, top, _, picture_height = content
    if shot.height:
        top, picture_height = shot.y, shot.height
    window = f"{shot.width}:{picture_height}:{shot.x}:{top}"
    if shot.follow and commands is not None:
        crop = [f"sendcmd=f='{escape_filter_path(commands)}'", f"crop@s{index}={window}"]
    else:
        crop = [f"crop={window}"]
    return crop, fit_chain((shot.width, picture_height), frame, background)


GLIDE = 0.8  # seconds a band window takes to ease to a new row inside a shot


def band_rows_expression(shots: list[Shot], clip_start: float) -> str:
    """The window's top row over the clip's time t: the first shot's row, plus each later
    shot's change, taken at once or eased in (smoothstep) around where it starts."""
    terms = [str(shots[0].y)]
    for before, shot in itertools.pairwise(shots):
        change = shot.y - before.y
        if not change:
            continue
        at = shot.start - clip_start
        if shot.glide:
            p = f"clip((t-{at - GLIDE / 2:.3f})/{GLIDE},0,1)"
            terms.append(f"{change}*{p}*{p}*(3-2*{p})")
        else:
            terms.append(f"{change}*gte(t,{at:.3f})")
    return "+".join(terms).replace("+-", "-")


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


def zoom_expression(
    edl: EDL,
    pulses: list[float] = (),
    pulse_scale: float = 1.0,
    room: list[tuple[float, float, float]] | None = None,
) -> str | None:
    """Scale over time: the planner's punch-ins, plus short pulses that ease back out.
    `room` gives reel stretches and the most each may be zoomed (see zoom_room)."""
    room = room or [(0.0, math.inf, math.inf)]
    terms = []

    def add(start: float, end: float, scale: float, shape: str = "") -> None:
        for a, b, most in room:
            low, high = max(start, a), min(end, b)
            amount = min(scale, most) - 1
            if high > low and amount >= 0.001:
                terms.append(f"{amount:.3f}*between(t,{low:.3f},{high:.3f}){shape}")

    for t in pulses:
        d = PULSE_SECONDS
        add(t, t + d, pulse_scale, f"*(1-(t-{t:.3f})/{d})")
    for zoom in edl.zooms:
        t = edl.to_output_time(zoom.time)
        if t is None:
            continue
        add(t, t + zoom.duration, zoom.scale)
    return "1+" + "+".join(terms) if terms else None


def zoom_room(
    edl: EDL, preset: Preset, source: tuple[int, int], layers: Layers
) -> list[tuple[float, float, float]]:
    """How far each stretch of the reel may be zoomed into. A window cropped to fill the
    frame may be; a picture shown whole, or a window widened so nothing in it is cut, only
    as far as its padding leaves room before the zoom would cut into it after all."""
    frame = preset.size
    width, height = frame
    if edl.crop_mode == "none":
        w, h = layers.black_bars[:2] if layers.black_bars else source
        box = _even(width * preset.framing.picture_width)
        top, bottom = layers.picture_band or (0, height)
        if edl.shots:  # cut to the band already, top and bottom
            w = edl.shots[0].width
            h = max(s.height or h for s in edl.shots)
        scale = min(box / w, (bottom - top) / h)
        return [(0.0, edl.duration, min(width / (w * scale), height / (h * scale)))]
    if edl.crop_mode != "smart" or not edl.shots:
        return [(0.0, edl.duration, math.inf)]
    clip = edl.clips[0]
    content = layers.content or (0, 0, *source)
    room = []
    for shot in edl.shots:
        w, h = shot.width, shot.height or content[3]
        most = math.inf
        if abs(w * height - width * h) > 2 * max(width, height):  # padded, not filling
            scale = min(width / w, height / h)
            most = min(width / (w * scale), height / (h * scale))
        room.append((shot.start - clip.start, shot.end - clip.start, most))
    return room


def picture_rect(
    size: tuple[int, int],
    frame: tuple[int, int],
    picture_width: float = 1.0,
    band: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Where a picture of `size` shown whole lands in the frame, x0, y0, x1, y1, as
    band_fit places it."""
    width, height = frame
    top, bottom = band or (0, height)
    box_w, box_h = _even(width * picture_width), bottom - top
    scale = min(box_w / size[0], box_h / size[1])
    w, h = round(size[0] * scale / 2) * 2, round(size[1] * scale / 2) * 2
    x0, y0 = (width - w) // 2, top + (box_h - h) // 2
    return x0, y0, x0 + w, y0 + h


def video_band(
    mode: CropMode,
    source: tuple[int, int],
    frame: tuple[int, int],
    picture_width: float = 1.0,
    band: tuple[int, int] | None = None,
) -> tuple[float, float] | None:
    """Rows of the frame covered by the picture when it is letterboxed, else None."""
    if mode != "none":
        return None
    width, height = frame
    top, bottom = band or (0, height)
    scaled = min(bottom - top, _even(width * picture_width) * source[1] / source[0])
    if scaled >= height - 1:
        return None
    top += (bottom - top - scaled) / 2
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

    def segment(label: str, crop: list[str], fit: list[str]) -> None:
        # Colour goes on after the crop, so only the pixels that are kept get graded (every
        # grading filter works pixel by pixel), and before the padding, which keeps its own.
        nonlocal segments
        fit = [*fit, "setsar=1", f"fps={preset.fps}"]
        graded, last = grade_chain(f"c{segments}", segments, layers)
        if graded:
            parts.append(f"[{label}]{','.join(crop) or 'null'}[c{segments}]")
            parts.extend(graded)
            parts.append(f"[{last}]{','.join(fit)}[v{segments}]")
        else:
            parts.append(f"[{label}]{','.join(crop + fit)}[v{segments}]")
        segments += 1

    for i, clip in enumerate(edl.clips):
        blurred, label = blur_chain(clip, i, layers.watermarks, preset.watermarks.strength)
        parts += blurred
        if edl.crop_mode == "none" and edl.shots:
            # Cut top and bottom at the picture's whole width, one window whose row changes
            # from shot to shot: at once on a cut, easing over GLIDE seconds inside a shot.
            first = edl.shots[0]
            h = first.height or (layers.content or (0, 0, *source))[3]
            y = band_rows_expression(edl.shots, clip.start)
            crop = [f"crop={first.width}:{h}:{first.x}:'{y}'"]
            fit = band_fit(frame, background, preset.framing.picture_width, layers.picture_band)
            segment(label, crop, fit)
            continue
        if edl.crop_mode == "smart" and edl.shots:
            # One continuous clip, framed shot by shot and joined back in order.
            content = layers.content or (0, 0, *source)
            count = len(edl.shots)
            parts.append(f"[{label}]split={count}" + "".join(f"[s{k}]" for k in range(count)))
            for k, shot in enumerate(edl.shots):
                start, end = shot.start - clip.start, shot.end - clip.start
                crop, fit = shot_parts(
                    shot, k, content, frame, background, layers.shot_commands.get(k)
                )
                trim = [f"trim=start={start:.3f}:end={end:.3f}", "setpts=PTS-STARTPTS"]
                segment(f"s{k}", trim + crop, fit)
            continue
        crop, fit = frame_parts(
            edl.crop_mode,
            source,
            frame,
            background,
            f"c{i}",
            layers.crop_commands.get(i),
            layers.black_bars,
            preset.framing.picture_width,
            layers.picture_band,
        )
        segment(label, crop, fit)
    joined = "".join(f"[v{i}]" for i in range(segments))
    parts.append(f"{joined}concat=n={segments}:v=1:a=0[base]")
    current = "base"

    def step(filters: str, label: str) -> None:
        nonlocal current
        parts.append(f"[{current}]{filters}[{label}]")
        current = label

    room = zoom_room(edl, preset, source, layers)
    if (zoom := zoom_expression(edl, layers.pulses, layers.pulse_scale, room)) is not None:
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
