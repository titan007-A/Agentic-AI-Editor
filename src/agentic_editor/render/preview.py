"""A still of what a preset makes, for the preset editor: the frame, the picture where the
preset puts it, and the preset's text, laid out by the same code a render uses. The
picture is a drawn stand-in (a singer on a stage) in the shape the editor picks, and the
placeholders get sample words. Captions are drawn here for a look at size and place;
a render draws them with libass, so letter shapes can differ a little."""

import io
import re
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter

from agentic_editor.presets import Preset
from agentic_editor.render import compose, text
from agentic_editor.render.video import picture_rect
from agentic_editor.settings import Settings

Shape = Literal["16:9", "4:3", "1:1", "4:5", "9:16"]
SHAPES: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "4:3": (1440, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
SAMPLES = {
    "song": "Bohemian Rhapsody",
    "artist": "Queen",
    "headline": "How this song was written in one night",
    "year": "1975",
    "day": "103",
    "song_number": "5",
}
FIELD = re.compile(r"\{(\w+)\}")
CAPTION = "this is how captions look on the video"
LINE = "A one-liner about the video"
TITLE = "The video's title"


def sample_text(words: str) -> str:
    return FIELD.sub(lambda m: SAMPLES.get(m.group(1), m.group(1).replace("_", " ")), words)


def stand_in(size: tuple[int, int]) -> Image.Image:
    """A singer under a spotlight, filling `size`: head, mic and shoulders show what a crop
    keeps and what it cuts."""
    w, h = size
    image = Image.new("RGB", size)
    canvas = ImageDraw.Draw(image)
    for y in range(h):  # dusk blue at the top to a warm floor
        t = y / max(1, h - 1)
        canvas.line(
            [(0, y), (w, y)], fill=(round(28 + 70 * t), round(36 + 30 * t), round(84 - 30 * t))
        )
    unit = min(w, h)
    cx = w / 2
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse((cx - unit * 0.45, h * 0.05, cx + unit * 0.45, h * 0.95), fill=110)
    image.paste((255, 214, 150), mask=glow.filter(ImageFilter.GaussianBlur(unit * 0.08)))
    head_r = unit * 0.09
    head_y = h * 0.42
    body = (60, 40, 52)
    canvas.rounded_rectangle(
        (cx - unit * 0.26, head_y + head_r * 1.3, cx + unit * 0.26, h + unit * 0.2),
        radius=unit * 0.12, fill=body,
    )  # fmt: skip
    canvas.ellipse(
        (cx - head_r, head_y - head_r, cx + head_r, head_y + head_r), fill=(222, 170, 135)
    )
    canvas.ellipse(
        (cx - head_r * 1.05, head_y - head_r * 1.1, cx + head_r * 1.05, head_y - head_r * 0.15),
        fill=(40, 26, 22),
    )
    mic = (cx + head_r * 0.2, head_y + head_r * 0.7)
    canvas.line(
        [mic, (mic[0] + unit * 0.05, mic[1] + unit * 0.35)],
        fill=(30, 30, 30),
        width=max(2, round(unit * 0.012)),
    )
    canvas.ellipse(
        (mic[0] - unit * 0.022, mic[1] - unit * 0.03, mic[0] + unit * 0.022, mic[1] + unit * 0.02),
        fill=(90, 90, 96),
    )
    return image


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to fill `size`, cutting the middle out."""
    scale = max(size[0] / image.width, size[1] / image.height)
    scaled = image.resize(
        (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
    )
    x, y = (scaled.width - size[0]) // 2, (scaled.height - size[1]) // 2
    return scaled.crop((x, y, x + size[0], y + size[1]))


def picture_place(
    preset: Preset, source: tuple[int, int], band: tuple[int, int] | None
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """Where the picture goes in the frame (x0, y0, x1, y1) and the shape of the part of
    the source shown there."""
    frame = preset.size
    if preset.framing.mode != "none":
        return (0, 0, *frame), frame
    if band is not None:
        # Band framing: the frame's width, the source cut top and bottom to fit the band.
        rows = compose.band_rows(preset, band, source[0])
        shown = (source[0], min(source[1], rows))
        return picture_rect(shown, frame, preset.framing.picture_width, band), shown
    return picture_rect(source, frame, preset.framing.picture_width, None), source


def _caption(canvas: ImageDraw.ImageDraw, preset: Preset, settings: Settings, face) -> None:
    style = preset.captions
    width, height = preset.size
    words = CAPTION.split()[: style.words_per_line]
    line = " ".join(words)
    if style.uppercase:
        line = line.upper()
    font = text.font_at(text.font_file(settings.fonts_dir, style.font), style.size)
    left, top, right, bottom = canvas.textbbox(
        (0, 0), line, font=font, stroke_width=round(style.outline)
    )
    w, h = right - left, bottom - top
    x = (width - w) / 2 - left
    if style.position == "top":
        y = style.margin - top
    elif style.position == "middle":
        y = (height - h) / 2 - top
    else:
        y = height - style.margin - h - top
    first, _, rest = line.partition(" ")
    highlight = style.animation in ("highlight", "karaoke")
    stroke = {"stroke_width": round(style.outline), "stroke_fill": style.outline_color}
    canvas.text(
        (x, y), first, font=font, fill=style.highlight_color if highlight else style.color, **stroke
    )
    if rest:
        offset = canvas.textlength(first + " ", font=font)
        canvas.text((x + offset, y), rest, font=font, fill=style.color, **stroke)


def render(preset: Preset, settings: Settings, shape: Shape = "16:9") -> bytes:
    """The preview as a JPEG, frame-sized."""
    frame = preset.size
    source = SHAPES[shape]
    face = compose.text_face(settings, None)
    shown = preset.model_copy(
        update={
            "text_blocks": [
                b.model_copy(update={"text": sample_text(b.text)}) for b in preset.text_blocks
            ]
        }
    )
    band = compose.picture_band(shown, face) if preset.framing.mode == "none" else None
    background = preset.framing.background
    if preset.framing.auto_background:
        background = "#2a2433"  # a render tones one from the footage; this stands for it
    image = Image.new("RGB", frame, background)

    (x0, y0, x1, y1), part = picture_place(preset, source, band)
    picture = _cover(stand_in(source), part) if part != source else stand_in(source)
    image.paste(picture.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), (x0, y0))

    placed = []
    if shown.text_blocks:
        letterbox = (
            (y0, y1) if preset.framing.mode == "none" and (y0, y1) != (0, frame[1]) else None
        )
        placed = [
            text.place(b, face(b.font, b.text), frame, letterbox, gap)
            for b, gap in zip(shown.text_blocks, compose.text_gaps(shown), strict=True)
        ]
    corners = None
    if preset.framing.mode == "none" and preset.framing.corner_radius:
        corners = ((x0, y0, x1, y1), preset.framing.corner_radius,
                   preset.framing.corner_color or background)  # fmt: skip
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        if placed or corners:
            _lay(image, text.draw(placed, frame, work / "text.png", corners))
        if preset.title_overlay.enabled:
            t = preset.title_overlay
            font = face(t.font, TITLE)
            dest = work / "title.png"
            _lay(
                image,
                text.draw_banner(TITLE, font, t.size, t.color, t.box_color, frame, t.offset, dest),
            )
        if preset.one_liner.enabled:
            o = preset.one_liner
            font = face(o.font, LINE)
            top = o.offset
            if o.position == "bottom":
                _, bottom = text.banner_extent(LINE, font, o.size, o.box_color, frame[0])
                top = frame[1] - o.offset - round(bottom)
            dest = work / "line.png"
            _lay(
                image, text.draw_banner(LINE, font, o.size, o.color, o.box_color, frame, top, dest)
            )

    canvas = ImageDraw.Draw(image)
    if preset.captions.enabled:
        _caption(canvas, preset, settings, face)

    if preset.logo.file:
        _logo(image, preset)

    out = io.BytesIO()
    image.save(out, "JPEG", quality=82)
    return out.getvalue()


def _lay(image: Image.Image, layer_file: Path) -> None:
    with Image.open(layer_file) as layer:
        image.paste(layer, (0, 0), layer)


def _logo(image: Image.Image, preset: Preset) -> None:
    """The logo where a render puts it; a file that can't be found or read is left out."""
    logo = preset.logo
    try:
        with Image.open(compose.asset_path(logo.file)) as found:
            mark = found.convert("RGBA")
    except (OSError, ValueError):
        return
    height = max(1, round(mark.height * logo.width / mark.width))
    mark = mark.resize((logo.width, height), Image.Resampling.LANCZOS)
    alpha = mark.getchannel("A").point(lambda a: round(a * logo.opacity))
    mark.putalpha(alpha)
    x = logo.margin if logo.position.endswith("left") else image.width - mark.width - logo.margin
    y = logo.margin if logo.position.startswith("top") else image.height - mark.height - logo.margin
    image.paste(mark, (x, y), mark)
