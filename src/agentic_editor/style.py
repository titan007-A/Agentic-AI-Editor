"""Drafting a preset from a reel that already has the look you want.

The layout, text and colour come from the reel's pixels: where the picture sits and the
colour around it, the taglines that stay put, the captions that change, a title that
only opens the reel. The vision model reads the words on a few frames and says roughly
where they are; the pixels then give each text's exact colour, position and size, and
every bundled font is tried to find the one that draws it most alike. Caption sizes are
measured against libass itself, which draws captions smaller than Pillow draws text.
The sound says whether it is speech or singing and whether the band was taken out, and
the planner model words the label and style notes. All of it is a draft to check: the
notes say what was measured, what was guessed and what was left out.
"""

import difflib
import itertools
import logging
import re
import statistics
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from scipy import ndimage

from agentic_editor import ffmpeg
from agentic_editor.analysis import layout
from agentic_editor.analysis.layout import Box, Line
from agentic_editor.analyze import SPARSE_WORDS, cached, file_hash, isolate_vocals
from agentic_editor.backends import Backends
from agentic_editor.edl import Cue
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.presets import (
    Audio,
    CaptionStyle,
    Framing,
    Grade,
    Hook,
    Preset,
    Silence,
    TextBlock,
    TitleOverlay,
)
from agentic_editor.render import captions as ass
from agentic_editor.render.audio import integrated_loudness
from agentic_editor.render.text import EDGE_MARGIN, font_family
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings
from agentic_editor.transcript import Transcript, Word

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]

LAYOUT_FRAMES = 24
# Frames are read padded to a square this size. On a tall frame the vision model's boxes
# drift down the further down the text is, by over a tenth of the height near the bottom;
# on a square they are a few percent off at most.
READ_SQUARE = 1280
OPENING = 4.0  # seconds: text seen only before this is a title that opens the reel
STEADY = 0.75  # share of the frames a tagline must be on, unchanged
SIMILAR = 0.8  # how alike two readings must be to count as the same text
ROW_SLACK = 0.06  # of the height: readings whose centres are this close are the same text
SAME_TEXT_SLACK = 0.15  # ...and this close, when they also read the same
WORD_REACH = 3.0  # seconds a frame to read may move to land on a word
MONO = 1.5  # saturation below which the picture is black and white
FULL = 0.95  # of the height: a picture this tall fills the frame
# Of the width: a picture narrower than this is inset on purpose. One a little narrower
# usually just has dark edges of its own.
INSET = 0.86
COVERAGE = 0.8  # Pillow's ink threshold matching what `layout.ink` counts as ink
LOUD = -12.0  # LUFS: a reel louder than this sets the preset's loudness target
MAX_LOUDNESS = -9.0
PART = 0.6  # of the box's width: text narrower than this is likely only part of it
DARK = 70  # brightest channel of a background a dark border would vanish into
THICKEST = 0.3  # of the line's height: a border wider than this is the background

FORMATS = {(9, 16): (1080, 1920), (4, 5): (1080, 1350), (1, 1): (1080, 1080),
           (16, 9): (1920, 1080)}  # fmt: skip

READ_PROMPT = (
    "This is one frame of a short video made for phones. List every piece of text drawn "
    "on it: captions, titles, taglines, labels, usernames, logos with words. Copy each text "
    "exactly as written, keeping its capital letters, accents and line breaks (as \\n). "
    "Give each block of text one entry with its box [x0, y0, x1, y1] in 0-1000 coordinates "
    "relative to the image width and height, the colour of most of its letters (one word, "
    "e.g. white, yellow, black) in color, and in other_color the colour of any word drawn "
    "in a different colour from the rest (empty if none). In scene, say in a few words "
    "what the frame shows, e.g. 'a woman singing into a microphone on stage'."
)

DESCRIBE_PROMPT = """You are helping set up a video editor that turns long videos into \
reels. Below is what was measured from one finished reel whose style should be copied.

Reel length: {duration:.0f} s
What the frames show: {scenes}
What is said or sung (transcribed): {words}
Text that stays on screen the whole reel:
{fixed}
Captions seen (samples): {captions}

Reply with:
- label: a short name for this style in 2-5 words: what the reels show, and the language \
of their text if it isn't English.
- content: "singing" if the soundtrack is someone singing, "speech" if people talk, \
"other" for neither.
- caption_language: the language the captions are written in (English name), or "" if \
there are none.
- style_notes: 2-4 sentences telling an editor which part of a long video to use for a \
reel like this and where to start and end it, e.g. "Pick the passage where the singer's \
voice is most impressive, usually the chorus. Start where a sung line begins and end \
after a note has died away." Write about what to pick and where to cut, not about this \
reel's length, colours, text or layout."""


# Asked as slogan or details, with a reason first, the planner model sorts these right; asked
# whether a text is "about this video", it says yes to everything.
PER_VIDEO_PROMPT = """Text shown on a short video from a page that posts many videos:

"{text}"

Classify it. "slogan": the page's own words that stay the same on every video it posts, \
such as what the page is about or a request to follow ("Follow for more", "Raw vocals \
only"). "details": words about this one video that would be different on the next one, \
such as its song title, artist, person, place, date, or a counter like "Day 12". First \
give a short reason, then the kind."""


class _Kind(BaseModel):
    reason: str
    kind: Literal["slogan", "details"]


class _Item(BaseModel):
    text: str
    box: list[int] = Field(min_length=4, max_length=4)
    color: str
    other_color: str


class _Read(BaseModel):
    scene: str
    items: list[_Item]


class _Described(BaseModel):
    label: str
    content: Literal["singing", "speech", "other"]
    caption_language: str
    style_notes: str


@dataclass(frozen=True)
class Sighting:
    """One piece of text the vision model read on one frame."""

    at: float
    text: str
    box: Box  # shares of the frame
    colour: str
    other: str = ""


@dataclass
class Measured:
    """A sighting's text as drawn: its lines and their ink boxes in frame pixels."""

    lines: list[str]
    boxes: list[Line]
    colour: tuple[int, int, int]
    fill: float  # share of the lines' boxes that is ink: thick letters fill more
    other: tuple[int, int, int] | None = None
    outline: tuple[tuple[int, int, int], int] | None = None
    backdrop: tuple[int, int, int] | None = None

    @property
    def top(self) -> int:
        return min(b[0] for b in self.boxes)

    @property
    def bottom(self) -> int:
        return max(b[1] for b in self.boxes)


@dataclass(frozen=True)
class FontFit:
    font: Path
    size: float  # Pillow size
    spacing: float
    error: float


@dataclass
class Draft:
    preset: Preset
    notes: list[str] = field(default_factory=list)


def slug(label: str) -> str:
    """A preset file name from a label: "Raw vocals, Spanish page" -> raw-vocals-spanish-page."""
    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:48].strip("-") or "learned-style"


def hex_colour(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def target_size(width: int, height: int) -> tuple[str, tuple[int, int]]:
    """The standard format nearest the reel's shape, as aspect ratio and size."""
    (num, den), size = min(FORMATS.items(), key=lambda f: abs(f[0][0] / f[0][1] - width / height))
    if abs(num / den - width / height) < 0.02:
        return f"{num}:{den}", size
    scaled = (1080, round(1080 * height / width / 2) * 2)
    return f"{scaled[0]}:{scaled[1]}", scaled


def read_times(duration: float, words: list[Word] = ()) -> list[float]:
    """Frames to read: two in the opening seconds, where a title would be, and four
    spread over the rest, each moved onto a word being said or sung nearby, since
    captions are only on screen while there are words."""
    if duration <= 2 * OPENING:
        start, picks = 0.0, [0.15, 0.4, 0.65, 0.9]
    else:
        start, picks = OPENING, [0.1, 0.37, 0.63, 0.9]
    middles = [(w.start + w.end) / 2 for w in words if w.start >= start]
    body = []
    for share in picks:
        at = start + (duration - start) * share
        near = min(middles, key=lambda m: abs(m - at), default=None)
        body.append(near if near is not None and abs(near - at) <= WORD_REACH else at)
    opening = [0.5, 2.5] if start else []
    return [*opening, *(round(t, 2) for t in dict.fromkeys(body))]


def clean(text: str) -> str:
    """The text as the bundled fonts can draw it: no emoji or other pictographs."""
    kept = "".join(c for c in text if unicodedata.category(c) not in ("So", "Sk", "Cs", "Co"))
    return "\n".join(" ".join(line.split()) for line in kept.split("\n") if line.strip())


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, _normal(a), _normal(b)).ratio() >= SIMILAR


def join_lines(items: list[Sighting]) -> list[Sighting]:
    """The vision model sometimes gives each line of a block its own entry. Lines in the
    same colour that sit right under each other become one block again."""
    merged: list[Sighting] = []
    for item in sorted(items, key=lambda s: s.box[1]):
        last = merged[-1] if merged else None
        if last is not None:
            height = min(last.box[3] - last.box[1], item.box[3] - item.box[1])
            touching = item.box[1] - last.box[3] <= 0.6 * height
            overlapping = min(last.box[2], item.box[2]) > max(last.box[0], item.box[0])
            if touching and overlapping and item.colour == last.colour:
                box = (min(last.box[0], item.box[0]), last.box[1],
                       max(last.box[2], item.box[2]), item.box[3])  # fmt: skip
                merged[-1] = Sighting(last.at, f"{last.text}\n{item.text}", box, last.colour,
                                      last.other or item.other)  # fmt: skip
                continue
        merged.append(item)
    return merged


def _same_rows(a: Box, b: Box) -> bool:
    """Whether two readings are on the same rows, allowing for the vision model's boxes
    being off by a few percent."""
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    near = abs((a[1] + a[3]) - (b[1] + b[3])) / 2 <= ROW_SLACK
    return near or overlap >= 0.5 * min(a[3] - a[1], b[3] - b[1])


def _together(a: Sighting, b: Sighting) -> bool:
    centres = abs((a.box[1] + a.box[3]) - (b.box[1] + b.box[3])) / 2
    return _same_rows(a.box, b.box) or (centres <= SAME_TEXT_SLACK and similar(a.text, b.text))


def group(sightings: list[Sighting]) -> list[list[Sighting]]:
    """Sightings of the same text across frames: on the same rows, or the same words
    a little further off."""
    groups: list[list[Sighting]] = []
    for s in sorted(sightings, key=lambda s: (s.box[1], s.at)):
        home = next((g for g in groups if any(_together(s, o) for o in g)), None)
        if home is None:
            groups.append([s])
        else:
            home.append(s)
    return groups


def _inside(box: Box, picture: Box) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return picture[0] < cx < picture[2] and picture[1] < cy < picture[3]


def is_handle(texts: list[str], box: Box) -> bool:
    """A username or site stamped in a corner: a watermark, which gets blurred anyway."""
    text = texts[0].strip().lower()
    if text.startswith("@") or re.search(r"\.(com|net|org|tv)\b|www\.", text):
        return True
    small = box[3] - box[1] < 0.02
    edge = box[0] > 0.55 or box[2] < 0.45 or box[1] < 0.06 or box[3] > 0.94
    return small and edge


def roles(
    groups: list[list[Sighting]], times: list[float], picture: Box, padded: bool
) -> list[tuple[str, list[Sighting]]]:
    """What each group of text is: fixed (a tagline on every video), captions (changing
    with what is said), title (only in the opening seconds), source (text that belongs to
    the video shown, inside a letterboxed picture), handle (a watermark) or other. Text
    that reads differently in the same place twice is captions even if seen only twice:
    lyrics come and go, so most frames can fall between them."""
    body = [t for t in times if t >= OPENING] or times
    found = []
    for g in groups:
        when = {s.at for s in g}
        seen = len(when & set(body))
        texts = [s.text for s in g]
        steady = all(similar(texts[0], t) for t in texts[1:])
        box = (min(s.box[0] for s in g), min(s.box[1] for s in g),
               max(s.box[2] for s in g), max(s.box[3] for s in g))  # fmt: skip
        if not steady:
            role = "captions"
        elif is_handle(texts, box):
            role = "handle"
        elif not when & set(body):
            role = "title"
        elif seen >= max(2, round(STEADY * len(body))):
            role = "source" if padded and _inside(box, picture) else "fixed"
        else:
            role = "other"
        found.append((role, g))
    return found


def outline_of(
    crop: np.ndarray, mask: np.ndarray, line_height: int
) -> tuple[tuple[int, int, int], int] | None:
    """A border drawn around the letters, as its colour and width in pixels; width 0 when
    the letters plainly have none. A border is a thin band unlike what lies beyond it.
    With no band, a light background means no border, but on a dark one a dark border
    would look the same, so that can't be told (None); nor can a "band" too thick for a
    border, which is the background behind the text."""
    if mask.sum() < 20:
        return None
    near = ndimage.binary_dilation(mask, iterations=2) & ~ndimage.binary_dilation(mask)
    if not near.any():
        return None
    colour = np.median(crop[near].astype(np.int16), axis=0)
    # The first pixel out blends the letters into the border, so the border is counted
    # from the next one.
    width, grown = 1, ndimage.binary_dilation(mask)
    for step in range(2, 16):
        wider = ndimage.binary_dilation(grown)
        ring = wider & ~grown
        if not ring.any() or np.all(np.abs(crop[ring] - colour) <= 40, axis=1).mean() < 0.6:
            break
        width, grown = step, wider
    beyond = ndimage.binary_dilation(mask, iterations=width + 6) & ~ndimage.binary_dilation(
        mask, iterations=width + 3
    )
    if not beyond.any() or np.all(np.abs(crop[beyond] - colour) <= 40, axis=1).mean() >= 0.6:
        return None if colour.max() < DARK else ((0, 0, 0), 0)
    if width > THICKEST * line_height:
        return None
    if width < 2 or np.abs(colour - crop[mask].astype(np.int16).mean(axis=0)).max() < 96:
        return (0, 0, 0), 0  # a shadow's edge, or too like the letters to be a border
    return tuple(int(c) for c in colour), width


def backdrop_of(crop: np.ndarray, mask: np.ndarray) -> tuple[int, int, int] | None:
    """A flat box behind the text, when there is one."""
    around = ~ndimage.binary_dilation(mask, iterations=6)
    pixels = crop[around].astype(np.int16)
    if len(pixels) < 50:
        return None
    colour = np.median(pixels, axis=0)
    flat = np.all(np.abs(pixels - colour) <= layout.TOLERANCE, axis=1).mean()
    return tuple(int(c) for c in colour) if flat >= 0.85 else None


def best_run(found: list[Line], top: float, bottom: float, count: int) -> list[Line]:
    """The consecutive lines that best cover the rows the vision model gave, `count` of
    them when that many were read."""
    sizes = [count] if 0 < count <= len(found) else range(1, len(found) + 1)

    def overlap(run: list[Line]) -> float:
        a, b = run[0][0], run[-1][1]
        inter = max(0.0, min(b, bottom) - max(a, top))
        return inter / (max(b, bottom) - min(a, top))

    def together(run: list[Line]) -> bool:
        # Lines of one block sit closer than a line's height apart.
        tall = max(b - a for a, b, _, _ in run)
        return all(nxt[0] - cur[1] <= tall for cur, nxt in itertools.pairwise(run))

    runs = [found[i : i + k] for k in sizes for i in range(len(found) - k + 1)]
    runs = [r for r in runs if together(r)]
    return max(runs, key=overlap) if runs else []


def measure(frame: np.ndarray, sighting: Sighting, picture: Box | None = None) -> Measured | None:
    """The sighting's text as drawn on `frame` (RGB at the draft's size). The vision
    model's box can be off by a few percent of the height, so the text's lines are looked
    for in a wider window and the run of them nearest the box is kept. Text on the padding
    is looked for outside `picture` only, so the picture's own bright spots can't pass for
    a line of it."""
    height, width, _ = frame.shape
    x0, y0, x1, y1 = sighting.box
    left, right = max(0, round((x0 - 0.05) * width)), min(width, round((x1 + 0.05) * width))
    top, bottom = max(0, round((y0 - 0.08) * height)), min(height, round((y1 + 0.08) * height))
    window = frame[top:bottom, left:right]
    text = clean(sighting.text)
    if window.size == 0 or not text:
        return None
    colour = layout.text_colour(window, sighting.colour)
    other = None
    if sighting.other and sighting.other.lower() != sighting.colour.lower():
        candidate = layout.text_colour(window, sighting.other)
        if candidate != colour:
            other = candidate
    count = len(text.split("\n"))

    def find(colours: list[tuple[int, int, int]]) -> tuple[np.ndarray, list[Line]]:
        drawn = np.any([layout.ink(window, c) for c in colours], axis=0)
        # The box's height bounds a letter's, with room for a box drawn too tight.
        mask = layout.letters(drawn, max(1.5 * (y1 - y0), 0.06) * height)
        if picture is not None:
            px0, py0, px1, py1 = picture
            mask[
                max(0, round(py0 * height) - top) : max(0, round(py1 * height) - top),
                max(0, round(px0 * width) - left) : max(0, round(px1 * width) - left),
            ] = False
        lines = best_run(layout.lines(mask), y0 * height - top, y1 * height - top, count)
        return mask, lines

    mask, found = find([colour, other] if other else [colour])
    if not found:
        return None
    if other is None and max(f[3] - f[2] for f in found) < PART * (x1 - x0) * width:
        # Only part of the text is in the colour the model named: the rest of its words
        # are in another, which the model left out (a highlighted word named instead).
        rows = slice(found[0][0], found[-1][1])
        cols = slice(round(x0 * width) - left, round(x1 * width) - left)
        name = layout.second_colour(window[rows, cols], window, colour)
        if name:
            other = layout.text_colour(window[rows, cols], name)
            mask, found = find([colour, other])
    boxes = [(t + top, b + top, a + left, z + left) for t, b, a, z in found]
    fill = float(np.mean([mask[t:b, a:z].mean() for t, b, a, z in found]))
    # Just the lines kept, with room around them for an outline or a box behind.
    pad = round(0.3 * (found[0][1] - found[0][0]))
    rows = slice(max(0, found[0][0] - pad), found[-1][1] + pad)
    cols = slice(max(0, min(f[2] for f in found) - pad), max(f[3] for f in found) + pad)
    crop, letters = window[rows, cols], mask[rows, cols]
    if other is not None:
        main, second = layout.ink(crop, colour) & letters, layout.ink(crop, other) & letters
        if second.sum() < 0.005 * letters.size:
            other = None  # named, but not really there
        elif second.sum() > main.sum():
            colour, other = other, colour  # the model named the highlighted word's colour
    border = outline_of(crop, letters, found[0][1] - found[0][0])
    return Measured(
        text.split("\n"), boxes, colour, fill, other, border, backdrop_of(crop, letters)
    )


def _ink_box(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int, int, int]:
    return font.getbbox(text)


def _rendered_fill(font: ImageFont.FreeTypeFont, text: str) -> float:
    x0, y0, x1, y1 = _ink_box(font, text)
    image = Image.new("L", (x1 - x0 + 4, y1 - y0 + 4), 0)
    ImageDraw.Draw(image).text((2 - x0, 2 - y0), text, font=font, fill=255)
    alpha = np.asarray(image, dtype=np.float32) / 255
    return float((alpha >= COVERAGE).mean())


def split_like(lines: list[str], widths: list[int], font: Path) -> list[str] | None:
    """The words re-broken into as many lines as were measured, matching their widths,
    for when the reading and the pixels disagree on where lines break."""
    if len(lines) == len(widths):
        return lines
    words = " ".join(lines).split()
    count = len(widths)
    if len(words) < count:
        return None
    sized = ImageFont.truetype(str(font), 100)
    total = sum(widths)

    def cost(i: int, j: int, k: int) -> float:
        share = sized.getlength(" ".join(words[i:j])) / sized.getlength(" ".join(words))
        return (share - widths[k] / total) ** 2

    best: dict[tuple[int, int], tuple[float, list[int]]] = {(0, 0): (0.0, [])}
    for k in range(count):
        for (start, done), (score, cuts) in list(best.items()):
            if done != k:
                continue
            for end in range(start + 1, len(words) - (count - k - 1) + 1):
                if k == count - 1 and end != len(words):
                    continue
                total_cost = score + cost(start, end, k)
                key = (end, k + 1)
                if key not in best or total_cost < best[key][0]:
                    best[key] = (total_cost, [*cuts, end])
    if (len(words), count) not in best:
        return None
    cuts = [0, *best[(len(words), count)][1]]
    return [" ".join(words[a:b]) for a, b in itertools.pairwise(cuts)]


def fit_font(measured: Measured, fonts: list[Path]) -> FontFit | None:
    """The bundled font, and its size, that draws the measured lines most alike: sized by
    the lines' widths, judged by their heights and by how thick the strokes are."""
    best = None
    for path in fonts:
        lines = split_like(measured.lines, [b[3] - b[2] for b in measured.boxes], path)
        if lines is None:
            continue
        probe = ImageFont.truetype(str(path), 100)
        sizes = []
        for text, (_, _, left, right) in zip(lines, measured.boxes, strict=True):
            x0, _, x1, _ = _ink_box(probe, text)
            if x1 > x0:
                sizes.append(100 * (right - left) / (x1 - x0))
        if not sizes:
            continue
        size = statistics.median(sizes)
        font = ImageFont.truetype(str(path), max(1, round(size)))
        heights = []
        for text, (top, bottom, _, _) in zip(lines, measured.boxes, strict=True):
            _, y0, _, y1 = _ink_box(font, text)
            heights.append(abs((y1 - y0) - (bottom - top)) / max(1, bottom - top))
        drawn = statistics.mean(_rendered_fill(font, t) for t in lines)
        error = statistics.mean(heights) + abs(drawn - measured.fill) / max(measured.fill, 0.05)
        spacing = 1.0
        if len(lines) > 1:
            ascent, descent = font.getmetrics()
            steps = [
                (measured.boxes[i][0] - _ink_box(font, lines[i])[1])
                - (measured.boxes[i - 1][0] - _ink_box(font, lines[i - 1])[1])
                for i in range(1, len(lines))
            ]
            spacing = min(2.0, max(0.8, statistics.median(steps) / (ascent + descent)))
        fit = FontFit(path, size, round(spacing, 2), error)
        if best is None or fit.error < best.error:
            best = fit
    return best


def _vote(fits: list[FontFit]) -> Path:
    """The font most readings agree on, a close fit counting for more."""
    votes: dict[Path, float] = {}
    for f in fits:
        votes[f.font] = votes.get(f.font, 0) + 1 / (0.05 + f.error)
    return max(votes, key=votes.get)


def agreed_colour(colours: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """The colour most readings agree on. Never a blend: the middle of white and yellow
    readings is a colour none of them showed."""
    close = [[c for c in colours if max(abs(a - b) for a, b in zip(c, o, strict=True)) <= 40]
             for o in colours]  # fmt: skip
    best = max(close, key=len)
    return tuple(int(statistics.median(c[i] for c in best)) for i in range(3))


def _typical(values: list[float]) -> float:
    """The value most readings agree on: the median of the largest bunch of close values.
    A caption moved off a face, or two lines read as one, stays out of it."""
    ordered = sorted(values)
    bunches = [[ordered[0]]]
    for v in ordered[1:]:
        if v - bunches[-1][-1] <= max(8.0, 0.08 * abs(v)):
            bunches[-1].append(v)
        else:
            bunches.append([v])
    middle = statistics.median(ordered)
    best = max(bunches, key=lambda b: (len(b), b[0] <= middle <= b[-1]))
    return statistics.median(best)


def consensus(samples: list[Measured], fonts: list[Path]) -> tuple[Measured, FontFit] | None:
    """One reading of a text that stays, and its font: the font the readings agree on,
    and the reading whose size is the typical one. A single reading with a bad box can't
    decide it."""
    fits = [(m, fit_font(m, fonts)) for m in samples]
    fits = [(m, f) for m, f in fits if f is not None]
    if not fits:
        return None
    font = _vote([f for _, f in fits])
    refit = [(m, fit_font(m, [font])) for m, _ in fits]
    refit = [(m, f) for m, f in refit if f is not None]
    size = _typical([f.size for _, f in refit])
    return min(refit, key=lambda mf: abs(mf[1].size - size))


def bundled_fonts(fonts_dir: Path) -> list[Path]:
    return sorted(p for p in fonts_dir.glob("*.[ot]tf"))


def ass_ink(
    lines: list[str], font: Path, position: str, frame: tuple[int, int], settings: Settings
) -> tuple[int, int]:
    """Top and bottom of the ink of these lines as libass draws them at size 100 with no
    margin: captions are sized in libass's units, not Pillow's."""
    width, height = frame
    style = CaptionStyle(font=font.stem, size=100, outline=0, position=position, margin=0,
                         uppercase=False)  # fmt: skip
    script = ass.build_ass(
        [Cue(start=0, end=2, text="\n".join(lines))], style, frame, font_family(font)
    )
    work = settings.cache_dir / "style-calibration"
    work.mkdir(parents=True, exist_ok=True)
    path = work / "probe.ass"
    path.write_text(script, encoding="utf-8")
    raw = subprocess.run(
        [settings.ffmpeg_bin, "-v", "error", "-f", "lavfi",
         "-i", f"color=black:s={width}x{height}:d=2", "-ss", "1",
         "-vf", f"subtitles=filename='{ffmpeg.escape_filter_path(path)}'"
                f":fontsdir='{ffmpeg.escape_filter_path(settings.fonts_dir)}',format=gray",
         "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    rows = np.nonzero((np.frombuffer(raw, np.uint8).reshape(height, width) > 128).any(axis=1))[0]
    if not len(rows):
        raise ValueError(f"libass drew nothing with {font.name}")
    return int(rows[0]), int(rows[-1]) + 1


def text_block(m: Measured, fit: FontFit, frame: tuple[int, int]) -> TextBlock:
    width, height = frame
    top_half = (m.top + m.bottom) / 2 < height / 2
    widest = max(b[3] - b[2] for b in m.boxes)
    lines = split_like(m.lines, [b[3] - b[2] for b in m.boxes], fit.font) or m.lines
    size = round(fit.size)
    return TextBlock(
        text="\n".join(lines),
        font=fit.font.stem,
        size=size,
        min_size=min(28, size),
        color=hex_colour(m.colour),
        anchor="top" if top_half else "bottom",
        offset=m.top if top_half else height - m.top,
        max_width=max(1000, min(width - 2 * EDGE_MARGIN, widest + 10)),
        line_spacing=fit.spacing,
        max_shrink=0.9,
    )


def caption_style(
    samples: list[Measured], fonts: list[Path], frame: tuple[int, int], settings: Settings
) -> tuple[dict, list[str]]:
    """Caption settings from a few measured captions."""
    height = frame[1]
    fits = [(m, fit_font(m, fonts)) for m in samples]
    fits = [(m, f) for m, f in fits if f is not None]
    if not fits:
        return {}, ["captions were seen but couldn't be measured"]
    font = _vote([f for _, f in fits])
    centre = statistics.median((m.top + m.bottom) / 2 for m, _ in fits) / height
    position = "top" if centre < 0.38 else "bottom" if centre > 0.62 else "middle"
    sizes, margins = [], []
    for m, _ in fits:
        lines = split_like(m.lines, [b[3] - b[2] for b in m.boxes], font) or m.lines
        top, bottom = ass_ink(lines, font, position, frame, settings)
        size = 100 * (m.bottom - m.top) / max(1, bottom - top)
        log.info("caption %r: ink rows %d-%d, libass size %.0f", lines, m.top, m.bottom, size)
        sizes.append(size)
        if position == "bottom":
            margins.append((height - m.bottom) - (height - bottom) * size / 100)
        elif position == "top":
            margins.append(m.top - top * size / 100)
    words = [len(line.split()) for m, _ in fits for line in m.lines]
    letters = [c for m, _ in fits for line in m.lines for c in line if c.isalpha()]
    upper = sum(c.isupper() for c in letters) / max(1, len(letters))
    colour = agreed_colour([m.colour for m, _ in fits])
    others = [m.other for m, _ in fits if m.other]
    outlines = [m.outline for m, _ in fits if m.outline is not None]
    settings_found = {
        "enabled": True,
        "font": font.stem,
        "size": max(12, min(300, round(_typical(sizes)))),
        "color": hex_colour(colour),
        "position": position,
        "margin": max(0, round(_typical(margins))) if margins else 0,
        "words_per_line": max(1, min(12, round(statistics.median(words)))),
        "uppercase": upper >= 0.9,
        "animation": "highlight" if others else "none",
    }
    if others:
        settings_found["highlight_color"] = hex_colour(agreed_colour(others))
    # Captions whose border couldn't be told keep the default one.
    if outlines:
        width = _typical([w for _, w in outlines])
        settings_found["outline"] = min(20, width) if width >= 1 else 0
        if width >= 1:
            colours = [c for c, w in outlines if w >= 1]
            settings_found["outline_color"] = hex_colour(agreed_colour(colours))
    return settings_found, []


def describe(
    llm: OllamaClient,
    duration: float,
    scenes: list[str],
    transcript: Transcript,
    fixed: list[str],
    caption_texts: list[str],
) -> _Described | None:
    words = " ".join(w.text for w in transcript.words)
    prompt = DESCRIBE_PROMPT.format(
        duration=duration,
        scenes="; ".join(dict.fromkeys(scenes)) or "(not read)",
        words=(words[:1200] + "...") if len(words) > 1200 else words or "(nothing)",
        fixed="\n".join(f"{i}. {t!r}" for i, t in enumerate(fixed)) or "(none)",
        captions=" | ".join(caption_texts[:6]) or "(none)",
    )
    try:
        reply = llm.chat_json(
            [{"role": "user", "content": prompt}], _Described.model_json_schema(), temperature=0.2
        )
        return _Described.model_validate(reply)
    except (LLMError, ValueError) as exc:
        log.warning("could not describe the style: %s", exc)
        return None


def per_video(llm: OllamaClient, text: str) -> bool:
    """Whether a text that stays on screen would differ from video to video. Asked one
    text at a time: in a longer question the planner model mixes the texts up."""
    try:
        reply = llm.chat_json(
            [{"role": "user", "content": PER_VIDEO_PROMPT.format(text=text)}],
            _Kind.model_json_schema(),
            temperature=0.0,
        )
        return _Kind.model_validate(reply).kind == "details"
    except (LLMError, ValueError) as exc:
        log.warning("could not tell whether %r changes per video: %s", text, exc)
        return False


def quoted(text: str) -> str:
    return "“" + text.replace("\n", " / ") + "”"


def caption_mode(
    caption_texts: list[str], transcript: Transcript, singing: bool, language: str
) -> tuple[str, str | None]:
    """speech or lyrics when the captions are the words heard, translated when they are
    in another language, visual when they are neither."""
    heard = {_normal(w.text) for w in transcript.words}
    shown = [w for t in caption_texts for w in _normal(t).split()]
    if shown and sum(w in heard for w in shown) / len(shown) >= 0.5:
        return ("lyrics" if singing else "speech"), None
    if transcript.words and language:
        return "translated", language
    return "visual", None


def in_square(size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Where a frame of `size` sits once fitted into the reading square, as shares of the
    square: left, top, width, height."""
    width, height = size
    if height >= width:
        share = width / height
        return (1 - share) / 2, 0.0, share, 1.0
    share = height / width
    return 0.0, (1 - share) / 2, 1.0, share


def from_square(box: list[int], size: tuple[int, int]) -> Box:
    """A 0-1000 box on the reading square, as shares of the frame itself."""
    left, top, w, h = in_square(size)
    x0, y0, x1, y1 = (min(1000, max(0, v)) / 1000 for v in box)
    return (
        _share((x0 - left) / w), _share((y0 - top) / h),
        _share((x1 - left) / w), _share((y1 - top) / h),
    )  # fmt: skip


def _share(value: float) -> float:
    return min(1.0, max(0.0, value))


def read_frames(
    vision: OllamaClient,
    video: Path,
    size: tuple[int, int],
    times: list[float],
    work: Path,
    ffmpeg_bin: str,
) -> tuple[list[Sighting], list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    sightings, scenes = [], []
    side = READ_SQUARE
    fit = f"scale={side}:{side}:force_original_aspect_ratio=decrease"
    pad = f"pad={side}:{side}:(ow-iw)/2:(oh-ih)/2:black"
    for i, at in enumerate(times):
        image = work / f"read-{i}.jpg"
        subprocess.run(
            [ffmpeg_bin, "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"{fit},{pad}", "-q:v", "2", str(image)],
            check=True,
        )  # fmt: skip
        try:
            reply = _Read.model_validate(
                vision.chat_json(
                    [{"role": "user", "content": READ_PROMPT}],
                    _Read.model_json_schema(),
                    images=[image],
                    temperature=0.0,
                )
            )
        except (LLMError, ValueError) as exc:
            log.warning("could not read the frame at %.1fs: %s", at, exc)
            continue
        scenes.append(reply.scene)
        log.info("%.1fs: %s; %d text(s)", at, reply.scene, len(reply.items))
        items = []
        for item in reply.items:
            x0, y0, x1, y1 = from_square(item.box, size)
            if x1 > x0 and y1 > y0 and item.text.strip():
                log.info("%.1fs: %r at %s, %s", at, item.text, item.box, item.color)
                items.append(Sighting(at, item.text, (x0, y0, x1, y1), item.color.strip(),
                                      item.other_color.strip()))  # fmt: skip
        sightings += join_lines(items)
    return sightings, scenes


def learn(
    source_ref: str,
    settings: Settings,
    backends: Backends,
    name: str | None = None,
    progress: Progress = lambda stage, message: log.info("%s: %s", stage, message),
) -> Draft:
    from agentic_editor.pipeline import resolve_source

    progress("download", f"fetching {source_ref}")
    source = resolve_source(source_ref, settings)
    info = ffmpeg.probe(source.path)
    work_dir = settings.cache_dir / file_hash(source.path)
    work_dir.mkdir(parents=True, exist_ok=True)
    router = ModelRouter(settings, backends)
    aspect, frame = target_size(info.width, info.height)
    width, height = frame
    notes: list[str] = []

    progress("analyze", "measuring the layout")
    small = (216, max(2, round(216 * height / width / 2) * 2))
    frames = layout.sample(source.path, info.duration, LAYOUT_FRAMES, small, settings.ffmpeg_bin)
    colour = layout.background(frames)
    picture = layout.picture_box(frames, colour)
    blurred = layout.blurred_fill(frames) if colour is None else None
    picture = blurred or picture
    padded = blurred is not None or (colour is not None and picture[3] - picture[1] < FULL)

    transcript, original = Transcript(), work_dir / "audio.wav"
    if info.has_audio:
        if not original.exists():
            ffmpeg.extract_audio(source.path, original)
        progress("transcribe", "listening")
        transcript = Transcript.model_validate(
            cached(
                work_dir / "transcript-original.json",
                lambda: router.transcribe(original).model_dump(),
            )
        )

    times = read_times(info.duration, transcript.words)
    progress("read", f"reading the text on {len(times)} frames")
    with router.vision() as vision:
        sightings, scenes = read_frames(
            vision, source.path, (info.width, info.height), times, work_dir / "style",
            settings.ffmpeg_bin,
        )  # fmt: skip
    if not scenes:
        notes.append("The vision model didn't answer, so no text was read: add it by hand")
    found = roles(group(sightings), times, picture, padded)
    for role, g in found:
        log.info("%s: %s", role, " | ".join(f"{s.at:.1f}s {s.text!r}" for s in g))

    fonts = bundled_fonts(settings.fonts_dir)
    full_frames = {
        at: layout.frame_at(source.path, at, frame, settings.ffmpeg_bin)
        for at in {s.at for _, g in found for s in g}
    }

    def measured(g: list[Sighting]) -> list[Measured]:
        found = []
        for s in g:
            if full_frames[s.at] is None:
                continue
            outside = padded and not _inside(s.box, picture)
            m = measure(full_frames[s.at], s, picture if outside else None)
            if m is not None:
                found.append(m)
        return found

    blocks: list[tuple[TextBlock, Measured, str]] = []
    for role, g in found:
        if role != "fixed":
            continue
        agreed = consensus(measured(g), fonts)
        if agreed is None:
            notes.append(f"Couldn't measure the text {quoted(g[0].text)}; add it by hand")
            continue
        best, fit = agreed
        blocks.append((text_block(best, fit, frame), best, g[0].text))

    # Captions moved off a face can show up as a second group; the main one sets the style.
    caption_groups = sorted((g for role, g in found if role == "captions"), key=len, reverse=True)
    caption_samples = measured(caption_groups[0]) if caption_groups else []
    caption_texts = [s.text for g in caption_groups for s in g]
    caption_settings, caption_notes = (
        caption_style(caption_samples[:4], fonts, frame, settings) if caption_samples else ({}, [])
    )
    notes += caption_notes

    with router.planner() as llm:
        progress("plan", "describing the style")
        described = describe(
            llm, info.duration, scenes, transcript, [b.text for b, _, _ in blocks], caption_texts
        )
        changing = {i for i, (b, _, _) in enumerate(blocks) if per_video(llm, b.text)}
    singing = described is not None and described.content == "singing"

    instrument_free = False
    if singing and info.has_audio:
        vocals, instrument_free = isolate_vocals(original, work_dir, router, progress)
        if len(transcript.words) < SPARSE_WORDS * info.duration / 60 and not instrument_free:
            sung = Transcript.model_validate(
                cached(
                    work_dir / "transcript-vocals.json",
                    lambda: router.transcribe(vocals).model_dump(),
                )
            )
            transcript = sung if len(sung.words) > len(transcript.words) else transcript

    label = (described.label.strip() if described else "") or f"Learned from {source.title}"
    preset = Preset(
        name=name or slug(label),
        label=label,
        aspect_ratio=aspect,
        resolution=f"{width}x{height}",
        fps=min((24, 25, 30, 50, 60), key=lambda f: abs(f - info.fps)),
        min_duration=max(5, round(info.duration * 0.75)),
        max_duration=max(10, round(info.duration * 1.5)),
        style_notes=described.style_notes.strip() if described else "",
    )
    updates: dict = {}

    framing, note = framing_for(colour if padded else None, picture, blurred, blocks, height)
    updates["framing"] = framing
    notes.append(note)

    # Text that stays.
    text_blocks = []
    for i, (block, _, read) in enumerate(blocks):
        text_blocks.append(block)
        where = f"{block.offset} px from the {block.anchor}"
        notes.append(
            f"Text on every video: {quoted(block.text)} ({block.font} {block.size}, {block.color}, "
            f"{where})"
        )
        if i in changing:
            notes.append(f"{quoted(block.text)} looks like it changes from video to video: edit it")
        kept = clean(read)
        dropped = "".join(dict.fromkeys(c for c in read if c not in kept and not c.isspace()))
        if dropped:
            notes.append(f"Left out {dropped}: the bundled fonts can't draw it")
    updates["text_blocks"] = text_blocks

    for role, g in found:
        if role == "source":
            notes.append(f"Left out {quoted(g[0].text)}: it belongs to the video shown")
        elif role == "handle":
            notes.append(f"Left out {quoted(g[0].text)}: a username or watermark (blurred anyway)")
        elif role == "title" and "title_overlay" not in updates:
            agreed = consensus(measured(g), fonts)
            if agreed:
                updates["title_overlay"] = title_overlay(*agreed, colour)
                updates["hook"] = Hook(overlay=True)
                notes.append(
                    f"Opening title: {quoted(g[0].text)} for the first seconds; each reel gets "
                    "its own"
                )

    # Captions.
    if caption_settings:
        mode, language = caption_mode(
            caption_texts, transcript, singing, described.caption_language if described else ""
        )
        caption_settings.update(mode=mode, language=language)
        captions = CaptionStyle(**caption_settings)
        updates["captions"] = captions
        notes.append(caption_note(captions))
    else:
        updates["captions"] = CaptionStyle(enabled=False)
        notes.append("Captions: none")

    # Look.
    if layout.saturation(frames) < MONO:
        updates["grade"] = Grade(look="mono")
        notes.append("Look: black and white")

    # Sound.
    if singing and instrument_free:
        updates["audio"] = Audio(source="vocals", highpass_hz=60, fade_in=0.02, fade_out=0.4)
        updates["silence"] = Silence(threshold_db=-40, min_duration=0.35)
        notes.append("Sound: singing with no instruments, so the vocals get isolated")
    elif info.has_audio:
        kind = described.content if described else "unknown"
        notes.append(f"Sound: the original audio ({kind})")
    if info.has_audio:
        # A quieter reel is usually just limited less, and the platform plays reels at
        # about the default level; only a clearly louder one is worth matching.
        loudness = integrated_loudness(original)
        audio = updates.get("audio", Audio())
        if loudness > LOUD:
            target = round(min(MAX_LOUDNESS, loudness))
            updates["audio"] = audio.model_copy(update={"loudness_lufs": float(target)})
            notes.append(f"Loudness: {loudness:.1f} LUFS measured, so {target} LUFS")
        else:
            notes.append(f"Loudness: {loudness:.1f} LUFS measured; kept {audio.loudness_lufs:g}")

    notes.append(
        f"Length: the reel runs {info.duration:.0f} s, so reels of "
        f"{preset.min_duration:.0f}-{preset.max_duration:.0f} s"
    )
    notes.append(
        "Not learned: logos, music beds, intros and outros; add those yourself if the reel had them"
    )
    preset = Preset.model_validate({**preset.model_dump(), **_dumped(updates)})
    return Draft(preset, notes)


def framing_for(
    colour: tuple[int, int, int] | None,
    picture: Box,
    blurred: Box | None,
    blocks: list[tuple[TextBlock, Measured, str]],
    height: int,
) -> tuple[Framing, str]:
    """How the picture fills the frame, and a note saying so."""
    if colour is None and blurred is None:
        return Framing(mode="smart"), "Layout: the picture fills the frame (cropped to fit)"
    share = picture[2] - picture[0]
    framing = Framing(mode="none", crop_black_bars=True)
    if blurred:
        # A blurred copy behind the picture isn't drawn here yet; a colour taken from the
        # footage is the nearest look.
        framing.auto_background = True
        around = "a blurred copy of itself (drawn here as a colour taken from the footage)"
    else:
        framing.background = hex_colour(colour)
        around = f"a {hex_colour(colour)} background"
    # Text sits this far from the picture, and is moved to keep that gap when a taller
    # picture comes along.
    above = [m.bottom for b, m, _ in blocks if b.anchor == "top"]
    below = [m.top for b, m, _ in blocks if b.anchor == "bottom"]
    if above:
        framing.text_gap_above = max(0, round(picture[1] * height - max(above)))
    if below:
        framing.text_gap_below = max(0, round(min(below) - picture[3] * height))
    if share < INSET:
        framing.picture_width = round(share, 2)
        around += f", {share:.0%} of the frame wide"
    return framing, f"Layout: the whole picture on {around}"


def title_overlay(
    title: Measured, fit: FontFit, background: tuple[int, int, int] | None
) -> TitleOverlay:
    """The hook banner's look from a title seen in the opening seconds."""
    box = title.backdrop if title.backdrop and title.backdrop != background else None
    # Banners are placed by where their first line starts, above its ink.
    face = ImageFont.truetype(str(fit.font), round(fit.size))
    lead = _ink_box(face, title.lines[0])[1]
    return TitleOverlay(
        font=fit.font.stem,
        size=round(fit.size / 0.8),  # the hook banner is drawn at 0.8 of this
        color=hex_colour(title.colour),
        box_color=hex_colour(box) if box else None,
        offset=max(0, title.top - lead),
    )


def caption_note(captions: CaptionStyle) -> str:
    language = f" ({captions.language})" if captions.language else ""
    highlight = (
        f" with the spoken word in {captions.highlight_color}"
        if captions.animation == "highlight"
        else ""
    )
    where = captions.position + (
        f" {captions.margin} px from the edge" if captions.position != "middle" else ""
    )
    return (
        f"Captions: {captions.mode}{language}, about {captions.words_per_line} words a line, "
        f"{captions.font} {captions.size}, {captions.color}{highlight}, {where}"
    )


def _dumped(updates: dict) -> dict:
    return {
        k: [v.model_dump() for v in value] if isinstance(value, list) else value.model_dump()
        for k, value in updates.items()
    }
