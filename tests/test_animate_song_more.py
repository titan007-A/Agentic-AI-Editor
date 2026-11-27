"""Song story reels made longer and fuller: parts about 30 s, a second picture over a long
line, words that never stretch over the music after a line, and two parts of a song joined
into one reel. Every lyric here is made up."""

import itertools
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from agentic_editor import ffmpeg, jobs
from agentic_editor.animate import images, songhook, songwriter, sound, studio
from agentic_editor.animate.plan import (
    MAX_SCENES,
    HookChoice,
    Plan,
    Song,
    SongPiece,
    scene_lines,
    song_length,
    tidy,
)
from agentic_editor.animate.timeline import SWITCH, build_song, player_data
from agentic_editor.db import Animation
from agentic_editor.transcript import Transcript, Word
from tests import test_api
from tests.test_animate import studio_env
from tests.test_animate_api import studio_on
from tests.test_animate_song import (
    LRC,
    SCRIPT_REPLY,
    StudioRouter,
    Writer,
    _heat,
    _line,
    _song_wav,
    needs_ffmpeg,
    sample_song,
    song_finder_stand_ins,
    story_reply,
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


def song_of(gaps: list[float], start: float = 10.0, step: float = 0.3) -> Song:
    """A made-up song part whose lines start `gaps` seconds apart (the last runs `gaps[-1]`
    to the part's end), each sung a word every `step` s."""
    lines, at = [], start
    for k, gap in enumerate(gaps):
        lines.append(_line(INVENTED[k % len(INVENTED)], round(at, 3), step))
        at += gap
    return Song(title="Paper Boats", artist="The Inventions", video="made", start=start - 0.3,
                end=round(at, 3), language="en", lyrics="lrclib", lines=lines)  # fmt: skip


def moments(n: int, keep_every_other: bool = False) -> dict:
    """A writer's story with `n` moments, each its own picture (or every other one asking
    to keep the picture before)."""
    return {
        "title": "Boats in the rain",
        "story": "A boy sails paper boats down the street while his grandmother watches.",
        "people": [{"name": "Leo", "look": "a boy of nine in a yellow raincoat"}],
        "moments": [
            {"n": k + 1, "new_picture": not (keep_every_other and k % 2), "camera": "push_in",
             "art": f"Picture {k + 1}: Leo on the wet street at dusk, street light"}
            for k in range(n)
        ],
        "caption": "Small boats, big rain.\n(Song credit: Paper Boats, The Inventions)",
    }  # fmt: skip


def english_reply(song: Song) -> dict:
    """The writer's lines as given, each with one key word."""
    return {
        "lines": [
            {"n": n, "words": line.text.split(), "key": [{"word": 1, "color": "amber"}],
             "split": 0}
            for n, line in enumerate(song.lines, 1)
        ]
    }  # fmt: skip


# ---- parts about 30 s ---------------------------------------------------------------------


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


def test_a_line_on_screen_long_gets_a_second_picture_on_one_of_its_words():
    song = songwriter.pictured(song_of([2.5, 4.2, 2.6, 3.8]))
    assert [line.pictures for line in song.lines] == [1, 2, 1, 2]
    assert scene_lines(song) == [0, 1, 1, 2, 3, 3]
    prompt = songwriter.story_prompt(song)[1]["content"]
    assert "6 moments" in prompt and "moment 3: line 2 (second picture: a new one)" in prompt
    # The writer keeps the picture for the second half of line 2: it gets a new one anyway.
    reply = moments(6)
    reply["moments"][2]["new_picture"] = False
    plan, _ = songwriter.draft(Writer(english_reply(song), reply), song)
    assert len(plan.scenes) == 6 and len({s.visual.art for s in plan.scenes}) == 6
    assert [s.narration for s in plan.scenes] == [song.lines[k].text for k in scene_lines(song)]
    tl = build_song(plan, "")
    assert len(tl.scenes) == 6 and len(tl.captions) == 4
    one, two = tl.scenes[1], tl.scenes[2]
    assert one.end == pytest.approx(two.start)
    words = tl.captions[1].words
    assert any(two.start == pytest.approx(w.start - SWITCH) for w in words[1:])
    assert two.start - one.start >= 1.0 and tl.scenes[3].start - two.start >= 1.0
    data = player_data(plan, tl)
    assert len(data["scenes"]) == 6 and len(data["captions"]) == 4
    # A hand edit keeps a scene for each picture; a moved picture keeps its own line.
    assert studio.edited(Animation(style="song"), plan) is not None
    with pytest.raises(ValueError, match="one scene for each picture"):
        studio.edited(Animation(style="song"), plan.model_copy(update={"scenes": plan.scenes[:5]}))
    moved = plan.model_copy(update={"scenes": [plan.scenes[3], *plan.scenes[:3], *plan.scenes[4:]]})
    assert [s.narration for s in tidy(moved).scenes] == [s.narration for s in plan.scenes]
    # Writing a line's second picture again says which it is.
    redone = {"n": 3, "new_picture": True, "art": "Leo's boat at the drain", "camera": "rise"}
    writer = Writer(redone)
    again = songwriter.redo_moment(writer, plan, 2)
    assert "its second picture" in writer.asked[0][-1]["content"]
    assert again.scenes[2].visual.art == "Leo's boat at the drain"


def test_a_new_picture_far_more_often_than_not():
    song = songwriter.pictured(song_of([2.5] * 10))
    plan, _ = songwriter.draft(Writer(english_reply(song), moments(10, True)), song)
    arts = [s.visual.art for s in plan.scenes]
    kept = sum(1 for a, b in itertools.pairwise(arts) if a == b)
    assert kept <= 2 and len(set(arts)) >= 8  # the writer asked to keep five


def test_a_thirty_second_reel_gets_twelve_to_sixteen_pictures():
    gaps = [2.5, 3.5, 2.8, 4.0, 3.2, 2.6, 3.8, 2.4, 3.0, 2.2]
    song = songwriter.pictured(song_of(gaps))
    assert song_length(song) == pytest.approx(30.3, abs=0.01)
    assert 12 <= len(scene_lines(song)) <= 16
    # Never more scenes than a reel has.
    crowded = songwriter.pictured(song_of([4.0] * 20))
    assert len(scene_lines(crowded)) == MAX_SCENES


# ---- two parts joined -----------------------------------------------------------------------


def _clicks(path: Path, duration: float, at: list[float], rate: int = 44100) -> Path:
    t = np.arange(int(duration * rate)) / rate
    x = 0.02 * np.sin(2 * np.pi * 110 * t)
    for c in at:
        k = int(c * rate)
        burst = np.random.default_rng(int(c * 100)).standard_normal(int(0.03 * rate))
        x[k : k + len(burst)] += 0.8 * burst * np.exp(-np.arange(len(burst)) / (0.006 * rate))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return path


@needs_ffmpeg
def test_the_cut_lands_on_a_beat_of_the_music(tmp_path):
    audio = _clicks(tmp_path / "song.wav", 12.0, [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 6.27])
    assert songhook.cut_point(audio, 5.6, 7.0, 6.0) == pytest.approx(6.27, abs=0.03)
    assert songhook.cut_point(audio, 2.1, 2.9, 2.2) == pytest.approx(2.5, abs=0.03)
    assert songhook.cut_point(audio, 8.0, 9.0, 8.4) == 8.4  # no beat there: as preferred


@needs_ffmpeg
def test_two_parts_join_into_one_reel_of_about_thirty_seconds(tmp_path):
    settings, _ = studio_env(tmp_path)
    stand_ins, router = song_finder_stand_ins(tmp_path, settings)
    song = songhook.find("Koi Apna Hoga Tony Kakkar", settings, router, **stand_ins)
    first, second = 2, 0  # the later part first, as picked
    joined = songhook.join(song, first, second, settings, router)
    assert joined.playing == [2, 0] and len(joined.pieces) == 2
    (a0, a1), (b0, b1) = [(p.start, p.end) for p in joined.pieces]
    assert a0 >= song.choices[2].start - 1.0 and b1 <= song.choices[0].end + 1.5
    assert 24.0 <= song_length(joined) <= 40.0
    ones = [line for line in joined.lines if line.piece == 0]
    twos = [line for line in joined.lines if line.piece == 1]
    assert ones and twos and joined.lines == ones + twos
    # The first stops after its last word, the second starts before its first.
    assert max(w.end for w in ones[-1].words) <= a1 and twos[0].words[0].start >= b0
    assert all(a0 <= w.start and w.end <= a1 for line in ones for w in line.words)
    # One reel's clock: its lines in order, the second part straight after the first.
    tl = build_song(Plan(title="x", style="song", song=joined, scenes=[]), "song.wav")
    assert tl.duration == pytest.approx(song_length(joined))
    assert tl.music_parts == [(a0, a1), (b0, b1)]
    starts = [c.words[0].start for c in tl.captions]
    assert starts == sorted(starts) and starts[len(ones)] >= a1 - a0
    # Aimed longer (a join that came out short is made again so), the stretches are longer.
    lines = songhook.parse_lrc(LRC)
    a, b = (songhook._window_of(song, n, lines) for n in (2, 0))
    heat = songhook.heat_curve(_heat(134.0, (20.0, 30.0)), 134.0)
    length = lambda pair: sum(w.end - w.start for w in pair)  # noqa: E731
    aimed = [songhook.stretches(lines, 134.0, heat, None, a, b, "", aim) for aim in (24, 36)]
    assert length(aimed[1]) > length(aimed[0])
    # Playing one part again leaves the join.
    again = songhook.rechoose(joined, 1, settings, router)
    assert again.pieces == [] and again.playing == [1]
    with pytest.raises(songhook.SongError):
        songhook.join(song, 1, 1, settings, router)


def _tones(path: Path, duration: float, loud: list[tuple[float, float, float]]) -> Path:
    """A 44.1 kHz stereo song: a chord at the given loudness (amplitude) in each stretch."""
    rate = 44100
    t = np.arange(int(duration * rate)) / rate
    x = np.zeros_like(t)
    for a, b, amp in loud:
        inside = (t >= a) & (t < b)
        at = t[inside]
        x[inside] = amp * (np.sin(2 * np.pi * 220 * at) + 0.5 * np.sin(2 * np.pi * 331 * at))
    stereo = np.stack([x, x], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes())
    return path


@needs_ffmpeg
def test_the_joined_sound_is_seamless_and_as_loud_on_both_sides(tmp_path):
    from agentic_editor.render import audio

    song = _tones(tmp_path / "song.wav", 80.0, [(0.0, 40.0, 0.08), (40.0, 80.0, 0.32)])
    spans = [(10.0, 24.0), (52.0, 68.0)]
    out = sound.joined(str(song), spans)
    assert len(out) == round(30.0 * sound.RATE)
    joint = int(14.0 * sound.RATE)
    before, after = out[joint - 4 * sound.RATE : joint - sound.RATE], out[joint + sound.RATE :]
    assert abs(sound.lufs(before) - sound.lufs(after)) < 0.5  # 12 dB apart in the song
    # No click: the joint moves no more from sample to sample than the music does.
    steps = np.abs(np.diff(out[:, 0]))
    music = max(steps[: joint - 6000].max(), steps[joint + 6000 :].max())
    assert steps[joint - 3000 : joint + 3000].max() <= 1.5 * music  # in-phase sum: at most 1.41
    # And the reel's sound: as long as the reel, at the presets' loudness.
    plan_song = Song(video="v", start=10.0, end=68.0, lines=[_line("one two three", 10.5)],
                     pieces=[SongPiece(start=a, end=b) for a, b in spans])  # fmt: skip
    tl = build_song(Plan(title="x", style="song", song=plan_song, scenes=[]), str(song))
    mixed = sound.mix(tl, [], "emotional", tmp_path / "mix.wav")
    with wave.open(str(mixed)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(30.0, abs=0.02)
    assert audio.integrated_loudness(mixed) == pytest.approx(sound.LOUDNESS, abs=1.0)
    # The loudness measured here is the one ffmpeg measures.
    heard = ffmpeg.read_audio(mixed, sound.RATE, channels=2).astype(np.float64)
    assert sound.lufs(heard) == pytest.approx(audio.integrated_loudness(mixed), abs=0.5)


# ---- in the studio: join two parts, make every part ----------------------------------------

CHOICES = [
    HookChoice(start=20.2, end=49.7, first="Paper boats"),
    HookChoice(start=59.6, end=90.1, first="Seven lanterns"),
    HookChoice(start=0.2, end=30.0, first="The kettle"),
]


@pytest.fixture
def parts_studio(env, monkeypatch):
    """Song stories made at once: three parts offered, a part chosen or two joined without
    timing anything, the writer answering every draft, nothing painted or mixed; the queue
    and the renders written down instead of run."""
    found = sample_song().model_copy(update={"choices": CHOICES, "playing": [0]})
    calls = {"rechoose": [], "join": [], "sent": [], "rendered": []}

    def rechoose(song, index, settings, router):
        calls["rechoose"].append(index)
        return song.model_copy(
            update={
                "start": song.choices[index].start - 0.3,
                "lines": found.lines,
                "playing": [index],
                "pieces": [],
            }
        )

    def join(song, first, second, settings, router):
        calls["join"].append([first, second])
        pieces = [SongPiece(start=19.9, end=25.0), SongPiece(start=60.0, end=65.0)]
        lines = [found.lines[0], found.lines[1].model_copy(update={"piece": 0}),
                 *[line.model_copy(update={"piece": 1, "start": line.start + 35.0,
                                           "words": [w.model_copy(update={"start": w.start + 35.0,
                                                                          "end": w.end + 35.0})
                                                     for w in line.words]})
                   for line in found.lines[2:]]]  # fmt: skip
        return song.model_copy(update={"lines": lines, "pieces": pieces,
                                       "playing": [first, second]})  # fmt: skip

    class Writes:
        """The writer: the lines as given, then a story with a moment for each picture."""

        def __init__(self):
            self.pending: list[Song] = []

        def chat_json(self, messages, schema, temperature=0.3):
            text = messages[-1]["content"]
            if "lines of the part a reel plays" in text:
                return SCRIPT_REPLY
            count = int(text.split("Write the reel: ")[1].split(" moments")[0])
            return story_reply(count)

    monkeypatch.setattr(songhook, "find", lambda *a, **k: found)
    monkeypatch.setattr(songhook, "rechoose", rechoose)
    monkeypatch.setattr(songhook, "join", join)
    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    monkeypatch.setattr(images, "available", lambda s: False)
    writer = Writes()

    def run(animation_id):
        studio.run_task(animation_id, env.sessions, env.settings, StudioRouter(writer))

    calls["run"] = run
    monkeypatch.setattr(jobs, "animate", run)
    monkeypatch.setattr(jobs, "process", calls["rendered"].append)
    studio_on(env)
    return calls


def test_two_parts_of_a_song_are_joined_into_the_reel_and_undo_goes_back(env, parts_studio):
    a = env.client.post("/api/animations", json={"idea": "Paper Boats", "style": "song"}).json()
    assert a["status"] == "ready", a["error"]
    joined = env.client.post(f"/api/animations/{a['id']}/change", json={"join": [2, 0]})
    assert joined.status_code == 200, joined.text
    got = joined.json()
    assert got["status"] == "ready", got["error"] and parts_studio["join"] == [[2, 0]]
    song = Song.model_validate(got["plan"]["song"])
    assert len(song.pieces) == 2 and song.playing == [2, 0]
    assert len(got["plan"]["scenes"]) == len(scene_lines(song))
    assert got["player"]["duration"] == pytest.approx(song_length(song), abs=0.01)
    assert " + " in got["notes"][-1]  # both stretches of the song named
    for wrong in ([1, 1], [0, 7]):
        assert env.client.post(f"/api/animations/{a['id']}/change",
                               json={"join": wrong}).status_code == 422  # fmt: skip
    back = env.client.post(f"/api/animations/{a['id']}/undo").json()
    assert back["plan"]["song"]["pieces"] == [] and back["status"] == "ready"
    # Playing one part again after a join is one part.
    one = env.client.post(f"/api/animations/{a['id']}/change", json={"choice": 1}).json()
    assert one["plan"]["song"]["pieces"] == [] and one["plan"]["song"]["playing"] == [1]


def _browser() -> bool:
    from tests.test_animate_render import _browser_works

    return _browser_works()


@pytest.mark.skipif(shutil.which("ffmpeg") is None or not _browser(), reason="needs Chromium")
def test_a_lines_second_picture_is_drawn_on_its_word_and_the_video_is_the_preview(tmp_path):
    from PIL import Image

    from agentic_editor.animate import render
    from tests.test_animate_render import ROOT, frame_at

    folder = tmp_path / "pictures"
    folder.mkdir()
    song = songwriter.pictured(song_of([4.2, 2.6], start=1.0))
    plan, _ = songwriter.draft(Writer(english_reply(song), moments(3)), song)
    colours = [(200, 40, 40), (40, 60, 200), (40, 170, 60)]
    scenes = []
    for k, (scene, colour) in enumerate(zip(plan.scenes, colours, strict=True)):
        name = f"{k:x}" * 24 + ".png"
        Image.new("RGB", (720, 1280), colour).save(folder / name)
        scenes.append(scene.model_copy(update={"visual": scene.visual.model_copy(
            update={"image": name})}))  # fmt: skip
    plan = plan.model_copy(update={"scenes": scenes})
    tl = build_song(plan, "")
    data = player_data(plan, tl)
    opts = render.options(ROOT / "fonts", ROOT / "models", folder)
    switch = tl.scenes[1].start
    early = Image.open(render.frame(data, opts, switch - 0.4, tmp_path / "a.jpg")).convert("RGB")
    late = Image.open(render.frame(data, opts, switch + 0.6, tmp_path / "b.jpg")).convert("RGB")
    r, g, b = early.getpixel((540, 1100))
    assert r > 120 and b < 90, (r, g, b)  # the line's first picture
    r, g, b = late.getpixel((540, 1100))
    assert b > 120 and r < 90, (r, g, b)  # its second, while the same line is up
    data["duration"] = min(data["duration"], 5.0)
    video = tmp_path / "picture.mp4"
    assert render.frames_to_video(data, opts, video, fps=10) == []
    made = frame_at(video, switch + 0.6, tmp_path / "made.png")
    diff = np.abs(np.asarray(late, dtype=float) - np.asarray(made, dtype=float)).mean()
    assert diff < 4.0, f"the video differs from the preview by {diff:.1f}"


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
