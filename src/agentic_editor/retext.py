"""A downloaded reel's caption box written again (presets.Retext): clip pages put one line
of what happens in a white box over the middle of the video ("N3on got EMOTIONAL when...").
For a download-only preset, that box is found, its words read, written again in other
words by the planner model (same meaning, names and facts), and drawn in a new box of the
same look over the old one, for as long as the old one shows.

- find_box: frames read small; the box is the steady near-white region (its letters
  closed over) that floats clear of the frame's edges, box-shaped, with dark letters in it.
- read_box: OCR of the box cut from a full-size frame, its lines joined top to bottom.
- rewrite: the planner model, held to a JSON reply, asked again once when its words stay too
  close to the old ones or run too long.
- draw: a frame-sized PNG with the new box (the old box's colours, corners rounded) and the
  words wrapped and sized as the old ones were, the box growing (about its middle) only
  when they can't fit; laid over the video by apply, the sound copied.
"""

import difflib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from agentic_editor import ffmpeg
from agentic_editor.analysis import layout, ocr
from agentic_editor.presets import Preset
from agentic_editor.render import text

log = logging.getLogger(__name__)

SAMPLES = 24  # frames looked at across the video
READ_WIDTH = 540  # px the frames are read at
WHITE = 222  # every channel at least this bright: the box's white
STEADY = 0.6  # share of the frames a box pixel is white in
PRESENT = 0.75  # share of the box's white seen in a frame for the box to be there
DARK = 110  # every channel at most this: a letter
MIN_WIDTH, MIN_HEIGHT, MAX_HEIGHT = 0.4, 0.03, 0.3  # of the frame
FILLED = 0.85  # share of its bounding rectangle a box covers once its letters are closed
PAD = 0.03  # of the frame's width: room between the box's sides and its words
LINE = 1.2  # line pitch, in letter sizes
SAME = 0.75  # words this alike (difflib ratio over words) to the old ones: asked again
CLOSE = 0.88  # after three tries, the least alike answer is taken if under this
LONGER = 1.4  # the new words may run this much longer than the old, at most
GROW = 1.5  # the new box may be this much taller than the old, at most


@dataclass(frozen=True)
class Box:
    """The caption box in source pixels (left, top, right, bottom), when it shows (s), and
    its colours."""

    x0: int
    y0: int
    x1: int
    y1: int
    start: float
    end: float
    fill: str = "#FFFFFF"
    ink: str = "#000000"

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(round(float(c)) for c in rgb))


def _white(pixels: np.ndarray) -> np.ndarray:
    return (pixels >= WHITE).all(axis=-1)


def on_white(frame: np.ndarray, line: "ocr.Line", share: float = 0.75) -> bool:
    """Whether a line of words is written on white (a caption box), not over the video:
    most of what isn't a letter in its box is white (`share` of it: less where emoji in
    colour sit at its end)."""
    crop = frame[line.y0 : line.y1, line.x0 : line.x1]
    if crop.size == 0:
        return False
    letters = (crop <= DARK).all(axis=-1)
    white = _white(crop)
    rest = ~letters
    # White words outlined in black over the video, or on a black bar, read the other way
    # round: there the dark is no thin letters and the white no background.
    return (
        bool(rest.any())
        and white[rest].mean() >= share
        and white.mean() >= share - 0.2
        and 0.03 <= letters.mean() <= 0.4
    )


def text_block(frame: np.ndarray, lines: list["ocr.Line"]) -> list["ocr.Line"]:
    """The biggest block of lines written on white, one under the other, in the middle of
    the frame (a caption box's words), top to bottom."""
    height = frame.shape[0]
    middle = [ln for ln in lines if 0.1 * height <= (ln.y0 + ln.y1) / 2 <= 0.9 * height]
    kept = sorted((ln for ln in middle if on_white(frame, ln)), key=lambda ln: ln.y0)
    blocks: list[list[ocr.Line]] = []
    for line in kept:
        tall = line.y1 - line.y0
        last = blocks[-1][-1] if blocks else None
        near = last is not None and line.y0 - last.y1 < 1.2 * tall
        if near and min(line.x1, last.x1) > max(line.x0, last.x0):
            blocks[-1].append(line)
        else:
            blocks.append([line])
    if not blocks:
        return []
    return max(blocks, key=lambda b: sum(ocr.letters(ln.text) for ln in b))


def _box_row(strip: np.ndarray) -> bool:
    """Whether a row (or column) is still the box: evenly white, or words on white (its
    letters dark, nearly all the rest white; a white hoodie beside the box has shading)."""
    if strip.size == 0:
        return False
    white = _white(strip)
    if white.mean() >= 0.93:
        return True
    letters = (strip <= DARK).all(axis=-1)
    edges = ~white & ~letters
    return letters.mean() >= 0.01 and edges.mean() <= 0.12 and white.mean() >= 0.6


def grow(frame: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """The white box round a block of words: first its sides moved out while the columns
    beyond are evenly white, then its top and bottom while the rows are still the box (a
    line of words OCR missed included)."""
    height, width = frame.shape[:2]
    reach_y, reach_x = 3 * max(1, y1 - y0), round(0.3 * width)

    def even(strip: np.ndarray) -> bool:
        return strip.size > 0 and _white(strip).mean() >= 0.93

    top, bottom, left, right = y0, y1, x0, x1
    while left > 0 and x0 - left < reach_x and even(frame[top:bottom, left - 1]):
        left -= 1
    while right < width and right - x1 < reach_x and even(frame[top:bottom, right]):
        right += 1
    while top > 0 and y0 - top < reach_y and _box_row(frame[top - 1, left:right]):
        top -= 1
    while bottom < height and bottom - y1 < reach_y and _box_row(frame[bottom, left:right]):
        bottom += 1
    return left, top, right, bottom


def read_area(frame: np.ndarray, rect: tuple[int, int, int, int], settings) -> list["ocr.Line"]:
    """The lines of words in a part of the frame, read from it cut out and made bigger
    (small letters read better), in the frame's pixels, top to bottom."""
    left, top, right, bottom = rect
    crop = Image.fromarray(frame[top:bottom, left:right])
    scale = max(1.0, 1000 / max(1, crop.width))
    if scale > 1:
        crop = crop.resize((1000, round(crop.height * scale)), Image.Resampling.LANCZOS)

    def placed(ln: "ocr.Line") -> "ocr.Line":
        x0, x1 = left + round(ln.x0 / scale), left + round(ln.x1 / scale)
        y0, y1 = top + round(ln.y0 / scale), top + round(ln.y1 / scale)
        return ln.model_copy(update={"x0": x0, "x1": x1, "y0": y0, "y1": y1})

    lines = [placed(ln) for ln in ocr.read(crop, settings.models_dir)]
    return sorted(lines, key=lambda ln: (ln.y0, ln.x0))


def rows_of(lines: list["ocr.Line"]) -> list[str]:
    """Lines read in pieces side by side joined into rows, top to bottom."""
    rows: list[list[ocr.Line]] = []
    for line in lines:
        if rows and abs(line.y0 - rows[-1][0].y0) < (line.y1 - line.y0) * 0.5:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [" ".join(p.text for p in sorted(row, key=lambda p: p.x0)) for row in rows]


def take_in_lines(
    frame: np.ndarray, rect: tuple[int, int, int, int], block: list["ocr.Line"], settings
) -> tuple[tuple[int, int, int, int], list[str]]:
    """The box with any line of its words that sits in a box of its own just above or below
    (captions with one white box per line, the short last line's narrower) taken in, and all
    its words read again from it cut out."""
    height = frame.shape[0]
    left, top, right, bottom = rect
    tall = float(np.median([ln.y1 - ln.y0 for ln in block]))
    margin = max(2, min(ln.y0 for ln in block) - top)  # the box's room above its words
    look = (left, max(0, round(top - 2 * tall)), right, min(height, round(bottom + 2 * tall)))
    lines = read_area(frame, look, settings)
    for line in lines:
        middle = (line.y0 + line.y1) / 2
        if top <= middle <= bottom or not on_white(frame, line, 0.6):
            continue
        # Its own box joins the box: white all the way between them over its width.
        between = (
            frame[line.y1 : top, line.x0 : line.x1]
            if middle < top
            else frame[bottom : line.y0, line.x0 : line.x1]
        )
        if between.size and _white(between).mean() < 0.8:
            continue
        if middle < top and top - line.y1 < 1.5 * tall:
            top = max(0, line.y0 - margin)
        elif middle > bottom and line.y0 - bottom < 1.5 * tall:
            bottom = min(height, line.y1 + margin)
    inside = [ln for ln in lines if top <= (ln.y0 + ln.y1) / 2 <= bottom]
    return (left, top, right, bottom), rows_of(inside)


def find_box(
    video: Path, duration: float, source: tuple[int, int], settings
) -> tuple[Box, list[str], int] | None:
    """The caption box in the video (source pixels, when it shows, its colours), its lines
    of words top to bottom, and how many lines of letters it holds; None when the video has
    none."""
    for share in (0.5, 0.25, 0.75):
        frame = layout.frame_at(video, duration * share, source, settings.ffmpeg_bin)
        if frame is None:
            continue
        lines = ocr.read(Image.fromarray(frame), settings.models_dir, whole_frame=True)
        block = text_block(frame, lines)
        if not block:
            continue
        rect = grow(frame, min(ln.x0 for ln in block), min(ln.y0 for ln in block),
                    max(ln.x1 for ln in block), max(ln.y1 for ln in block))  # fmt: skip
        if rect[2] - rect[0] < MIN_WIDTH * source[0]:
            continue
        rect, words = take_in_lines(frame, rect, block, settings)
        box = _timed(video, duration, source, frame, rect, settings)
        return box, words or [ln.text for ln in block], max(len(words), len(block))
    return None


def _timed(
    video: Path, duration: float, source: tuple[int, int], frame: np.ndarray,
    rect: tuple[int, int, int, int], settings,
) -> Box:  # fmt: skip
    """The box with its colours (from `frame`) and when it shows: the frames read small in
    which its white is there."""
    left, top, right, bottom = rect
    crop = frame[top:bottom, left:right].reshape(-1, 3)
    bright = _white(crop)
    fill = np.median(crop[bright], axis=0)
    letters = crop[(crop <= DARK).all(axis=1)]
    ink = np.median(letters, axis=0) if len(letters) else np.zeros(3)
    width = READ_WIDTH
    height = max(2, round(READ_WIDTH * source[1] / source[0] / 2) * 2)
    frames = layout.sample(video, max(0.5, duration), SAMPLES, (width, height),
                           settings.ffmpeg_bin)  # fmt: skip
    sx, sy = width / source[0], height / source[1]
    rows = slice(math.ceil(top * sy), max(math.ceil(top * sy) + 1, math.floor(bottom * sy)))
    cols = slice(math.ceil(left * sx), max(math.ceil(left * sx) + 1, math.floor(right * sx)))
    share = _white(frames[:, rows, cols]).reshape(len(frames), -1).mean(axis=1)
    seen = share >= bright.mean() * PRESENT
    shown = np.flatnonzero(seen) if seen.any() else np.arange(len(frames))
    step = duration / max(1, len(frames))
    start = 0.0 if shown[0] == 0 else shown[0] * step
    end = duration if shown[-1] >= len(frames) - 1 else (shown[-1] + 1) * step
    return Box(
        x0=max(0, left - 2), y0=max(0, top - 2), x1=min(source[0], right + 2),
        y1=min(source[1], bottom + 2), start=round(start, 2), end=round(end, 2),
        fill=_hex(fill), ink=_hex(ink),
    )  # fmt: skip


REWRITE = (
    "This is the caption a clips page wrote over a short video, in a box over its middle:\n"
    '"{old}"\n\n'
    "Write it again in other words for another page: the same meaning, the same people (names "
    "exactly as written) and facts, the same tone and language. About {words} words, never "
    "more than {most}. If the old one puts words in CAPITALS for the punch, do so too, one to "
    "three words. End with one or two emoji that fit. No hashtags, no quotation marks, no "
    "'this video'.{hint}"
)
SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _words(line: str) -> list[str]:
    return re.findall(r"[\w’']+", line.lower())


def alike(old: str, new: str) -> float:
    """How alike two captions' words are, 0 to 1."""
    return difflib.SequenceMatcher(None, _words(old), _words(new)).ratio()


def rewrite(llm, old: str, hint: str = "") -> str:
    """The caption in other words. Asked up to three times while its words stay too close
    to the old ones or run too long; then the least alike answer that still reads as new
    (under CLOSE), else ValueError."""
    count = len(_words(old))
    prompt = REWRITE.format(
        old=old, words=count, most=count + 4, hint=f"\n\n{hint.strip()}" if hint.strip() else ""
    )
    problem, best = "", None
    for attempt in range(3):
        asked = prompt if not problem else f"{prompt}\n\nYour last answer {problem}. Again."
        reply = llm.chat_json(
            [{"role": "user", "content": asked}], SCHEMA, temperature=0.6 + 0.2 * attempt
        )
        new = " ".join(str(reply.get("text", "")).replace('"', "").split())
        too_long = len(new) > max(LONGER * len(old), len(old) + 20)
        if not new:
            problem = "was empty"
            continue
        if too_long:
            problem = f"was too long ({len(new)} letters for {len(old)}): shorter"
            continue
        likeness = alike(old, new)
        if likeness < SAME:
            return new
        if best is None or likeness < best[0]:
            best = (likeness, new)
        problem = (
            "kept nearly the same words: say it with other verbs and another order, keeping "
            "only the names"
        )
    if best is not None and best[0] < CLOSE:
        return best[1]
    raise ValueError(f"the model couldn't write the caption again ({problem})")


def layout_words(
    words: str, face: text.Face, box: Box, old_lines: int, frame: tuple[int, int]
) -> tuple[list[str], int, int, int]:
    """The new words in lines, their size, and the new box's top and bottom: sized as the
    old words were (from their line count), smaller down to three quarters, then the box
    taller about its middle, up to GROW."""
    pad = round(frame[0] * PAD)
    room = box.width - 2 * pad
    # The old words' size from their line pitch: the box holds its lines and about half a
    # line of room above and below them.
    usual = box.height / (max(1, old_lines) + 0.5) / LINE
    size = max(12, round(usual))
    smallest = max(12, round(usual * 0.75))

    def fit(s: int) -> tuple[list[str], float]:
        lines = text.balance(words, face, s, room).split("\n")
        return lines, (len(lines) + 0.5) * s * LINE

    lines, tall = fit(size)
    while tall > box.height and size > smallest:
        size -= 1
        lines, tall = fit(size)
    middle = (box.y0 + box.y1) / 2
    height = min(max(box.height, tall), box.height * GROW)
    top = max(0, round(middle - height / 2))
    return lines, size, top, min(frame[1], round(middle + height / 2))


def draw(
    words: str, box: Box, old_lines: int, frame: tuple[int, int], face: text.Face, dest: Path
) -> Path:
    """The new caption box on a clear frame-sized picture."""
    lines, size, top, bottom = layout_words(words, face, box, old_lines, frame)
    image = Image.new("RGBA", frame, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    radius = max(4, round(min(box.height, bottom - top) * 0.12))
    canvas.rounded_rectangle((box.x0, top, box.x1, bottom), radius=radius, fill=box.fill)
    line_height = size * LINE
    y = (top + bottom) / 2 - len(lines) * line_height / 2
    ascent, descent = face.metrics(size)
    for i, line in enumerate(lines):
        x = (box.x0 + box.x1) / 2 - face.length(line, size) / 2
        # Each line in the middle of its own row.
        row = y + i * line_height + (line_height - ascent - descent) / 2
        face.draw(canvas, (x, row), line, size, box.ink)
    image.save(dest)
    return dest


def apply(
    video: Path, dest: Path, preset: Preset, settings, encoder: list[str], llm, work_dir: Path
) -> list[str]:
    """`video` with its caption box written again, saved at `dest`; what was done, in words
    (a line starting "no caption box" when there was nothing to write again, and `dest`
    then left as it was)."""
    from agentic_editor.render import compose

    info = ffmpeg.probe(video)
    source = (info.width, info.height)
    found = find_box(video, info.duration, source, settings)
    if found is None:
        return ["no caption box found in the video: left as it was"]
    box, lines, rows = found
    old = " ".join(lines).strip()
    new = rewrite(llm, old, preset.retext.hint)
    face = compose.text_face(settings, None)(preset.retext.font, new)
    overlay = draw(new, box, rows, source, face, work_dir / "retext.png")
    shown = f"between(t,{box.start:.2f},{box.end:.2f})"
    ffmpeg.run(["-y", "-i", str(video), "-i", str(overlay), "-filter_complex",
                f"[0:v][1:v]overlay=0:0:enable='{shown}',format=yuv420p[v]",
                "-map", "[v]", "-map", "0:a:0?", *encoder, "-c:a", "copy",
                "-movflags", "+faststart", str(dest)])  # fmt: skip
    (work_dir / "retext.json").write_text(
        json.dumps({"box": box.__dict__, "old": old, "new": new}, ensure_ascii=False, indent=2)
    )
    return [
        f'caption box written again: "{old}" -> "{new}" '
        f"(box {box.width}x{box.height} at {box.x0},{box.y0}, {box.start:.1f}-{box.end:.1f} s)"
    ]
