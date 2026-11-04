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


def test_the_picture_moves_by_what_is_set_and_never_leaves_the_frame():
    x0, y0, x1, y1 = picture_rect(WIDE, FRAME)
    assert (x0, y0, x1, y1) == (0, 656, 1080, 1264)
    assert picture_rect(WIDE, FRAME, shift=(0, -300)) == (0, 356, 1080, 964)
    assert picture_rect(WIDE, FRAME, shift=(0, 5000)) == (0, 1312, 1080, 1920)
    assert picture_rect(WIDE, FRAME, shift=(0, -5000))[1] == 0
    # a narrower picture moves sideways too, and odd moves round to even pixels
    assert picture_rect(WIDE, FRAME, 0.6, shift=(101, 0))[0] == 216 + 100
    band = video_band("none", WIDE, FRAME, shift=(0, -300))
    assert band == pytest.approx((356, 964), abs=1)
    assert video_band("none", WIDE, FRAME, shift=(0, 9999)) == pytest.approx((1312, 1920), abs=1)


def test_the_graph_moves_the_picture_in_its_padding():
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    graph, _ = build_graph(edl, _preset(picture_y=-300, picture_x=40), WIDE, Layers())
    assert (
        "pad=1080:1920:'clip(trunc((ow-iw)/2)+40,0,ow-iw)':'clip(trunc((oh-ih)/2)-300,0,1920-ih)'"
        in graph
    )


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
     ((-90, 120), 0.7, (400, 1500), "top"), ((0, -80), 1.0, (300, 1700), "bottom")],
)  # fmt: skip
def test_ffmpeg_draws_the_picture_where_picture_rect_says(tmp_path, shift, width, band, anchor):
    fit = band_fit(FRAME, "#000000", width, band, anchor, shift)
    out = tmp_path / "f.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=white:s={WIDE[0]}x{WIDE[1]}",
         "-vf", ",".join(fit), "-frames:v", "1", str(out)],
        check=True,
    )  # fmt: skip
    seen = Image.open(out).convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()
    # ffmpeg rounds the scaled height its own way, a pixel either side (unmoved too)
    want = picture_rect(WIDE, FRAME, width, band, anchor, shift)
    assert all(abs(a - b) <= 1 for a, b in zip(seen, want, strict=True)), (seen, want)
    assert seen[0] == want[0] and (seen[1] == want[1] or seen[3] == want[3])


def test_between_text_the_picture_moves_only_inside_the_band_so_the_text_stays():
    band = (400, 1500)
    assert picture_rect(WIDE, FRAME, 1.0, band, "top", (0, 5000)) == (0, 892, 1080, 1500)
    assert picture_rect(WIDE, FRAME, 1.0, band, "bottom", (0, -5000))[1] == 400
    assert video_band("none", WIDE, FRAME, 1.0, band, "middle", (0, 5000)) == pytest.approx(
        (892, 1500), abs=1
    )
