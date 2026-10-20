import pytest
from pydantic import ValidationError

from agentic_editor.presets import Preset, load_presets, save_preset
from agentic_editor.settings import ROOT


def minimal(**overrides) -> dict:
    return {"name": "test", "label": "Test", "min_duration": 10, "max_duration": 30, **overrides}


def test_bundled_presets_load():
    presets = load_presets(ROOT / "presets")
    assert {"shorts-9x16", "podcast-9x16", "highlight-16x9", "square-1x1"} <= presets.keys()
    assert presets["vocal-music-9x16"].audio.source == "vocals"


def test_bundled_presets_only_use_bundled_fonts():
    fonts = {p.stem for p in (ROOT / "fonts").glob("*.ttf")}
    for preset in load_presets(ROOT / "presets").values():
        used = {preset.captions.font, preset.title_overlay.font}
        used |= {block.font for block in preset.text_blocks}
        assert used <= fonts, preset.name


def test_resolution_must_match_aspect_ratio():
    with pytest.raises(ValidationError, match="does not match"):
        Preset.model_validate(minimal(aspect_ratio="9:16", resolution="1920x1080"))


def test_min_duration_cannot_exceed_max():
    with pytest.raises(ValidationError, match="shorter|longer"):
        Preset.model_validate(minimal(min_duration=40, max_duration=30))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Preset.model_validate(minimal(caption_colour="#FFFFFF"))


def test_colors_must_be_hex():
    with pytest.raises(ValidationError):
        Preset.model_validate(minimal(captions={"color": "white"}))


def test_name_must_match_file_name(tmp_path):
    (tmp_path / "other.yaml").write_text("name: test\nlabel: x\nmin_duration: 1\nmax_duration: 2\n")
    with pytest.raises(ValueError, match="must match"):
        load_presets(tmp_path)


def test_save_round_trips(tmp_path):
    preset = Preset.model_validate(minimal(text_blocks=[
        {"text": "Línea uno\nline two", "size": 40, "anchor": "top", "offset": 300}
    ]))
    save_preset(preset, tmp_path)
    assert load_presets(tmp_path)["test"] == preset
