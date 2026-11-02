from pathlib import Path

from agentic_editor.presets import load_preset
from agentic_editor.render import compose, preview
from agentic_editor.settings import get_settings

PRESETS = Path(__file__).parent.parent / "presets"


def test_a_tall_picture_fills_the_band_between_the_text_and_a_wide_one_sits_in_it():
    preset = load_preset(PRESETS / "vocal-music-it-9x16.yaml")
    band = compose.picture_band(preset, compose.text_face(get_settings(), None))
    (x0, y0, x1, y1), _ = preview.picture_place(preset, preview.SHAPES["9:16"], band)
    assert (x0, x1) == (0, 1080) and (y0, y1) == band
    (x0, y0, x1, y1), _ = preview.picture_place(preset, preview.SHAPES["16:9"], band)
    assert (x0, x1) == (0, 1080) and band[0] < y0 < y1 < band[1]


def test_cropping_modes_fill_the_frame():
    preset = load_preset(PRESETS / "shorts-9x16.yaml")
    rect, shown = preview.picture_place(preset, preview.SHAPES["16:9"], None)
    assert rect == (0, 0, 1080, 1920) and shown == (1080, 1920)


def test_placeholders_get_sample_words():
    assert preview.sample_text("Song: {song}\nDay {day} {unknown_thing}") == (
        "Song: Bohemian Rhapsody\nDay 103 unknown thing"
    )
