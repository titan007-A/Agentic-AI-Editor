import json
import shutil
import wave
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agentic_editor.animate import planner, sound, studio, timeline, voice
from agentic_editor.animate.plan import Plan, is_emoji, problems, tidy
from agentic_editor.animate.timeline import Spoken, build, player_data
from agentic_editor.db import Animation, make_sessionmaker
from agentic_editor.settings import Settings
from agentic_editor.transcript import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def scene(narration: str, kind: str = "title", **fields) -> dict:
    return {"narration": narration, "visual": {"kind": kind, **fields}}


def sample_plan(**changes) -> Plan:
    scenes = [
        scene(
            "A whole city vanished into the desert.",
            "title",
            text="The city the desert swallowed",
            highlight="desert",
        ),
        scene(
            "In 1992 a satellite found its tracks.",
            "timeline",
            items=[
                {"year": "300 AD", "text": "It sinks"},
                {"year": "1992", "text": "Tracks found"},
            ],
        ),
        scene(
            "It lay under 4,000 years of sand.",
            "stat",
            value="4,000",
            suffix="years",
            text="under the sand",
        ),
        scene(
            "Why did it vanish? A hidden cave.",
            "reveal",
            text="Why did it vanish?",
            answer="A hidden cave",
            emoji="🕳️",
        ),
    ]
    data = {"title": "The Lost City", "kind": "facts", "style": "glow", "voice": "bm_george",
            "mood": "mysterious", "scenes": scenes, "cta": "Follow for more lost history.",
            "caption": "A city swallowed by sand. #history"}  # fmt: skip
    data.update(changes)
    return tidy(Plan.model_validate(data))


def story_plan(**changes) -> Plan:
    """A short story acted by two characters."""
    scenes = [
        scene("Maya walks down her street every morning.", "scene", setting="street",
              actors=[{"name": "Maya", "action": "walk_in", "mood": "neutral", "prop": "☕"}]),
        scene("A little boy waves at her and shouts hello.", "scene", setting="street",
              actors=[{"name": "Maya", "action": "idle", "mood": "surprised"},
                      {"name": "Leo", "action": "wave", "mood": "happy", "says": "Hello there!"}]),
        scene("She gives him a gift, and he jumps for joy.", "scene", setting="room",
              actors=[{"name": "Maya", "action": "give", "mood": "happy", "prop": "🎁"},
                      {"name": "Leo", "action": "celebrate", "mood": "happy"}]),
    ]  # fmt: skip
    cast = [{"name": "Maya", "look": "woman"}, {"name": "Leo", "look": "boy"}]
    data = {"title": "The Gift", "kind": "story", "style": "story", "voice": "bm_fable",
            "mood": "emotional", "scenes": scenes, "cast": cast,
            "cta": "Follow for more stories.", "caption": "x"}  # fmt: skip
    data.update(changes)
    return tidy(Plan.model_validate(data))


def spoken(text: str, rate: float = 0.38) -> Spoken:
    """Kokoro's answer without Kokoro: each word `rate` seconds, punctuation on its word."""
    words, t = [], 0.0
    for w in text.split():
        words.append(Word(text=w, start=t, end=t + rate - 0.05))
        t += rate
    return Spoken(path="", duration=round(t, 3), words=words)


def voices_for(plan: Plan) -> list[Spoken]:
    return [spoken(s.narration) for s in plan.scenes] + [spoken(plan.cta)]


# ---- plan --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ok"),
    [("🐪", True), ("❤️", True), ("👍🏽", True), ("🇮🇳", True), ("👨‍👩‍👧", True), ("🕳️", True),
     ("a", False), ("camel", False), ("", False), ("🐪🐫x", False)],
)  # fmt: skip
def test_only_emoji_count_as_emoji(text, ok):
    assert is_emoji(text) is ok


def test_tidy_mends_what_a_writer_gets_slightly_wrong():
    plan = tidy(
        Plan.model_validate(
            {
                "title": "  The   Lost City ",
                "scenes": [
                    scene(
                        "  Two   spaces. ",
                        "icons",
                        items=[{"emoji": "camel", "motion": "gallop"}] * 5,
                    ),
                    scene("x", "title", text="Big news", highlight="nothing like it"),
                    scene("", "title", text="dropped: says nothing"),
                ],
            }
        )
    )
    assert plan.title == "The Lost City"
    icons = plan.scenes[0]
    assert icons.narration == "Two spaces."
    assert len(icons.visual.items) == 3  # icons hold three
    assert {i.emoji for i in icons.visual.items} == {"✨"} and {
        i.motion for i in icons.visual.items
    } == {"pop"}
    assert plan.scenes[1].visual.highlight == ""  # not in the headline
    assert len(plan.scenes) == 2  # a scene with no narration is dropped


def test_a_scene_that_only_asks_people_to_follow_is_dropped():
    plan = sample_plan(
        scenes=[*[s.model_dump() for s in sample_plan().scenes], scene("Follow for more history!")]
    )
    assert len(plan.scenes) == 4 and "Follow" not in plan.scenes[-1].narration


def test_problems_say_what_to_fix():
    assert problems(sample_plan(), "short") == [
        "the narration is 28 words; this length needs 50-85"
    ]
    bad = sample_plan(
        scenes=[
            scene("One.", "stat", value="many"),
            scene("Two.", "list", items=[]),
            scene("Three.", "place"),
        ]
    )
    found = problems(bad, "auto")
    assert any("value must be a number" in p for p in found)
    assert any("scene 2 (list): needs at least 2 items" in p for p in found)
    assert any("scene 3 (place): missing its main field" in p for p in found)
    assert any(
        "a reel needs at least 3 scenes" in p
        for p in problems(sample_plan(scenes=[scene("x")]), "auto")
    )


def test_what_is_shown_must_be_what_is_said_and_the_ending_is_the_systems():
    scenes = [
        scene("Did you know this city vanished?", "list", text="F", items=[{"text": "a"}] * 2),
        scene("It lay under the sand for ages.", "stat", value="4,000", suffix="years"),
        scene("Legends put it somewhere in the south.", "place", text="Ubar"),
        scene(
            "Why did it happen? Nobody knows for sure, but follow for more to find out.",
            "reveal",
            text="Why?",
            answer="A cave",
        ),
    ]
    found = problems(sample_plan(scenes=scenes), "auto")
    assert "scene 1 must be the hook: a title, or a story's most striking moment" in found
    assert "scene 1 (list): no 'Did you know' or 'In this video'" in found
    assert "scene 2 (stat): say the number 4,000 in the narration" in found
    assert "scene 3 (place): name Ubar in the narration" in found
    assert "scene 4 (reveal): say the answer (A cave) in the narration" in found
    assert "scene 4 (reveal): don't ask people to follow; the ending is added after" in found
    assert problems(sample_plan(), "medium") == [
        "the narration is 28 words; this length needs 90-150"
    ]


# ---- timeline ----------------------------------------------------------------------------


def test_scenes_follow_each_other_and_last_their_voice():
    plan = sample_plan()
    tl = build(plan, voices_for(plan))
    assert [s.kind for s in tl.scenes] == ["title", "timeline", "stat", "reveal", "cta"]
    for before, after in zip(tl.scenes, tl.scenes[1:], strict=False):
        assert after.start == pytest.approx(before.end)
    first = tl.scenes[0]
    assert first.voice_start == pytest.approx(timeline.FIRST_LEAD)
    assert first.end >= first.voice_end + plan.scenes[0].hold - 1e-9
    assert (
        tl.scenes[2].end - tl.scenes[2].start >= timeline.MIN_SCENE["stat"]
    )  # a stat gets time to count
    assert tl.duration == pytest.approx(tl.scenes[-1].end + timeline.TAIL)


def test_visual_beats_land_on_the_words_that_name_them():
    plan = sample_plan()
    tl = build(plan, voices_for(plan))
    dates, stat, reveal = tl.scenes[1], tl.scenes[2], tl.scenes[3]
    when = {w.text.strip(".,"): w.start for w in dates.words}
    assert dates.beats[1] == pytest.approx(when["1992"])  # the 1992 row as "1992" is said
    assert stat.beats[0] == pytest.approx(
        next(w.start for w in stat.words if "4,000" in w.text) - 0.1
    )
    answer = next(w.start for w in reveal.words if w.text.startswith("hidden"))
    assert reveal.beats[1] == pytest.approx(answer)
    # The Follow button is tapped as the closing line ends.
    cta = tl.scenes[-1]
    assert cta.beats == [pytest.approx(max(cta.start + 0.7, cta.voice_end - 0.45))]


def test_subtitles_show_a_few_words_at_a_time_breaking_after_punctuation():
    plan = sample_plan()
    tl = build(plan, voices_for(plan))
    assert all(1 <= len(c.words) <= timeline.CHUNK_WORDS for c in tl.captions)
    assert [w.text for w in tl.captions[0].words] == ["A", "whole", "city"]
    ends = [c for c in tl.captions if c.words[-1].text.endswith(".")]
    assert ends  # a sentence's last word ends its chunk
    for a, b in zip(tl.captions, tl.captions[1:], strict=False):
        assert a.end <= b.start + 1e-6  # never two at once


def test_sound_effects_sit_on_the_beats():
    plan = sample_plan()
    tl = build(plan, voices_for(plan))
    sounds = [(c.sound, round(c.t, 3)) for c in tl.cues]
    assert ("boom", 0.0) in sounds
    assert sum(1 for s, _ in sounds if s == "whoosh") == len(tl.scenes) - 1
    assert ("chime", round(tl.scenes[-1].beats[0], 3)) in sounds
    assert ("boom", round(tl.scenes[3].beats[1], 3)) in sounds  # the answer lands with a hit
    assert sum(1 for s, _ in sounds if s == "tick") > 5  # the counter ticks


def test_the_player_gets_every_scene_with_its_times():
    plan = sample_plan()
    data = player_data(plan, build(plan, voices_for(plan)))
    assert data["style"] == "glow" and data["title"] == "The Lost City"
    assert [s["kind"] for s in data["scenes"]] == ["title", "timeline", "stat", "reveal", "cta"]
    assert data["scenes"][-1]["visual"] == {"kind": "cta", "text": "Follow for more lost history."}
    assert data["header"]["start"] == data["scenes"][1]["start"]
    json.dumps(data)  # sent to the browser as it is


# ---- sound -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["whoosh", "pop", "tick", "ding", "drop", "boom", "riser", "shimmer", "chime", "step"]
)
def test_every_sound_effect_is_made_and_peaks_at_full_scale(name):
    x = sound.sfx(name)
    assert x.ndim == 2 and x.shape[1] == 2 and len(x) > 100
    assert np.max(np.abs(x)) == pytest.approx(1.0)


@pytest.mark.parametrize("mood", list(sound.MOODS))
def test_music_is_as_long_as_asked_and_quiet_under_the_voice(mood):
    x = sound.music(mood, 6.0, seed=3)
    assert x.shape == (6 * sound.RATE, 2)
    rms_db = 20 * np.log10(np.sqrt(np.mean(x**2)))
    assert rms_db == pytest.approx(sound.MUSIC_RMS_DB, abs=0.5)
    assert np.array_equal(x, sound.music(mood, 6.0, seed=3))  # the same reel, the same music


def test_the_voice_is_compressed_so_its_peaks_come_closer_to_its_loudness():
    t = np.arange(sound.RATE) / sound.RATE
    quiet = 0.05 * np.sin(2 * np.pi * 200 * t)
    loud = 0.9 * np.sin(2 * np.pi * 200 * t)
    x = np.stack([np.concatenate([quiet, loud])] * 2, axis=1)
    y = sound.compress(x)
    assert np.max(np.abs(y[: sound.RATE])) == pytest.approx(0.05, rel=0.05)  # below: untouched
    assert np.max(np.abs(y[-sound.RATE // 2 :])) < 0.9 * 0.5  # above: brought down


def tone_wav(path: Path, seconds: float, rate: int = 24000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.4 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


@needs_ffmpeg
def test_the_mix_is_as_long_as_the_reel_at_the_presets_loudness(tmp_path):
    from agentic_editor.render import audio

    plan = sample_plan()
    voices = voices_for(plan)
    tl = build(plan, voices)
    files = [tone_wav(tmp_path / f"v{i}.wav", v.duration) for i, v in enumerate(voices)]
    dest = sound.mix(tl, files, plan.mood, tmp_path / "mix.wav")
    with wave.open(str(dest)) as w:
        assert w.getnchannels() == 2 and w.getframerate() == sound.RATE
        assert w.getnframes() / w.getframerate() == pytest.approx(tl.duration, abs=0.05)
    assert audio.measure_loudness(dest).integrated == pytest.approx(sound.LOUDNESS, abs=1.0)


# ---- voice -------------------------------------------------------------------------------


def tok(text, start, end, whitespace=" "):
    return SimpleNamespace(text=text, start_ts=start, end_ts=end, whitespace=whitespace)


def test_kokoro_tokens_become_words_with_their_punctuation():
    tokens = [tok("In", 0.1, 0.2), tok("1992", 0.2, 0.7, ""), tok(",", None, None),
              tok("they", 0.8, 0.9), tok("found", 0.9, 1.2, ""), tok("it", 1.2, 1.3, ""),
              tok(".", 1.3, 1.35)]  # fmt: skip
    words = voice._words(tokens, offset=2.0)
    assert [w.text for w in words] == ["In", "1992,", "they", "foundit."]
    assert words[1].start == pytest.approx(2.2) and words[1].end == pytest.approx(2.7)
    assert words[-1].end == pytest.approx(3.35)


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def __call__(self, text, voice, speed):
        import torch

        self.calls += 1
        n = len(text.split())
        tokens = [tok(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]
        yield SimpleNamespace(audio=torch.zeros(int(n * 0.3 * voice_rate())), tokens=tokens)


def voice_rate() -> int:
    return voice.RATE


def test_a_line_is_spoken_once_and_kept(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    fake = FakePipeline()
    monkeypatch.setattr(voice, "_pipeline", lambda lang: fake)
    first = voice.speak("Ubar was real all along.", "bm_george", tmp_path)
    again = voice.speak("Ubar was real all along.", "bm_george", tmp_path)
    assert fake.calls == 1 and again == first
    assert first.duration == pytest.approx(1.5) and [w.text for w in first.words][-1] == "along."
    voice.speak("Ubar was real all along.", "af_heart", tmp_path)
    assert fake.calls == 2  # another voice is another recording


def test_a_voice_is_chosen_from_a_kind_or_by_the_reel():
    assert voice.resolve("calm", "facts", "curious") == "af_heart"
    assert voice.resolve("bm_lewis", "facts", "curious") == "bm_lewis"
    assert voice.resolve("auto", "story", "mysterious") == "bm_fable"
    assert voice.resolve("auto", "facts", "mysterious") == "bm_george"
    assert voice.resolve("auto", "lesson", "curious") == "af_heart"
    assert voice.resolve("auto", "news", "tense") == "am_puck"


# ---- planner -----------------------------------------------------------------------------


def reply_for(plan: Plan, **changes) -> dict:
    data = {
        "title": plan.title, "kind": plan.kind, "mood": plan.mood, "voice_kind": "narrator",
        "scenes": [s.model_dump() for s in plan.scenes], "cta": plan.cta, "caption": plan.caption,
        "cast": [c.model_dump() for c in plan.cast],
    }  # fmt: skip
    data.update(changes)
    return data


class Writer:
    """A planner model that answers with the replies given, one per call, and keeps what it
    was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked: list[list[dict]] = []

    def chat_json(self, messages, schema, temperature=0.3):
        self.asked.append(messages)
        return self.replies.pop(0)


def test_a_draft_takes_the_brief_and_sends_back_what_fails_once():
    short = sample_plan()
    longer = sample_plan(scenes=[s.model_dump() for s in short.scenes] * 2)
    writer = Writer(reply_for(short), reply_for(longer))
    brief = planner.Brief("the lost city of Ubar", length="short")
    found = [planner.Found("Ubar", "https://en.wikipedia.org/wiki/Ubar", "Ubar is a lost city...")]
    plan, left = planner.draft(writer, brief, found)
    assert len(writer.asked) == 2
    assert "the narration is 28 words" in writer.asked[1][-1]["content"]
    assert "Ubar is a lost city" in writer.asked[0][1]["content"]
    assert len(plan.scenes) == 8 and left == []
    assert plan.style == "glow" and plan.voice == "bm_george"  # facts: glow, narrator: George
    assert plan.sources[0].url == "https://en.wikipedia.org/wiki/Ubar"


def long_plan(**changes) -> Plan:
    return sample_plan(scenes=[s.model_dump() for s in sample_plan().scenes] * 2, **changes)


def test_a_fix_that_breaks_off_keeps_the_first_draft():
    from agentic_editor.llm import LLMError

    class Breaks(Writer):
        def chat_json(self, messages, schema, temperature=0.3):
            if len(self.asked) == 1:
                self.asked.append(messages)
                raise LLMError("did not return JSON")
            return super().chat_json(messages, schema, temperature)

    short = sample_plan()
    plan, left = planner.draft(Breaks(reply_for(short)), planner.Brief("ubar", length="short"), [])
    assert plan.scenes == short.scenes and left == [
        "the narration is 28 words; this length needs 50-85"
    ]


def test_the_planner_gets_room_for_a_whole_reel():
    client = SimpleNamespace(context=None, max_reply=None)
    planner.roomy(client)
    assert (client.context, client.max_reply) == (planner.CONTEXT, planner.REPLY)
    planner.roomy(Writer())  # a client without those settings is left alone


def test_a_lesson_gets_paper_and_the_voice_asked_for():
    lesson = long_plan(kind="lesson")
    writer = Writer(reply_for(lesson, kind="lesson", voice_kind="calm"))
    plan, _ = planner.draft(writer, planner.Brief("patience", kind="lesson", voice="am_puck"), [])
    assert plan.style == "paper" and plan.voice == "am_puck" and plan.kind == "lesson"


def test_a_change_keeps_the_style_voice_and_sources_and_a_scene_is_written_again():
    plan = long_plan(style="paper", voice="af_heart", sources=[{"title": "Ubar", "url": "u"}])
    changed_reply = reply_for(plan, title="Ubar: the city that sank")
    changed, _ = planner.change(
        Writer(changed_reply), plan, "a darker title", planner.Brief("ubar")
    )
    assert changed.title == "Ubar: the city that sank"
    assert (changed.style, changed.voice, changed.sources) == ("paper", "af_heart", plan.sources)
    new_scene = scene(
        "Nobody believed the old maps.", "quote", text="Nobody believed it.", by="A scholar"
    )
    redone = planner.redo_scene(Writer(new_scene), plan, 1)
    assert redone.scenes[1].visual.kind == "quote" and redone.scenes[0] == plan.scenes[0]
    with pytest.raises(planner.PlannerError):
        planner.redo_scene(Writer(new_scene), plan, 9)


def test_sources_come_from_wikipedia_and_the_web_except_for_lessons(monkeypatch, tmp_path):
    from agentic_editor import caption

    article = caption.Article("Ubar", "https://en.wikipedia.org/wiki/Ubar", "Ubar text " * 900)
    monkeypatch.setattr(caption, "gather_topic", lambda *a, **k: caption.Sources([article]))
    body = "Archaeologists in 1992 found " * 5
    wiki = "https://en.wikipedia.org/wiki/Ubar#History"
    hits = [{"title": "Ubar - Wikipedia", "href": wiki, "body": body},
            {"title": "Lost city found", "href": "https://x.test/a", "body": body},
            {"title": "too short", "href": "https://x.test/b", "body": "tiny"}]  # fmt: skip
    found = planner.gather("Ubar", "facts", tmp_path, search=lambda q, n: hits)
    assert [f.url for f in found] == ["https://en.wikipedia.org/wiki/Ubar", "https://x.test/a"]
    assert len(found[0].text) == planner.SOURCE_CHARS
    assert planner.gather("patience", "lesson", tmp_path, search=lambda q, n: hits) == []
    # A made-up story names no real subject: nothing to look up. A true one does.
    assert planner.gather("a kind shoeshine boy", "story", tmp_path, search=lambda q, n: hits) == []
    assert planner.gather("Ubar", "story", tmp_path, search=lambda q, n: hits, topic="Ubar")
    asked = []
    planner.gather(
        "a city that sank",
        "news",
        tmp_path,
        search=lambda q, n: asked.append(q) or [],
        topic="Ubar",
        query="Ubar ruins",
    )
    assert asked == ["Ubar ruins news"]


def test_the_subject_of_an_idea_is_named_before_searching():
    writer = Writer({"subject": " Ubar ", "search": "Ubar lost city Oman"})
    assert planner.subject(writer, "the lost city that vanished into the desert") == (
        "Ubar",
        "Ubar lost city Oman",
    )
    assert "the lost city that vanished" in writer.asked[0][0]["content"]
    assert planner.subject(Writer({"subject": "", "search": ""}), "patience pays") == (
        "",
        "patience pays",
    )


# ---- studio ------------------------------------------------------------------------------


class Router:
    def __init__(self, writer):
        self.writer = writer

    def planner(self):
        return nullcontext(self.writer)


def studio_env(tmp_path):
    settings = Settings(
        media_dir=tmp_path / "media", cache_dir=tmp_path / "cache", database=tmp_path / "app.db"
    )
    return settings, make_sessionmaker(settings.database)


def fake_speak(text, voice_id, cache, speed=None):
    return spoken(text)


def test_a_draft_is_written_voiced_and_ready_to_play(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    plan = sample_plan(scenes=[s.model_dump() for s in sample_plan().scenes] * 2)
    with Session() as db:
        db.add(Animation(id="a1", owner_id="u1", idea="the lost city", task={"op": "draft"}))
        db.commit()
    studio.run_task("a1", Session, settings, Router(Writer(reply_for(plan))), speak=fake_speak)
    with Session() as db:
        a = db.get(Animation, "a1")
        assert a.status == "ready" and a.error is None and a.task is None
        assert len(a.plan["scenes"]) == 8 and a.history == []
        assert a.player["scenes"][-1]["kind"] == "cta" and a.player["duration"] > 10
        assert a.notes[-1].startswith(f"{a.player['duration']:.0f} s")
        a.task = {"op": "change", "request": "darker title"}
        db.commit()
    studio.run_task(
        "a1", Session, settings, Router(Writer(reply_for(plan, title="Darker"))), speak=fake_speak
    )
    with Session() as db:
        a = db.get(Animation, "a1")
        assert a.plan["title"] == "Darker" and len(a.history) == 1  # the draft kept for undo
        assert studio.undo(a) and a.plan["title"] == plan.title and a.history == []
        assert not studio.undo(a)


def test_a_failed_draft_says_why_and_is_never_left_busy(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    tiny = sample_plan(scenes=[scene("Only one.")])
    with Session() as db:
        db.add(Animation(id="a2", owner_id="u1", idea="x", task={"op": "draft"}))
        db.commit()
    studio.run_task(
        "a2", Session, settings, Router(Writer(reply_for(tiny), reply_for(tiny))), speak=fake_speak
    )
    with Session() as db:
        a = db.get(Animation, "a2")
        assert a.status == "failed" and "didn't manage a reel" in a.error and a.task is None


def test_a_render_is_a_video_with_the_plans_caption(tmp_path):
    settings, Session = studio_env(tmp_path)
    plan = sample_plan()
    with Session() as db:
        db.add(Animation(id="a3", owner_id="u1", idea="x", plan=plan.model_dump(mode="json")))
        db.commit()
    drawn = {}

    def draw(plan, tl, voices, out_dir, fonts_dir, models_dir, progress):
        drawn["duration"] = tl.duration
        out_dir.mkdir(parents=True)
        return out_dir / "animation.mp4", out_dir / "thumbnail.jpg", ["subtitle: A WHOLE"]

    events = []
    result, used = studio.render_job(
        "a3", Session, settings, lambda *a: events.append(a), "job1", fake_speak, draw
    )
    (out,) = result.outputs
    assert out.preset == "animation-glow" and out.edl.duration == pytest.approx(
        drawn["duration"], abs=0.01
    )
    assert out.issues == ["text shrunk to fit: subtitle: A WHOLE"]
    assert used.caption == plan.caption and result.title == "The Lost City"
    assert ("title", "The Lost City") in events
    assert studio.label("animation-paper") == "Animation · Clean Paper"


# ---- characters ---------------------------------------------------------------------------


def test_character_scenes_are_mended_and_everyone_who_acts_is_cast():
    plan = story_plan(
        cast=[{"name": "Maya", "look": "lady"}],
        scenes=[
            scene("They meet.", "scene", setting="moon", actors=[
                {"name": "Maya", "action": "moonwalk", "mood": "giddy", "prop": "gift"},
                {"name": "Leo", "action": "wave"}, {"name": "Third", "action": "wave"},
            ]),
        ],
    )  # fmt: skip
    v = plan.scenes[0].visual
    assert v.setting == "room"  # not one of the places drawn: a room
    assert len(v.actors) == 2  # two at a time on the stage
    maya = v.actors[0]
    assert (maya.action, maya.mood, maya.prop) == ("idle", "neutral", "")
    assert [(c.name, c.look) for c in plan.cast] == [("Maya", "man"), ("Leo", "man")]


def test_a_scene_without_actors_must_be_fixed():
    plan = story_plan(
        scenes=[*[s.model_dump() for s in story_plan().scenes], scene("Empty.", "scene")]
    )
    assert "scene 4 (scene): needs at least one actor from the cast" in problems(plan, "short")


def test_actors_come_on_in_turn_and_the_bubble_opens_as_the_line_is_said():
    plan = story_plan()
    tl = build(plan, voices_for(plan))
    meet = tl.scenes[1]
    assert meet.beats[0] == pytest.approx(meet.start + 0.1)
    assert meet.beats[1] == pytest.approx(meet.start + 0.35)
    said = next(w.start for w in meet.words if w.text.startswith("hello"))
    assert meet.beats[2] == pytest.approx(max(meet.voice_start + 0.2, said - 0.1))
    sounds = [c for c in tl.cues if c.sound == "step"]
    walk = tl.scenes[0]
    assert sounds and all(
        walk.beats[0] < c.t < walk.beats[0] + timeline.ENTRANCES["walk_in"][0] for c in sounds
    )
    assert any(c.sound == "pop" and c.t == pytest.approx(meet.beats[2]) for c in tl.cues)
    assert any(c.sound == "chime" for c in tl.cues if tl.scenes[2].start <= c.t < tl.scenes[2].end)
    data = player_data(plan, tl)
    assert data["cast"] == [{"name": "Maya", "look": "woman"}, {"name": "Leo", "look": "boy"}]


def test_stories_told_with_characters_get_the_character_style():
    story = story_plan()
    writer = Writer(reply_for(story, kind="story"), reply_for(story, kind="story"))
    plan, _ = planner.draft(
        writer, planner.Brief("a kind stranger", kind="story", length="short"), []
    )
    assert plan.style == "story" and [c.name for c in plan.cast] == ["Maya", "Leo"]
    assert plan.scenes[1].visual.actors[1].says == "Hello there!"
    # Asked for: as asked. A story without character scenes: paper.
    assert planner._style_for("kinetic", "story", acted=True) == "kinetic"
    assert planner._style_for("auto", "story", acted=False) == "paper"
    assert planner._style_for("auto", "facts", acted=True) == "glow"


def test_names_overrule_a_look_that_disagrees_and_big_things_are_not_held():
    plan = story_plan(
        cast=[{"name": "Grandpa Eli", "look": "woman"}, {"name": "Mrs Patel", "look": "boy"},
              {"name": "Lily", "look": "girl"}],
        scenes=[scene("They meet.", "scene", actors=[
            {"name": "Grandpa Eli", "action": "think", "prop": "🪑"},
            {"name": "Uncle Raj", "action": "give", "prop": "🎁"},
        ])],
    )  # fmt: skip
    looks = {c.name: c.look for c in plan.cast}
    assert looks == {
        "Grandpa Eli": "old man",
        "Mrs Patel": "woman",
        "Lily": "girl",
        "Uncle Raj": "old man",
    }
    eli, raj = plan.scenes[0].visual.actors
    assert eli.prop == "" and raj.prop == "🎁"
