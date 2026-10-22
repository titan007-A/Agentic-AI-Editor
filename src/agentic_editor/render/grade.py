"""Colour: a light automatic correction, a named look, an optional .cube LUT, and a
background colour taken from the footage.

Everything is measured on frames sampled across the reel and turned into ffmpeg filters
for the picture alone, so padding and text keep their own colours. The automatic
correction is deliberately small: it fixes a cast or a flat, dim picture, it doesn't
restyle footage that already looks right.
"""

import colorsys
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLES = 8
WIDTH = 160
MAX_GAIN = 0.08  # white balance never moves a channel by more than this
LOOKS: dict[str, list[str]] = {
    "natural": ["eq=contrast=1.03:saturation=1.05"],
    "warm": ["colorbalance=rm=0.06:gm=0.01:bm=-0.06:rh=0.04:bh=-0.04", "eq=saturation=1.05"],
    "cool": ["colorbalance=rm=-0.05:bm=0.06:rh=-0.03:bh=0.05"],
    "teal-orange": [
        "colorbalance=rs=-0.08:bs=0.10:rh=0.10:gh=0.02:bh=-0.08",
        "eq=contrast=1.06:saturation=1.1",
    ],
    "film": [
        "curves=preset=medium_contrast",
        "eq=saturation=0.92",
        "colorbalance=rh=0.03:bh=-0.02",
    ],
    "moody": ["curves=preset=darker", "eq=contrast=1.08:saturation=0.85", "colorbalance=bs=0.05"],
    "vivid": ["vibrance=intensity=0.25", "eq=contrast=1.05"],
    "mono": ["hue=s=0", "eq=contrast=1.1"],
    "faded": ["curves=master='0/0.08 0.5/0.52 1/0.94'", "eq=saturation=0.85"],
}


@dataclass(frozen=True)
class Stats:
    """What the picture looks like: luma percentiles and mean channel levels, 0 to 1."""

    low: float
    high: float
    mean: float
    rgb: tuple[float, float, float]


def measure(
    video: Path, start: float, end: float, ffmpeg_bin: str, crop: tuple[int, int, int, int] | None
) -> Stats:
    """Colour statistics from frames sampled across [start, end], bars excluded."""
    pixels = []
    for i in range(SAMPLES):
        at = start + (end - start) * (i + 0.5) / SAMPLES
        chain = (f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else "") + (
            f"scale={WIDTH}:-2,format=rgb24"
        )
        raw = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
             "-vf", chain, "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        pixels.append(np.frombuffer(raw, np.uint8).reshape(-1, 3))
    return stats(np.concatenate(pixels).astype(np.float64) / 255.0)


def stats(rgb: np.ndarray) -> Stats:
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
    means = rgb.mean(axis=0)
    return Stats(
        low=float(np.percentile(luma, 1)),
        high=float(np.percentile(luma, 99)),
        mean=float(luma.mean()),
        rgb=(float(means[0]), float(means[1]), float(means[2])),
    )


def correction(s: Stats) -> list[str]:
    """A small fix for a colour cast, crushed or washed-out levels, and a dim picture."""
    filters = []
    grey = sum(s.rgb) / 3
    gains = [min(1 + MAX_GAIN, max(1 - MAX_GAIN, grey / c)) if c > 0.02 else 1.0 for c in s.rgb]
    if max(abs(g - 1) for g in gains) > 0.015:
        r, g, b = gains
        filters.append(f"colorchannelmixer=rr={r:.3f}:gg={g:.3f}:bb={b:.3f}")
    black = min(s.low, 0.08) if s.low > 0.03 else 0.0
    white = max(s.high, 0.85) if s.high < 0.95 else 1.0
    if black > 0 or white < 1:
        levels = ":".join(f"{c}imin={black:.3f}:{c}imax={white:.3f}" for c in ("r", "g", "b"))
        filters.append(f"colorlevels={levels}")
    if s.mean < 0.32:
        filters.append(f"eq=gamma={min(1.25, 1 + (0.32 - s.mean)):.3f}")
    elif s.mean > 0.68:
        filters.append(f"eq=gamma={max(0.85, 1 - (s.mean - 0.68)):.3f}")
    return filters


def background(s: Stats, text_is_dark: bool) -> str:
    """A background colour toned to the footage: deep and muted behind light text, pale
    behind dark text, so the text stays readable."""
    h, sat, _ = colorsys.rgb_to_hsv(*s.rgb)
    if text_is_dark:
        r, g, b = colorsys.hsv_to_rgb(h, min(sat, 0.25), 0.86)
    else:
        r, g, b = colorsys.hsv_to_rgb(h, min(sat, 0.45), 0.16)
    return "#" + "".join(f"{round(v * 255):02X}" for v in (r, g, b))


def is_dark(hex_color: str) -> bool:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


def look_filters(look: str, lut: Path | None) -> list[str]:
    from agentic_editor.ffmpeg import escape_filter_path

    filters = list(LOOKS.get(look, []))
    if lut is not None:
        filters.append(f"lut3d=file='{escape_filter_path(lut)}'")
    return filters
