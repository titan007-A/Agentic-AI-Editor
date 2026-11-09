"""How a song's caption is written when the first way finds nothing (caption_about_song)."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agentic_editor import caption, jobs
from agentic_editor.presets import PostCaption

WIKI = caption.Sources([caption.Article("Bohemian Rhapsody", "u", "A song by Queen.")], [])
ARTIST = caption.Sources([caption.Article("Juvenile (rapper)", "u", "An American rapper.")], [])


class Router:
    def planner(self):
        return nullcontext(SimpleNamespace(context=None))


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Wikipedia knows `songs`, the artist pages and web know `elsewhere`, Shazam hears
    `heard`, the post's text names `read`; it records what was written about."""
    state = SimpleNamespace(
        songs={}, elsewhere={}, heard=None, read=None, asked_shazam=0, saved=[], wrote=[]
    )

    def identify(llm, title, description, headline):
        if state.read is None:
            raise caption.CaptionError("the video doesn't say which song it is")
        return state.read

    def gather(song, artist, language, cache, transport=None):
        if (song, artist) in state.songs:
            return state.songs[(song, artist)]
        raise caption.CaptionError(f"Wikipedia has no article on “{song}”")

    def elsewhere(song, artist, language, cache, transport=None, find=None):
        if (song, artist) in state.elsewhere:
            return state.elsewhere[(song, artist)]
        raise caption.CaptionError(f"nothing found on “{song}”")

    def heard(source_ref, settings):
        state.asked_shazam += 1
        return state.heard

    def write(llm, song, artist, sources, *args, **kwargs):
        state.wrote.append((song, artist, [a.title for a in sources.articles]))
        return caption.Written(f"about {song}", [], [])

    def from_video(*args):
        state.wrote.append(("video",))
        return caption.Written("about the video", [], [])

    monkeypatch.setattr(caption, "identify", identify)
    monkeypatch.setattr(caption, "gather", gather)
    monkeypatch.setattr(caption, "elsewhere", elsewhere)
    monkeypatch.setattr(caption, "write", write)
    monkeypatch.setattr(jobs, "heard_song", heard)
    monkeypatch.setattr(jobs, "caption_from_video", from_video)
    settings = SimpleNamespace(cache_dir=tmp_path)

    def run(named=("", "")):
        return jobs.caption_about_song(
            Router(), settings, named, ("t", "d", "h"), PostCaption(), "src", {}, tmp_path,
            lambda song, artist: state.saved.append((song, artist)),
        )  # fmt: skip

    state.run = run
    return state


def test_a_song_wikipedia_knows_is_written_from_its_article_without_asking_shazam(world):
    world.read = ("Bohemian Rhapsody", "Queen")
    world.songs[world.read] = WIKI
    written = world.run()
    assert written.text == "about Bohemian Rhapsody" and written.notes == []
    assert world.asked_shazam == 0 and world.saved == [("Bohemian Rhapsody", "Queen")]


def test_a_name_wikipedia_finds_only_tidied_is_saved_tidied(world):
    world.read = ("Ojos Verdes", "nicki.nicole")
    world.songs[("Ojos Verdes", "nicki nicole")] = WIKI
    world.run()
    assert world.saved == [("Ojos Verdes", "nicki nicole")]


def test_a_wrong_name_from_the_post_gives_way_to_what_shazam_hears(world):
    world.read = ("bohemia", "freddy")
    world.heard = ("Bohemian Rhapsody", "Queen")
    world.songs[world.heard] = WIKI
    written = world.run()
    assert written.text == "about Bohemian Rhapsody" and world.saved == [world.heard]


def test_an_unnamed_song_is_named_by_shazam(world):
    world.heard = ("Bohemian Rhapsody", "Queen")
    world.songs[world.heard] = WIKI
    assert world.run().text == "about Bohemian Rhapsody"


def test_a_song_without_an_article_is_written_from_the_artist_and_says_so(world):
    world.read = ("Back That Thang Up", "Juvenile")
    world.elsewhere[world.read] = ARTIST
    written = world.run()
    assert world.wrote == [("Back That Thang Up", "Juvenile", ["Juvenile (rapper)"])]
    assert written.notes == [
        "Wikipedia has no article on “Back That Thang Up”: written from Juvenile (rapper). "
        "Check it before posting."
    ]


def test_shazam_s_name_is_the_one_looked_up_elsewhere_first(world):
    world.read = ("Tour_En_Vivo.mp3", "Emilia")
    world.heard = ("La_Original.mp3", "Emilia")  # neither has an article
    world.elsewhere[world.read] = ARTIST
    world.elsewhere[world.heard] = caption.Sources([caption.Article("Emilia", "u", "x")], [])
    world.run()
    assert world.wrote[0][:2] == world.heard


def test_nothing_on_the_song_anywhere_is_written_about_from_the_video(world):
    world.read = ("Unknown", "Nobody")
    written = world.run()
    assert world.wrote == [("video",)]
    assert written.notes == [
        "nothing found on “Unknown” by Nobody: written about what the video shows"
    ]


def test_a_song_a_person_named_is_never_swapped_for_shazam_s(world):
    world.heard = ("Something Else", "Other")
    world.songs[world.heard] = WIKI
    world.elsewhere[("Piano Man", "Billy Joel")] = ARTIST
    written = world.run(named=("Piano Man", "Billy Joel"))
    assert world.asked_shazam == 0 and world.wrote[0][:2] == ("Piano Man", "Billy Joel")
    assert written.notes[0].startswith("Wikipedia has no article on “Piano Man”")
