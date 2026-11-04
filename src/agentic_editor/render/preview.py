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

from agentic_editor import subtitles
from agentic_editor.edl import Cue
from agentic_editor.presets import Preset
from agentic_editor.render import compose, mark, text
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
# As long as a one-liner can be (its max_words): the preview shows its longest case.
LINE = "A one-liner the model writes about what happens in the video, as long as allowed"
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
        anchor = compose.picture_anchor(preset)
        return picture_rect(shown, frame, preset.framing.picture_width, band, anchor), shown
    return picture_rect(source, frame, preset.framing.picture_width, None), source


def _caption(
    canvas: ImageDraw.ImageDraw,
    preset: Preset,
    settings: Settings,
    face,
    keep_off: list[tuple[float, float]] = (),
) -> None:
    """A sample caption where a render puts it: at its position, or, where that covers
    `keep_off` (the picture shown whole, the text), the clear place a render would pick."""
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
    position, margin = style.position, style.margin
    if keep_off:
        cue = Cue(start=0.0, end=1.0, text=line)
        blocked = [(0.5, [(a - compose.BANNER_GAP, b + compose.BANNER_GAP) for a, b in keep_off])]
        height_ = round(style.size * 1.25 + 2 * style.outline)
        (spot,) = subtitles.place([cue], style.position, style.margin, height_, height, blocked)
        position = {2: "bottom", 5: "middle", 8: "top"}[spot.alignment]
        margin = spot.margin
    if position == "top":
        y = margin - top
    elif position == "middle":
        y = (height - h) / 2 - top
    else:
        y = height - margin - h - top
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
    return render_checked(preset, settings, shape)[0]


def render_checked(
    preset: Preset, settings: Settings, shape: Shape = "16:9"
) -> tuple[bytes, list[str]]:
    """The preview as a JPEG, and what looks wrong in it, in words a person can act on."""
    notes: list[str] = []
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
    limiting = (
        [g for g in compose.text_gaps(shown) if g is not None]
        if preset.framing.mode == "none" and preset.framing.picture_rows is None
        else []
    )
    if limiting and band is None:
        notes.append(
            "The text leaves less than a quarter of the frame for the video, so the video "
            "ignores it and fills the frame behind the text. Make the text smaller or move it "
            "nearer the edges."
        )
    background = preset.framing.background
    if preset.framing.auto_background:
        background = "#2a2433"  # a render tones one from the footage; this stands for it
    image = Image.new("RGB", frame, background)
    if preset.framing.background_image:
        try:
            with tempfile.TemporaryDirectory() as work:
                still = compose.backdrop(
                    preset.framing.background_image, frame, Path(work) / "background.png"
                )
                image = Image.open(still).convert("RGB")
        except (FileNotFoundError, OSError):
            pass  # a picture not uploaded yet: the colour stands in

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
    inks = [p.band for p in placed]
    for i, (block, p) in enumerate(zip(shown.text_blocks, placed, strict=True), 1):
        lines = block.text.split("\n")
        fitted = text.fit_width(lines, p.font, block.size, block.min_size, block.max_width)
        widest = max(p.font.length(line, fitted) for line in lines)
        if widest > block.max_width + 2:
            notes.append(
                f"Text {i} is too long: even at its smallest size ({block.min_size}) it is "
                f"wider than its max width ({block.max_width} px). Shorten it or break the line."
            )
        elif p.size < fitted:
            notes.append(
                f"Text {i} is squeezed from size {fitted} to {p.size} to keep its gap to the "
                "video. Lower the gap or move the text nearer the edge."
            )
        top, bottom = p.band
        if preset.framing.mode == "none" and min(bottom, y1) - max(top, y0) > 2:
            notes.append(
                f"Text {i} runs over the video by {round(min(bottom, y1) - max(top, y0))} px."
            )
        for j in range(i, len(inks)):
            other = inks[j]
            if min(bottom, other[1]) - max(top, other[0]) > 2:
                notes.append(f"Text {i} and text {j + 1} overlap.")
    corners = None
    if preset.framing.mode == "none" and preset.framing.corner_radius:
        corners = ((x0, y0, x1, y1), preset.framing.corner_radius,
                   preset.framing.corner_color or background)  # fmt: skip
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        pictures = []
        for n, i in enumerate(preset.images, 1):
            try:
                path = compose.asset_path(i.file)
                with Image.open(path) as found:
                    tall = round(found.height * i.width / found.width)
            except (OSError, ValueError):
                notes.append(f"Picture {n}: the file isn't there or isn't a picture; upload it.")
                continue  # the rest still shows
            pictures.append((path, i.x, i.y, i.width, i.opacity))
            box = (i.x, i.y, i.x + i.width, i.y + tall)
            if box[0] < 0 or box[1] < 0 or box[2] > frame[0] or box[3] > frame[1]:
                notes.append(f"Picture {n} runs off the edge of the frame.")
            for k, (top, bottom) in enumerate(inks, 1):
                if min(bottom, box[3]) - max(top, box[1]) > 2 and _wide_overlap(
                    placed[k - 1], box, frame[0]
                ):
                    notes.append(f"Picture {n} covers part of text {k}.")
        if placed or corners or pictures:
            _lay(image, text.draw(placed, frame, work / "text.png", corners, pictures))
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
            words = " ".join(LINE.split()[: o.max_words])
            font = face(o.font, words)
            # Laid out as a render lays it out: kept off the picture shown whole.
            off = (y0, y1) if preset.framing.mode == "none" and (y0, y1) != (0, frame[1]) else None
            wrapped, size, offset = compose.off_picture(
                words, font, o.size, o.box_color, frame, o.position, o.offset, off
            )
            above, below = text.banner_extent(wrapped, font, size, o.box_color, frame[0])
            top = offset
            if o.position == "bottom":
                top = frame[1] - offset - round(below)
            if size < o.size or offset != o.offset:
                notes.append(
                    "A long one-liner is made smaller or moved to keep off the video. "
                    "For it to stay as set, give it a smaller size or fewer words, or move "
                    "the text and the video apart."
                )
            if off is not None and top + below > off[0] and top + above < off[1]:
                notes.append(
                    "A long one-liner covers part of the video even made smaller. Give it "
                    "fewer words or a smaller size, or move the text and the video apart."
                )
            dest = work / "line.png"
            _lay(
                image,
                text.draw_banner(wrapped, font, size, o.color, o.box_color, frame, top, dest),
            )

    canvas = ImageDraw.Draw(image)
    if preset.captions.enabled:
        # Like a render: captions that avoid faces also keep off the text and the picture.
        keep_off = []
        if preset.captions.avoid_faces:
            keep_off = [
                ink for b, ink in zip(shown.text_blocks, inks, strict=True) if b.text.strip()
            ]
            if preset.framing.mode == "none" and (y0, y1) != (0, frame[1]):
                keep_off.append((y0, y1))
        _caption(canvas, preset, settings, face, keep_off)

    if preset.logo.file and not _logo(image, preset):
        notes.append("The logo file isn't there or isn't a picture; upload it again.")
    notes += _watermark(image, preset, settings)

    out = io.BytesIO()
    image.save(out, "JPEG", quality=82)
    return out.getvalue(), list(dict.fromkeys(notes))


def _wide_overlap(block: text.Placed, box: tuple[int, int, int, int], width: int) -> bool:
    """Whether a picture's columns meet a centred text block's widest line."""
    wide = max(block.font.length(line, block.size) for line in block.lines)
    left, right = (width - wide) / 2, (width + wide) / 2
    return min(right, box[2]) - max(left, box[0]) > 2


def _lay(image: Image.Image, layer_file: Path) -> None:
    with Image.open(layer_file) as layer:
        image.paste(layer, (0, 0), layer)


def _logo(image: Image.Image, preset: Preset) -> bool:
    """The logo where a render puts it; False when the file can't be found or read."""
    logo = preset.logo
    try:
        with Image.open(compose.asset_path(logo.file)) as found:
            mark = found.convert("RGBA")
    except (OSError, ValueError):
        return False
    height = max(1, round(mark.height * logo.width / mark.width))
    mark = mark.resize((logo.width, height), Image.Resampling.LANCZOS)
    alpha = mark.getchannel("A").point(lambda a: round(a * logo.opacity))
    mark.putalpha(alpha)
    x = logo.margin if logo.position.endswith("left") else image.width - mark.width - logo.margin
    y = logo.margin if logo.position.startswith("top") else image.height - mark.height - logo.margin
    image.paste(mark, (x, y), mark)
    return True


def _watermark(image: Image.Image, preset: Preset, settings: Settings) -> list[str]:
    """The preset's own watermark pasted from the very PNG a render lays over the video,
    and what to check about it."""
    own = preset.watermark
    if not own.enabled:
        return []
    if not own.text.strip() and not own.file:
        return ["The watermark is on but has no words or picture; give it one."]
    notes = []
    picture = None
    if own.file:
        try:
            picture = compose.asset_path(own.file)
            with Image.open(picture):
                pass
        except (OSError, ValueError):
            notes.append("The watermark picture isn't there or isn't a picture; upload it.")
            picture = None
            if not own.text.strip():
                return notes
    with tempfile.TemporaryDirectory() as folder:
        png, where = mark.layer(own, preset.size, settings.fonts_dir, picture,
                                Path(folder) / "watermark.png")  # fmt: skip
        _lay(image, png)
    w, h = where[2] - where[0], where[3] - where[1]
    if w > preset.size[0] or h > preset.size[1]:
        notes.append("The watermark is bigger than the frame; make it smaller.")
    elif preset.platform == "instagram_reels" and mark.under_instagram(where, preset.size):
        notes.append(
            "The watermark sits where Instagram draws its buttons or caption over a reel; "
            "move it up or left to keep it seen."
        )
    return notes
