"""Song story reels: the song found and its part chosen and timed (songhook), its lines
shown and its story written (songwriter), and the reel timed, heard and drawn."""

import json
import shutil
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest

from agentic_editor import jobs
from agentic_editor.animate import images, songhook, songwriter, studio
from agentic_editor.animate.plan import (
    HookChoice,
    Plan,
    Scene,
    Song,
    SongLine,
    SongWord,
    Visual,
    tidy,
)
from agentic_editor.animate.timeline import build_song, player_data
from agentic_editor.db import Animation
from agentic_editor.transcript import Transcript, Word
from tests import test_api
from tests.test_animate import studio_env
from tests.test_animate_api import studio_on

env = test_api.env  # the editor with a signed-in admin (a fixture)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

LINE_ONE = "Yeh sapne pe sapna saja lete hain"
LAST_SHOWN = "लोग जो अपनों पे जाँ देते थे वो राहें सुहानी हवा हो गईं"
LRC = """[ar: Tony Kakkar]
[00:00.15] Tere chalne ke baahar koi tera apna hoga
[00:05.23] Ye jaan kar bhi vaada tujh ko sehna hoga
[00:10.18] Tere chalne ke baahar koi tere apna hoga
[00:15.04] Yeh jaan kar bhi vaada tujh ko sehna hoga
[00:20.20] Yeh sapne pe sapna saja lete hain
[00:22.52] Yeh baadal ko tere sada dete hain
[00:24.94] Yeh tere hain apne doston se kam-tar
[00:27.58] Yeh tere yaadon ka mela lete hain
[00:29.95] Kinare mein mehngi sada ho gayi
[00:32.53] Yeh basti hai khidki mein khaali ho gayi
[00:34.93] Log jo apno pe jaan dete thay woh raahein suhaani hawa ho gayi
[00:39.79][00:49.66] Tere chalne ke baahar koi tera apna hoga
[00:44.55] Yeh jaan kar bhi vaada tujh ko sehna hoga
[00:54.38] Yeh jaan kar bhi vaada tujh ko sehna hoga
[00:59.62] Koi shaam rukta anjaana hai
[01:02.02] Mai kehna sunta hoon kya kaam hai
[01:04.52] Nahi koi mausam ki raatein karu mai
[01:07.15] Toh kehte hain deewana naraz hai
[01:09.77] Saagar ke shehron mein dil maangta hoon mai
[01:12.32] Acha rahu mai yeh kyu dekhta hoon
[01:14.62] Woh jaana hee jaate hai, jaa kar rahein ge
[01:17.03] Mai jaane se unko kyu hee tokta hu
[01:19.62] Tere chalne ke baahar koi tera apna hoga
[01:24.08] Yeh jaan kar bhi vaada tujh ko sehna hoga
[02:09.25]
"""


# ---- what was asked, lyrics and the upload ----------------------------------------------


def test_the_song_name_and_the_part_wanted_are_told_apart():
    assert songhook.asked("Koi Apna Hoga Tony Kakkar") == songhook.Asked(
        "Koi Apna Hoga Tony Kakkar", ""
    )
    assert songhook.asked("Koi Apna Hoga | ye sapne pe  sapna") == songhook.Asked(
        "Koi Apna Hoga", "ye sapne pe sapna"
    )
    assert songhook.asked("Kesariya\nkesariya tera ishq hai piya").part == (
        "kesariya tera ishq hai piya"
    )


def test_synced_lyrics_come_in_time_order_and_a_line_sung_twice_comes_twice():
    lines = songhook.parse_lrc(LRC)
    assert lines[0] == (0.15, "Tere chalne ke baahar koi tera apna hoga")
    assert [t for t, _ in lines] == sorted(t for t, _ in lines)
    assert sum(1 for t, text in lines if text.startswith("Tere chalne ke baahar koi tera")) == 4
    assert (49.66, "Tere chalne ke baahar koi tera apna hoga") in lines
    assert all(text for _, text in lines)  # the closing empty line and the tag are left out


def test_the_lyrics_are_the_song_asked_for_not_another_with_lyrics():
    records = [
        {"trackName": "Zindagi Bata De", "artistName": "Tony Kakkar", "syncedLyrics": LRC},
        {"trackName": "Koi Apna Hoga", "artistName": "Tony Kakkar", "syncedLyrics": "",
         "plainLyrics": "words"},
        {"trackName": "Koi Apna Hoga", "artistName": "Tony Kakkar", "duration": 134.0,
         "syncedLyrics": LRC},
    ]  # fmt: skip
    found = songhook.best_lyrics(records, "Koi Apna Hoga Tony Kakkar")
    assert (found.title, found.artist, found.duration) == ("Koi Apna Hoga", "Tony Kakkar", 134.0)
    assert songhook.best_lyrics(records[:2], "Koi Apna Hoga Tony Kakkar") is None


def test_the_songs_own_upload_is_chosen_never_a_slowed_or_lofi_take():
    found = [
        songhook.Candidate("lofi", "Koi Apna Hoga (Lofi - Slowed Reverb)", "Lofi Heaven", 166,
                           7_000_000),
        songhook.Candidate("own", "Koi Apna Hoga - Tony Kakkar", "Tony Kakkar", 135, 9_800_000),
        songhook.Candidate("other", "Tony Kakkar - Zindagi Bata De", "Tony Kakkar", 156,
                           30_000_000),
        songhook.Candidate("lyrics", "Tony Kakkar - Koi Apna Hoga | (Lyrics)", "Musicgenree", 135,
                           509_348),
    ]  # fmt: skip
    chosen = songhook.pick(found, "Koi Apna Hoga Tony Kakkar", "Tony Kakkar", 134.0)
    assert chosen.id == "own"
    with pytest.raises(songhook.SongError):
        songhook.pick([], "nothing")


def test_syllables_are_counted_in_latin_and_devanagari():
    assert songhook.syllables("sapna") == 2
    assert songhook.syllables("baadal") == 2
    assert songhook.syllables("सपना") == 3  # च ह र: one each
    assert songhook.syllables("राहें") == 2  # स् joins ल
    assert songhook.syllables("a") == 1


# ---- moving the lines onto the upload ---------------------------------------------------


def test_lines_move_onto_where_singing_starts_after_a_pause():
    level = np.full(int(30 / songhook.FRAME), -70.0)
    starts = [2.0, 6.0, 11.0, 15.5, 21.0]
    for s in starts:  # each line sung from 0.4 s after its lyric time, for 2.5 s
        a = int((s + 0.4) / songhook.FRAME)
        level[a : a + int(2.5 / songhook.FRAME)] = -20.0
    found = songhook.onsets(level)
    assert [round(t, 1) for t, _ in found] == [round(s + 0.4, 1) for s in starts]
    assert songhook.shift(starts, found, 3.0) == pytest.approx(0.4, abs=0.03)


# ---- the hook -----------------------------------------------------------------------------


def _heat(duration: float, bump: tuple[float, float]) -> list:
    """A most-replayed graph that falls slowly, with people going back to `bump`."""
    step = duration / 100
    out = []
    for i in range(100):
        t = i * step
        value = 1.0 if i == 0 else 0.7 - 0.3 * t / duration
        if bump[0] <= t < bump[1]:
            value += 0.12
        out.append([t, t + step, value])
    return out


def test_the_part_people_replay_is_the_hook_and_whole_sections_are_kept():
    lines = songhook.parse_lrc(LRC)
    duration = 134.0
    heat = songhook.heat_curve(_heat(duration, (20.0, 30.0)), duration)
    best = songhook.distinct(songhook.windows(lines, duration, heat, None, "", "Koi Apna Hoga"))
    first = best[0]
    assert lines[first.first][1] == "Yeh sapne pe sapna saja lete hain"
    assert songhook.SHORTEST <= first.end - first.start <= songhook.LONGEST
    assert lines[first.last + 1][1].startswith("Tere chalne")  # ends where the chorus begins
    # No two of the parts offered mostly overlap.
    for a in best:
        for b in best:
            if a is not b:
                shared = min(a.end, b.end) - max(a.start, b.start)
                assert shared < 0.4 * min(a.end - a.start, b.end - b.start)


def test_words_typed_from_a_part_choose_it():
    lines = songhook.parse_lrc(LRC)
    heat = songhook.heat_curve(_heat(134.0, (20.0, 30.0)), 134.0)
    best = songhook.windows(lines, 134.0, heat, None, "koi shaks rukta meherban", "")
    assert lines[best[0].first][1] == "Koi shaam rukta anjaana hai"


def test_without_a_graph_the_louder_part_counts():
    lines = songhook.parse_lrc(LRC)
    energy = np.zeros(int(134 / 0.5))
    energy[int(60 / 0.5) : int(80 / 0.5)] = 2.0
    best = songhook.windows(lines, 134.0, None, energy, "", "")
    assert best[0].start >= 59.0


# ---- every word's time --------------------------------------------------------------------


def test_lyric_words_are_laid_over_the_heard_words_by_how_long_they_take():
    heard = [
        Word(text="ये", start=20.4, end=20.6),
        Word(text="सपने", start=20.6, end=21.0),
        Word(text="पे", start=21.0, end=21.2),
        Word(text="सपना", start=21.2, end=21.7),
        Word(text="सजा", start=21.7, end=22.0),
        Word(text="लेते", start=22.0, end=22.2),
        Word(text="हैं", start=22.2, end=22.5),
    ]
    laid = songhook.lay_words(
        ["Yeh", "sapne", "pe", "sapna", "saja", "lete", "hain"], 20.2, 22.52, heard
    )
    assert [w for w, _, _ in laid] == ["Yeh", "sapne", "pe", "sapna", "saja", "lete", "hain"]
    starts = [a for _, a, _ in laid]
    assert starts == sorted(starts) and 20.2 <= starts[0] <= 20.5 and starts[-1] < 22.5
    assert abs(starts[3] - 21.2) < 0.3  # "sapna" where it was heard
    # Nothing heard: spread over the line from its start.
    spread = songhook.lay_words(["one", "two", "three"], 5.0, 9.0, [])
    assert spread[0][1] == 5.0 and spread[-1][2] <= 9.0


def _line(text: str, start: float, step: float = 0.35, said: str = "") -> SongLine:
    words = [
        SongWord(text=w, start=round(start + k * step, 3), end=round(start + (k + 1) * step, 3))
        for k, w in enumerate(text.split())
    ]
    return SongLine(text=text, said=said or text, start=start, end=words[-1].end, words=words)


def test_a_long_line_is_shown_as_two_where_its_meaning_breaks():
    line = _line("Log jo apno pe jaan dete thay woh raahein suhaani hawa ho gayi", 34.9)
    first, second = songhook.split_long(line, 7)
    assert first.text == "Log jo apno pe jaan dete thay"
    assert second.text == "woh raahein suhaani hawa ho gayi"
    assert first.end == second.start and second.end == line.end
    assert first.said == first.text and second.said == second.text
    # Without being told where: still two, split near the middle.
    a, b = songhook.split_long(line)
    assert 4 <= len(a.words) <= 9 and len(a.words) + len(b.words) == 13
    short = _line("Yeh sapne pe sapna saja lete hain", 20.2)
    assert songhook.split_long(short, 3) == [short]


# ---- finding a song, start to end -------------------------------------------------------


class SongRouter:
    """The models a song is found with, stood in for: the 'vocals' are the song itself, and
    whisper hears the lyric words where they are."""

    def __init__(self, heard: list[Word]):
        self.heard = heard
        self.separated = 0
        self.fetched: list[str] = []
        self.languages: list[str | None] = []

    def separate_vocals(self, audio, out_dir):
        self.separated += 1
        vocals = Path(out_dir) / "song_(Vocals).wav"
        shutil.copyfile(audio, vocals)
        other = Path(out_dir) / "song_(Instrumental).wav"
        other.write_bytes(b"")
        return type("Stems", (), {"vocals": vocals, "instrumental": other})()

    def transcribe(self, audio, language=None):
        self.languages.append(language)
        return Transcript(language="hi", words=self.heard)

    def planner(self):
        raise AssertionError("finding a song uses no planning model")


def _song_wav(path: Path, duration: float, sung: list[tuple[float, float]]) -> Path:
    rate = 16000
    t = np.arange(int(duration * rate)) / rate
    x = np.zeros_like(t)
    for a, b in sung:
        x[(t >= a) & (t < b)] = 0.3 * np.sin(2 * np.pi * 220 * t[(t >= a) & (t < b)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def song_finder_stand_ins(tmp_path: Path, settings) -> tuple[dict, "SongRouter"]:
    """What songhook.find looks up, searches and downloads (as its keyword arguments), and
    the models it hears with, stood in for: Koi Apna Hoga's lyrics over a tone where each
    line is sung, its most replayed part at 20-30 s."""
    lines = songhook.parse_lrc(LRC)
    duration = 134.0
    source = _song_wav(tmp_path / "source.wav", duration, [(t, t + 2.0) for t, _ in lines])
    heard = [Word(text=w, start=20.2 + 0.3 * k, end=20.5 + 0.3 * k) for k, w in
             enumerate(["ये", "सपने", "पे", "सपना", "सजा", "लेते", "हैं"])]  # fmt: skip
    router = SongRouter(heard)

    def fetch(candidate, settings_):
        folder = songhook.song_folder(settings_, candidate.id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / "song.wav")
        router.fetched.append(candidate.id)
        meta = {"id": candidate.id, "title": candidate.title, "channel": "Tony Kakkar",
                "duration": duration, "heatmap": _heat(duration, (20.0, 30.0))}  # fmt: skip
        return folder / "song.wav", meta

    record = {"trackName": "Koi Apna Hoga", "artistName": "Tony Kakkar", "duration": 134.0,
              "syncedLyrics": LRC}  # fmt: skip
    found = [songhook.Candidate("own", "Koi Apna Hoga - Tony Kakkar", "Tony Kakkar", 134, 10**7)]
    return {"lookup": lambda name: [record], "searcher": lambda q: found, "fetch": fetch}, router


@needs_ffmpeg
def test_a_song_is_found_its_hook_chosen_and_every_word_timed(tmp_path):
    settings, _ = studio_env(tmp_path)
    lines = songhook.parse_lrc(LRC)
    stand_ins, router = song_finder_stand_ins(tmp_path, settings)
    said: list[str] = []
    song = songhook.find("Koi Apna Hoga Tony Kakkar", settings, router, said.append, **stand_ins)
    assert router.fetched == ["own"] and router.separated == 1
    # The lyrics are Hindi in Latin letters: whisper is told so, not left to guess.
    assert router.languages == ["hi"]
    assert (song.title, song.artist, song.video, song.lyrics) == (
        "Koi Apna Hoga", "Tony Kakkar", "own", "lrclib",
    )  # fmt: skip
    assert song.lines[0].said == "Yeh sapne pe sapna saja lete hain"
    assert song.start == pytest.approx(19.9, abs=0.2) and 12 <= song.end - song.start <= 32
    assert song.language == "hi" and len(song.choices) >= 2
    assert all(w.start < w.end for line in song.lines for w in line.words)
    assert said[0] == "looking up the lyrics"
    kept = json.loads((songhook.song_folder(settings, "own") / songhook.LINES).read_text())
    assert len(kept["lines"]) == len(lines)

    # Another part offered: timed again from what was kept, nothing found again.
    other = next(n for n, c in enumerate(song.choices) if abs(c.start - song.start) > 5)
    again = songhook.rechoose(song, other, settings, router)
    wanted = max(0.0, song.choices[other].start - songhook.LEAD_IN)
    assert again.start == pytest.approx(wanted, abs=0.05)
    assert again.video == "own" and router.fetched == ["own"] and router.separated == 1
    with pytest.raises(songhook.SongError):
        songhook.rechoose(song, 9, settings, router)


@needs_ffmpeg
def test_two_reels_of_one_song_at_once_share_its_vocals_and_hear_their_own_part(tmp_path):
    folder = tmp_path / "songs" / "own"
    folder.mkdir(parents=True)
    audio = _song_wav(folder / "song.wav", 60.0, [(10.0, 50.0)])

    class Slow(SongRouter):
        def __init__(self):
            super().__init__([])
            self.clips: list[float] = []

        def separate_vocals(self, audio, out_dir):
            time.sleep(0.3)  # long enough for the other reel to ask meanwhile
            return super().separate_vocals(audio, out_dir)

        def transcribe(self, audio, language=None):
            with wave.open(str(audio)) as w:
                self.clips.append(round(w.getnframes() / w.getframerate(), 1))
            time.sleep(0.2)
            return Transcript(language="hi", words=[Word(text="x", start=0.1, end=0.4)])

    router = Slow()
    with ThreadPoolExecutor(2) as pool:
        vocals = list(pool.map(lambda _: songhook.separated(audio, router), range(2)))
    assert router.separated == 1 and vocals[0] == vocals[1] == folder / "vocals.wav"
    parts = [(10.0, 30.0), (30.0, 42.0)]
    with ThreadPoolExecutor(2) as pool:
        heard = list(pool.map(lambda p: songhook.hear(vocals[0], *p, router, "hi"), parts))
    assert sorted(router.clips) == [12.0, 20.0]
    assert [h[0][0].start for h in heard] == [10.1, 30.1]
    assert not list(folder.glob("*.wav.part*")) and len(list(folder.glob("heard-*.json"))) == 2


def test_lyrics_tell_whisper_their_language():
    english = [
        "I keep your letters in a drawer",
        "The city lights are cold tonight",
        "You said that you would wait for me",
        "But I don't know where you are",
    ]
    spanish = ["Tu voz se quedó en mi ventana", "La noche me encuentra sola",
               "Y no sé por qué te fuiste", "Pero mi corazón te espera"]  # fmt: skip
    assert songhook.lyrics_language([text for _, text in songhook.parse_lrc(LRC)]) == "hi"
    assert songhook.lyrics_language(english) == "en"
    assert songhook.lyrics_language(spanish) == "es"
    assert songhook.lyrics_language(["ये रातें अब ख़ाली लगती हैं"]) == "hi"
    assert songhook.lyrics_language(["یہ راتیں اب خالی ہیں"]) == "ur"
    assert songhook.lyrics_language(["la la la", "na na"]) is None


def test_a_song_nobody_can_find_says_so():
    with pytest.raises(songhook.SongError, match="song's name"):
        songhook.find("  ", None, None)


# ---- the writer -------------------------------------------------------------------------


class Writer:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked: list[list[dict]] = []

    def chat_json(self, messages, schema, temperature=0.3):
        self.asked.append(messages)
        return self.replies.pop(0)


def sample_song() -> Song:
    lines = [
        _line("Yeh sapne pe sapna saja lete hain", 20.2),
        _line("Yeh baadal ko tere sada dete hain", 22.6),
        _line("Log jo apno pe jaan dete thay woh raahein suhaani hawa ho gayi", 25.0, 0.3),
    ]
    return Song(query="Koi Apna Hoga Tony Kakkar", title="Koi Apna Hoga", artist="Tony Kakkar",
                video="own", start=19.9, end=30.0, language="hi", lyrics="lrclib",
                lines=lines)  # fmt: skip


SCRIPT_REPLY = {
    "lines": [
        {"n": 1, "words": ["ये", "सपने", "पे", "सपना", "सजा", "लेते", "हैं"],
         "key": [{"word": 1, "color": "white"}, {"word": 3, "color": "amber"},
                 {"word": 6, "color": "red"}], "split": 0},
        {"n": 2, "words": ["ये", "बादल", "को", "तेरे"],  # a word short: kept as written
         "key": [{"word": 1, "color": "red"}], "split": 0},
        {"n": 3, "words": LAST_SHOWN.split(),
         "key": [{"word": 2, "color": "amber"}, {"word": 4, "color": "red"}], "split": 7},
    ]
}  # fmt: skip


def test_lines_are_shown_in_devanagari_word_for_word_with_their_key_words():
    shown, notes = songwriter.shown(Writer(SCRIPT_REPLY), sample_song())
    first = shown.lines[0]
    assert first.text == "ये सपने पे सपना सजा लेते हैं"
    assert [(w.text, w.color) for w in first.words if w.key] == [("सपने", "white"),
                                                                 ("सपना", "amber")]  # fmt: skip
    assert first.words[0].start == 20.2  # the times stay the lyric words'
    # A line that didn't come back one for one stays as written, its key word kept.
    assert shown.lines[1].text == "Yeh baadal ko tere sada dete hain"
    assert [w.text for w in shown.lines[1].words if w.key] == ["baadal"]
    assert notes == ["1 lyric lines are shown as the lyrics wrote them"]
    # The long line is two, where the writer said its meaning breaks.
    assert [line.text for line in shown.lines[2:]] == [
        "लोग जो अपनों पे जाँ देते थे",
        "वो राहें सुहानी हवा हो गईं",
    ]
    assert shown.lines[2].said == "Log jo apno pe jaan dete thay"


def story_reply(n: int) -> dict:
    return {
        "title": "Masks at the door",
        "story": "Relatives smile through masks; only the grandfather cared.",
        "people": [{"name": "Dadaji", "look": "an old grandfather in a white kurta and cap"},
                   {"name": "Ravi", "look": "a young man in a blue kurta"}],
        "moments": [
            {"n": k + 1, "new_picture": k != 1, "camera": "push_in" if k else "close_up",
             "art": f"Moment {k + 1}: Dadaji and Ravi at the door at night, warm diya light"}
            for k in range(n)
        ],
        "caption": "Har sapna apna nahi hota.\n(Song credit: Koi Apna Hoga, Tony Kakkar)",
    }  # fmt: skip


def test_a_found_song_becomes_a_story_with_a_moment_for_every_line():
    writer = Writer(SCRIPT_REPLY, story_reply(4))
    plan, _ = songwriter.draft(writer, sample_song())
    assert plan.style == "song" and plan.title == "Masks at the door"
    assert len(plan.scenes) == len(plan.song.lines) == 4
    assert [s.narration for s in plan.scenes] == [line.text for line in plan.song.lines]
    assert all(s.visual.kind == "moment" and s.visual.art for s in plan.scenes)
    # A moment that goes on with the one before keeps its picture.
    assert plan.scenes[1].visual.art == plan.scenes[0].visual.art
    assert plan.scenes[0].visual.camera == "close_up"
    assert [p.name for p in plan.song.people] == ["Dadaji", "Ravi"]
    assert "Song credit" in plan.caption and plan.cta == ""
    asked = writer.asked[1][1]["content"]
    assert "ये सपने पे सपना सजा लेते हैं" in asked and "4 moments" in asked
    # A person named in a picture is painted as they look.
    painted = songwriter.painted(plan, plan.scenes[0].visual.art)
    assert "an old grandfather in a white kurta and cap" in painted and "Dadaji" not in painted


def test_a_story_short_of_moments_is_sent_back_once():
    writer = Writer(SCRIPT_REPLY, story_reply(2), story_reply(4))
    plan, _ = songwriter.draft(writer, sample_song())
    assert (
        len(writer.asked) == 3
        and "one moment for each of the 4 lines" in writer.asked[2][-1]["content"]
    )
    assert len(plan.scenes) == 4


def test_the_story_changes_in_words_and_one_moment_is_written_again():
    plan, _ = songwriter.draft(Writer(SCRIPT_REPLY, story_reply(4)), sample_song())
    changed_reply = story_reply(4) | {"title": "Rain at the door"}
    changed, _ = songwriter.change(Writer(changed_reply), plan, "make it rain")
    assert changed.title == "Rain at the door"
    assert changed.song.lines == plan.song.lines  # the song and its words stay
    again = songwriter.redo_moment(
        Writer({"n": 3, "new_picture": True, "art": "Ravi alone in the rain", "camera": "rise"}),
        plan,
        2,
    )
    assert again.scenes[2].visual.art == "Ravi alone in the rain"
    assert again.scenes[2].visual.camera == "rise" and again.scenes[0] == plan.scenes[0]


def test_key_word_colours_are_only_the_known_ones_and_only_on_key_words():
    song = sample_song()
    words = [w.model_copy(update={"key": k == 1, "color": "purple" if k == 1 else "red"})
             for k, w in enumerate(song.lines[0].words)]  # fmt: skip
    song.lines[0] = song.lines[0].model_copy(update={"words": words})
    plan = tidy(Plan(title="x", style="song", song=song, scenes=[
        Scene(narration=line.text, visual=Visual(kind="moment", art="a"))
        for line in song.lines
    ]))  # fmt: skip
    first = plan.song.lines[0].words
    assert [(w.key, w.color) for w in first[:3]] == [(False, ""), (True, "white"), (False, "")]
    assert plan.scenes[0].visual.camera == "push_in"
    # A lyric saying "follow me" is a lyric, not a request to follow.
    sung = Plan(title="x", style="song", scenes=[Scene(narration="Follow me to the river")])
    assert len(tidy(sung).scenes) == 1


# ---- the reel's timing and sound ----------------------------------------------------------


def song_plan() -> Plan:
    plan, _ = songwriter.draft(Writer(SCRIPT_REPLY, story_reply(4)), sample_song())
    return plan


def test_each_line_is_a_scene_from_just_before_it_is_sung_and_the_song_plays_through():
    plan = song_plan()
    tl = build_song(plan, "/songs/own/song.wav")
    assert tl.duration == pytest.approx(30.0 - 19.9)
    assert tl.music == "/songs/own/song.wav" and tl.music_from == 19.9
    assert tl.scenes[0].start == 0.0 and tl.scenes[-1].end == pytest.approx(tl.duration)
    for a, b in zip(tl.scenes, tl.scenes[1:], strict=False):
        assert a.end == pytest.approx(b.start)
        assert b.start == pytest.approx(b.words[0].start - 0.15, abs=0.01)
    assert len(tl.captions) == len(plan.song.lines) and tl.cues == []
    assert tl.captions[0].words[0].start == pytest.approx(20.2 - 19.9)
    data = player_data(plan, tl)
    assert data["style"] == "song" and len(data["scenes"]) == 4  # no closing scene
    keys = [(w["text"], w["color"]) for w in data["captions"][0]["words"] if w["key"]]
    assert keys == [("सपने", "white"), ("सपना", "amber")]


@needs_ffmpeg
def test_the_reels_sound_is_the_songs_part_at_the_presets_loudness(tmp_path):
    from agentic_editor.animate import sound
    from agentic_editor.render import audio

    rate = 44100
    t = np.arange(int(40 * rate)) / rate
    tone = (0.6 * np.sin(2 * np.pi * 330 * t) * (t > 20)).astype(np.float32)
    song = tmp_path / "song.wav"
    with wave.open(str(song), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((tone * 32767).astype("<i2").tobytes())
    plan = song_plan()
    tl = build_song(plan, str(song))
    out = sound.mix(tl, [], plan.mood, tmp_path / "mix.wav")
    length = float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert length == pytest.approx(tl.duration, abs=0.05)
    assert audio.integrated_loudness(out) == pytest.approx(sound.LOUDNESS, abs=1.0)


# ---- in the studio ------------------------------------------------------------------------


class StudioRouter:
    def __init__(self, writer):
        self.writer = writer

    def planner(self):
        return nullcontext(self.writer)


def test_a_song_story_is_drafted_painted_and_ready_to_play(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    asked = []

    def find(idea, settings_, router, say, **kwargs):
        asked.append(idea)
        say("finding the song on YouTube")
        return sample_song()

    monkeypatch.setattr(songhook, "find", find)
    painted_prompts = []

    def paint(pictures, style, settings_, progress, **kwargs):
        painted_prompts.extend((p.prompt, style, p.empty) for p in pictures)
        return [f"{n:024x}.png" for n in range(len(pictures))]

    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    with Session() as db:
        db.add(Animation(id="s1", owner_id="u1", idea="Koi Apna Hoga | ye sapne pe sapna",
                         style="song", task={"op": "draft"}))  # fmt: skip
        db.commit()
    writer = Writer(SCRIPT_REPLY, story_reply(4))
    studio.run_task("s1", Session, settings, StudioRouter(writer), paint=paint)
    with Session() as db:
        a = db.get(Animation, "s1")
        assert a.status == "ready" and a.error is None, a.error
        plan = Plan.model_validate(a.plan)
        assert asked == ["Koi Apna Hoga | ye sapne pe sapna"]
        assert a.player["style"] == "song" and len(a.player["scenes"]) == 4
        assert a.notes[-1].startswith("10 s, Koi Apna Hoga by Tony Kakkar, 0:19.9-0:30.0")
    # One picture for the moment that goes on with the one before; people written out.
    assert len(painted_prompts) == 3
    assert all(style == "song" and not empty for _, style, empty in painted_prompts)
    assert "an old grandfather in a white kurta and cap" in painted_prompts[0][0]
    assert plan.scenes[0].visual.image == plan.scenes[1].visual.image != ""
    assert plan.scenes[2].visual.image != plan.scenes[0].visual.image


def test_without_the_image_model_a_song_story_is_still_made(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    monkeypatch.setattr(songhook, "find", lambda *a, **k: sample_song())
    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    monkeypatch.setattr(images, "available", lambda s: False)
    with Session() as db:
        db.add(Animation(id="s2", owner_id="u1", idea="Koi Apna Hoga", style="song",
                         task={"op": "draft"}))  # fmt: skip
        db.commit()
    studio.run_task("s2", Session, settings, StudioRouter(Writer(SCRIPT_REPLY, story_reply(4))))
    with Session() as db:
        a = db.get(Animation, "s2")
        assert a.status == "ready"
        assert "painted pictures need the image model (installed on the Mac)" in a.notes


def test_a_song_that_cant_be_found_fails_in_plain_words(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)

    def find(*a, **k):
        raise songhook.SongError("YouTube found nothing for 'zzz'")

    monkeypatch.setattr(songhook, "find", find)
    with Session() as db:
        db.add(Animation(id="s3", owner_id="u1", idea="zzz", style="song", task={"op": "draft"}))
        db.commit()
    studio.run_task("s3", Session, settings, StudioRouter(Writer()))
    with Session() as db:
        a = db.get(Animation, "s3")
        assert a.status == "failed" and a.error == "YouTube found nothing for 'zzz'"


def test_a_hand_edit_keeps_one_scene_a_line():
    plan = song_plan()
    made = Animation(style="song")
    assert studio.edited(made, plan) is not None
    fewer = plan.model_copy(update={"scenes": plan.scenes[:-1]})
    with pytest.raises(ValueError, match="one scene for each lyric line"):
        studio.edited(made, fewer)


# ---- the API ------------------------------------------------------------------------------


@pytest.fixture
def song_studio(env, monkeypatch):
    """Song stories made at once: the song stood in for, the writer answering from a queue,
    the pictures not painted, the sound not mixed."""
    replies = [SCRIPT_REPLY, story_reply(4)] * 4
    model = Writer(*replies)
    rechosen = []

    def rechoose(song, index, settings, router):
        rechosen.append(index)
        return song.model_copy(update={"start": song.choices[index].start - 0.3})

    choices = [
        HookChoice(start=20.2, end=30.0, first="Yeh sapne"),
        HookChoice(start=59.6, end=79.6, first="Koi shaam"),
    ]
    found = sample_song().model_copy(update={"choices": choices})
    monkeypatch.setattr(songhook, "find", lambda *a, **k: found)
    monkeypatch.setattr(songhook, "rechoose", rechoose)
    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    monkeypatch.setattr(images, "available", lambda s: False)
    monkeypatch.setattr(
        jobs,
        "animate",
        lambda animation_id: studio.run_task(
            animation_id, env.sessions, env.settings, StudioRouter(model)
        ),
    )
    studio_on(env)
    return rechosen


def test_a_song_story_is_made_from_a_songs_name_and_plays_another_part_when_asked(env, song_studio):
    made = env.client.post(
        "/api/animations", json={"idea": "Koi Apna Hoga Tony Kakkar | ye sapne", "style": "song"}
    )
    assert made.status_code == 200, made.text
    a = made.json()
    assert a["status"] == "ready" and a["plan"]["style"] == "song", a["error"]
    assert a["idea"] == "Koi Apna Hoga Tony Kakkar | ye sapne"
    assert a["plan"]["song"]["title"] == "Koi Apna Hoga" and a["scenes"] == 4
    # Its style stays: a Song story is drawn to its song.
    switched = env.client.patch(f"/api/animations/{a['id']}", json={"style": "glow"})
    assert switched.status_code == 422
    # Another of the parts offered: timed again and written again.
    other = env.client.post(f"/api/animations/{a['id']}/change", json={"choice": 1})
    assert other.status_code == 200, other.text
    assert song_studio == [1] and other.json()["status"] == "ready"
    assert other.json()["plan"]["song"]["start"] == pytest.approx(59.3)
    assert env.client.post(f"/api/animations/{a['id']}/change", json={"choice": 5}).status_code == (
        422
    )


def test_only_a_song_story_plays_another_part(env, song_studio, monkeypatch):
    from agentic_editor.animate import planner
    from tests.test_animate import long_plan, reply_for

    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(
        jobs,
        "animate",
        lambda animation_id: studio.run_task(
            animation_id, env.sessions, env.settings,
            StudioRouter(Writer(reply_for(long_plan()))), speak=_fake_speak,
        ),
    )  # fmt: skip
    a = env.client.post("/api/animations", json={"idea": "the lost city of Ubar"}).json()
    assert a["status"] == "ready", a["error"]
    asked = env.client.post(f"/api/animations/{a['id']}/change", json={"choice": 0})
    assert asked.status_code == 422


def _fake_speak(text, voice_id, cache, speed=None):
    from tests.test_animate import fake_speak

    return fake_speak(text, voice_id, cache, speed)


# ---- drawn ----------------------------------------------------------------------------------


def _browser() -> bool:
    from tests.test_animate_render import _browser_works

    return _browser_works()


@pytest.mark.skipif(shutil.which("ffmpeg") is None or not _browser(), reason="needs Chromium")
def test_a_song_story_is_drawn_picture_under_its_lyrics_and_the_video_is_the_preview(tmp_path):
    from PIL import Image

    from agentic_editor.animate import render
    from tests.test_animate_render import ROOT, frame_at

    folder = tmp_path / "pictures"
    folder.mkdir()
    plan = song_plan()
    names = ["a" * 24 + ".png", "b" * 24 + ".png"]
    Image.new("RGB", (720, 1280), (200, 40, 40)).save(folder / names[0])
    Image.new("RGB", (720, 1280), (40, 60, 200)).save(folder / names[1])
    scenes = [
        s.model_copy(update={"visual": s.visual.model_copy(update={"image": names[min(k, 1)]})})
        for k, s in enumerate(plan.scenes)
    ]
    plan = plan.model_copy(update={"scenes": scenes})
    tl = build_song(plan, "")
    data = player_data(plan, tl)
    data["duration"] = 4.0
    opts = render.options(ROOT / "fonts", ROOT / "models", folder)
    at = 2.0  # the first line has been sung: its key word is up, in amber
    shown = Image.open(render.frame(data, opts, at, tmp_path / "shown.jpg")).convert("RGB")
    r, g, b = shown.getpixel((540, 1100))  # the painting under the lyrics
    assert r > 120 and g < 80 and b < 80, (r, g, b)
    amber = sum(
        1
        for x in range(60, 1000, 4)
        for y in range(280, 520, 4)
        if (lambda p: p[0] > 220 and 140 < p[1] < 210 and p[2] < 110)(shown.getpixel((x, y)))
    )
    assert amber > 40, "the key word isn't drawn in amber at the top"
    later = Image.open(render.frame(data, opts, 3.5, tmp_path / "later.jpg")).convert("RGB")
    r, g, b = later.getpixel((540, 1100))  # the next line's picture has faded in
    assert b > 120 and r < 90, (r, g, b)
    video = tmp_path / "picture.mp4"
    assert render.frames_to_video(data, opts, video, fps=10) == []
    made = frame_at(video, at, tmp_path / "made.png")
    diff = np.abs(np.asarray(shown, dtype=float) - np.asarray(made, dtype=float)).mean()
    assert diff < 4.0, f"the video differs from the preview by {diff:.1f}"
