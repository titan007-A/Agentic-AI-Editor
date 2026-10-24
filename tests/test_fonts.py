import shutil

import pytest

from agentic_editor.edl import Cue
from agentic_editor.presets import CaptionStyle
from agentic_editor.render import fonts
from agentic_editor.render.captions import build_ass
from agentic_editor.render.text import Face, draw_banner
from agentic_editor.settings import ROOT

ANTON = ROOT / "fonts" / "Anton-Regular.ttf"
BOLD = ROOT / "fonts" / "OpenSans-ExtraBold.ttf"
REGULAR = ROOT / "fonts" / "OpenSans-Regular.ttf"
DEVANAGARI = set(range(0x0900, 0x0980))


@pytest.fixture
def downloads(monkeypatch):
    """Stand-in Noto downloads: a copy of a bundled font, made to claim the scripts its
    name says, so nothing is fetched from the network."""
    fetched = []
    claims = {"Devanagari": DEVANAGARI, "JP": {0x6F22, 0x5B57}, "SC": {0x6F22, 0x5B57}}

    def download(url, dest):
        fetched.append(url)
        shutil.copyfile(REGULAR, dest)

    real = fonts.coverage

    def coverage(path):
        extra = next((set(c) for name, c in claims.items() if name in path.name), set())
        return real(path) | extra

    monkeypatch.setattr(fonts, "_download", download)
    monkeypatch.setattr(fonts, "coverage", coverage)
    return fetched


def test_the_character_map_says_what_a_font_can_draw():
    anton = fonts.coverage(ANTON)
    assert ord("A") in anton and ord("é") in anton
    assert ord("न") not in anton and ord("П") not in anton  # Anton has no Cyrillic
    assert ord("П") in fonts.coverage(BOLD)


def test_latin_text_never_downloads_anything(downloads, tmp_path):
    assert fonts.chain(ANTON, ["Ça va? ÑANDÚ — 100%"], tmp_path) == [ANTON]
    assert downloads == []


def test_a_script_the_font_lacks_gets_its_noto_font_once(downloads, tmp_path):
    chain = fonts.chain(ANTON, ["नमस्ते WORLD"], tmp_path)
    assert [p.name for p in chain] == ["Anton-Regular.ttf", "NotoSansDevanagari-Bold.ttf"]
    assert downloads == [fonts.NOTO_URL.format(name="NotoSansDevanagari", weight="Bold")]
    fonts.chain(ANTON, ["दुनिया"], tmp_path)
    assert len(downloads) == 1  # kept in the models folder
    # Open Sans Regular is a light face, so its Noto partner is too.
    regular = fonts.chain(REGULAR, ["नमस्ते"], tmp_path / "light")
    assert regular[1].name == "NotoSansDevanagari-Regular.ttf"


def test_chinese_characters_follow_the_caption_language(downloads, tmp_path):
    assert fonts.chain(BOLD, ["漢字"], tmp_path, "Japanese")[1].name == "NotoSansJP-Bold.otf"
    assert fonts.chain(BOLD, ["漢字"], tmp_path, "zh")[1].name == "NotoSansSC-Bold.otf"


def test_text_no_font_can_draw_fails_loudly_instead_of_drawing_boxes(downloads, tmp_path):
    with pytest.raises(fonts.MissingGlyphs, match=r"U\+E000"):
        fonts.chain(ANTON, ["hello "], tmp_path)


def test_a_failed_download_fails_loudly(monkeypatch, tmp_path):
    def offline(url, dest):
        raise OSError("no network")

    monkeypatch.setattr(fonts, "_download", offline)
    with pytest.raises(fonts.MissingGlyphs, match="couldn't download"):
        fonts.chain(ANTON, ["नमस्ते"], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_text_is_split_into_runs_by_the_font_that_draws_it():
    runs = fonts.runs("HI ПРИВЕТ МИР!", [ANTON, BOLD])
    assert runs == [("HI ", ANTON), ("ПРИВЕТ МИР", BOLD), ("!", ANTON)]


def test_captions_switch_font_for_what_the_style_font_cant_draw():
    cues = [Cue(start=0, end=1, text="Привет мир")]
    ass = build_ass(cues, CaptionStyle(), (1080, 1920), "Anton", font_chain=[ANTON, BOLD])
    assert r"{\fnOpen Sans ExtraBold\b1}ПРИВЕТ МИР{\fnAnton\b0}" in ass


def test_overlays_draw_with_the_fallback_font_not_empty_boxes(tmp_path):
    import numpy as np
    from PIL import Image

    def drawn(face):
        draw_banner("ПРИВЕТ", face, 72, "#FFFFFF", None, (1080, 400), 100, tmp_path / "b.png")
        ink = np.array(Image.open(tmp_path / "b.png"))[..., 3] > 0
        rows, cols = np.nonzero(ink.any(axis=1))[0], np.nonzero(ink.any(axis=0))[0]
        return ink[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]

    boxes, letters = drawn(Face([ANTON])), drawn(Face([ANTON, BOLD]))
    assert boxes.shape != letters.shape or not np.array_equal(boxes, letters)
    # The letters are Open Sans's own, not Anton's empty boxes.
    assert np.array_equal(letters, drawn(Face([BOLD])))
