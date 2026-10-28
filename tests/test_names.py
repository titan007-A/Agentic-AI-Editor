import pytest

from agentic_editor import names
from agentic_editor.presets import load_presets
from agentic_editor.settings import ROOT


@pytest.mark.parametrize(
    ("title", "song", "artist"),
    [
        ("Bohemian Rhapsody - Queen", "Bohemian Rhapsody", "Queen"),
        ("Felicità — Al Bano & Romina Power", "Felicità", "Al Bano & Romina Power"),
        ("Empire State of Mind – Jay-Z", "Empire State of Mind", "Jay-Z"),
        ("07. Without Me (2) - Eminem", "Without Me", "Eminem"),
        ("Take Me Home (Live 1982) - John Denver", "Take Me Home", "John Denver"),
        (
            "Sweet Dreams (Are Made of This) - Eurythmics",
            "Sweet Dreams (Are Made of This)",
            "Eurythmics",
        ),
        ("99 Problems - Jay-Z", "99 Problems", "Jay-Z"),
        ("1999 - Prince", "1999", "Prince"),
    ],
)
def test_song_and_artist_come_from_the_name(title, song, artist):
    assert names.song_and_artist(title) == (song, artist)


def test_a_name_without_a_dash_is_refused_with_how_to_fix_it():
    with pytest.raises(names.UnnamedSong, match="Song - Artist"):
        names.song_and_artist("IMG_4379")
    with pytest.raises(names.UnnamedSong, match="nothing on one side"):
        names.song_and_artist("Bohemian Rhapsody - ")


def test_the_voicehq_preset_names_the_song_and_artist():
    preset = load_presets(ROOT / "presets")["voicehq-9x16"]
    filled = names.fill(preset, "Felicità — Al Bano & Romina Power")
    assert filled.text_blocks[1].text == "Song: Felicità\nArtist: Al Bano & Romina Power"
    assert filled.text_blocks[0].text == preset.text_blocks[0].text
    assert preset.text_blocks[1].text == "Song: {song}\nArtist: {artist}"  # left as it was


def test_a_preset_without_the_names_doesnt_need_them():
    preset = load_presets(ROOT / "presets")["vocal-music-it-9x16"]
    assert names.fill(preset, "IMG_4379") is preset
