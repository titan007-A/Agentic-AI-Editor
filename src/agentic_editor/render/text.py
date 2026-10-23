"""Fixed text blocks (series taglines, titles) drawn with Pillow into a transparent PNG.

Pillow rather than ffmpeg's drawtext: drawtext renders the newline as a glyph in some
fonts and can't measure ink, and laying text out here keeps the result identical on
every machine as long as the bundled fonts are used. Text a bundled font can't draw is
drawn in the Noto font for its script (see fonts)."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agentic_editor.presets import TextBlock
from agentic_editor.render import fonts

EDGE_MARGIN = 24
BOX_PAD = 0.35  # of the size, around a banner's text inside its box


def font_file(fonts_dir: Path, name: str) -> Path:
    for ext in (".ttf", ".otf"):
        path = fonts_dir / f"{name}{ext}"
        if path.exists():
            return path
    available = ", ".join(sorted(p.stem for p in fonts_dir.glob("*.[ot]tf")))
    raise FileNotFoundError(f"font '{name}' is not in {fonts_dir} (available: {available})")


def bundled_fonts(fonts_dir: Path) -> list[Path]:
    return sorted(fonts_dir.glob("*.[ot]tf"))


def font_family(path: Path) -> str:
    """The name libass finds this face by. A weight other than regular or bold is a family
    of its own there ("Open Sans ExtraBold"); the shared name gets the regular face."""
    family, style = ImageFont.truetype(str(path), 20).getname()
    return family if style in ("Regular", "Bold", "Italic", "Bold Italic") else f"{family} {style}"


class Face:
    """A font and the fonts that draw what it can't (see fonts.chain), measured and drawn
    piece by piece so every character gets a real glyph. Pieces in other fonts sit on the
    same baseline."""

    def __init__(self, chain: list[Path]):
        self.chain = list(chain)
        self.path = self.chain[0]

    def runs(self, text: str) -> list[tuple[str, Path]]:
        return fonts.runs(text, self.chain) if len(self.chain) > 1 else [(text, self.path)]

    def length(self, text: str, size: int) -> float:
        return sum(_font(path, size).getlength(piece) for piece, path in self.runs(text))

    def metrics(self, size: int) -> tuple[int, int]:
        pairs = [_font(path, size).getmetrics() for path in self.chain]
        return max(a for a, _ in pairs), max(d for _, d in pairs)

    def bbox(self, text: str, size: int) -> tuple[float, float, float, float]:
        """Ink box of one line: x from where it starts, y from the line's top."""
        if len(self.chain) == 1:
            return _font(self.path, size).getbbox(text)
        ascent, x, boxes = self.metrics(size)[0], 0.0, []
        for piece, path in self.runs(text):
            font = _font(path, size)
            if piece.strip():
                x0, y0, x1, y1 = font.getbbox(piece, anchor="ls")
                boxes.append((x + x0, ascent + y0, x + x1, ascent + y1))
            x += font.getlength(piece)
        if not boxes:
            return 0.0, 0.0, 0.0, 0.0
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def draw(self, canvas: ImageDraw.ImageDraw, at: tuple[float, float], text: str, size: int,
             fill: str) -> None:  # fmt: skip
        if len(self.chain) == 1:
            canvas.text(at, text, font=_font(self.path, size), fill=fill)
            return
        x, y = at
        baseline = y + self.metrics(size)[0]
        for piece, path in self.runs(text):
            font = _font(path, size)
            canvas.text((x, baseline), piece, font=font, fill=fill, anchor="ls")
            x += font.getlength(piece)


def face_of(font: "Face | Path") -> Face:
    return font if isinstance(font, Face) else Face([font])


@dataclass(frozen=True)
class Placed:
    lines: list[str]
    font: Face
    size: int
    top: float
    color: str
    line_height: float

    @property
    def band(self) -> tuple[float, float]:
        """Rows of the frame the block's ink covers."""
        spacing = self.line_height / line_height(self.font, self.size, 1.0)
        ink_top, ink_bottom = ink_extent(self.lines, self.font, self.size, spacing)
        return self.top + ink_top, self.top + ink_bottom


@lru_cache(maxsize=256)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def line_height(font: Face | Path, size: int, spacing: float) -> float:
    ascent, descent = face_of(font).metrics(size)
    return (ascent + descent) * spacing


def ink_extent(
    lines: list[str], font: Face | Path, size: int, spacing: float
) -> tuple[float, float]:
    """(top, bottom) of the visible ink relative to the first line's top."""
    face = face_of(font)
    step = line_height(face, size, spacing)
    boxes = [
        (i * step + box[1], i * step + box[3])
        for i, line in enumerate(lines)
        if line.strip()
        for box in [face.bbox(line, size)]
    ]
    if not boxes:
        return 0.0, 0.0
    return min(b[0] for b in boxes), max(b[1] for b in boxes)


def fit_width(lines: list[str], font: Face | Path, size: int, min_size: int, max_width: int) -> int:
    face = face_of(font)
    while size > min_size:
        if max(face.length(line, size) for line in lines) <= max_width:
            break
        size -= 1
    return size


def place(
    block: TextBlock,
    font: Face | Path,
    frame: tuple[int, int],
    video_band: tuple[float, float] | None,
    gap: int | None,
) -> Placed:
    """Size and position a block, moving it clear of the video by `gap` px and
    shrinking it (within block.max_shrink) only if moving isn't enough."""
    face = face_of(font)
    _, height = frame
    lines = block.text.split("\n")
    size = fit_width(lines, face, block.size, block.min_size, block.max_width)
    floor = max(block.min_size, round(size * block.max_shrink))
    wanted = block.offset if block.anchor == "top" else height - block.offset

    def position(candidate: int) -> tuple[float, bool]:
        ink_top, ink_bottom = ink_extent(lines, face, candidate, block.line_spacing)
        top = wanted - ink_top
        if video_band is None or gap is None:
            return top, True
        if block.anchor == "top":
            limit = video_band[0] - gap
            top = min(top, limit - ink_bottom)
            return top, top + ink_top >= EDGE_MARGIN
        limit = video_band[1] + gap
        top = max(top, limit - ink_top)
        return top, top + ink_bottom <= height - EDGE_MARGIN

    top, fits = position(size)
    while not fits and size > floor:
        size -= 1
        top, fits = position(size)
    # Even with no room beside the video at the smallest allowed size, overlapping the
    # picture is better than running off the frame.
    ink_top, ink_bottom = ink_extent(lines, face, size, block.line_spacing)
    top = min(max(top, EDGE_MARGIN - ink_top), height - EDGE_MARGIN - ink_bottom)
    return Placed(lines, face, size, top, block.color, line_height(face, size, block.line_spacing))


def draw(placed: list[Placed], frame: tuple[int, int], dest: Path) -> Path:
    width, _ = frame
    image = Image.new("RGBA", frame, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    for block in placed:
        for i, line in enumerate(block.lines):
            x = (width - block.font.length(line, block.size)) / 2
            block.font.draw(canvas, (x, block.top + i * block.line_height), line, block.size,
                            block.color)  # fmt: skip
    image.save(dest)
    return dest


def font_at(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return _font(path, size)


def caption_font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    """The font as large as libass draws a caption of `size`, for measuring captions."""
    return ImageFont.truetype(str(path), size * fonts.libass_scale(path))


def caption_measure(chain: list[Path], size: float, upper: bool) -> Callable[[str], float]:
    """Width in pixels of a caption line as libass draws it, piece by piece in the fonts of
    the chain."""
    sized = {path: caption_font(path, size) for path in chain}

    def measure(line: str) -> float:
        line = line.upper() if upper else line
        return sum(sized[path].getlength(piece) for piece, path in fonts.runs(line, chain))

    return measure


def balance(text: str, font: Face | Path, size: int, max_width: int) -> str:
    """Split a line that is too wide into two of similar length, rather than shrinking it."""
    face = face_of(font)
    words = text.split()
    if face.length(text, size) <= max_width or len(words) < 4:
        return text
    best = min(
        range(1, len(words)),
        key=lambda i: abs(
            face.length(" ".join(words[:i]), size) - face.length(" ".join(words[i:]), size)
        ),
    )
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def banner_extent(
    text: str, font: Face | Path, size: int, box: str | None, frame_width: int
) -> tuple[float, float]:
    """Top and bottom of what draw_banner draws, box included, relative to its `top`."""
    lines = text.split("\n")
    size = fit_width(lines, font, size, 24, frame_width - 160)
    ink_top, ink_bottom = ink_extent(lines, font, size, 1.0)
    pad = size * BOX_PAD if box else 0.0
    return ink_top - pad, ink_bottom + pad


def draw_banner(
    text: str,
    font: Face | Path,
    size: int,
    color: str,
    box: str | None,
    frame: tuple[int, int],
    top: int,
    dest: Path,
) -> Path:
    """A centered title with an optional solid box behind it."""
    face = face_of(font)
    width, _ = frame
    lines = text.split("\n")
    size = fit_width(lines, face, size, 24, width - 160)
    step = line_height(face, size, 1.0)
    image = Image.new("RGBA", frame, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    if box:
        ink_top, ink_bottom = ink_extent(lines, face, size, 1.0)
        text_width = max(face.length(line, size) for line in lines)
        pad = size * BOX_PAD
        canvas.rounded_rectangle(
            (
                (width - text_width) / 2 - pad,
                top + ink_top - pad,
                (width + text_width) / 2 + pad,
                top + ink_bottom + pad,
            ),
            radius=pad,
            fill=box,
        )
    for i, line in enumerate(lines):
        face.draw(canvas, ((width - face.length(line, size)) / 2, top + i * step), line, size,
                  color)  # fmt: skip
    image.save(dest)
    return dest
