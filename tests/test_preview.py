import io
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


def test_with_text_on_one_side_only_the_picture_keeps_the_gap_set_to_that_text():
    from agentic_editor.presets import TextBlock
    from agentic_editor.render.video import band_fit, picture_rect

    base = load_preset(PRESETS / "vocal-music-it-9x16.yaml")
    assert compose.picture_anchor(base) == "middle"
    top_only = base.model_copy(
        update={"text_blocks": [b for b in base.text_blocks if b.anchor == "top"]}
    )
    assert compose.picture_anchor(top_only) == "top"
    bottom_only = base.model_copy(
        update={"text_blocks": [b for b in base.text_blocks if b.anchor == "bottom"]}
    )
    assert compose.picture_anchor(bottom_only) == "bottom"
    rows = base.model_copy(
        update={"framing": base.framing.model_copy(update={"picture_rows": (400, 1400)})}
    )
    assert compose.picture_anchor(rows) == "middle"
    assert isinstance(top_only.text_blocks[0], TextBlock)

    band = compose.picture_band(top_only, compose.text_face(get_settings(), None))
    wide = (1920, 1080)
    _, y0, _, y1 = picture_rect(wide, (1080, 1920), 1.0, band, "top")
    assert y0 == band[0] and y1 - y0 == 608
    assert band_fit((1080, 1920), "#000000", 1.0, band, "top")[1].endswith(
        f":(ow-iw)/2:{band[0]}:color=0x000000"
    )
    (_, py0, _, _), _ = preview.picture_place(top_only, wide, band)
    assert py0 == band[0]


def test_pictures_are_drawn_where_the_preset_puts_them_even_partly_off_the_frame(tmp_path):
    from PIL import Image

    from agentic_editor.render import text

    badge = tmp_path / "badge.png"
    Image.new("RGBA", (100, 50), (0, 200, 0, 255)).save(badge)
    pictures = [(badge, 10, 20, 100, 1.0), (badge, 350, 280, 100, 0.5), (badge, -50, 0, 100, 1.0)]
    out = text.draw([], (400, 300), tmp_path / "o.png", None, pictures)
    drawn = Image.open(out)
    assert drawn.getpixel((60, 40)) == (0, 200, 0, 255)
    assert drawn.getpixel((390, 290))[3] in (127, 128)  # half see-through, clipped at the edge
    assert drawn.getpixel((5, 5))[1] == 200  # the left half of one placed off the left edge

    preset = load_preset(PRESETS / "square-1x1.yaml")
    picture = {"file": str(badge), "x": 500, "y": 500, "width": 200}
    preset = type(preset).model_validate(preset.model_dump() | {"images": [picture]})
    still = Image.open(io.BytesIO(preview.render(preset, get_settings())))
    assert still.convert("RGB").getpixel((600, 520))[1] > 150


def test_the_preview_says_what_looks_wrong():
    settings = get_settings()
    preset = load_preset(PRESETS / "vocal-music-it-9x16.yaml")
    assert preview.render_checked(preset, settings)[1] == []

    huge = [b.model_copy(update={"size": 300, "min_size": 300, "text": b.text + "\nmore\nlines"})
            for b in preset.text_blocks]  # fmt: skip
    notes = preview.render_checked(preset.model_copy(update={"text_blocks": huge}), settings)[1]
    assert any("less than a quarter" in n for n in notes)

    narrow = [b.model_copy(update={"max_width": 200}) for b in preset.text_blocks]
    notes = preview.render_checked(preset.model_copy(update={"text_blocks": narrow}), settings)[1]
    assert any(n.startswith("Text 1 is too long") for n in notes)

    gone = {"file": "media/assets/nothing-here.png", "x": 10, "y": 10, "width": 100}
    with_gone = type(preset).model_validate(preset.model_dump() | {"images": [gone]})
    assert any("Picture 1" in n for n in preview.render_checked(with_gone, settings)[1])
