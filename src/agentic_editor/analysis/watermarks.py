"""Finds overlays burnt into a video: logos, channel names, usernames, including the kind
that jumps between corners partway through.

An overlay keeps the same sharp edges at the same pixels while the picture under it moves,
so the detector looks, window by window, for small patches of edges that stay put while
enough of the rest of the frame changes. The windows overlap, so a mark that jumps every few
seconds still fills some of them from end to end. Patches are then followed across windows, which
gives each overlay a box and the stretches of time it is on screen. Whether a patch is an
overlay or a still part of the scene (a sign on a wall, a framed photo) is for the vision
model to confirm; this module only finds candidates.
"""

import subprocess
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel

SAMPLE_FPS = 2.0
SAMPLE_WIDTH = 480
WINDOW_SECONDS = 4.0
WINDOW_STEP = 1.0  # seconds between window starts
# Once a mark's strokes are known, short windows tell when it is on screen, to within a
# fraction of a second of each jump.
PRESENCE_SECONDS = 1.5
# Bump when detection changes, so cached candidates from the old detector aren't reused.
VERSION = 2

EDGE_LEVEL = 28  # gray-level step that counts as an edge
PERSISTENT_SHARE = 0.9  # of a window's frames an edge has to be in
STATIC_STD = 10.0  # pixels under an overlay barely change
MOVING_STD = 18.0
MIN_MOTION = 0.03  # share of the frame that must move for a window to tell us anything
MAX_AREA = 0.08  # overlays are small; bigger static patches are scenery
MIN_EDGE_PIXELS = 25
# Overlays sit near an edge of the frame; still scenery behind a speaker fills the middle.
EDGE_BAND = 0.2


class Watermark(BaseModel):
    """A box in source pixels and the source-time spans it is visible."""

    x: int
    y: int
    width: int
    height: int
    spans: list[tuple[float, float]]
    windows: int
    confirmed: bool | None = None
    # Source time of the clearest sighting, where the vision model is shown the mark.
    seen_at: float | None = None

    def visible(self, start: float, end: float) -> list[tuple[float, float]]:
        """Visible spans clipped to [start, end)."""
        return [(max(a, start), min(b, end)) for a, b in self.spans if b > start and a < end]


@dataclass
class _Track:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in sample pixels
    windows: list[int] = field(default_factory=list)


def edge_mask(frame: np.ndarray) -> np.ndarray:
    f = frame.astype(np.int16)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:] = np.abs(f[:, 1:] - f[:, :-1])
    gy[1:, :] = np.abs(f[1:, :] - f[:-1, :])
    return np.maximum(gx, gy) > EDGE_LEVEL


Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in sample pixels


def edge_share(frames: np.ndarray) -> np.ndarray:
    """For each pixel, the share of the window's frames in which it sits on an edge."""
    return np.mean([edge_mask(f) for f in frames], axis=0)


def scan_window(frames: np.ndarray, share: np.ndarray | None = None) -> list[Box]:
    """Boxes of edges that stay put through one window while the scene moves."""
    from scipy import ndimage

    std = frames.std(axis=0)
    if (std > MOVING_STD).mean() < MIN_MOTION:
        return []  # a still shot: everything persists, so nothing can be told apart
    share = edge_share(frames) if share is None else share
    mask = (share >= PERSISTENT_SHARE) & (std < STATIC_STD)
    height, width = mask.shape
    labels, _ = ndimage.label(ndimage.binary_dilation(mask, iterations=4))
    boxes = []
    for index, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is None:
            continue
        ys, xs = found
        w, h = xs.stop - xs.start, ys.stop - ys.start
        if w * h > MAX_AREA * width * height or w > 0.9 * width or w < 6 or h < 4:
            continue
        if mask[found][labels[found] == index].sum() < MIN_EDGE_PIXELS:
            continue
        boxes.append((xs.start, ys.start, xs.stop, ys.stop))
    return boxes


def contrast(share: np.ndarray, reference: np.ndarray) -> float:
    """How much more the overlay's strokes are edges than the rest of its box."""
    if not reference.any():
        return 0.0
    off = share[~reference].mean() if (~reference).any() else 0.0
    return float(share[reference].mean() - off)


def present(share: np.ndarray, reference: np.ndarray) -> bool:
    """Whether a box's edges in one window show the overlay's own pattern.

    Compares the pattern with the rest of the box rather than asking for still pixels: a
    semi-transparent mark changes value when the picture under it fades or cuts, but its
    strokes still stand out, while busy texture has edges everywhere and stands out nowhere.
    """
    if not reference.any():
        return False
    on = share[reference].mean()
    off = share[~reference].mean() if (~reference).any() else 0.0
    return on >= 0.6 and on - off >= 0.3


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _overlap(a: Box, b: Box) -> float:
    """How much of the smaller box lies inside the other: a window often sees only some of
    an overlay's words, and those pieces have to join the track of the whole."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / smaller if smaller else 0.0


def _area(box: Box) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def track(windows: Iterable[list[Box]], max_area: float = float("inf")) -> list[_Track]:
    tracks: list[_Track] = []
    for index, boxes in enumerate(windows):
        for box in boxes:
            best = max(tracks, key=lambda t: _overlap(t.box, box), default=None)
            union = best and (
                (max(best.box[2], box[2]) - min(best.box[0], box[0]))
                * (max(best.box[3], box[3]) - min(best.box[1], box[1]))
            )
            larger = best and max(_area(best.box), _area(box))
            # Pieces of one overlay join it; a patch of scenery that drifts would otherwise
            # keep growing the box until it covers half the picture.
            if (
                best is not None
                and _overlap(best.box, box) >= 0.5
                and union <= min(max_area, 2 * larger)
            ):
                b = best.box
                best.box = (
                    min(b[0], box[0]),
                    min(b[1], box[1]),
                    max(b[2], box[2]),
                    max(b[3], box[3]),
                )
                if best.windows[-1] != index:
                    best.windows.append(index)
            else:
                tracks.append(_Track(box, [index]))
    return tracks


def merge_touching(tracks: list[_Track], max_area: float, gap: int = 6) -> list[_Track]:
    """A logo and the handle beside it come out as two patches; treat them as one, as long
    as the pair still has the size of an overlay rather than a stretch of scenery."""
    merged: list[_Track] = []
    for t in sorted(tracks, key=lambda t: t.box):
        for m in merged:
            a, b = m.box, t.box
            # Words of one line sit a space apart, which is wider than `gap` for big text.
            rows = min(a[3], b[3]) - max(a[1], b[1])
            same_line = rows >= 0.5 * min(a[3] - a[1], b[3] - b[1])
            across = max(gap, round(0.8 * min(a[3] - a[1], b[3] - b[1]))) if same_line else gap
            near = (
                a[0] - across <= b[2]
                and b[0] - across <= a[2]
                and a[1] - gap <= b[3]
                and b[1] - gap <= a[3]
            )
            shared = set(m.windows) & set(t.windows)
            union = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
            small = (union[2] - union[0]) * (union[3] - union[1]) <= max_area
            if near and small and len(shared) >= 0.5 * min(len(m.windows), len(t.windows)):
                m.box = union
                m.windows = sorted(set(m.windows) | set(t.windows))
                break
        else:
            merged.append(_Track(t.box, list(t.windows)))
    return merged


def near_edge(box: tuple[int, int, int, int], width: int, height: int) -> bool:
    x0, y0, x1, y1 = box
    return (
        x0 <= EDGE_BAND * width
        or x1 >= (1 - EDGE_BAND) * width
        or y0 <= EDGE_BAND * height
        or y1 >= (1 - EDGE_BAND) * height
    )


def spans(
    starts: list[float], window_seconds: float, duration: float, step: float | None = None
) -> list[tuple[float, float]]:
    """Merge the start times of windows into time spans, bridging a single missing window."""
    step = window_seconds if step is None else step
    merged: list[list[float]] = []
    for start in starts:
        # Bridged by where the last window's coverage ends, so overlapping windows with one
        # missed between them never leave a gap.
        if merged and start <= merged[-1][1] + window_seconds + 2 * step + 1e-6:
            merged[-1][1] = start
        else:
            merged.append([start, start])
    return [(round(a, 3), round(min(duration, b + window_seconds), 3)) for a, b in merged]


def windows_of(
    frames: Iterable[np.ndarray], per_window: int, every: int | None = None
) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    """Windows of `per_window` frames starting every `every` frames, as (first frame,
    frames, edge share). A video shorter than one window is one window."""
    every = per_window if every is None else every
    recent: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=per_window)
    count = 0
    for frame in frames:
        recent.append((frame, edge_mask(frame)))
        count += 1
        first = count - len(recent)
        if len(recent) == per_window and first % every == 0:
            yield first, np.stack([f for f, _ in recent]), np.mean([m for _, m in recent], axis=0)
    if count < per_window and count >= 2:
        yield 0, np.stack([f for f, _ in recent]), np.mean([m for _, m in recent], axis=0)


def find(
    frames: Callable[[], Iterable[np.ndarray]],
    source_size: tuple[int, int],
    duration: float,
    sample_fps: float = SAMPLE_FPS,
    window_seconds: float = WINDOW_SECONDS,
    step: float = WINDOW_STEP,
) -> list[Watermark]:
    """Candidate overlays in grayscale frames sampled at sample_fps. `frames` is called
    twice: once to find the overlays, once to see in which windows each one shows."""
    per_window = max(2, round(sample_fps * window_seconds))
    every = max(1, round(sample_fps * step))
    starts: list[float] = []
    window_boxes: list[list[Box]] = []
    sample_height = sample_width = 0
    for first, stack, share in windows_of(frames(), per_window, every):
        sample_height, sample_width = stack.shape[1:]
        starts.append(first / sample_fps)
        window_boxes.append(scan_window(stack, share))
    if not sample_width:
        return []

    # A few seconds of stillness is often just a pause; real overlays outlast it. As many
    # overlapping windows as span two windows back to back.
    needed = round(window_seconds / step) + 1
    min_windows = needed if len(window_boxes) >= needed else 1
    frame_area = sample_width * sample_height
    # Pieces are joined first: a word that only some windows saw still belongs to its line.
    tracks = [
        t
        for t in merge_touching(track(window_boxes, MAX_AREA * frame_area), MAX_AREA * frame_area)
        if len(t.windows) >= min_windows and near_edge(t.box, sample_width, sample_height)
    ]
    if not tracks:
        return []

    # Detection needs motion, but once an overlay's strokes are known they can be
    # recognised in any window, including still ones and ones where the mark was faded.
    short = max(2, round(sample_fps * PRESENCE_SECONDS))
    short_starts: list[float] = []
    crops: list[list[np.ndarray]] = [[] for _ in tracks]
    for first, _, share in windows_of(frames(), short, 1):
        short_starts.append(first / sample_fps)
        for i, t in enumerate(tracks):
            x0, y0, x1, y1 = t.box
            crops[i].append(share[y0:y1, x0:x1])
    starts_at = np.array(short_starts)

    scale = source_size[0] / sample_width
    found = []
    for t, boxes in zip(tracks, crops, strict=True):
        # The strokes are what was an edge throughout the windows the mark was found in.
        inside = [
            k
            for w in t.windows
            for k in np.flatnonzero(
                (starts_at >= starts[w])
                & (starts_at + PRESENCE_SECONDS <= starts[w] + window_seconds)
            )
        ]
        if not inside:
            continue
        reference = np.mean([boxes[k] >= PERSISTENT_SHARE for k in inside], axis=0) >= 0.5
        windows = [k for k, share in enumerate(boxes) if present(share, reference)]
        if len(windows) * (1 / sample_fps) < min(window_seconds, duration / 2):
            continue  # its pattern doesn't really stand out: scenery, not an overlay
        clearest = max(windows, key=lambda k: contrast(boxes[k], reference))
        x0, y0, x1, y1 = (round(v * scale) for v in t.box)
        # Padding follows the shorter side, the text height for a handle: padded by its
        # length, it takes in so much scene that the vision model no longer sees an overlay.
        pad = max(6, round(min(0.4 * min(x1 - x0, y1 - y0), 0.03 * source_size[0])))
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(source_size[0], x1 + pad), min(source_size[1], y1 + pad)
        found.append(
            Watermark(
                x=x0 // 2 * 2,
                y=y0 // 2 * 2,
                width=(x1 - x0) // 2 * 2,
                height=(y1 - y0) // 2 * 2,
                spans=spans(
                    [short_starts[k] for k in windows], PRESENCE_SECONDS, duration, 1 / sample_fps
                ),
                windows=len(windows),
                seen_at=round(min(duration, short_starts[clearest] + PRESENCE_SECONDS / 2), 3),
            )
        )
    return found


def sample_frames(
    video: Path, ffmpeg_bin: str, fps: float = SAMPLE_FPS, width: int = SAMPLE_WIDTH
) -> Iterator[np.ndarray]:
    """Grayscale frames, streamed so a long video never sits in memory at once."""
    cmd = [
        ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "error", "-i", str(video),
        "-vf", f"fps={fps},scale={width}:-2:flags=area,format=gray",
        "-f", "rawvideo", "-",
    ]  # fmt: skip
    probe = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-v", "error", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    height = len(probe.stdout) // width
    size = width * height
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        while chunk := process.stdout.read(size):
            if len(chunk) < size:
                break
            yield np.frombuffer(chunk, np.uint8).reshape(height, width)


def detect(
    video: Path, source_size: tuple[int, int], duration: float, ffmpeg_bin: str
) -> list[Watermark]:
    return find(lambda: sample_frames(video, ffmpeg_bin), source_size, duration)


class _Verdict(BaseModel):
    overlay: bool
    what: str


def _evidence(video: Path, mark: Watermark, ffmpeg_bin: str, dest: Path) -> list[Path]:
    """The frame with the box drawn on it, and a close-up of the box."""
    from PIL import Image, ImageDraw

    # The middle of a span can fall where a jumping mark is in its other place.
    start, end = max(mark.spans, key=lambda s: s[1] - s[0])
    at = mark.seen_at if mark.seen_at is not None else (start + end) / 2
    frame = dest / "frame.png"
    subprocess.run(
        [ffmpeg_bin, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", str(frame)],
        check=True,
    )  # fmt: skip
    image = Image.open(frame).convert("RGB")
    pad_x, pad_y = mark.width // 2, mark.height // 2
    close = image.crop(
        (
            max(0, mark.x - pad_x),
            max(0, mark.y - pad_y),
            min(image.width, mark.x + mark.width + pad_x),
            min(image.height, mark.y + mark.height + pad_y),
        )
    )
    close = close.resize(
        (max(384, close.width), round(close.height * max(384, close.width) / close.width))
    )
    marked = image.copy()
    line = max(3, image.width // 300)
    ImageDraw.Draw(marked).rectangle(
        (mark.x, mark.y, mark.x + mark.width, mark.y + mark.height), outline=(255, 0, 0), width=line
    )
    marked.thumbnail((1024, 1024))
    paths = [dest / "marked.jpg", dest / "close.jpg"]
    marked.save(paths[0], quality=88)
    close.save(paths[1], quality=92)
    return paths


CONFIRM_PROMPT = (
    "The first image is a frame from a video with a red box drawn on it. The second is a "
    "close-up of the area inside the box. Was what is inside the box added on top of the "
    "video afterwards, as a watermark, logo, channel or app name, @username, website or other "
    "branding? Or is it part of the filmed scene itself, such as a wall, furniture, a window, "
    "a lamp, a person, their clothes, or a real sign in the room? Answer overlay=true only "
    "for something added on top."
)


def confirm(vision, video: Path, marks: list[Watermark], ffmpeg_bin: str, work_dir: Path):
    """Ask the vision model which candidates are overlays rather than parts of the scene."""
    from agentic_editor.llm import LLMError, parse

    confirmed = []
    for i, mark in enumerate(marks):
        dest = work_dir / f"watermark-{i}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            reply = vision.chat_json(
                [{"role": "user", "content": CONFIRM_PROMPT}],
                _Verdict.model_json_schema(),
                images=_evidence(video, mark, ffmpeg_bin, dest),
                temperature=0.0,
            )
            overlay = parse(_Verdict, reply).overlay
        except LLMError:
            # Unsure is treated as yes: a watermark must never be left in.
            overlay = True
        confirmed.append(mark.model_copy(update={"confirmed": overlay}))
    return confirmed
