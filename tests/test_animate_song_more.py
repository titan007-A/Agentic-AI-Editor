"""Song story reels made longer: parts about 30 s, and a line's words only over its singing
(never over the music after it), the part never longer than it may be. Every lyric here is
made up."""

import numpy as np
import pytest

from agentic_editor.animate import songhook
from agentic_editor.animate.plan import (
    HookChoice,
)
from agentic_editor.transcript import Transcript, Word
from tests import test_api
from tests.test_animate import studio_env
from tests.test_animate_song import (
    LRC,
    _heat,
    _song_wav,
    needs_ffmpeg,
    song_finder_stand_ins,
)

env = test_api.env  # the editor with a signed-in admin (a fixture)

# Made-up English lines, a few seconds apart.
INVENTED = [
    "Paper boats are sailing down the gutter",
    "Seven lanterns blinking on the pier",
    "The kettle hums a tune for nobody",
    "My shoelaces remember every road",
    "Grandma folds the evening into squares",
    "A bicycle is sleeping by the fence",
    "The radio forgets the weather again",
    "We count the stars that fell into the pool",
    "Your coat still hangs behind the kitchen door",
    "Tomorrow waits outside in muddy boots",
]


def test_a_part_is_about_thirty_seconds_of_whole_lines_and_whole_sections():
    lines = songhook.parse_lrc(LRC)
    heat = songhook.heat_curve(_heat(134.0, (20.0, 30.0)), 134.0)
    found = songhook.windows(lines, 134.0, heat, None, "", "")
    best = songhook.distinct(found)
    assert (songhook.SHORTEST, songhook.LONGEST, songhook.AIM) == (24.0, 40.0, 30.0)
    first = best[0]
    assert abs((first.end - first.start) - 30.0) < 3.0
    assert first.start == 20.2  # the part people go back to, from its first line
    assert first.why["section"] == 1.0 and first.end == lines[first.last + 1][0]  # whole lines
    assert all(24.0 <= w.end - w.start <= 40.0 for w in best) and len(best) == 4


@needs_ffmpeg
def test_a_found_song_plays_about_thirty_seconds_and_says_which_part(tmp_path):
    settings, _ = studio_env(tmp_path)
    stand_ins, router = song_finder_stand_ins(tmp_path, settings)
    song = songhook.find("Koi Apna Hoga Tony Kakkar", settings, router, **stand_ins)
    assert 24.0 <= song.end - song.start <= 40.0
    assert song.playing == [0] and song.pieces == []
    again = songhook.rechoose(song, 2, settings, router)
    assert again.playing == [2] and 24.0 <= again.end - again.start <= 40.0


# ---- a line's words over its singing, never over the music after it --------------------


def test_a_lines_words_never_stretch_over_the_music_after_it():
    said = ["lanterns", "on", "the", "pier"]  # 5 syllables: at most 4 s to sing
    sung = [Word(text=w, start=45.3 + 0.4 * k, end=45.65 + 0.4 * k) for k, w in enumerate(said)]
    # Whisper "heard" words in the harmonica solo after it, until the next line at 58 s.
    ghosts = [Word(text="oh", start=t, end=t + 0.5) for t in (49.5, 52.0, 55.0, 57.3)]
    laid = songhook.lay_words(said, 45.32, 58.0, sung + ghosts)
    assert laid[-1][2] <= 45.32 + songhook.longest_singing(said)
    assert laid[-1][2] == pytest.approx(sung[-1].end, abs=0.01)
    # Ghost words a second apart all through it: cut where the line can't still be sung.
    steady = [Word(text="ah", start=46.9 + 0.9 * k, end=47.5 + 0.9 * k) for k in range(12)]
    laid = songhook.lay_words(said, 45.32, 58.0, sung + steady)
    assert laid[-1][2] <= 45.32 + songhook.longest_singing(said) + 0.01
    # Nothing heard near the line's start (only ghosts later): spread over its singing time.
    laid = songhook.lay_words(said, 45.32, 58.0, ghosts)
    assert laid[0][1] == 45.32 and laid[-1][2] <= 45.32 + 0.32 * 5 + 0.4 + 0.01


class HearingRouter:
    """Whisper hearing words at set song times (the clip it is given starts at `clip_at`)."""

    def __init__(self, heard: list[Word], clip_at: float):
        self.heard, self.clip_at = heard, clip_at

    def transcribe(self, audio, language=None):
        return Transcript(language="en", words=[
            Word(text=w.text, start=round(w.start - self.clip_at, 3),
                 end=round(w.end - self.clip_at, 3))
            for w in self.heard if w.start >= self.clip_at
        ])  # fmt: skip


def _words_heard(lines: list[tuple[float, str]], sung_at: dict[int, float]) -> list[Word]:
    out = []
    for k, (t, text) in enumerate(lines):
        at = sung_at.get(k, t)
        out += [Word(text=w, start=round(at + 0.35 * n, 3), end=round(at + 0.35 * n + 0.3, 3))
                for n, w in enumerate(text.split())]  # fmt: skip
    return out


@needs_ffmpeg
def test_a_part_ends_soon_after_its_last_sung_word_not_over_an_instrumental(tmp_path):
    folder = tmp_path / "songs" / "made"
    folder.mkdir(parents=True)
    # Six lines 4 s apart (the last of two words), then a long solo until the next line.
    lines = [(10.0 + 4.0 * k, INVENTED[k]) for k in range(5)]
    lines += [(30.0, "Lanterns glowing"), (58.0, INVENTED[6])]
    vocals = _song_wav(folder / "vocals.wav", 70.0,
                       [(t, t + 2.4) for t, _ in lines] + [(36.0, 56.0)])  # fmt: skip
    heard = _words_heard(lines, {}) + [
        Word(text="whoa", start=t, end=t + 0.6) for t in np.arange(36.0, 57.0, 1.1)
    ]
    window = songhook.Window(0, 5, 10.0, 36.0)
    song = songhook.timed(lines, window, 70.0, vocals, HearingRouter(heard, 9.7))
    last_sung = max(w.end for w in heard if w.start < 33.0)
    assert song.lines[-1].words[-1].end <= 30.0 + songhook.longest_singing(lines[5][1].split())
    assert song.end <= last_sung + songhook.TAIL + 0.6
    # And the ranking doesn't take the solo for the last line's singing.
    found = songhook.windows(lines, 70.0, None, None, "", "", (10.0, 40.0, 24.0))
    ending_at_solo = next(w for w in found if w.first == 0 and w.last == 5)
    assert ending_at_solo.end < 30.0 + 6.0
    assert not any(w.first <= 5 < w.last for w in found)  # no lyric held over the solo


@needs_ffmpeg
def test_a_line_written_before_its_singing_starts_where_the_singing_does(tmp_path):
    folder = tmp_path / "songs" / "made"
    folder.mkdir(parents=True)
    lines = [(30.0, INVENTED[0]), (37.0, INVENTED[1]), (41.0, INVENTED[2]), (45.0, INVENTED[3])]
    # The first line is written at 30 s but sung from 33 s: an intro before it.
    vocals = _song_wav(folder / "vocals.wav", 60.0, [(33.0, 36.0), (37.0, 39.5), (41.0, 43.5),
                                                     (45.0, 47.5)])  # fmt: skip
    heard = _words_heard(lines, {0: 33.0})
    song = songhook.timed(lines, songhook.Window(0, 3, 30.0, 48.0), 60.0, vocals,
                          HearingRouter(heard, 29.7))  # fmt: skip
    assert song.start == pytest.approx(33.0 - 0.05 - songhook.LEAD_IN, abs=0.05)
    assert song.lines[0].words[0].start >= 32.9


# ---- more pictures ------------------------------------------------------------------------


CHOICES = [
    HookChoice(start=20.2, end=49.7, first="Paper boats"),
    HookChoice(start=59.6, end=90.1, first="Seven lanterns"),
    HookChoice(start=0.2, end=30.0, first="The kettle"),
]


def test_the_singer_edit_keeps_its_shorter_hook(monkeypatch):
    from agentic_editor.animate import singer

    asked = {}

    def find(query, settings, router, say, **kwargs):
        asked.update(kwargs)
        raise songhook.SongError("no song here")

    monkeypatch.setattr(songhook, "find", find)
    with pytest.raises(singer.SingerError):
        singer.find_hook("Paper Boats", None)
    assert asked["bounds"] == (15.0, 32.0, 20.0)


# ---- long instrumentals inside a song, and messy names ----------------------------------


def test_a_part_with_long_music_between_its_lines_is_offered_only_without_another():
    # A slow song: verses of lines 4 s apart, with 20 s instrumental builds between them.
    starts = [10, 14, 18, 22, 26, 30, 54, 58, 62, 66, 70, 74, 98, 102, 106, 110, 114, 118]
    lines = [(float(t), INVENTED[k % len(INVENTED)]) for k, t in enumerate(starts)]
    found = songhook.windows(lines, 130.0, None, None, "", "")
    assert found and all(w.music <= songhook.MOST_MUSIC for w in found)
    assert all(not (w.first <= 5 < w.last) for w in found)  # none held over the first build
    # A song with nothing else still offers its parts, the music counted against them.
    sparse = [(10.0, INVENTED[0]), (40.0, INVENTED[1]), (44.0, INVENTED[2])]
    only = songhook.windows(sparse, 60.0, None, None, "", "")
    assert only and only[0].music > songhook.MOST_MUSIC and only[0].why["music"] < 0


@needs_ffmpeg
def test_timing_never_makes_a_part_longer_than_its_longest(tmp_path):
    folder = tmp_path / "songs" / "made"
    folder.mkdir(parents=True)
    lines = [(10.0 + 4.0 * k, INVENTED[k]) for k in range(7)]
    vocals = _song_wav(folder / "vocals.wav", 60.0, [(t, t + 3.0) for t, _ in lines])
    heard = _words_heard(lines, {})
    song = songhook.timed(lines, songhook.Window(0, 5, 10.0, 34.0), 60.0, vocals,
                          HearingRouter(heard, 9.7), longest=15.0)  # fmt: skip
    assert song.end - song.start <= 15.0 and len(song.lines) == 3  # ends at a line's end
    assert song.end <= lines[3][0] and song.lines[-1].end == song.end


def test_a_songs_name_is_shown_clean_however_lrclib_wrote_it():
    assert songhook.names("Paper Boats - The Inventions", "The Inventions") == (
        "Paper Boats", "The Inventions")  # fmt: skip
    assert songhook.names("The Inventions - Paper Boats", "The Inventions") == (
        "Paper Boats", "The Inventions")  # fmt: skip
    # Title and singer both the whole name: the upload's title and channel say which is which.
    messy = "the inventions  paper boats"
    assert songhook.names(messy, messy, "The Inventions - Paper Boats (Official HD Video)",
                          "TheInventionsVEVO") == ("Paper Boats", "The Inventions")  # fmt: skip
    assert songhook.names(messy, messy, "Paper Boats", "The Inventions - Topic") == (
        "Paper Boats", "The Inventions")  # fmt: skip
    assert songhook.names("", "", "Paper Boats - The Inventions [Lyrics]", "The Inventions") == (
        "Paper Boats", "The Inventions")  # fmt: skip
    assert songhook.names("paper boats", "the inventions") == ("Paper Boats", "The Inventions")
