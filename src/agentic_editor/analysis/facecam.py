"""Where a streamer's camera is in a stream's picture: the box of webcam laid over the game
(or the website, the desktop) in a corner or along an edge, for the facecam layout
(presets.Facecam, render/facecam.py).

Frames spread over the clip are read small. The streamer is the face that stays put in
them (faces in the game come and go and move); the camera box is the rectangle round it
whose sides are edges that stay put while the picture changes (the box's border, or the
step from the camera to what is beside it), each side either such a line or the frame's
own edge. A line inside the room (a door, a shelf) can win over the frame's edge: the
camera panel then shows less of the room, never part of the game, which is the error that
looks wrong. A face that fills much of the picture with no box round it is a stream that
is all camera ("full")."""

import itertools
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agentic_editor.analysis import layout

log = logging.getLogger(__name__)

YUNET = "face_detection_yunet_2023mar.onnx"
SAMPLES = 36  # frames looked at across the clip
WIDTH = 960  # px the frames are read at
STEADY = 0.5  # share of the frames the streamer's face must be in, in one place
EDGE_LEVEL = 8.0  # grey levels a lasting edge steps by
FRAME_SIDE = 0.6  # what the frame's own edge scores as a side of the box
SURE = 0.55  # a box scoring under this isn't trusted
STRIP = 8  # px either side of a line compared
CANDIDATES = 6  # lines tried per side, besides the frame's edge
FULL_FACE = 0.08  # a face this wide (share of the picture) with no box: all camera
FULL_AREA = 0.55  # a box this much of the picture: all camera


@dataclass(frozen=True)
class Camera:
    """The camera in source pixels: `box` (x, y, width, height) of the camera, None when
    the picture is all camera, and the streamer's `face` (x, y, width, height)."""

    face: tuple[int, int, int, int] | None
    box: tuple[int, int, int, int] | None
    score: float = 0.0

    @property
    def full(self) -> bool:
        return self.box is None


def inside(face: tuple[int, int, int, int] | None, box: tuple[int, int, int, int]) -> bool:
    """Whether the face's middle is in the box."""
    if face is None:
        return False
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    return box[0] <= cx <= box[0] + box[2] and box[1] <= cy <= box[1] + box[3]


def model(models_dir: Path) -> Path:
    from agentic_editor import upscale

    return upscale.fetch_models(models_dir, (YUNET,)) / YUNET


def faces(frames: np.ndarray, detector_path: Path) -> list[list[tuple[float, float, float, float]]]:
    """Faces in each frame, x, y, width, height in the frames' pixels."""
    import cv2

    height, width = frames.shape[1:3]
    detector = cv2.FaceDetectorYN.create(str(detector_path), "", (width, height), 0.6, 0.3, 50)
    found = []
    for frame in frames:
        _, rows = detector.detect(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        found.append([] if rows is None else [tuple(map(float, r[:4])) for r in rows])
    return found


def steady_face(
    found: list[list[tuple[float, float, float, float]]],
) -> tuple[tuple[float, float, float, float], float] | None:
    """The face that stays in one place through most frames (its median box), and the
    share of frames it is in; None when no face does for STEADY of them."""
    seen = [(i, f) for i, faces_in in enumerate(found) for f in faces_in]
    best = None
    for _, (x, y, w, h) in seen:
        cx, cy = x + w / 2, y + h / 2
        near: dict[int, tuple[float, float, float, float]] = {}
        for i, (x2, y2, w2, h2) in seen:
            close = abs(x2 + w2 / 2 - cx) < 0.8 * max(w, 12) and abs(y2 + h2 / 2 - cy) < 0.8 * max(
                h, 12
            )
            if close and 0.6 < w2 / w < 1.6:
                near.setdefault(i, (x2, y2, w2, h2))
        share = len(near) / max(1, len(found))
        if best is None or share > best[1]:
            box = tuple(float(v) for v in np.median(np.array(list(near.values())), axis=0))
            best = (box, share)
    if best is None or best[1] < STEADY:
        return None
    return best


def _coverage(edges: np.ndarray, at: int, start: float, end: float, vertical: bool) -> float:
    """Share of a line's pixels on a lasting edge (within a pixel of it)."""
    length = edges.shape[0] if vertical else edges.shape[1]
    start, end = int(max(0, start)), int(min(length, end))
    if end - start < 3:
        return 0.0
    across = edges.shape[1] if vertical else edges.shape[0]
    near = [c for c in (at - 1, at, at + 1) if 0 <= c < across]
    if not near:
        return 0.0
    line = edges[start:end, near].max(axis=1) if vertical else edges[near, start:end].max(axis=0)
    return float((line > EDGE_LEVEL).mean())


@dataclass
class Picture:
    """What the box is found from, all at the read size: lasting edges across columns
    (`across`, between x and x+1) and rows (`down`), how much each pixel changes from
    frame to frame (`motion`), and one frame's colours (`colour`)."""

    across: np.ndarray
    down: np.ndarray
    motion: np.ndarray
    colour: np.ndarray

    @classmethod
    def of(cls, frames: np.ndarray) -> "Picture":
        grey = frames.astype(np.float32).mean(axis=3)
        return cls(
            across=np.median(np.abs(np.diff(grey, axis=2)), axis=0),
            down=np.median(np.abs(np.diff(grey, axis=1)), axis=0),
            motion=np.abs(np.diff(grey, axis=0)).mean(axis=0),
            colour=frames[len(frames) // 2].astype(np.float32),
        )

    @property
    def size(self) -> tuple[int, int]:
        return self.motion.shape[1], self.motion.shape[0]


SIDES = ("left", "right", "top", "bottom")


def _lines(picture: Picture, face: tuple[float, ...], side: str) -> list[int]:
    """Where the box's `side` may be: the lines of lasting edge beside the face that run
    furthest along it, best first, and the frame's edge."""
    width, height = picture.size
    x, y, w, h = face
    vertical = side in ("left", "right")
    edges = picture.across if vertical else picture.down
    if vertical:
        start, end = y - 0.6 * h, y + 1.6 * h
        places = (
            range(int(x - 0.4 * w), 0, -1) if side == "left" else range(int(x + 1.4 * w), width - 1)
        )
    else:
        start, end = x - 0.6 * w, x + 1.6 * w
        places = (
            range(int(y - 0.4 * h), 0, -1) if side == "top" else range(int(y + 1.6 * h), height - 1)
        )
    scored = sorted(
        ((_coverage(edges, at, start, end, vertical), at) for at in places), reverse=True
    )
    picked: list[int] = []
    for share, at in scored:
        if share < 0.35 or len(picked) >= CANDIDATES:
            break
        if all(abs(at - p) > 4 for p in picked):
            picked.append(at)
    frame_edge = {"left": 0, "right": width, "top": 0, "bottom": height}[side]
    return [*picked, frame_edge]


def _strips(side: str, at: int, start: int, end: int) -> tuple[tuple[slice, slice], ...]:
    """The strip just inside the box along a side, and the one just outside."""
    before = slice(max(0, at - 2 - STRIP), max(0, at - 2))
    after = slice(at + 2, at + 2 + STRIP)
    span = slice(start, end)
    if side == "left":
        return (span, after), (span, before)
    if side == "right":
        return (span, before), (span, after)
    if side == "top":
        return (after, span), (before, span)
    return (before, span), (after, span)


def _side(picture: Picture, side: str, at: int, box: tuple[int, int, int, int]) -> float:
    """How much a side looks like the camera's edge: a lasting line along all of it that
    stops at its corners, with something else beyond it (other colours, or other motion)."""
    width, height = picture.size
    left, top, right, bottom = box
    if at == {"left": 0, "right": width, "top": 0, "bottom": height}[side]:
        return FRAME_SIDE
    vertical = side in ("left", "right")
    edges = picture.across if vertical else picture.down
    line = at if side in ("left", "top") else at - 1
    start, end = (top, bottom) if vertical else (left, right)
    along = _coverage(edges, line, start, end, vertical)
    reach = 0.3 * (end - start)
    limit = height if vertical else width
    beyond = [
        _coverage(edges, line, a, b, vertical)
        for a, b in ((start - reach, start - 2), (end + 2, end + reach))
        if a >= 0 and b <= limit
    ]
    runs_on = max(beyond, default=0.0)
    inside, outside = _strips(side, at, start, end)
    moving_in, moving_out = (
        float(picture.motion[s].mean()) if picture.motion[s].size else 0.0
        for s in (inside, outside)
    )
    colours = [
        picture.colour[s].reshape(-1, 3).mean(0)
        for s in (inside, outside)
        if picture.colour[s].size
    ]
    step = float(np.abs(colours[0] - colours[1]).mean()) if len(colours) == 2 else 0.0
    other = min(
        1.0, max(step / 40, abs(np.log((moving_in + 0.5) / (moving_out + 0.5))) / np.log(4))
    )
    return 0.55 * along + 0.25 * other * along + 0.2 * max(0.0, along - runs_on)


def camera_box(
    picture: Picture, face: tuple[float, float, float, float]
) -> tuple[tuple[int, int, int, int], float] | None:
    """The best box round the face, left, top, right, bottom at the read size, and its
    score; None when no box of a camera's shape fits round it."""
    width, height = picture.size
    x, y, w, h = face
    options = {side: _lines(picture, face, side) for side in SIDES}
    best = None
    for left, right, top, bottom in itertools.product(*(options[s] for s in SIDES)):
        wide, tall = right - left, bottom - top
        if wide < 2 * w or tall < 1.8 * h or not 0.75 <= wide / tall <= 2.4:
            continue
        if (left == 0) + (right == width) + (top == 0) + (bottom == height) > 2:
            continue  # a box on three edges is the whole picture's width or height
        box = (left, top, right, bottom)
        sides = zip(SIDES, (left, right, top, bottom), strict=True)
        score = float(np.mean([_side(picture, s, at, box) for s, at in sides]))
        # A camera frames the face near its middle, a little high; it is rarely huge.
        across, down = (x + w / 2 - left) / wide, (y + h / 2 - top) / tall
        score -= 0.6 * max(0.0, abs(across - 0.5) - 0.2) + 0.6 * max(0.0, abs(down - 0.42) - 0.22)
        score -= 0.3 * max(0.0, wide * tall / (width * height) - 0.35)
        if best is None or score > best[1]:
            best = (box, score)
    return best


def find(
    video: Path,
    start: float,
    end: float,
    source: tuple[int, int],
    models_dir: Path,
    ffmpeg_bin: str,
) -> Camera | None:
    """The streamer's camera in `video` between `start` and `end`, in source pixels; None
    when no face stays put (no streamer on camera) or no box round it is sure."""
    width = WIDTH
    height = max(2, round(WIDTH * source[1] / source[0] / 2) * 2)
    frames = layout.sample(
        video, max(0.5, end - start), SAMPLES, (width, height), ffmpeg_bin, start
    )
    if len(frames) < 4:
        return None
    height, width = frames.shape[1:3]
    steady = steady_face(faces(frames, model(models_dir)))
    if steady is None:
        return None
    face, _ = steady
    scale = source[0] / width
    in_source = tuple(round(v * scale) for v in face)
    found = camera_box(Picture.of(frames), face)
    if found is None or found[1] < SURE:
        if face[2] / width >= FULL_FACE:
            return Camera(in_source, None, found[1] if found else 0.0)
        log.info("facecam: no sure camera box round the face at %s (%s)", in_source, found)
        return None
    (left, top, right, bottom), score = found
    if (right - left) * (bottom - top) / (width * height) >= FULL_AREA:
        return Camera(in_source, None, score)
    box = (round(left * scale), round(top * scale), round((right - left) * scale),
           round((bottom - top) * scale))  # fmt: skip
    return Camera(in_source, box, score)
