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


def test_a_preset_names_the_song_when_its_text_has_it():
    preset = load_presets(ROOT / "presets")["voicehq-9x16"]
    assert names.names_song(preset)
    assert not names.names_song(load_presets(ROOT / "presets")["vocal-music-it-9x16"])


def test_a_link_s_song_and_artist_come_from_lines_in_the_notes():
    # An Instagram link is called "Video by <account>" and can't be renamed.
    preset = load_presets(ROOT / "presets")["voicehq-9x16"]
    assert names.wanted(preset) == {"song", "artist"}
    assert names.wanted(load_presets(ROOT / "presets")["vocal-music-it-9x16"]) == set()
    notes = "song: Felicità\nArtist: Al Bano & Romina Power\nstart at the chorus"
    given, rest = names.from_notes(notes, names.wanted(preset))
    assert given == {"song": "Felicità", "artist": "Al Bano & Romina Power"}
    assert rest == "start at the chorus"
    filled = names.fill(preset, "Video by illusive.paradox", given)
    assert filled.text_blocks[1].text == "Song: Felicità\nArtist: Al Bano & Romina Power"
    # Only one of them in the notes: the name gives the other, and the notes win.
    assert names.song_and_artist("Felicita - Al Bano", {"artist": "Al Bano & Romina Power"}) == (
        "Felicita",
        "Al Bano & Romina Power",
    )
    # Neither: the reel says both ways to give them.
    with pytest.raises(names.UnnamedSong, match=r"Song - Artist' or add lines 'song: \.\.\.'"):
        names.fill(preset, "Video by illusive.paradox", {"song": "Felicità"})


def stories(text: str):
    preset = load_presets(ROOT / "presets")["voicehq-9x16"]
    block = preset.text_blocks[1].model_copy(update={"text": text})
    return preset.model_copy(update={"text_blocks": [preset.text_blocks[0], block]})


def test_the_year_comes_from_the_video_s_name():
    preset = stories("{song} ({year}) – {artist}")
    filled = names.fill(preset, "Perdóname (1980) - Camilo Sesto")
    assert filled.text_blocks[1].text == "Perdóname (1980) – Camilo Sesto"
    # The notes win over the name, and without either the reel says how to give it.
    given = names.fill(preset, "Perdóname (1980) - Camilo Sesto", {"year": "1981"})
    assert given.text_blocks[1].text == "Perdóname (1981) – Camilo Sesto"
    with pytest.raises(names.MissingField, match=r"'year: \.\.\.'.*Song \(1980\) - Artist"):
        names.fill(preset, "Perdóname - Camilo Sesto")


def test_the_preset_s_own_words_come_from_lines_in_the_notes():
    preset = stories("Día {day} : Canción : {song_number}")
    assert names.fields(preset) == {"day", "song_number"}
    notes = "start where she sings the chorus\nDay: 103\nsong number: 5"
    given, rest = names.from_notes(notes, names.fields(preset))
    assert given == {"day": "103", "song_number": "5"}
    assert rest == "start where she sings the chorus"
    filled = names.fill(preset, "IMG_5071", given)
    assert filled.text_blocks[1].text == "Día 103 : Canción : 5"
    with pytest.raises(names.MissingField, match=r"'song number: \.\.\.'"):
        names.fill(preset, "IMG_5071", {"day": "103"})
