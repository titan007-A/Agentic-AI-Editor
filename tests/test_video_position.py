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


def _between_text(**framing) -> Preset:
    from agentic_editor.presets import OneLiner, TextBlock

    return Preset(
        name="t", label="t", min_duration=1, max_duration=60,
        text_blocks=[TextBlock(text="Above", size=40, anchor="top", offset=300),
                     TextBlock(text="Below", size=40, anchor="bottom", offset=400)],
        one_liner=OneLiner(enabled=True, position="top", offset=150, size=30),
        framing=Framing(mode="none", text_gap_above=20, text_gap_below=20, **framing),
    )  # fmt: skip


def test_ticked_the_captions_and_title_move_along():
    from agentic_editor.render import compose

    moved = compose.moved_layout(_between_text(picture_y=100))
    assert moved.captions.margin == 520 - 100  # bottom captions: nearer the bottom
    assert moved.title_overlay.offset == 300 + 100


def test_ticked_the_text_moves_with_the_video_and_nothing_is_cut():
    from agentic_editor.render import compose
    from agentic_editor.settings import get_settings

    face = compose.text_face(get_settings(), None)
    still = _between_text()
    moved = compose.moved_layout(_between_text(picture_y=100))
    assert [b.offset for b in moved.text_blocks] == [400, 300]  # top down, bottom up = down
    assert moved.one_liner.offset == 250 and moved.framing.picture_y == 0
    before, after = compose.picture_band(still, face), compose.picture_band(moved, face)
    assert (after[0] - before[0], after[1] - before[1]) == (100, 100)
    tall = (1080, 1920)
    a = preview.picture_layout(still, tall, before)[2]
    b = preview.picture_layout(moved, tall, after)[2]
    assert b.y - a.y == 100 and b.height == a.height == b.full  # moved whole, none cut


def test_unticked_the_text_stays_and_the_video_is_cut():
    from agentic_editor.render import compose

    fixed = _between_text(picture_y=100, text_follows_video=False)
    assert compose.moved_layout(fixed) == fixed


def test_with_no_text_to_move_the_video_still_moves_by_itself():
    from agentic_editor.render import compose

    alone = _preset(picture_y=-200)
    assert compose.moved_layout(alone).framing.picture_y == -200
    smart = Preset(name="s", label="s", min_duration=1, max_duration=60,
                   framing=Framing(mode="smart", picture_y=50))  # fmt: skip
    assert compose.moved_layout(smart) == smart


def test_the_preview_lays_out_moved_text_as_the_render_does():
    from agentic_editor.settings import get_settings

    settings = get_settings()
    from agentic_editor.presets import CaptionStyle

    # (captions off: they keep off the picture and the text by their own rule)
    quiet = {"captions": CaptionStyle(enabled=False)}
    still = _between_text(picture_center=False).model_copy(update=quiet)
    still, _ = preview.render_checked(still, settings, "9:16")
    # 96 px: whole JPEG blocks, so the two stills compress alike
    moved, _ = preview.render_checked(
        _between_text(picture_y=96).model_copy(update=quiet), settings, "9:16"
    )
    import io

    a = Image.open(io.BytesIO(still)).convert("L")
    b = Image.open(io.BytesIO(moved)).convert("L")
    # the whole picture, text and all, 96 px lower: rows match once shifted
    top = a.crop((0, 100, 1080, 1700))
    low = b.crop((0, 196, 1080, 1796))
    diff = sum(abs(x - y) for x, y in zip(top.get_flattened_data(), low.get_flattened_data(),
                                           strict=True)) / (1080 * 1600)  # fmt: skip
    assert diff < 3


def _one_liner_only(**framing) -> Preset:
    """A one-liner on top and nothing else holding the picture (a talent-show page)."""
    from agentic_editor.presets import OneLiner

    return Preset(
        name="p", label="p", min_duration=1, max_duration=60,
        one_liner=OneLiner(enabled=True, position="top", offset=412),
        framing=Framing(mode="none", **framing),
    )  # fmt: skip


def test_text_on_one_side_moves_the_picture_whole_not_taller():
    """Moved up, the band used to open down to the frame's bottom: the picture grew."""
    from agentic_editor.render import compose
    from agentic_editor.settings import get_settings

    face = compose.text_face(get_settings(), None)
    still = compose.moved_layout(_one_liner_only(), face)
    before = compose.picture_band(still, face)
    assert before[1] == 1920
    for dy in (-160, -96):
        moved = compose.moved_layout(_one_liner_only(picture_y=dy), face)
        after = compose.picture_band(moved, face)
        assert (after[0] - before[0], after[1] - before[1]) == (dy, dy)
        assert moved.one_liner.offset == 412 + dy and moved.framing.picture_y == 0
        # the picture keeps to the one-liner, as before the move
        assert compose.picture_anchor(moved) == compose.picture_anchor(still) == "top"
        tall, wide = (1080, 1920), (1920, 1080)
        for size in (tall, wide):
            a = compose.shown_rows(still, size, face)
            b = compose.shown_rows(moved, size, face)
            assert (b[0] - a[0], b[1] - a[1]) == (dy, dy)  # same size, dy higher
    # Down, past the frame's bottom: what runs off it is cut, the top follows the text.
    low = compose.moved_layout(_one_liner_only(picture_y=160), face)
    assert compose.picture_band(low, face) == (before[0] + 160, 1920)
    # Without a face to measure with, as before.
    assert compose.moved_layout(_one_liner_only(picture_y=-160)).framing.picture_rows is None


def test_rows_given_with_no_text_keep_the_picture_in_the_middle():
    from agentic_editor.render import compose

    assert compose.picture_anchor(_preset(picture_rows=(430, 1412))) == "middle"


def test_a_centred_video_sits_in_the_middle_whatever_its_shape():
    from agentic_editor.render import compose
    from agentic_editor.settings import get_settings

    face = compose.text_face(get_settings(), None)
    for preset in (_one_liner_only(picture_center=True, picture_y=-300),
                   _between_text(picture_center=True)):  # fmt: skip
        laid = compose.moved_layout(preset, face)
        assert laid.framing.picture_y == 0  # picture_y doesn't apply when centred
        for size in ((1080, 1920), (1920, 1080), (1080, 1350)):
            placed, _ = compose.centered(laid, size, face)
            top, bottom = compose.shown_rows(placed, size, face)
            assert abs((top + bottom) / 2 - 960) <= 2, (size, top, bottom)
            # the text that follows the video keeps its gap to it
            if preset.text_blocks:
                continue
            line = compose.line_rows(placed, face)
            gap = compose.shown_rows(laid, size, face)[0] - compose.line_rows(laid, face)[1]
            assert top - line[1] == pytest.approx(gap)
    # Off: nothing moves.
    plain = _one_liner_only(picture_center=False)
    assert compose.centered(plain, (1080, 1920), face) == (plain, 0)


def test_a_centred_video_with_text_that_stays_moves_by_itself():
    from agentic_editor.render import compose
    from agentic_editor.settings import get_settings

    face = compose.text_face(get_settings(), None)
    preset = _one_liner_only(picture_center=True, text_follows_video=False)
    placed, dy = compose.centered(preset, (1920, 1080), face)
    assert dy and placed.framing.picture_y == dy and placed.one_liner.offset == 412


def test_the_preview_centres_the_stand_in():
    import io

    from agentic_editor.presets import CaptionStyle
    from agentic_editor.settings import get_settings

    quiet = {"captions": CaptionStyle(enabled=False)}
    preset = _one_liner_only(picture_center=True).model_copy(update=quiet)
    shot = Image.open(io.BytesIO(preview.render(preset, get_settings(), "16:9"))).convert("L")
    # The stand-in's floor is bright to its last row: a 16:9 picture centred ends at 1264.
    lit = [y for y in range(700, 1920) if shot.getpixel((540, y)) > 40]
    assert abs(lit[-1] - 1263) <= 4


def test_a_centred_video_is_measured_as_band_framing_shows_it(make_analysis):
    """Band framing cut the picture's edge text away: the shorter picture is what's centred."""
    from agentic_editor import pipeline
    from agentic_editor.edl import Shot
    from agentic_editor.render import compose
    from agentic_editor.settings import get_settings

    settings = get_settings()
    face = compose.text_face(settings, None)
    preset = compose.moved_layout(
        _one_liner_only(picture_center=True, picture_rows=(422, 1500)), face
    )
    analysis = make_analysis([], 9).model_copy(update={"width": 1080, "height": 1920})
    edl = EDL(clips=[Clip(start=0, end=9)], crop_mode="none", picture=(0, 400, 1080, 1092),
              shots=[Shot(start=0, end=9, x=0, width=1080, y=500, height=902)])  # fmt: skip
    target, noted = pipeline.centred(edl, preset, analysis, settings)
    top, bottom = compose.shown_rows(target, (1080, 902), face)
    assert bottom - top == 902 and abs((top + bottom) / 2 - 960) <= 2
    assert "to the middle of the frame" in noted.decisions[-1]
    # No windows (the whole picture shown): measured whole.
    whole = edl.model_copy(update={"shots": [], "picture": None})
    target, _ = pipeline.centred(whole, preset, analysis, settings)
    top, bottom = compose.shown_rows(target, (1080, 1920), face)
    assert abs((top + bottom) / 2 - 960) <= 2


def test_centring_is_the_default_and_a_hand_moved_picture_keeps_its_place():
    from agentic_editor.presets import Framing

    assert Framing().picture_center is True
    assert Framing(picture_y=-160).picture_center is False  # saved before the default
    assert Framing(picture_y=-160, picture_center=True).picture_center is True  # said so
    assert Framing.model_validate({"mode": "none", "picture_y": 0}).picture_center is True
