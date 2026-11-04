import shutil
import subprocess

import pytest
from PIL import Image

from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Framing, Preset
from agentic_editor.render import preview
from agentic_editor.render.video import (
    Layers,
    band_fit,
    build_graph,
    picture_cut,
    picture_rect,
    video_band,
    zoom_room,
)

FRAME = (1080, 1920)
WIDE = (1920, 1080)


def _preset(**framing) -> Preset:
    return Preset(
        name="p", label="p", min_duration=1, max_duration=60,
        framing=Framing(mode="none", **framing),
    )  # fmt: skip


def test_left_unmoved_nothing_changes():
    assert band_fit(FRAME, "#000000") == band_fit(FRAME, "#000000", shift=(0, 0))
    assert "clip(" not in "".join(band_fit(FRAME, "#000000", 1.0, (400, 1500), "top"))
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    graph, _ = build_graph(edl, _preset(), WIDE, Layers())
    assert "clip(" not in graph


def test_the_picture_moves_by_what_is_set_and_what_runs_off_is_cut():
    assert picture_rect(WIDE, FRAME) == (0, 656, 1080, 1264)
    assert picture_rect(WIDE, FRAME, shift=(0, -300)) == (0, 356, 1080, 964)
    # past the frame's edge: the part that would leave it is cut, a quarter stays
    assert picture_rect(WIDE, FRAME, shift=(0, 1000)) == (0, 1656, 1080, 1920)
    cut = picture_cut(WIDE, FRAME, shift=(0, 5000))
    assert cut.height == 152 and cut.rect == (0, 1768, 1080, 1920) and cut.cut_top == 0
    up = picture_cut(WIDE, FRAME, shift=(0, -5000))
    assert up.rect == (0, 0, 1080, 152) and up.cut_top == 608 - 152
    # a narrower picture moves sideways too, and odd moves round to even pixels
    assert picture_rect(WIDE, FRAME, 0.6, shift=(101, 0))[0] == 216 + 100
    assert video_band("none", WIDE, FRAME, shift=(0, -300)) == (356, 964)


def test_the_graph_moves_the_picture_by_exact_numbers():
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    graph, _ = build_graph(edl, _preset(picture_y=-300, picture_x=40), WIDE, Layers())
    assert "scale=1080:608:flags=lanczos,pad=1080:1920:0:356:color=0x000000" in graph
    moved, _ = build_graph(edl, _preset(picture_y=1000), WIDE, Layers())
    assert "scale=1080:608:flags=lanczos,crop=1080:264:0:0,pad=1080:1920:0:1656" in moved


def test_the_zoom_keeps_off_the_edge_of_a_moved_picture():
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    middle = zoom_room(edl, _preset(), WIDE, Layers())[0][2]
    moved = zoom_room(edl, _preset(picture_y=500), WIDE, Layers())[0][2]
    assert middle == pytest.approx(1.0) and moved == pytest.approx(1.0)
    small = _preset(picture_width=0.5)
    room = zoom_room(edl, small, WIDE, Layers())[0][2]
    shifted = zoom_room(edl, small.model_copy(update={"framing": small.framing.model_copy(
        update={"picture_y": 400})}), WIDE, Layers())[0][2]  # fmt: skip
    assert shifted < room


def test_the_preview_puts_the_picture_where_the_render_does():
    preset = _preset(picture_y=-250)
    rect, _ = preview.picture_place(preset, WIDE, None)
    assert rect == picture_rect(WIDE, FRAME, shift=(0, -250))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize(
    ("shift", "width", "band", "anchor"),
    [((0, -300), 1.0, None, "middle"), ((60, 5000), 0.6, None, "middle"),
     ((-90, 120), 0.7, (400, 1500), "top"), ((0, -80), 1.0, (300, 1700), "bottom"),
     ((0, 400), 1.0, (400, 1100), "middle")],
)  # fmt: skip
def test_ffmpeg_draws_the_picture_where_picture_rect_says(tmp_path, shift, width, band, anchor):
    fit = band_fit(FRAME, "#000000", width, band, anchor, shift, WIDE)
    out = tmp_path / "f.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=white:s={WIDE[0]}x{WIDE[1]}",
         "-vf", ",".join(fit), "-frames:v", "1", str(out)],
        check=True,
    )  # fmt: skip
    seen = Image.open(out).convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()
    assert seen == picture_rect(WIDE, FRAME, width, band, anchor, shift)


def test_between_text_a_moved_picture_is_cut_where_it_meets_the_text():
    band = (400, 1500)
    tall = (1080, 1920)  # fills the band: it can't move without being cut
    whole = picture_cut(tall, FRAME, 1.0, band, "middle")
    assert (whole.y, whole.height) == (400, 1100)
    down = picture_cut(tall, FRAME, 1.0, band, "middle", (0, 200))
    assert down.rect[1] == 600 and down.rect[3] == 1500 and down.cut_top == 0  # bottom cut off
    up = picture_cut(tall, FRAME, 1.0, band, "middle", (0, -200))
    assert up.rect == (whole.x, 400, whole.x + whole.width, 1300) and up.cut_top == 200
    # the text's rows are never covered
    assert video_band("none", tall, FRAME, 1.0, band, "middle", (0, 200)) == (600.0, 1500.0)
