"""Singer edits (animate/singer.py): the song's hook, the singer's photos, one scene a sung
line, and how the studio, its routes and the renderer treat them. No model, no download:
the hook finder, the cutout and the writer are stand-ins."""

import io
import itertools
import json
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agentic_editor import jobs
from agentic_editor.animate import images, singer, songhook, studio
from agentic_editor.animate.plan import (
    Plan,
    SingerEdit,
    SingerPhoto,
    Song,
    SongLine,
    SongWord,
    tidy,
)  # fmt: skip
from agentic_editor.db import Animation
from agentic_editor.transcript import Word
from tests import test_api
from tests.test_animate import needs_ffmpeg, studio_env, tone_wav
from tests.test_animate_api import studio_on
from tests.test_animate_song import song_finder_stand_ins

env = test_api.env


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    """A test never fetches the background removal model (176 MB)."""

    def refuse(models_dir):
        raise AssertionError("a test asked for the cutout model")

    monkeypatch.setattr(singer, "cut_model", refuse)
    monkeypatch.setattr(singer, "ENLARGE_WITH_MODELS", False)  # no Real-ESRGAN in a test


LYRIC = [
    "Mere jaisa nahi hai koi",
    "Mai auron jaisa kyu banu?",
    "Khud se baatein kar sakta hu",
    "Auron se phir kyun milu?",
]


def timed_words(lines=LYRIC, start=0.4, each=0.32, breath=0.5) -> list[Word]:
    out, at = [], start
    for line in lines:
        for word in line.split():
            out.append(Word(text=word, start=round(at, 3), end=round(at + each * 0.9, 3)))
            at += each
        at += breath
    return out


def hook(tmp_path: Path, words=None, lines=None) -> singer.Hook:
    audio = tone_wav(tmp_path / "hook.wav", 8.0)
    return singer.Hook(audio, 8.0, "Maa, I Tried", "KiD Boi", words or timed_words(),
                       lines if lines is not None else list(LYRIC))  # fmt: skip


def photo_png(color=(40, 160, 60), size=(400, 700)) -> bytes:
    picture = Image.new("RGB", size, color)
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def edit_with_photos(cut=True) -> SingerEdit:
    photo = (
        SingerPhoto(
            image="a" * 24 + ".png",
            cutout="b" * 24 + ".png",
            face=[0.3, 0.05, 0.4, 0.15],
            aspect=0.5,
        )
        if cut
        else SingerPhoto(image="a" * 24 + ".png")
    )
    return SingerEdit(query="maa i tried", title="Maa, I Tried", artist="KiD Boi",
                      audio="c" * 24 + ".wav", duration=8.0, photos=[photo])  # fmt: skip


# ---- lines -----------------------------------------------------------------------------


def test_the_lyric_lines_become_the_scenes_when_they_match_the_timed_words(tmp_path):
    lines = singer.sung_lines(hook(tmp_path))
    assert [x.text for x in lines] == LYRIC
    assert lines[0].start == 0.4 and lines[0].words[0].text == "Mere"
    assert all(a.end <= b.start for a, b in itertools.pairwise(lines))


def test_without_the_lyric_the_singers_breaths_decide_the_lines(tmp_path):
    lines = singer.sung_lines(hook(tmp_path, lines=["a lyric that doesn't match"]))
    assert [x.text for x in lines] == LYRIC  # a 0.5 s breath after each line


def test_timed_lines_take_the_words_sung_inside_them(tmp_path):
    words = timed_words()
    given = [{"text": "", "start": 0.0, "end": 3.0}, {"text": "", "start": 3.0, "end": 9.0}]
    lines = singer.sung_lines(hook(tmp_path, words=words, lines=given))
    assert len(lines) >= 2 and lines[0].words[0].text == "Mere"
    assert sum(len(x.words) for x in lines) == len(words)


def test_a_very_quick_line_joins_the_next_and_a_long_one_is_cut(tmp_path):
    quick = [Word(text="Oh", start=0.0, end=0.2), *timed_words(["one two three four"], 1.0)]
    lines = singer.sung_lines(hook(tmp_path, words=quick, lines=[]))
    assert lines[0].text.startswith("Oh one")
    slow = [Word(text=f"w{i}", start=i * 0.9, end=i * 0.9 + 0.8) for i in range(6)]
    slow[3] = Word(text="w3", start=3.3, end=3.9)  # the widest gap is before w3
    lines = singer.sung_lines(hook(tmp_path, words=slow, lines=[]))
    assert len(lines) >= 2 and all(x.end - x.start <= singer.LONG_LINE for x in lines)


# ---- the writer's reply, held to the rules -----------------------------------------------


def test_a_reply_that_gives_nothing_still_makes_a_whole_edit(tmp_path):
    h = hook(tmp_path)
    lines = singer.sung_lines(h)
    plan = singer.plan_of({}, h, lines, edit_with_photos(), paints=False)
    assert plan.style == "singer" and len(plan.scenes) == len(lines)
    shots = [s.visual.shot for s in plan.scenes]
    assert shots[0] in ("hero", "close") and all(a != b for a, b in itertools.pairwise(shots))
    assert all(s.visual.kind == "verse" and s.visual.highlight for s in plan.scenes)
    assert plan.title == "Maa, I Tried - KiD Boi" and "#kidboi" in plan.caption
    assert plan.singer.lines == lines and plan.singer.audio == "c" * 24 + ".wav"


def test_the_writers_choices_are_kept_where_they_follow_the_rules(tmp_path):
    h = hook(tmp_path)
    lines = singer.sung_lines(h)
    reply = {
        "title": "Maa, I Tried - KiD Boi",
        "caption": "Khud se khush.\n\nSong: Maa, I Tried by KiD Boi\n#kidboi",
        "scenes": [
            {"line": 1, "shot": "far", "art": "a lone boy in a grey crowd", "highlight":
             "Mere jaisa nahi hai koi", "notes": ["Koi aur?"], "doodle": "⭐"},
            {"line": 2, "shot": "close", "art": "a mirror", "highlight": "auron", "notes": [],
             "doodle": "❓"},
            {"line": 3, "shot": "close", "art": "a room", "highlight": "nope", "notes":
             ["one two three four five six seven"], "doodle": "nothing"},
            {"line": 4, "shot": "painted", "art": "x", "highlight": "milu?", "notes": [],
             "doodle": ""},
        ],
    }  # fmt: skip
    plan = singer.plan_of(reply, h, lines, edit_with_photos(), paints=False)
    v = [s.visual for s in plan.scenes]
    assert v[0].shot in ("hero", "close")  # the reel opens on the singer
    assert v[0].highlight == "jaisa"  # the whole line given back: the rule picks
    assert v[1].highlight == "auron" and v[1].shot == "close"
    assert v[2].shot != "close" and v[2].highlight == "baatein sakta"  # a long line: two
    assert [i.text for i in v[2].items] == []  # a note too long to be one is dropped
    assert v[0].emoji == "⭐" and v[1].emoji == ""  # never two doodles running
    assert v[3].shot != "painted"  # nothing paints here
    assert v[0].art == "a lone boy in a grey crowd"


def test_without_a_cutout_the_photo_is_shown_whole(tmp_path):
    h = hook(tmp_path)
    plan = singer.plan_of({}, h, singer.sung_lines(h), edit_with_photos(cut=False), False)
    assert {s.visual.shot for s in plan.scenes} <= {"print", "words"}


def test_a_sung_line_asking_to_follow_is_never_dropped():
    def plan_of(kind):
        visual = {"kind": kind, "text": "follow me baby", "shot": "hero", "highlight": "me baby"}
        return Plan.model_validate(
            {"style": "singer", "scenes": [{"narration": "follow me baby", "visual": visual}]}
        )

    kept = tidy(plan_of("verse"))
    assert len(kept.scenes) == 1 and kept.scenes[0].visual.shot == "hero"
    assert kept.scenes[0].visual.highlight == "me baby"
    assert tidy(plan_of("title")).scenes == []  # the old rule stands for narrated reels


# ---- timeline and player ---------------------------------------------------------------


def test_scenes_follow_the_song_with_no_gap_and_end_with_it(tmp_path):
    h = hook(tmp_path)
    plan = singer.plan_of({}, h, singer.sung_lines(h), edit_with_photos(), False)
    tl = singer.timeline_of(plan)
    assert tl.scenes[0].start == 0.0 and tl.scenes[-1].end == tl.duration == 8.0
    assert all(a.end == b.start for a, b in zip(tl.scenes, tl.scenes[1:], strict=False))
    for scene, line in zip(tl.scenes, plan.singer.lines, strict=True):
        assert scene.start <= line.start and scene.beats == [w.start for w in line.words]
    data = singer.player_of(plan, tl)
    assert data["style"] == "singer" and data["captions"] == []
    assert data["singer"]["photos"][0]["cutout"] == "b" * 24 + ".png"
    assert data["scenes"][1]["visual"]["shot"] == plan.scenes[1].visual.shot
    settings, _ = studio_env(tmp_path)
    assert studio.player_of(plan, tl, settings) == data  # the studio hands singer edits over


def test_an_edit_by_hand_keeps_what_is_sung(tmp_path):
    h = hook(tmp_path)
    plan = singer.plan_of({}, h, singer.sung_lines(h), edit_with_photos(), False)
    stored = plan.model_dump(mode="json")
    changed = json.loads(json.dumps(stored))
    changed["scenes"][0]["narration"] = "something else"
    changed["scenes"][0]["visual"]["text"] = "something else"
    changed["scenes"][0]["visual"]["shot"] = "print"
    changed["scenes"][0]["visual"]["highlight"] = "koi"
    changed["singer"]["audio"] = "../../etc/passwd"
    kept = singer.edited(stored, Plan.model_validate(changed))
    assert kept.scenes[0].narration == LYRIC[0] and kept.scenes[0].visual.text == LYRIC[0]
    assert kept.scenes[0].visual.shot == "print" and kept.scenes[0].visual.highlight == "koi"
    assert kept.singer.audio == "c" * 24 + ".wav"
    fewer = json.loads(json.dumps(stored))
    fewer["scenes"] = fewer["scenes"][1:]
    with pytest.raises(ValueError, match="one scene for each sung line"):
        singer.edited(stored, Plan.model_validate(fewer))


# ---- photos ----------------------------------------------------------------------------


def test_photos_are_kept_by_their_content_and_bad_files_refused(tmp_path):
    settings, _ = studio_env(tmp_path)
    data = photo_png()
    name = singer.save_photo(data, settings)
    assert name == singer.save_photo(data, settings)
    assert (images.folder(settings) / name).exists() and name.endswith(".png")
    with pytest.raises(ValueError, match="isn't a photo"):
        singer.save_photo(b"not a picture", settings)
    with pytest.raises(ValueError, match="only 50x50 pixels"):
        singer.save_photo(photo_png(size=(50, 50)), settings)


def test_stray_blobs_leave_the_cutout():
    mask = np.zeros((100, 100), dtype=np.float32)
    mask[20:90, 30:70] = 1.0  # the singer
    mask[2:5, 2:5] = 1.0  # a speck
    kept = singer.keep_subject(mask)
    assert kept[50, 50] == 1.0 and kept[3, 3] == 0.0


def test_the_singer_is_cut_out_and_their_face_found(tmp_path):
    settings, _ = studio_env(tmp_path)
    name = singer.save_photo(photo_png(size=(400, 800)), settings)

    def mask_of(picture, model):
        mask = np.zeros((picture.height, picture.width), dtype=np.float32)
        mask[100:780, 120:280] = 1.0
        return mask

    cut = singer.cut_out(name, settings, mask_of=mask_of, face_of=lambda p, s: [150, 110, 100, 120])
    assert cut is not None and 0.2 < cut.aspect < 0.4
    picture = Image.open(images.folder(settings) / cut.name)
    assert picture.mode == "RGBA" and picture.getpixel((0, 0))[3] == 0
    assert 0 < cut.face[0] < 0.5 and cut.face[1] < 0.2
    # kept: the second time nothing is worked out again
    again = singer.cut_out(name, settings, mask_of=lambda *a: 1 / 0)
    assert again == cut
    empty = singer.save_photo(photo_png(color=(10, 10, 10)), settings)
    nobody = lambda picture, model: np.zeros((picture.height, picture.width), np.float32)  # noqa: E731
    assert singer.cut_out(empty, settings, mask_of=nobody, face_of=lambda p, s: None) is None


# ---- the hook finder, whichever way it answers -------------------------------------------


@needs_ffmpeg
def test_a_whole_song_with_its_hook_place_is_cut_and_its_times_moved(tmp_path):
    settings, _ = studio_env(tmp_path)
    song = tone_wav(tmp_path / "song.wav", 20.0)
    found = {
        "audio": str(song), "start": 10.0, "end": 16.0, "title": "T", "artist": "A",
        "words": [{"text": "one", "start": 10.5, "end": 10.9},
                  {"text": "two", "start": 11.0, "end": 11.4},
                  {"text": "three", "start": 12.0, "end": 12.5},
                  {"text": "late", "start": 17.0, "end": 17.5}],
    }  # fmt: skip
    h = singer.hook_of(found, settings)
    assert h.duration == pytest.approx(6.0, abs=0.05)
    assert [w.text for w in h.words] == ["one", "two", "three"] and h.words[0].start == 0.5
    assert h.audio.parent == singer.songs_dir(settings)


@needs_ffmpeg
def test_a_songhook_song_gives_its_lines_words_in_the_lyrics_spelling(tmp_path, monkeypatch):
    settings, _ = studio_env(tmp_path)
    audio = tone_wav(tmp_path / "song.wav", 30.0)
    found = Song(
        title="Maa, I Tried", artist="KiD Boi", video="abc", start=10.0, end=16.0, lines=[
            SongLine(text="मेरे जैसा", said="Mere jaisa", start=10.2, end=11.5, words=[
                SongWord(text="मेरे", start=10.3, end=10.7),
                SongWord(text="जैसा", start=10.8, end=11.4)]),
            SongLine(text="नहीं है कोई", said="Nahi hai koi", start=11.6, end=13.0, words=[
                SongWord(text="नहीं", start=11.7, end=12.0),
                SongWord(text="है", start=12.1, end=12.2),
                SongWord(text="कोई", start=12.3, end=12.8)]),
        ],
    )  # fmt: skip
    asked = []
    monkeypatch.setattr(songhook, "find", lambda idea, *a, **k: asked.append(idea) or found)
    monkeypatch.setattr(songhook, "audio_path", lambda settings, s: audio)
    h = singer.find_hook("maa i tried | mere jaisa", settings, router=object())
    assert asked == ["maa i tried | mere jaisa"]
    assert [w.text for w in h.words] == ["Mere", "jaisa", "Nahi", "hai", "koi"]
    assert h.words[0].start == pytest.approx(0.3) and h.duration == pytest.approx(6.0, abs=0.05)
    assert [x.text for x in singer.sung_lines(h)] == ["Mere jaisa", "Nahi hai koi"]

    def nothing(*a, **k):
        raise songhook.SongError("the song has no part of 15-32 s made of whole lines")

    monkeypatch.setattr(songhook, "find", nothing)
    with pytest.raises(singer.SingerError, match="15-32 s"):
        singer.find_hook("maa i tried", settings, router=object())


@needs_ffmpeg
def test_the_song_finders_own_song_makes_the_hook(tmp_path):
    """The Song story's finder, end to end on stand-ins, gives a singer edit its hook: the
    chosen part's sound, its words timed from the part's start."""
    settings, _ = studio_env(tmp_path)
    found, router = song_finder_stand_ins(tmp_path, settings)
    song = songhook.find("Koi Apna Hoga Tony Kakkar", settings, router, **found)
    h = singer.find_hook(
        "Koi Apna Hoga Tony Kakkar", settings, router,
        finder=lambda q, settings: songhook.find(q, settings, router, **found),
    )  # fmt: skip
    assert h.duration == pytest.approx(song.end - song.start, abs=0.05)
    sung = [w for line in song.lines for w in line.words]
    assert len(h.words) == len(sung) and h.words[0].start == pytest.approx(
        sung[0].start - song.start, abs=0.01
    )
    assert len(singer.sung_lines(h)) >= len(song.lines) - 1


# ---- the studio's work -------------------------------------------------------------------


class Writer:
    def __init__(self, reply):
        self.reply = reply
        self.asked = []

    def chat_json(self, messages, schema, images=None, temperature=0.3):
        self.asked.append(messages)
        return self.reply


class Router:
    def __init__(self, writer, eyes=None):
        self.writer, self.eyes = writer, eyes

    def planner(self):
        return nullcontext(self.writer)

    def vision(self):
        return nullcontext(self.eyes)


def fake_cut(name, settings):
    return singer.Cutout("d" * 24 + ".png", [0.3, 0.05, 0.4, 0.15], 0.5)


def made_singer(Session, settings, photos=1):
    names = [singer.save_photo(photo_png(color=(i * 30, 90, 40)), settings) for i in range(photos)]
    with Session() as db:
        plan = singer.skeleton("maa i tried kid boi", names)
        db.add(Animation(id="s1", owner_id="u1", idea="maa i tried kid boi", style="singer",
                         task={"op": "draft"}, plan=plan))  # fmt: skip
        db.commit()
    return names


def test_a_singer_edit_is_made_from_the_song_and_the_photos(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    names = made_singer(Session, settings, photos=2)
    h = hook(tmp_path)
    monkeypatch.setattr(singer, "find_hook", lambda *a, **k: h)
    writer = Writer({"title": "Maa, I Tried - KiD Boi", "caption": "c", "scenes": [
        {"line": 1, "shot": "close", "art": "a crowd", "highlight": "koi", "notes": [],
         "doodle": ""}]})  # fmt: skip
    monkeypatch.setattr(singer, "cut_out", fake_cut)
    studio.run_task("s1", Session, settings, Router(writer), paint=None)
    with Session() as db:
        a = db.get(Animation, "s1")
        assert a.status == "ready", a.error
        plan = Plan.model_validate(a.plan)
    assert len(plan.scenes) == 4 and plan.scenes[0].visual.shot == "close"
    assert [p.image for p in plan.singer.photos] == names
    assert all(p.cutout == "d" * 24 + ".png" for p in plan.singer.photos)
    assert "Maa, I Tried" in writer.asked[0][-1]["content"]
    assert a.player["style"] == "singer" and a.history == []  # the empty start isn't kept


def test_photos_nobody_stands_out_in_are_shown_whole(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    made_singer(Session, settings)
    h = hook(tmp_path)
    monkeypatch.setattr(singer, "find_hook", lambda *a, **k: h)
    singer.run_task("s1", Session, settings, Router(Writer({})), cutter=lambda n, s: None)
    with Session() as db:
        a = db.get(Animation, "s1")
        plan = Plan.model_validate(a.plan)
    assert a.status == "ready" and any("as prints" in n for n in a.notes)
    assert {s.visual.shot for s in plan.scenes} <= {"print", "words"}


def test_the_writer_failing_still_gives_an_edit_and_a_scene_can_be_written_again(
    tmp_path, monkeypatch
):
    settings, Session = studio_env(tmp_path)
    made_singer(Session, settings)
    h = hook(tmp_path)
    monkeypatch.setattr(singer, "find_hook", lambda *a, **k: h)

    class Broken:
        def chat_json(self, *a, **k):
            raise RuntimeError("model down")

    singer.run_task("s1", Session, settings, Router(Broken()), cutter=fake_cut)
    with Session() as db:
        a = db.get(Animation, "s1")
        assert a.status == "ready" and any("by rule" in n for n in a.notes)
        before = Plan.model_validate(a.plan)
        a.task = {"op": "scene", "scene": 2, "request": "make it lonely"}
        db.commit()
    reply = {"title": "x", "caption": "y", "scenes": [
        {"line": 3, "shot": "far", "art": "an empty road", "highlight": "khud", "notes":
         ["Akela?"], "doodle": ""}]}  # fmt: skip
    writer = Writer(reply)
    singer.run_task("s1", Session, settings, Router(writer), cutter=fake_cut)
    with Session() as db:
        a = db.get(Animation, "s1")
        after = Plan.model_validate(a.plan)
    assert after.scenes[2].visual.art == "an empty road"
    assert after.scenes[0] == before.scenes[0] and after.title == before.title
    assert "make it lonely" in writer.asked[0][-1]["content"] and len(a.history) == 1


def test_no_song_is_a_plain_failure(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    made_singer(Session, settings)

    def nothing(*a, **k):
        raise singer.SingerError("no song found for 'maa i tried kid boi'")

    monkeypatch.setattr(singer, "find_hook", nothing)
    studio.run_task("s1", Session, settings, Router(Writer({})))
    with Session() as db:
        a = db.get(Animation, "s1")
        assert a.status == "failed" and a.error == "no song found for 'maa i tried kid boi'"


def test_pictures_are_painted_empty_behind_the_photo_and_with_the_singer_when_painted(tmp_path):
    settings, _ = studio_env(tmp_path)
    h = hook(tmp_path)
    edit = edit_with_photos().model_copy(update={"look": "a young man in a brown hoodie"})
    reply = {"scenes": [
        {"line": 1, "shot": "hero", "art": "an empty street", "highlight": "", "notes": [],
         "doodle": ""},
        {"line": 2, "shot": "painted", "art": "he walks into the sun", "highlight": "",
         "notes": [], "doodle": ""}]}  # fmt: skip
    plan = singer.plan_of(reply, h, singer.sung_lines(h), edit, paints=True)
    asked = []

    def paint(pictures, style, settings, progress):
        asked.extend(pictures)
        assert style == "singer"
        return ["e" * 24 + ".png"] * len(pictures)

    painted, notes = singer.paint_plan(plan, settings, paint, lambda *a: None)
    assert asked[0].empty and not asked[1].empty
    assert "brown hoodie" in asked[1].prompt and "brown hoodie" not in asked[0].prompt
    assert painted.scenes[0].visual.image == "e" * 24 + ".png" and notes == []
    assert "amber" in images.full_prompt(asked[0], "singer")


def test_a_singer_edit_renders_through_its_own_renderer(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    h = hook(tmp_path)
    plan = singer.plan_of({}, h, singer.sung_lines(h), edit_with_photos(), False)
    with Session() as db:
        db.add(Animation(id="s2", owner_id="u1", idea="x", style="singer",
                         plan=plan.model_dump(mode="json"), status="ready"))  # fmt: skip
        db.commit()

    def draw(plan, out_dir, settings, progress=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "animation.mp4").write_bytes(b"video")
        return out_dir / "animation.mp4", None, []

    monkeypatch.setattr(singer, "render", draw)
    result, used = studio.render_job("s2", Session, settings, lambda *a: None, "run1")
    out = result.outputs[0]
    assert out.preset == "animation-singer" and out.song == ("Maa, I Tried", "KiD Boi")
    assert out.edl.clips[0].end == 8.0 and used.style == "singer"
    assert studio.label("animation-singer") == "Animation · Singer edit"


# ---- the routes --------------------------------------------------------------------------


@pytest.fixture
def singer_env(env, monkeypatch, tmp_path):
    env.settings.cache_dir = tmp_path / "cache"
    studio_on(env)
    h = hook(tmp_path)
    monkeypatch.setattr(singer, "find_hook", lambda *a, **k: h)
    monkeypatch.setattr(singer, "cut_out", fake_cut)
    monkeypatch.setattr(
        jobs, "animate",
        lambda animation_id: studio.run_task(animation_id, env.sessions, env.settings,
                                             Router(Writer({}))),
    )  # fmt: skip
    return env


def test_photos_are_uploaded_and_a_singer_edit_made_from_them(singer_env):
    client = singer_env.client
    sent = client.post(
        "/api/animation-photos", files={"file": ("me.png", photo_png(), "image/png")}
    )
    assert sent.status_code == 200, sent.text
    name = sent.json()["name"]
    assert client.get(f"/api/animation-images/{name}").status_code == 200
    bad = client.post("/api/animation-photos", files={"file": ("x.txt", b"hello", "text/plain")})
    assert bad.status_code == 422

    body = {"idea": "maa i tried kid boi", "style": "singer"}
    assert client.post("/api/animations", json=body).status_code == 422  # no photo
    missing = {**body, "photos": ["f" * 24 + ".png"]}
    assert client.post("/api/animations", json=missing).status_code == 422
    narrated = {"idea": "the lost city", "photos": [name]}
    assert client.post("/api/animations", json=narrated).status_code == 422

    made = client.post("/api/animations", json={**body, "photos": [name]})
    assert made.status_code == 200, made.text
    a = made.json()
    assert a["status"] == "ready", a["error"]
    assert a["style"] == "singer" and a["scenes"] == 4 and a["player"]["style"] == "singer"
    assert a["plan"]["singer"]["photos"][0]["image"] == name

    # its style stays; a narrated reel can't become one either
    switch = client.patch(f"/api/animations/{a['id']}", json={"style": "glow"})
    assert switch.status_code == 422
    plan = a["plan"]
    plan["scenes"][1]["visual"]["shot"] = "words"
    edited = client.put(f"/api/animations/{a['id']}", json={"plan": plan})
    assert (
        edited.status_code == 200
        and edited.json()["plan"]["scenes"][1]["visual"]["shot"] == "words"
    )
    plan["scenes"] = plan["scenes"][:2]
    assert client.put(f"/api/animations/{a['id']}", json={"plan": plan}).status_code == 422


def test_the_player_page_brings_the_singer_style_along(singer_env):
    page = singer_env.client.get("/api/animation-player/player.html")
    assert page.status_code == 200 and 'src="singer.js?v=' in page.text
    script = singer_env.client.get("/api/animation-player/singer.js")
    assert script.status_code == 200 and "PlayerStyles" in script.text
    font = singer_env.client.get("/api/animation-player/Kalam-Bold.ttf")
    assert font.status_code == 200


def test_the_head_is_found_from_the_outline_and_a_wild_face_box_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(singer, "SMALL_SIDE", 100)  # kept at its own size: the mask lines up
    solid = np.zeros((800, 300), dtype=bool)
    yy, xx = np.mgrid[0:800, 0:300]
    solid |= (xx - 150) ** 2 + (yy - 60) ** 2 < 45**2  # the head
    solid[100:120, 135:165] = True  # the neck
    solid[120:800, 40:260] = True  # shoulders down
    x, y, w, h = singer.head_of(solid)
    assert abs((x + w / 2) - 0.5) < 0.05 and y < 0.03
    assert 0.25 < w < 0.4 and 0.11 < h < 0.17  # down to the neck at 100-120 of 800
    settings, _ = studio_env(tmp_path)
    name = singer.save_photo(photo_png(size=(300, 800)), settings)
    mask = lambda picture, models: solid.astype(np.float32)  # noqa: E731
    jacket = lambda picture, settings: [40, 0, 220, 200]  # noqa: E731
    cut = singer.cut_out(name, settings, mask_of=mask, face_of=jacket)
    assert cut.face[2] < 0.5  # the face finder's jacket-wide box wasn't taken


# ---- marks on the photos ---------------------------------------------------------------


def textured(size=(400, 300)) -> Image.Image:
    """A photo-like picture: smooth shading and fine grain, no flat colour."""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0 : size[1], 0 : size[0]]
    base = np.stack([60 + xx * 0.3, 90 + yy * 0.25, 140 + (xx + yy) * 0.1], axis=2)
    base = base + rng.normal(0, 4, base.shape)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def test_a_stamp_is_painted_out_of_the_photo_and_its_neighbours_with_it(tmp_path, monkeypatch):
    monkeypatch.setattr(singer, "SMALL_SIDE", 100)  # kept at its own size: boxes line up
    from PIL import ImageDraw, ImageFont

    from agentic_editor.analysis.ocr import Line
    from agentic_editor.settings import ROOT

    settings, _ = studio_env(tmp_path)
    clean = textured()
    stamped = clean.copy()
    draw = ImageDraw.Draw(stamped)
    font = ImageFont.truetype(str(ROOT / "fonts" / "Montserrat-Bold.ttf"), 22)
    draw.text((40, 120), "@pageofsongs", font=font, fill=(255, 255, 255))
    draw.text((40, 95), "SONGS", font=font, fill=(255, 255, 255))
    draw.text((250, 250), "Live", font=font, fill=(255, 255, 255))  # background, no stamp
    out = io.BytesIO()
    stamped.save(out, "PNG")
    name = singer.save_photo(out.getvalue(), settings)
    lines = [
        Line(x0=40, y0=122, x1=200, y1=146, text="@pageofsongs", score=0.99),
        Line(x0=40, y0=97, x1=120, y1=121, text="SONGS", score=0.99),
        Line(x0=250, y0=252, x1=300, y1=276, text="Live", score=0.99),
    ]
    nobody = lambda picture, models: np.zeros((picture.height, picture.width), np.float32)  # noqa: E731
    kept, marks = singer.clean_photo(name, settings, read=lambda pic: lines, mask_of=nobody)
    assert marks == 2 and kept != name
    after = np.asarray(Image.open(images.folder(settings) / kept).convert("RGB"), dtype=float)
    before = np.asarray(stamped, dtype=float)
    plain = np.asarray(clean, dtype=float)
    box = (slice(95, 150), slice(40, 200))
    assert np.abs(before[box] - plain[box]).mean() > 3 * np.abs(after[box] - plain[box]).mean()
    corner = (slice(250, 280), slice(250, 300))
    assert np.array_equal(after[corner], before[corner])  # background words stay
    # kept: the second time nothing is read again
    assert singer.clean_photo(name, settings, read=lambda pic: 1 / 0) == (kept, 2)


def test_any_words_on_the_singer_go_and_a_clean_photo_stays_as_it_is(tmp_path):
    from agentic_editor.analysis.ocr import Line

    settings, _ = studio_env(tmp_path)
    picture = textured()
    out = io.BytesIO()
    picture.save(out, "PNG")
    name = singer.save_photo(out.getvalue(), settings)

    def person(pic, models):
        mask = np.zeros((pic.height, pic.width), np.float32)
        mask[50:300, 100:250] = 1.0
        return mask

    on_shirt = [Line(x0=120, y0=150, x1=220, y1=170, text="BRAND", score=0.9)]
    kept, marks = singer.clean_photo(name, settings, read=lambda p: on_shirt, mask_of=person)
    assert marks == 1 and kept != name
    other = singer.save_photo(photo_png(color=(10, 120, 200)), settings)
    assert singer.clean_photo(other, settings, read=lambda p: []) == (other, 0)


def test_photos_are_cleaned_before_the_singer_is_cut_out(tmp_path):
    settings, _ = studio_env(tmp_path)
    names = [singer.save_photo(photo_png(color=(i * 40, 80, 60)), settings) for i in range(2)]
    cut_from = []

    def cleaner(name, settings):
        return ("e" * 24 + ".png", 1) if name == names[0] else (name, 0)

    def cutter(name, settings):
        cut_from.append(name)
        return fake_cut(name, settings)

    (images.folder(settings) / ("e" * 24 + ".png")).write_bytes(photo_png())
    photos, wiped = singer.photos_of(names, settings, cutter, cleaner)
    assert wiped == 1 and cut_from == ["e" * 24 + ".png", names[1]]
    assert photos[0].image == "e" * 24 + ".png" and photos[1].image == names[1]


# ---- the singer painted in from their photos ------------------------------------------------


def likeness_plan(tmp_path, settings) -> Plan:
    h = hook(tmp_path)
    photo_name = singer.save_photo(photo_png(size=(300, 600)), settings)
    cut = Image.new("RGBA", (200, 500), (0, 0, 0, 0))
    cut.paste(Image.new("RGBA", (120, 460), (200, 120, 90, 255)), (40, 20))
    cut_name = "c" * 24 + ".png"
    cut.save(images.folder(settings) / cut_name)
    edit = edit_with_photos().model_copy(
        update={
            "photos": [
                SingerPhoto(
                    image=photo_name, cutout=cut_name, face=[0.3, 0.05, 0.4, 0.15], aspect=0.4
                )
            ]
        }
    )
    reply = {"scenes": [
        {"line": 1, "shot": "hero", "art": "a rain-soaked rooftop at night", "highlight": "",
         "notes": [], "doodle": ""},
        {"line": 2, "shot": "words", "art": "an old brick wall", "highlight": "", "notes": [],
         "doodle": ""},
        {"line": 3, "shot": "mirror", "art": "a dusty dressing room", "highlight": "",
         "notes": [], "doodle": ""},
        {"line": 4, "shot": "print", "art": "a pinboard", "highlight": "", "notes": [],
         "doodle": ""}]}  # fmt: skip
    return singer.plan_of(reply, h, singer.sung_lines(h), edit, paints=True)


def test_with_the_likeness_model_the_singer_is_painted_into_their_scenes(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = likeness_plan(tmp_path, settings)
    asked = []

    def likeness(pictures, settings, progress):
        asked.extend(pictures)
        return ["f" * 24 + ".png" if i != 2 else "" for i in range(len(pictures))]

    painted, notes = singer.paint_plan(plan, settings, images.paint, None, likeness)
    shots = [s.visual.shot for s in plan.scenes]
    assert shots == ["hero", "words", "mirror", "print"]
    assert asked[0].refs and not asked[1].refs and asked[2].refs and not asked[3].refs
    assert "reference photo" in asked[0].prompt and "rooftop" in asked[0].prompt
    assert "mirror" in asked[2].prompt and "no letters" in asked[2].prompt
    assert "nobody in it" in asked[1].prompt and "reference" not in asked[1].prompt
    reference = Image.open(asked[0].refs[0])
    assert reference.mode == "RGB" and reference.getpixel((0, 0)) == (128, 128, 128)
    v = [s.visual for s in painted.scenes]
    assert v[0].with_singer and v[0].image == "f" * 24 + ".png"
    assert not v[1].with_singer and v[1].image
    # a picture that couldn't be painted: the singer cut out stands in, as without the model
    assert not v[2].with_singer and v[2].image == "" and "photo cut out" in notes[0]
    data = singer.player_of(painted, singer.timeline_of(painted))
    assert data["scenes"][0]["visual"]["with_singer"] is True


def test_the_likeness_painter_asks_the_runner_for_klein_with_the_photos(tmp_path):
    from tests.test_animate import FakeProc

    settings, _ = studio_env(tmp_path)
    settings.identity_model = "mflux-community/flux2-klein-4b-mflux-q8"
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (128, 128, 128)).save(ref)
    pics = [images.Picture("the person on a rooftop", 5, refs=(str(ref),)),
            images.Picture("an empty rooftop", 6)]  # fmt: skip
    names = images.paint_likeness(pics, settings, run=FakeProc)
    asked = FakeProc.last
    assert asked["kind"] == "likeness" and asked["model"] == settings.identity_model
    assert asked["steps"] == 4 and (asked["width"], asked["height"]) == (720, 1280)
    assert asked["jobs"][0]["refs"] == [str(ref)] and asked["jobs"][1]["refs"] == []
    assert all(names) and names[0] != names[1]
    assert images.likeness_name(pics[0], "m") != images.likeness_name(
        images.Picture("the person on a rooftop", 5, refs=("/x/other.png",)), "m"
    )
    assert not images.identity_available(settings)  # not on this machine


def test_without_the_likeness_model_today_s_cutouts_stay(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    made_singer(Session, settings)
    h = hook(tmp_path)
    monkeypatch.setattr(singer, "find_hook", lambda *a, **k: h)
    monkeypatch.setattr(singer, "cut_out", fake_cut)
    monkeypatch.setattr(singer, "clean_photo", lambda name, settings: (name, 0))
    singer.run_task("s1", Session, settings, Router(Writer({})))
    with Session() as db:
        plan = Plan.model_validate(db.get(Animation, "s1").plan)
    assert not any(s.visual.with_singer or s.visual.image for s in plan.scenes)


def test_the_paint_runner_loads_klein_for_likenesses(tmp_path, monkeypatch):
    import sys
    import types

    from agentic_editor.animate import paint_runner

    made = {}

    class Klein:
        def __init__(self, model_config=None, model_path=None):
            made["klein"] = (model_config, model_path)

    config = types.SimpleNamespace(flux2_klein_4b=lambda: "klein-4b", z_image_turbo=lambda: "zit")
    modules = {
        "mflux": types.ModuleType("mflux"),
        "mflux.models": types.ModuleType("mflux.models"),
        "mflux.models.common": types.ModuleType("mflux.models.common"),
        "mflux.models.common.config": types.SimpleNamespace(ModelConfig=config),
        "mflux.models.flux2": types.ModuleType("mflux.models.flux2"),
        "mflux.models.flux2.variants": types.SimpleNamespace(Flux2KleinEdit=Klein),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    paint_runner.load({"kind": "likeness", "model": "some/klein-q8"})
    assert made["klein"] == ("klein-4b", "some/klein-q8")


def test_the_painted_face_is_found_and_kept_for_the_words_to_keep_off(tmp_path):
    settings, _ = studio_env(tmp_path)
    path = tmp_path / "painted.png"
    Image.new("RGB", (720, 1280), (90, 60, 40)).save(path)
    face = singer.painted_face(path, settings, face_of=lambda picture, s: [300, 200, 120, 160])
    assert face == [round(300 / 720, 4), round(200 / 1280, 4), round(120 / 720, 4), 0.125]
    assert singer.painted_face(path, settings, face_of=lambda picture, s: None) == []
    assert singer.painted_face(tmp_path / "missing.png", settings) == []

    def verse(face):
        visual = {"kind": "verse", "text": "a b", "shot": "hero", "face": face}
        scenes = [{"narration": "a b", "visual": visual}]
        return tidy(Plan.model_validate({"style": "singer", "scenes": scenes})).scenes[0].visual

    assert verse(face).face == face
    assert verse([2, 0, 0, 0]).face == []


def test_a_picture_is_never_asked_for_writing_or_a_photo():
    assert (
        singer.place_of("Huge words filling the frame, no singer visible.") == singer.FALLBACK_ART
    )
    assert (
        singer.place_of("A rainy street at night, a neon sign glowing") == "A rainy street at night"
    )
    assert singer.place_of("Torn photo taped to a wall") == singer.FALLBACK_ART
    assert "typography" not in singer.likeness_prompt("hero", "a rooftop at dusk, bold typography")


# ---- any photo taken in and made ready ----------------------------------------------------


def encoded(picture: Image.Image, kind: str, **options) -> bytes:
    out = io.BytesIO()
    picture.save(out, kind, **options)
    return out.getvalue()


def test_any_photo_a_phone_or_a_browser_saves_is_taken(tmp_path):
    settings, _ = studio_env(tmp_path)
    face = textured((900, 700))
    sent = {
        "jpeg-cmyk": encoded(face.convert("CMYK"), "JPEG"),
        "webp": encoded(face, "WEBP"),
        "png-alpha": encoded(Image.new("RGBA", (700, 700), (0, 0, 0, 0)), "PNG"),
        "png-16bit": encoded(
            Image.fromarray((np.arange(700 * 700).reshape(700, 700) % 60000).astype(np.uint16)),
            "PNG",
        ),
        "gif": encoded(
            face.convert("P"), "GIF", save_all=True, append_images=[Image.new("P", face.size, 3)]
        ),
    }
    from PIL import features

    if features.check("avif"):
        sent["avif"] = encoded(face, "AVIF")
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        sent["heic"] = encoded(face, "HEIF")
    except (ImportError, KeyError, OSError):
        pass
    for kind, data in sent.items():
        name = singer.save_photo(data, settings)
        kept = Image.open(images.folder(settings) / name)
        assert kept.mode == "RGB" and min(kept.size) >= singer.SMALL_SIDE, kind
        if kind == "png-alpha":
            assert kept.getpixel((10, 10)) == (255, 255, 255)  # see-through on white
        if kind == "gif":
            assert np.asarray(kept).std() > 5  # the first frame, not the plain second one
    assert {"webp", "png-16bit"} <= set(sent)


def test_a_small_photo_is_enlarged_and_a_tiny_one_refused_saying_why(tmp_path):
    settings, _ = studio_env(tmp_path)
    taken = singer.take_photo(encoded(textured((197, 197)), "WEBP"), settings)
    kept = Image.open(images.folder(settings) / taken.name)
    assert min(kept.size) == singer.ENLARGE_SIDE and "197x197" in taken.notes[0]
    with pytest.raises(singer.PhotoRefused, match="only 100x80 pixels") as refused:
        singer.take_photo(encoded(textured((100, 80)), "PNG"), settings)
    assert refused.value.details["format"] == "PNG" and refused.value.details["pixels"] == "100x80"
    assert refused.value.details["bytes"] > 0
    with pytest.raises(singer.PhotoRefused, match="isn't a photo") as refused:
        singer.take_photo(b"%PDF-1.4 not a picture", settings)
    assert refused.value.details["format"] == "unknown"


def test_a_huge_photo_is_made_smaller_and_a_bomb_refused(tmp_path, monkeypatch):
    settings, _ = studio_env(tmp_path)
    name = singer.save_photo(
        encoded(Image.new("RGB", (4000, 3000), (90, 30, 30)), "JPEG"), settings
    )
    assert Image.open(images.folder(settings) / name).size == (1600, 1200)
    monkeypatch.setattr(singer, "MAX_PIXELS", 1_000_000)
    with pytest.raises(singer.PhotoRefused, match="far too big"):
        singer.take_photo(encoded(Image.new("RGB", (1200, 1000)), "PNG"), settings)


def test_borders_and_screenshot_bars_are_trimmed():
    from PIL import ImageDraw

    shot = Image.new("RGB", (600, 1300), (250, 250, 250))
    draw = ImageDraw.Draw(shot)
    draw.text((20, 10), "9:41", fill=(0, 0, 0))  # a status bar with a little writing
    draw.rectangle((540, 12, 580, 22), fill=(0, 0, 0))
    shot.paste(textured((600, 700)), (0, 250))
    draw.rectangle((0, 1240, 600, 1300), fill=(20, 20, 20))  # the navigation bar
    left, top, right, bottom = singer.bars_of(shot)
    assert (left, right) == (0, 600) and 240 <= top <= 251 and 949 <= bottom <= 951
    assert singer.bars_of(Image.new("RGB", (300, 300), (0, 0, 0))) == (0, 0, 300, 300)


def test_a_group_photo_is_cropped_to_its_main_person_with_their_face():
    solid = np.zeros((600, 900), dtype=bool)
    solid[200:600, 100:320] = True  # someone on the left
    solid[120:600, 300:620] = True  # the singer, nearest the camera, touching them
    solid[220:600, 600:820] = True  # someone on the right
    faces = [(170, 210, 60, 70), (420, 130, 110, 130), (680, 230, 60, 70)]
    left, top, right, bottom = singer.main_person(solid, faces)
    assert 230 < left < 330 and 600 < right < 720  # between the neighbours' faces
    assert top < 130 and bottom == 600
    assert singer.main_person(np.zeros((10, 10), bool), []) is None


def test_a_photo_is_made_ready_once_and_says_what_was_done(tmp_path):
    from agentic_editor.analysis.ocr import Line

    settings, _ = studio_env(tmp_path)
    cover = Image.new("RGB", (800, 800), (5, 5, 5))
    cover.paste(textured((300, 450)), (250, 300))
    name = singer.save_photo(encoded(cover, "PNG"), settings)
    title = [Line(x0=200, y0=60, x1=600, y1=110, text="Billy Joel / Piano Man", score=0.9)]

    def person(pic, models):
        mask = np.zeros((pic.height, pic.width), np.float32)
        mask[300:750, 250:550] = 1.0
        return mask

    made, notes = singer.prepare_photo(
        name, settings, read=lambda pic: title, mask_of=person,
        faces_of=lambda pic, s: [(350, 330, 100, 120)],
    )  # fmt: skip
    assert (
        made != name and "text painted out (1 places)" in notes and "cropped to the singer" in notes
    )
    ready = Image.open(images.folder(settings) / made)
    assert ready.width < ready.height and min(ready.size) >= singer.SMALL_SIDE
    again = singer.prepare_photo(name, settings, read=lambda pic: 1 / 0)
    assert again == (made, notes)
    plain = singer.save_photo(photo_png(color=(40, 40, 40)), settings)
    nobody = lambda pic, models: np.zeros((pic.height, pic.width), np.float32)  # noqa: E731
    same = singer.prepare_photo(plain, settings, read=lambda pic: [], mask_of=nobody,
                                faces_of=lambda pic, s: [])  # fmt: skip
    assert same == (plain, [])


def test_a_refused_upload_is_logged_with_why_and_the_reason_sent_back(singer_env, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    tiny = encoded(textured((90, 90)), "WEBP")
    sent = singer_env.client.post(
        "/api/animation-photos", files={"file": ("OIP.webp", tiny, "image/webp")}
    )
    assert sent.status_code == 422 and "only 90x90 pixels" in sent.json()["detail"]
    logged = [r.getMessage() for r in caplog.records if "singer photo refused" in r.getMessage()]
    assert (
        logged
        and "WEBP" in logged[0]
        and f"{len(tiny)} bytes" in logged[0]
        and "90x90" in logged[0]
    )
    small = encoded(textured((197, 197)), "WEBP")
    taken = singer_env.client.post(
        "/api/animation-photos", files={"file": ("OIP.webp", small, "image/webp")}
    )
    assert taken.status_code == 200 and "197x197" in taken.json()["notes"][0]


def test_a_small_face_away_from_the_singer_leaves_the_outline():
    solid = np.zeros((600, 900), dtype=bool)
    solid[100:600, 300:700] = True  # the singer
    solid[250:600, 60:300] = True  # a drummer behind, touching them
    faces = [(450, 120, 120, 140), (120, 270, 60, 70)]
    kept = singer.without_others(solid, faces)
    assert not kept[290, 140] and kept[300, 500]  # the drummer's head gone, the singer kept
    assert singer.without_others(solid, faces[:1]) is solid


def test_with_no_face_the_crop_keeps_room_for_hair_and_shoulders():
    solid = np.zeros((800, 800), dtype=bool)
    solid[300:600, 300:500] = True  # only the bright face of a dark cover
    left, top, right, bottom = singer.main_person(solid, [])
    assert left <= 240 and right >= 560 and top <= 225 and bottom >= 675


def test_a_face_alone_is_painted_from_the_whole_photo_and_the_look_named(tmp_path):
    settings, _ = studio_env(tmp_path)
    folder = images.folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (200, 260), (200, 160, 140, 255)).save(folder / ("c" * 24 + ".png"))
    head = SingerPhoto(
        image="a" * 24 + ".png", cutout="c" * 24 + ".png", face=[0.1, 0.05, 0.8, 0.8]
    )
    assert singer.reference_of(head, settings) == folder / head.image
    body = head.model_copy(update={"face": [0.3, 0.02, 0.4, 0.15]})
    assert singer.reference_of(body, settings) != folder / head.image
    prompt = singer.likeness_prompt("hero", "a rooftop at dusk", "a man with dark wavy hair")
    assert "reference photo (a man with dark wavy hair)" in prompt


def test_a_face_alone_is_shown_whole_not_cut_out(tmp_path):
    settings, _ = studio_env(tmp_path)
    names = [singer.save_photo(photo_png(color=(i * 50, 60, 70)), settings) for i in range(2)]

    def cutter(name, settings):
        face = [0.1, 0.05, 0.8, 0.8] if name == names[0] else [0.3, 0.02, 0.4, 0.15]
        return singer.Cutout("d" * 24 + ".png", face, 0.6)

    photos, _ = singer.photos_of(names, settings, cutter, lambda n, s: (n, 0))
    assert photos[0].cutout == "" and photos[1].cutout == "d" * 24 + ".png"
