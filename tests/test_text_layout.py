from agentic_editor.presets import TextBlock
from agentic_editor.render.text import EDGE_MARGIN, font_file, ink_extent, place
from agentic_editor.settings import ROOT

FONTS = ROOT / "fonts"
FRAME = (1080, 1920)


def block(**overrides) -> TextBlock:
    values = {
        "text": "Follow us for more\niconic raw vocals",
        "size": 60,
        "anchor": "bottom",
        "offset": 470,
    }
    return TextBlock(**{**values, **overrides})


def ink_top(placed):
    return placed.top + ink_extent(placed.lines, placed.font, placed.size, 1.0)[0]


def test_block_stays_on_its_anchor_when_video_leaves_room():
    path = font_file(FONTS, "OpenSans-ExtraBold")
    placed = place(block(), path, FRAME, video_band=(656, 1264), gap=130)
    assert round(ink_top(placed)) == 1920 - 470


def test_bottom_block_is_pushed_below_a_tall_video():
    path = font_file(FONTS, "OpenSans-ExtraBold")
    placed = place(block(), path, FRAME, video_band=(400, 1500), gap=100)
    assert round(ink_top(placed)) == 1600


def test_top_block_shrinks_no_further_than_allowed():
    path = font_file(FONTS, "OpenSans-ExtraBold")
    top = block(anchor="top", offset=300, size=60, max_shrink=0.9)
    placed = place(top, path, FRAME, video_band=(120, 1700), gap=0)
    assert placed.size == 54
    assert round(ink_top(placed)) == EDGE_MARGIN


def test_block_that_cannot_clear_the_video_stays_inside_the_frame():
    path = font_file(FONTS, "OpenSans-ExtraBold")
    placed = place(block(max_shrink=0.5), path, FRAME, video_band=(100, 1820), gap=100)
    ink_bottom = placed.top + ink_extent(placed.lines, placed.font, placed.size, 1.0)[1]
    assert round(ink_bottom) == 1920 - EDGE_MARGIN


def test_long_lines_shrink_to_fit_width():
    path = font_file(FONTS, "OpenSans-ExtraBold")
    placed = place(block(text="x" * 80, max_width=900), path, FRAME, None, None)
    assert placed.size < 60
