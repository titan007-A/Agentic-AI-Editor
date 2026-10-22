"""When text burnt into the picture is on screen: name banners, titles, signs.

The vision model says where text is, but only on the few frames it looks at. Each box it
finds is followed through the reel by its own edge pattern: on the frame where it was
seen, the text's strokes are edges; on any other frame the box shows that text if the
same pixels are edges far more often than the rest of the box. Comparing with the rest
of the box, rather than asking for a match alone, keeps busy texture (a striped shirt)
from passing as text.
"""

import subprocess
from pathlib import Path

import numpy as np

from agentic_editor.analysis.watermarks import edge_mask

FPS = 4.0
WIDTH = 480
MIN_MATCH = 0.2  # share of the text's own edge pixels that must be edges again
CONTRAST = 2.5  # ...and this many times the share in the rest of the box
MIN_PATTERN = 30  # edge pixels a box needs to be recognisable at all

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in source pixels


def grab(video: Path, start: float, end: float, ffmpeg_bin: str) -> tuple[list[float], np.ndarray]:
    """Grayscale frames across [start, end] at FPS, WIDTH pixels wide, and their times."""

    def run(args: list[str]) -> bytes:
        return subprocess.run(
            [ffmpeg_bin, "-v", "error", "-ss", f"{start:.3f}", *args],
            capture_output=True,
            check=True,
        ).stdout

    scale = f"scale={WIDTH}:-2:flags=area,format=gray"
    first = run(["-i", str(video), "-frames:v", "1", "-vf", scale, "-f", "rawvideo", "-"])
    height = len(first) // WIDTH
    raw = run(["-t", f"{end - start:.3f}", "-i", str(video), "-vf", f"fps={FPS},{scale}",
               "-f", "rawvideo", "-"])  # fmt: skip
    frames = np.frombuffer(raw, np.uint8)[: len(raw) // (WIDTH * height) * WIDTH * height]
    frames = frames.reshape(-1, height, WIDTH)
    return [start + i / FPS for i in range(len(frames))], frames


def visible(region: np.ndarray, pattern: np.ndarray) -> bool:
    edges = edge_mask(region)
    on = edges[pattern].mean()
    off = edges[~pattern].mean() if (~pattern).any() else 0.0
    return on >= MIN_MATCH and on >= CONTRAST * off


def track(
    times: list[float], frames: np.ndarray, found: list[tuple[float, Box]], source_width: int
) -> list[tuple[float, list[Box]]]:
    """For each frame time, the boxes whose text is on screen. `found` is where the vision
    model saw text, and when."""
    scale = frames.shape[2] / source_width if len(frames) else 1.0
    present: list[list[Box]] = [[] for _ in times]
    for seen, box in found:
        x0, y0, x1, y1 = (round(v * scale) for v in box)
        if x1 - x0 < 2 or y1 - y0 < 2 or not times:
            continue
        reference = min(range(len(times)), key=lambda i: abs(times[i] - seen))
        pattern = edge_mask(frames[reference][y0:y1, x0:x1])
        if pattern.sum() < MIN_PATTERN:
            continue
        shown = [visible(f[y0:y1, x0:x1], pattern) for f in frames]
        # A single frame lost to motion blur or a flash doesn't end the text's stretch.
        for i in range(1, len(shown) - 1):
            shown[i] = shown[i] or (shown[i - 1] and shown[i + 1])
        for i, on in enumerate(shown):
            if on:
                present[i].append(box)
    return list(zip(times, present, strict=True))
