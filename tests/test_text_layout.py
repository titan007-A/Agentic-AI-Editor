from agentic_editor.presets import TextBlock
from agentic_editor.render.text import EDGE_MARGIN, font_family, font_file, ink_extent, place
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


def test_a_long_one_liner_is_split_into_two_even_lines():
    from agentic_editor.render.text import balance
    from agentic_editor.settings import get_settings

    font = get_settings().fonts_dir / "OpenSans-ExtraBold.ttf"
    text = "The voice that made a whole stadium go completely silent"
    first, second = balance(text, font, 64, 900).split("\n")
    assert abs(len(first) - len(second)) < 15
    assert balance("Short line", font, 64, 900) == "Short line"


def test_captions_ask_libass_for_the_weight_by_its_own_family_name():
    assert font_family(font_file(FONTS, "OpenSans-ExtraBold")) == "Open Sans ExtraBold"
    assert font_family(font_file(FONTS, "OpenSans-Regular")) == "Open Sans"
    assert font_family(font_file(FONTS, "Anton-Regular")) == "Anton"


def test_captions_are_measured_at_the_size_libass_draws_them():
    from agentic_editor.render.text import caption_font

    # libass draws Anton at size 84 about 396 px wide for this line; Pillow's own size 84
    # is 686 px, which split and shrank captions that fit.
    width = caption_font(font_file(FONTS, "Anton-Regular"), 84).getlength("THE QUICK BROWN FOX")
    assert 380 < width < 410


def test_a_caption_holding_a_word_too_wide_for_the_frame_is_drawn_smaller():
    from agentic_editor.edl import Cue
    from agentic_editor.render.compose import caption_scales

    cues = [Cue(start=0, end=1, text="short"), Cue(start=1, end=2, text="x" * 30)]
    scales = caption_scales(cues, lambda line: 40.0 * len(line), 950, pop=False)
    assert scales[0] == 1.0 and 40.0 * 30 * scales[1] <= 950


def test_a_banner_reserves_every_row_it_draws_box_included(tmp_path):
    import numpy as np
    from PIL import Image

    from agentic_editor.render.text import banner_extent, draw_banner

    font = font_file(FONTS, "Anton-Regular")
    words = "The voice that made\nthe stadium go silent"
    above, below = banner_extent(words, font, 72, "#000000", 1080)
    draw_banner(words, font, 72, "#FFFFFF", "#000000", FRAME, 300, tmp_path / "b.png")
    rows = np.nonzero(np.array(Image.open(tmp_path / "b.png"))[..., 3].any(axis=1))[0]
    # The old estimate, 1.3 x size per line, ended 41 px above the box's bottom.
    assert 300 + above - 1 <= rows[0] and rows[-1] <= 300 + below + 1
    assert below - above > 1.3 * 72 * 2 + 30


def test_a_text_block_band_is_where_its_ink_is(tmp_path):
    import numpy as np
    from PIL import Image

    from agentic_editor.render.text import draw

    placed = place(block(), font_file(FONTS, "OpenSans-ExtraBold"), FRAME, None, None)
    draw([placed], FRAME, tmp_path / "t.png")
    rows = np.nonzero(np.array(Image.open(tmp_path / "t.png"))[..., 3].any(axis=1))[0]
    top, bottom = placed.band
    assert abs(rows[0] - top) <= 1 and abs(rows[-1] + 1 - bottom) <= 1
