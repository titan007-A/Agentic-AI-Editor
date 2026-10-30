from types import SimpleNamespace

import pytest

from agentic_editor import ffmpeg, shazam

FELICITA = {"title": "Felicità", "subtitle": "Al Bano & Romina Power"}


def faked(monkeypatch, duration=60.0):
    cut = []

    def run(args):
        cut.append(float(args[args.index("-ss") + 1]))
        open(args[-1], "wb").close()

    monkeypatch.setattr(ffmpeg, "probe", lambda path: SimpleNamespace(duration=duration))
    monkeypatch.setattr(ffmpeg, "run", run)
    return cut


def test_a_recognised_song_is_kept_for_the_next_job(monkeypatch, tmp_path):
    cut = faked(monkeypatch)
    asked = []
    found = shazam.song(
        tmp_path / "v.mp4", tmp_path / "work", lambda c: asked.append(c) or FELICITA
    )
    assert found == ("Felicità", "Al Bano & Romina Power") and cut == [5.0]
    assert not (tmp_path / "work" / "shazam.wav").exists()
    # Regenerated or run again, the source isn't heard twice.
    assert shazam.song(tmp_path / "v.mp4", tmp_path / "work", lambda c: 1 / 0) == found
    assert len(asked) == 1


def test_no_match_hears_the_middle_too_and_isnt_kept(monkeypatch, tmp_path):
    cut = faked(monkeypatch, duration=90.0)
    assert shazam.song(tmp_path / "v.mp4", tmp_path / "work", lambda clip: None) is None
    assert cut == [5.0, 35.0]
    assert not (tmp_path / "work" / "shazam-v2.json").exists()


def test_shazam_out_of_reach_is_no_answer(monkeypatch, tmp_path):
    faked(monkeypatch)

    def down(clip):
        raise TimeoutError

    assert shazam.song(tmp_path / "v.mp4", tmp_path / "work", down) is None


def test_where_the_excerpts_start():
    assert shazam.starts(15.0) == [0.0]
    assert shazam.starts(40.0) == [5.0]  # the middle would hear the same stretch
    assert shazam.starts(90.0) == [5.0, 35.0]


def test_a_track_needs_both_names():
    assert shazam.named(FELICITA) == ("Felicità", "Al Bano & Romina Power")
    assert shazam.named({"title": "Felicità"}) is None
    assert shazam.named(None) is None


MUSICBRAINZ = {
    ("(I Can't Get No) Satisfaction", "The Rolling Stones"): {"i can t get no satisfaction"},
    ("Caruso (Tindari)", "Lucio Dalla"): {"caruso live in tindari"},
    ("Caruso", "Lucio Dalla"): {"caruso", "caruso live"},
}


@pytest.mark.parametrize(
    ("heard", "kept"),
    [
        (("Summer of '69 (Live)", "Bryan Adams"), ("Summer of '69", "Bryan Adams")),
        (
            ("We Will Rock You (Live at Live Aid, Wembley Stadium, 13th July 1985)", "Queen"),
            ("We Will Rock You", "Queen"),
        ),
        (("Payphone (feat. Wiz Khalifa)", "Maroon 5"), ("Payphone", "Maroon 5")),
        (("Bohemian Rhapsody - Remastered 2011", "Queen"), ("Bohemian Rhapsody", "Queen")),
        (
            ("Felicità – Radio Edit", "Al Bano & Romina Power"),
            ("Felicità", "Al Bano & Romina Power"),
        ),
        (("Payphone", "Maroon 5 feat. Wiz Khalifa"), ("Payphone", "Maroon 5")),
        # A bracket that doesn't say what it is: MusicBrainz knows the name with it...
        (
            ("(I Can't Get No) Satisfaction", "The Rolling Stones"),
            ("(I Can't Get No) Satisfaction", "The Rolling Stones"),
        ),
        # ... or only without it.
        (("Caruso (Tindari)", "Lucio Dalla"), ("Caruso", "Lucio Dalla")),
        # Neither, or MusicBrainz out of reach: it stays rather than cut the name.
        (
            ("Sweet Dreams (Are Made of This)", "Eurythmics"),
            ("Sweet Dreams (Are Made of This)", "Eurythmics"),
        ),
    ],
)
def test_only_the_song_and_artist_are_kept(heard, kept):
    def look(song, artist):
        return MUSICBRAINZ.get((song, artist), set())

    assert shazam.names_only(*heard, look) == kept


def test_a_bracket_that_says_what_it_is_goes_without_asking():
    def look(song, artist):
        raise AssertionError("MusicBrainz isn't needed")

    assert shazam.names_only("Caruso (Live in Tindari)", "Lucio Dalla", look)[0] == "Caruso"


def test_musicbrainz_out_of_reach_keeps_the_bracket():
    kept = shazam.names_only("Caruso (Tindari)", "Lucio Dalla", lambda song, artist: None)
    assert kept == ("Caruso (Tindari)", "Lucio Dalla")


def test_musicbrainz_is_asked_for_the_artist_s_titles(monkeypatch):
    import httpx

    monkeypatch.setattr(shazam, "_asked", [-10.0])
    seen = []

    def reply(request):
        seen.append(request)
        titles = [{"title": "(I Can’t Get No) Satisfaction"}, {"title": "Satisfaction"}]
        return httpx.Response(200, json={"recordings": titles})

    found = shazam.titles('Say "Yes"', "The Rolling Stones", httpx.MockTransport(reply))
    assert found == {"i can t get no satisfaction", "satisfaction"}
    query = seen[0].url.params["query"]
    assert query == 'recording:"Say \\"Yes\\"" AND artist:"The Rolling Stones"'
    assert "agentic-editor" in seen[0].headers["user-agent"]
    down = httpx.MockTransport(lambda request: httpx.Response(503))
    assert shazam.titles("Caruso", "Lucio Dalla", down) is None
