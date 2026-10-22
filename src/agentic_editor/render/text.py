"""Fixed text blocks (series taglines, titles) drawn with Pillow into a transparent PNG.

Pillow rather than ffmpeg's drawtext: drawtext renders the newline as a glyph in some
fonts and can't measure ink, and laying text out here keeps the result identical on
every machine as long as the bundled fonts are used."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agentic_editor.presets import TextBlock

EDGE_MARGIN = 24


def font_file(fonts_dir: Path, name: str) -> Path:
    for ext in (".ttf", ".otf"):
        path = fonts_dir / f"{name}{ext}"
        if path.exists():
            return path
    available = ", ".join(sorted(p.stem for p in fonts_dir.glob("*.[ot]tf")))
    raise FileNotFoundError(f"font '{name}' is not in {fonts_dir} (available: {available})")


def font_family(path: Path) -> str:
    return ImageFont.truetype(str(path), 20).getname()[0]


@dataclass(frozen=True)
class Placed:
    lines: list[str]
    font: Path
    size: int
    top: float
    color: str
    line_height: float


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def line_height(path: Path, size: int, spacing: float) -> float:
    ascent, descent = _font(path, size).getmetrics()
    return (ascent + descent) * spacing


def ink_extent(lines: list[str], path: Path, size: int, spacing: float) -> tuple[float, float]:
    """(top, bottom) of the visible ink relative to the first line's top."""
    font = _font(path, size)
    step = line_height(path, size, spacing)
    boxes = [
        (i * step + font.getbbox(line)[1], i * step + font.getbbox(line)[3])
        for i, line in enumerate(lines)
        if line.strip()
    ]
    if not boxes:
        return 0.0, 0.0
    return min(b[0] for b in boxes), max(b[1] for b in boxes)


def fit_width(lines: list[str], path: Path, size: int, min_size: int, max_width: int) -> int:
    while size > min_size:
        font = _font(path, size)
        if max(font.getlength(line) for line in lines) <= max_width:
            break
        size -= 1
    return size


def place(
    block: TextBlock,
    path: Path,
    frame: tuple[int, int],
    video_band: tuple[float, float] | None,
    gap: int | None,
) -> Placed:
    """Size and position a block, moving it clear of the video by `gap` px and
    shrinking it (within block.max_shrink) only if moving isn't enough."""
    _, height = frame
    lines = block.text.split("\n")
    size = fit_width(lines, path, block.size, block.min_size, block.max_width)
    floor = max(block.min_size, round(size * block.max_shrink))
    wanted = block.offset if block.anchor == "top" else height - block.offset

    def position(candidate: int) -> tuple[float, bool]:
        ink_top, ink_bottom = ink_extent(lines, path, candidate, block.line_spacing)
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
    if not fits:
        # No room beside the video even at the smallest allowed size: overlapping the
        # picture is better than running off the frame.
        ink_top, ink_bottom = ink_extent(lines, path, size, block.line_spacing)
        top = min(max(top, EDGE_MARGIN - ink_top), height - EDGE_MARGIN - ink_bottom)
    return Placed(lines, path, size, top, block.color, line_height(path, size, block.line_spacing))


def draw(placed: list[Placed], frame: tuple[int, int], dest: Path) -> Path:
    width, _ = frame
    image = Image.new("RGBA", frame, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    for block in placed:
        font = _font(block.font, block.size)
        for i, line in enumerate(block.lines):
            x = (width - font.getlength(line)) / 2
            canvas.text((x, block.top + i * block.line_height), line, font=font, fill=block.color)
    image.save(dest)
    return dest


def font_at(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return _font(path, size)


def balance(text: str, path: Path, size: int, max_width: int) -> str:
    """Split a line that is too wide into two of similar length, rather than shrinking it."""
    font = _font(path, size)
    words = text.split()
    if font.getlength(text) <= max_width or len(words) < 4:
        return text
    best = min(
        range(1, len(words)),
        key=lambda i: abs(
            font.getlength(" ".join(words[:i])) - font.getlength(" ".join(words[i:]))
        ),
    )
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def draw_banner(
    text: str,
    path: Path,
    size: int,
    color: str,
    box: str | None,
    frame: tuple[int, int],
    top: int,
    dest: Path,
) -> Path:
    """A centered title with an optional solid box behind it."""
    width, _ = frame
    lines = text.split("\n")
    size = fit_width(lines, path, size, 24, width - 160)
    font = _font(path, size)
    step = line_height(path, size, 1.0)
    image = Image.new("RGBA", frame, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    if box:
        ink_top, ink_bottom = ink_extent(lines, path, size, 1.0)
        text_width = max(font.getlength(line) for line in lines)
        pad = size * 0.35
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
        canvas.text(
            ((width - font.getlength(line)) / 2, top + i * step), line, font=font, fill=color
        )
    image.save(dest)
    return dest
