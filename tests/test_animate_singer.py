"""Singer edits (animate/singer.py): the song's hook, the singer's photos, one scene a sung
line, and how the studio, its routes and the renderer treat them. No model, no download:
the hook finder, the cutout and the writer are stand-ins."""

import io
import itertools
import json
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agentic_editor import jobs
from agentic_editor.animate import images, singer, studio
from agentic_editor.animate.plan import Plan, SingerEdit, SingerPhoto, tidy
from agentic_editor.db import Animation
from agentic_editor.transcript import Word
from tests import test_api
from tests.test_animate import needs_ffmpeg, studio_env, tone_wav
from tests.test_animate_api import studio_on

env = test_api.env


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    """A test never fetches the background removal model (176 MB)."""

    def refuse(models_dir):
        raise AssertionError("a test asked for the cutout model")

    monkeypatch.setattr(singer, "cut_model", refuse)


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
    with pytest.raises(ValueError, match="too small"):
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
    song = tone_wav(tmp_path / "song.wav", 30.0)
    song_obj = types.SimpleNamespace(
        title="Maa, I Tried", artist="KiD Boi", video="abc", start=10.0, end=16.0, lines=[
            {"text": "मेरे जैसा", "said": "Mere jaisa", "start": 10.2, "end": 11.5, "words": [
                {"text": "मेरे", "start": 10.3, "end": 10.7},
                {"text": "जैसा", "start": 10.8, "end": 11.4}]},
            {"text": "नहीं है कोई", "said": "Nahi hai koi", "start": 11.6, "end": 13.0, "words": [
                {"text": "नहीं", "start": 11.7, "end": 12.0},
                {"text": "है", "start": 12.1, "end": 12.2},
                {"text": "कोई", "start": 12.3, "end": 12.8}]},
        ],
    )  # fmt: skip
    fake = types.ModuleType("agentic_editor.animate.songhook")

    class SongError(RuntimeError):
        pass

    fake.SongError = SongError
    fake.find = lambda idea, settings, router, say: song_obj
    fake.audio_path = lambda settings, s: song
    monkeypatch.setitem(sys.modules, "agentic_editor.animate.songhook", fake)
    import agentic_editor.animate as package

    monkeypatch.setattr(package, "songhook", fake, raising=False)
    h = singer.find_hook("maa i tried", settings, router=object())
    assert [w.text for w in h.words] == ["Mere", "jaisa", "Nahi", "hai", "koi"]
    assert h.words[0].start == pytest.approx(0.3) and h.duration == pytest.approx(6.0, abs=0.05)
    assert [x.text for x in singer.sung_lines(h)] == ["Mere jaisa", "Nahi hai koi"]

    def nothing(idea, settings, router, say):
        raise SongError("the song has no part of 12-32 s made of whole lines")

    fake.find = nothing
    with pytest.raises(singer.SingerError, match="12-32 s"):
        singer.find_hook("maa i tried", settings, router=object())


def test_without_a_song_finder_the_owner_is_told_so(tmp_path, monkeypatch):
    settings, _ = studio_env(tmp_path)
    monkeypatch.setitem(sys.modules, "agentic_editor.animate.songhook", None)
    with pytest.raises(singer.SingerError, match="isn't installed"):
        singer.find_hook("anything", settings)


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


def test_the_head_is_found_from_the_outline_and_a_wild_face_box_ignored(tmp_path):
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
