import io
import shutil
import subprocess

import pytest
from PIL import Image, ImageChops

from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import OwnWatermark, Preset
from agentic_editor.render import mark, preview
from agentic_editor.render.video import Layers, build_graph
from agentic_editor.settings import get_settings

FRAME = (1080, 1920)
FONTS = get_settings().fonts_dir


def _preset(**own) -> Preset:
    return Preset(
        name="m", label="m", min_duration=1, max_duration=60,
        watermark=OwnWatermark(enabled=True, **own),
    )  # fmt: skip


def test_the_position_is_a_share_of_the_room_beside_the_mark():
    at = OwnWatermark(x=0, y=0)
    assert mark.box(at, (200, 50), FRAME) == (0, 0, 200, 50)
    at = OwnWatermark(x=1, y=1)
    assert mark.box(at, (200, 50), FRAME) == (880, 1870, 1080, 1920)
    at = OwnWatermark(x=0.5, y=0.5)
    assert mark.box(at, (200, 50), FRAME) == (440, 935, 640, 985)


def test_words_are_drawn_at_the_opacity_set(tmp_path):
    own = OwnWatermark(enabled=True, text="@oldsongs", opacity=0.5, outline=0)
    image = mark.drawn(own, FONTS, None)
    assert max(image.getchannel("A").get_flattened_data()) in (127, 128)
    png, where = mark.layer(own, FRAME, FONTS, None, tmp_path / "w.png")
    full = Image.open(png)
    assert full.size == FRAME
    inside = full.crop(where).getchannel("A")
    assert max(inside.get_flattened_data()) > 100
    assert full.getbbox() is not None and full.getbbox()[0] >= where[0]


def test_a_picture_goes_above_the_words(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(logo)
    own = OwnWatermark(enabled=True, text="@page", width=120, opacity=1)
    image = mark.drawn(own, FONTS, logo)
    assert image.width >= 120 and image.height > 120
    assert image.getpixel((image.width // 2, 10))[:3] == (255, 0, 0)
    with pytest.raises(ValueError):
        mark.drawn(OwnWatermark(enabled=True), FONTS, None)


def test_the_mark_is_laid_over_everything_last():
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="center")
    graph, _ = build_graph(
        edl, _preset(text="@a"), (1920, 1080), Layers(text_overlay=2, own_mark=3)
    )
    assert graph.index("[2:v]overlay") < graph.index("[3:v]overlay=0:0[marked]")
    assert "[marked]format=yuv420p[out]" in graph
    plain, _ = build_graph(edl, _preset(text="@a"), (1920, 1080), Layers())
    assert "marked" not in plain


def test_the_preview_shows_the_mark_where_it_is_set_and_warns_under_instagram():
    settings = get_settings()
    top = _preset(text="@oldsongs", x=0.5, y=0.05, color="#FF0000", opacity=1)
    still, notes = preview.render_checked(top, settings, "9:16")
    assert notes == []
    image = Image.open(io.BytesIO(still)).convert("RGB")
    drawn = mark.drawn(top.watermark, settings.fonts_dir, None)
    x0, y0, x1, y1 = mark.box(top.watermark, drawn.size, FRAME)
    red = [p for p in image.crop((x0, y0, x1, y1)).get_flattened_data() if p[0] > 180 and p[1] < 80]
    assert len(red) > 200

    low = _preset(text="@oldsongs", x=0.5, y=1)
    assert any("Instagram" in n for n in preview.render_checked(low, settings)[1])
    empty = _preset()
    assert any("no words or picture" in n for n in preview.render_checked(empty, settings)[1])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_render_puts_the_mark_on_the_pixels_the_preview_shows(tmp_path):
    """The same PNG over a grey frame, laid by ffmpeg as a render lays it and pasted as
    the preview pastes it, comes out the same, pixel for pixel within encoding error."""
    own = OwnWatermark(enabled=True, text="@oldsongs", size=80, color="#FFDD00", x=0.8, y=0.3)
    png, where = mark.layer(own, FRAME, FONTS, None, tmp_path / "w.png")
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x404040:s={FRAME[0]}x{FRAME[1]}",
         "-i", str(png), "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p",
         "-frames:v", "1", "-pix_fmt", "rgb24", str(frame)],
        check=True,
    )  # fmt: skip
    rendered = Image.open(frame).convert("RGB")
    pasted = Image.new("RGB", FRAME, (64, 64, 64))
    with Image.open(png) as layer:
        pasted.paste(layer, (0, 0), layer)

    region = ImageChops.difference(rendered.crop(where), pasted.crop(where)).convert("L")
    off = [d for d in region.get_flattened_data() if d > 16]
    assert len(off) < 0.02 * region.width * region.height
    # and the mark is there at all, not a grey frame matching a grey frame
    lit = ImageChops.difference(rendered.crop(where), Image.new("RGB", region.size, (64,) * 3))
    assert sum(d > 60 for d in lit.convert("L").get_flattened_data()) > 500
