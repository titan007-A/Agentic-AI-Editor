"""The preset's own watermark (a page name, a picture, or both) drawn once into a
transparent frame-sized PNG. A render lays that PNG over every frame at 0:0 and the
preset preview pastes the very same picture, so the mark lands on the same pixels in
both. Set by hand in the preset: the style learner never writes it."""

from pathlib import Path

from PIL import Image, ImageDraw

from agentic_editor.presets import OwnWatermark
from agentic_editor.render import text

GAP = 0.25  # of the text size, between the picture and the words under it
# Where Instagram draws its buttons, name and caption over a 9:16 reel, as shares of the
# frame: the right column and the bottom rows.
UI_RIGHT = 0.15
UI_BOTTOM = 0.2
BUTTONS_FROM = 0.45  # the buttons run from here down


def _picture(mark: OwnWatermark, asset: Path) -> Image.Image:
    with Image.open(asset) as found:
        picture = found.convert("RGBA")
    tall = max(1, round(picture.height * mark.width / picture.width))
    return picture.resize((mark.width, tall), Image.Resampling.LANCZOS)


def _words(mark: OwnWatermark, fonts_dir: Path) -> Image.Image:
    font = text.font_at(text.font_file(fonts_dir, mark.font), mark.size)
    stroke = round(mark.outline)
    left, top, right, bottom = font.getbbox(mark.text, stroke_width=stroke)
    image = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (-left, -top), mark.text, font=font, fill=mark.color,
        stroke_width=stroke, stroke_fill=mark.outline_color,
    )  # fmt: skip
    return image


def drawn(mark: OwnWatermark, fonts_dir: Path, asset: Path | None) -> Image.Image:
    """The mark alone, at its size and opacity: the picture (when `asset`) above the
    words (when it has any), centred on each other."""
    pieces = []
    if asset is not None:
        pieces.append(_picture(mark, asset))
    if mark.text.strip():
        pieces.append(_words(mark, fonts_dir))
    if not pieces:
        raise ValueError("a watermark needs words or a picture")
    gap = round(mark.size * GAP) if len(pieces) > 1 else 0
    width = max(p.width for p in pieces)
    image = Image.new("RGBA", (width, sum(p.height for p in pieces) + gap), (0, 0, 0, 0))
    y = 0
    for piece in pieces:
        image.alpha_composite(piece, ((width - piece.width) // 2, y))
        y += piece.height + gap
    if mark.opacity < 1:
        image.putalpha(image.getchannel("A").point(lambda a: round(a * mark.opacity)))
    return image


def box(mark: OwnWatermark, size: tuple[int, int], frame: tuple[int, int]) -> tuple[int, ...]:
    """Where a mark of `size` goes: x and y are shares of the room left beside it, so 0
    is flush with the left (top) edge, 1 with the right (bottom) and 0.5 the middle; it
    never runs off the frame."""
    x = round(max(0, frame[0] - size[0]) * mark.x)
    y = round(max(0, frame[1] - size[1]) * mark.y)
    return x, y, x + size[0], y + size[1]


def layer(
    mark: OwnWatermark, frame: tuple[int, int], fonts_dir: Path, asset: Path | None, dest: Path
) -> tuple[Path, tuple[int, ...]]:
    """The frame-sized transparent PNG with the mark where it goes, and its box."""
    image = drawn(mark, fonts_dir, asset)
    where = box(mark, image.size, frame)
    full = Image.new("RGBA", frame, (0, 0, 0, 0))
    full.alpha_composite(image.crop((0, 0, min(image.width, frame[0]),
                                     min(image.height, frame[1]))), where[:2])  # fmt: skip
    full.save(dest)
    return dest, where


def under_instagram(where: tuple[int, ...], frame: tuple[int, int]) -> bool:
    """Whether a mark at `where` meets the parts of a reel Instagram draws over."""
    _, _, right, bottom = where
    in_buttons = right > frame[0] * (1 - UI_RIGHT) and bottom > frame[1] * BUTTONS_FROM
    return in_buttons or bottom > frame[1] * (1 - UI_BOTTOM)
