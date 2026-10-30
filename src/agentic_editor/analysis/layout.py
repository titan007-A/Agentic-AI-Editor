"""How a finished reel is laid out: the colour around its picture, where the picture sits,
and the exact pixels of a piece of text on it. Used to draft a preset from a reel."""

import subprocess
from pathlib import Path

import numpy as np
from scipy import ndimage

TOLERANCE = 14  # per channel; compression noise on a flat colour stays inside this
SOLID = 0.9  # share of the top and bottom edges that must be one colour for a padded layout
STRIP = 0.02  # of the height: the edge rows the background colour is read from
SEEN = 0.1  # share of frames a pixel must differ from the background in to be picture
PICTURE_ROW = 0.3  # share of a row (or column) that must be picture
STILL = 6.0  # grey levels a pixel spreads over the reel by below which it never moved
TEXT_ROW = 0.8  # share of a row unlike the background past which it is picture, still or not
MOVING_ROW = 0.02  # share of a row that has to move for a patchy row to count as picture
INK = 48  # per channel: how far a pixel may be from the text colour and still be text
FULL_HEIGHT = 0.9  # a picture band taller than this fills the frame
BLURRED = 0.5  # detail around the picture, against inside it, below which it is a blur
EDGE = 4.0  # how much stronger than a typical row step a picture's edge is
# A picture that is partly a still image, a photo above a clip: rows where more than MOVES
# of the pixels change over the reel are the clip, rows where less than STILL_ROW do are
# the photo. It counts when the photo has STILL_PART of the picture's rows and the clip
# MOVING_PART; a normal video moves nearly everywhere, a strip of changing lyrics is thin.
MOVES = 0.5
STILL_ROW = 0.1
STILL_PART = 0.2
MOVING_PART = 0.25

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 as shares of the frame
Line = tuple[int, int, int, int]  # top, bottom, left, right in pixels

NAMED = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "yellow": (255, 225, 0),
    "gold": (230, 190, 40),
    "orange": (255, 140, 0),
    "red": (225, 30, 30),
    "pink": (255, 105, 180),
    "purple": (150, 70, 220),
    "blue": (40, 110, 255),
    "cyan": (0, 220, 230),
    "green": (40, 200, 70),
    "brown": (130, 80, 40),
    "beige": (225, 200, 160),
    "cream": (255, 245, 210),
    "teal": (0, 150, 140),
    "magenta": (230, 0, 160),
    "lime": (160, 230, 40),
    "navy": (20, 30, 110),
    "maroon": (120, 20, 30),
}
PALETTE = np.array(sorted(set(NAMED.values())), np.float32)
PALETTE_NAMES = [next(n for n, c in NAMED.items() if c == tuple(p)) for p in PALETTE.astype(int)]
# Dark and grey pixels around letters are outlines, shadows and blending, not a colour of
# the words.
NOT_WORDS = {"black", "gray", "silver", "navy", "maroon", "brown"}


def sample(
    video: Path,
    duration: float,
    count: int,
    size: tuple[int, int],
    ffmpeg_bin: str,
    start: float = 0.0,
) -> np.ndarray:
    """`count` frames spread over `duration` seconds from `start`, as RGB arrays of `size`."""
    width, height = size
    raw = subprocess.run(
        [ffmpeg_bin, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
         "-i", str(video),
         "-vf", f"fps={count / duration:.6f},scale={width}:{height}:flags=area",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    frames = np.frombuffer(raw, np.uint8)
    return frames[: len(frames) // (width * height * 3) * width * height * 3].reshape(
        -1, height, width, 3
    )


def frame_at(video: Path, at: float, size: tuple[int, int], ffmpeg_bin: str) -> np.ndarray | None:
    """The frame at `at` as RGB, or None when there is none (a time past the last frame)."""
    width, height = size
    raw = subprocess.run(
        [ffmpeg_bin, "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:{height}:flags=lanczos", "-pix_fmt", "rgb24", "-f", "rawvideo",
         "-"],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    if len(raw) < width * height * 3:
        return None
    return np.frombuffer(raw[: width * height * 3], np.uint8).reshape(height, width, 3)


def background(frames: np.ndarray) -> tuple[int, int, int] | None:
    """The flat colour along the top and bottom edges, when there is one: the padding
    around a picture that doesn't fill the frame."""
    strip = max(1, round(frames.shape[1] * STRIP))
    edges = np.concatenate([frames[:, :strip], frames[:, -strip:]], axis=1).reshape(-1, 3)
    colour = np.median(edges, axis=0)
    flat = np.all(np.abs(edges.astype(np.int16) - colour) <= TOLERANCE, axis=1).mean()
    return tuple(int(c) for c in colour) if flat >= SOLID else None


def _longest_run(on: np.ndarray, gap: int) -> tuple[int, int] | None:
    """The longest stretch of True, bridging gaps of up to `gap` False values."""
    runs: list[tuple[int, int]] = []
    for i in np.nonzero(on)[0]:
        if runs and i - runs[-1][1] <= gap:
            runs[-1] = (runs[-1][0], i + 1)
        else:
            runs.append((i, i + 1))
    return max(runs, key=lambda r: r[1] - r[0]) if runs else None


def picture_box(frames: np.ndarray, colour: tuple[int, int, int] | None) -> Box:
    """Where the picture sits. A pixel is picture if it differs from the background in
    enough frames; across a whole reel even a dark stage lights up now and then, while
    padding never does.

    Text on the padding, a repost's caption above the picture, can cover half its rows,
    as much as a dark stage does of its own. But it never moves, and it leaves background
    between its letters, so a row like that only counts as picture if something in it
    moves."""
    if colour is None:
        return (0.0, 0.0, 1.0, 1.0)
    _, height, width, _ = frames.shape
    differs = np.any(np.abs(frames.astype(np.int16) - colour) > TOLERANCE, axis=3)
    picture = differs.mean(axis=0) > SEEN
    # Shares of the picture's own columns, so a still row of a narrow picture is as full
    # as one of a wide picture.
    across = _longest_run(picture.mean(axis=0) > PICTURE_ROW, gap=max(1, width // 100))
    left, right = across or (0, width)
    share = picture[:, left:right].mean(axis=1)
    still = frames.astype(np.float32).mean(axis=3).std(axis=0) <= STILL
    moving = (~still[:, left:right]).mean(axis=1)
    is_picture = (share > TEXT_ROW) | ((share > PICTURE_ROW) & (moving > MOVING_ROW))
    rows = _longest_run(is_picture, gap=max(1, height // 100))
    if rows is None:
        return (0.0, 0.0, 1.0, 1.0)
    cols = _longest_run(
        picture[rows[0] : rows[1]].mean(axis=0) > PICTURE_ROW, gap=max(1, width // 100)
    )
    cols = cols or (0, width)
    return (cols[0] / width, rows[0] / height, cols[1] / width, rows[1] / height)


def blurred_fill(frames: np.ndarray) -> Box | None:
    """Where a sharp picture sits on a blurred, enlarged copy of itself, the other common
    way to fill a tall frame, or None. The picture's top and bottom edges are a hard line
    across the whole width on the same rows in every frame; and the copy, blurred, has
    far less fine detail than the picture."""
    _, height, _, _ = frames.shape
    grey = frames.astype(np.float32).mean(axis=3)
    step = np.median(np.abs(np.diff(grey, axis=1)).mean(axis=2), axis=0)
    half = len(step) // 2
    top, bottom = int(np.argmax(step[:half])), half + int(np.argmax(step[half:]))
    floor = EDGE * np.median(step)
    if min(step[top], step[bottom]) < floor or not 0.2 < (bottom - top) / height < FULL_HEIGHT:
        return None
    detail = np.abs(np.diff(grey, axis=2)).mean(axis=(0, 2))
    inside = detail[top + 1 : bottom + 1].mean()
    outside = np.concatenate([detail[: top + 1], detail[bottom + 1 :]]).mean()
    if outside > BLURRED * inside:
        return None
    return (0.0, (top + 1) / height, 1.0, (bottom + 1) / height)


def saturation(frames: np.ndarray) -> float:
    """Mean distance of each pixel from its own grey, 0 for a black-and-white picture."""
    pixels = frames.astype(np.float32)
    return float(np.abs(pixels - pixels.mean(axis=-1, keepdims=True)).mean())


def _clusters(pixels: np.ndarray, k: int = 6, rounds: int = 10) -> list[np.ndarray]:
    """k-means of colours. It starts from the darkest pixel and then, one at a time, the
    pixel farthest from every start so far, so a small word in its own colour gets a
    cluster of its own and the result is repeatable."""
    starts = [pixels[np.argmin(pixels.sum(axis=1))]]
    for _ in range(k - 1):
        far = np.min([np.abs(pixels - c).sum(axis=1) for c in starts], axis=0)
        starts.append(pixels[np.argmax(far)])
    centres = np.array(starts)
    for _ in range(rounds):
        nearest = np.argmin(((pixels[:, None] - centres[None]) ** 2).sum(axis=2), axis=1)
        centres = np.array(
            [pixels[nearest == j].mean(axis=0) if (nearest == j).any() else centres[j]
             for j in range(k)]
        )  # fmt: skip
    nearest = np.argmin(((pixels[:, None] - centres[None]) ** 2).sum(axis=2), axis=1)
    return [pixels[nearest == j] for j in range(k) if (nearest == j).mean() >= 0.005]


def text_colour(crop: np.ndarray, name: str) -> tuple[int, int, int]:
    """The exact colour of the letters in `crop`, which the vision model called `name`.
    For a colour it named, the pixels nearer that name than any other, so a yellow word
    beside white ones keeps to itself; otherwise, of the crop's main colours, the one
    nearest. The median of the pixels close to it leaves out blended edge pixels."""
    words = name.lower().split() if name else []
    known = words[-1] if words and words[-1] in NAMED else None
    target = np.array(NAMED[known or "white"], np.float32)
    pixels = crop.reshape(-1, 3).astype(np.float32)
    group = None
    if known:
        nearest = np.argmin(np.abs(pixels[:, None] - PALETTE[None]).sum(axis=2), axis=1)
        mine = pixels[nearest == np.argmin(np.abs(PALETTE - target).sum(axis=1))]
        if len(mine) >= max(20, 0.002 * len(pixels)):
            group = mine
    if group is None:
        group = min(_clusters(pixels), key=lambda g: np.abs(g.mean(axis=0) - target).sum())
    centre = np.median(group, axis=0)
    core = group[np.abs(group - centre).max(axis=1) <= 24]
    colour = np.median(core if len(core) else group, axis=0)
    # Flat white and black come out a step off after compression; put them back.
    colour = np.where(colour >= 240, 255, np.where(colour <= 12, 0, colour))
    return tuple(int(c) for c in np.round(colour))


def _names(pixels: np.ndarray) -> np.ndarray:
    """Each pixel's nearest named colour, as an index into PALETTE."""
    flat = pixels.reshape(-1, 3).astype(np.float32)
    return np.argmin(np.abs(flat[:, None] - PALETTE[None]).sum(axis=2), axis=1)


def second_colour(crop: np.ndarray, around: np.ndarray, colour: tuple[int, int, int]) -> str | None:
    """The name of a second colour words in `crop` are drawn in, besides `colour`: the
    most common named colour in it after `colour` and the background (the most common
    in `around`, which has more of the background than the letters' own rows do)."""
    counts = np.bincount(_names(crop), minlength=len(PALETTE))
    first = int(np.argmin(np.abs(PALETTE - np.array(colour)).sum(axis=1)))
    background = int(np.argmax(np.bincount(_names(around), minlength=len(PALETTE))))
    floor = max(20.0, 0.2 * counts[first])
    counts[[first, background]] = 0
    for i in np.argsort(counts)[::-1]:
        if counts[i] < floor:
            return None
        if PALETTE_NAMES[i] not in NOT_WORDS:
            return PALETTE_NAMES[i]
    return None


def ink(crop: np.ndarray, colour: tuple[int, int, int]) -> np.ndarray:
    return np.all(np.abs(crop.astype(np.int16) - colour) <= INK, axis=2)


def letters(mask: np.ndarray, tallest: float) -> np.ndarray:
    """The mask without shapes that can't be letters: anything touching the edge of the
    window searched (the text is inside it) or taller than the text can be. What is left
    of a bright picture behind captions is then too small to join a line of text."""
    labels, count = ndimage.label(mask)
    keep = np.zeros(count + 1, bool)
    height, width = mask.shape
    for i, (rows, cols) in enumerate(ndimage.find_objects(labels), start=1):
        edge = rows.start == 0 or cols.start == 0 or rows.stop == height or cols.stop == width
        keep[i] = not edge and rows.stop - rows.start <= tallest
    return keep[labels]


def lines(mask: np.ndarray) -> list[Line]:
    """The text lines in an ink mask, top to bottom. A row belongs to a line when it has
    a little ink; small breaks (between an accent and its letter) don't split one."""
    width = mask.shape[1]
    rows = mask.sum(axis=1) >= max(2, width * 0.005)
    runs: list[list[int]] = []
    for y in np.nonzero(rows)[0]:
        if runs and y - runs[-1][1] <= 2:
            runs[-1][1] = y + 1
        else:
            runs.append([y, y + 1])
    if not runs:
        return []
    tallest = max(b - a for a, b in runs)
    # An accent or dot left on its own joins the line below it.
    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= tallest * 0.12:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    found = []
    for top, bottom in merged:
        if bottom - top < tallest * 0.35:
            continue
        cols = np.nonzero(mask[top:bottom].any(axis=0))[0]
        found.append((top, bottom, int(cols[0]), int(cols[-1]) + 1))
    return found


def plainer_side(frames: np.ndarray, rows: tuple[int, int], top: int, bottom: int) -> str:
    """Which of the still rows next to the moving `rows`, "above" or "below", hold less
    detail: a window taller than the moving part shows a dark strip there rather than the
    edge of a photo's caption."""
    reach = max(1, (bottom - top) // 10)
    grey = np.median(frames.astype(np.float32).mean(axis=3), axis=0)
    detail = np.abs(np.diff(grey, axis=1)).mean(axis=1)
    above = detail[max(top, rows[0] - reach) : rows[0]]
    below = detail[rows[1] : min(bottom, rows[1] + reach)]
    if not len(above) or not len(below):
        return "above" if len(above) else "below"
    return "above" if above.mean() < below.mean() else "below"


def moving_rows(frames: np.ndarray, top: int, bottom: int) -> tuple[int, int] | None:
    """The rows between `top` and `bottom` where a picture that is partly a still image
    moves, as rows of the frames; None when it all moves, as a normal video does, or
    nothing does."""
    height = bottom - top
    if len(frames) < 2 or height <= 0:
        return None
    still = frames[:, top:bottom].astype(np.float32).mean(axis=3).std(axis=0) <= STILL
    share = (~still).mean(axis=1)
    run = _longest_run(share > MOVES, gap=max(1, height // 50))
    if run is None or run[1] - run[0] < MOVING_PART * height:
        return None
    if (share < STILL_ROW).mean() < STILL_PART:
        return None
    return top + run[0], top + run[1]


# Where the action is (framing.keep_action): pixels that change by this many grey levels
# from one frame to the next move; a frame pair where most of the picture changes is a cut
# or the camera swinging, which says nothing about where the action is; and the action
# spans the columns and rows holding at least this share of the busiest one's movement.
ACTION_LEVEL = 24
ACTION_GLOBAL = 0.5
ACTION_SHARE = 0.15


def action_box(frames: np.ndarray) -> tuple[int, int, int, int] | None:
    """The box (x0, y0, x1, y1, in the frames' pixels) holding the movement across these
    consecutive frames: a person falling, a bar dropping. None when nothing moves."""
    if len(frames) < 2:
        return None
    grey = frames.astype(np.float32).mean(axis=3)
    moved = np.abs(np.diff(grey, axis=0)) > ACTION_LEVEL
    moved = moved[moved.mean(axis=(1, 2)) < ACTION_GLOBAL]
    if not len(moved):
        return None
    height, width = moved.shape[1:]
    spans = []
    for energy, size in ((moved.sum(axis=(0, 1)), width), (moved.sum(axis=(0, 2)), height)):
        energy = ndimage.uniform_filter1d(energy.astype(np.float32), max(1, size // 30))
        if energy.max() <= 0:
            return None
        busy = np.flatnonzero(energy >= ACTION_SHARE * energy.max())
        spans.append((int(busy[0]), int(busy[-1]) + 1))
    (x0, x1), (y0, y1) = spans
    return x0, y0, x1, y1
