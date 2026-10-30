from types import SimpleNamespace

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
    assert not (tmp_path / "work" / "shazam.json").exists()


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
