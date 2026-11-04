"""Text keeps its end of the frame; the picture makes way for it."""

from agentic_editor.presets import Framing, OneLiner, Preset, TextBlock
from agentic_editor.render import compose, preview
from agentic_editor.settings import get_settings

FACE = compose.text_face(get_settings(), None)
TALL = (480, 854)


def _military(**line) -> Preset:
    """aman's: a one-liner at the top, the page's words at the bottom, the picture between."""
    bottom = TextBlock(text="Sígueme Estos Marines encontraron\nuna solución al Anillo Rojo\n"
                       "de la Muerte", font="Montserrat-Bold", size=47, anchor="bottom",
                       offset=429)  # fmt: skip
    return Preset(
        name="military", label="m", min_duration=1, max_duration=60, text_blocks=[bottom],
        one_liner=OneLiner(**{"enabled": True, "font": "Rubik-Bold", "size": 50,
                              "position": "top", "offset": 260, **line}),
        framing=Framing(mode="none", text_gap_above=0, text_gap_below=16),
    )  # fmt: skip


def test_a_whole_reel_one_liner_keeps_its_rows_and_the_picture_starts_under_it():
    preset = _military()
    line_top, line_bottom = compose.line_rows(preset, FACE)
    assert 200 <= line_top <= 260  # where it is set (its box reaches a little above)
    band = compose.picture_band(preset, FACE)
    assert band[0] >= line_bottom + compose.BANNER_GAP
    (_, y0, _, y1), _ = preview.picture_place(preset, TALL, band)
    assert y0 >= line_bottom + compose.BANNER_GAP and y1 <= band[1]
    notes = preview.render_checked(preset, get_settings(), "9:16")[1]
    assert not any("one-liner" in n for n in notes)


def test_a_bottom_one_liner_keeps_its_rows_at_the_bottom():
    preset = _military(position="bottom", offset=200)
    preset = preset.model_copy(update={"text_blocks": []})
    line_top, _ = compose.line_rows(preset, FACE)
    band = compose.picture_band(preset, FACE)
    assert band[1] <= line_top - compose.BANNER_GAP
    assert compose.picture_anchor(preset) == "bottom"


def test_a_one_liner_for_a_few_seconds_or_off_keeps_nothing():
    assert compose.line_rows(_military(duration=3.0), FACE) is None
    assert compose.line_rows(_military(enabled=False), FACE) is None
    off = _military(enabled=False)
    assert compose.picture_band(off, FACE)[0] == 0  # as before: only the text limits it
