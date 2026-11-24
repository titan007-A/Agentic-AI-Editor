import re
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from agentic_editor import agent
from agentic_editor.clipper import hooks, hunt, moments
from agentic_editor.llm import LLMError
from agentic_editor.settings import Settings
from agentic_editor.transcript import Word

PEAK = 1000.0


def said(*rows: tuple[float, str]) -> list[Word]:
    """Words at stream times: (time of the first word, the words), a word every 0.3 s."""
    words = []
    for at, text in rows:
        for n, w in enumerate(text.split()):
            words.append(Word(text=w, start=at + 0.3 * n, end=at + 0.3 * n + 0.25))
    return words


def a_look(words=(), peak=PEAK, why=('"kekw" ×9', "loud")) -> hooks.Look:
    moment = moments.Moment(peak - 18, peak + 9, peak, 3.0, list(why))
    look = hooks.Look(moment, peak - hooks.LEAD, peak + hooks.TAIL, list(words))
    look.lines = hooks.lines_of(look.words)
    look.loud = np.full(int((look.end - look.start) / hooks.STEP), -30.0)
    return look


ROWS = (
    (PEAK - 20, "yeah so okay chat listen up"),
    (PEAK - 14, "you really think you can beat me in a race?"),
    (PEAK - 8, "watch this"),
    (PEAK - 1, "OH MY GOD HE CRASHED"),
    (PEAK + 5, "bro that is insane"),
)


class FakeModel:
    """The planning model: its read of a moment, then its ratings of the openings."""

    def __init__(self, read: dict, scores: dict[str, int], crazy: bool = True):
        self.read, self.scores, self.crazy, self.asked = read, scores, crazy, []

    def chat_json(self, messages, schema, **kwargs):
        prompt = messages[-1]["content"]
        self.asked.append(prompt)
        if "Is the streamer" in prompt:
            return {"crazy": self.crazy, "what": "he crashes" if self.crazy else "nothing"}
        if "You are scrolling" in prompt:
            options = re.findall(r"Option ([A-Z]): you hear: (.*?)\. Sound", prompt)
            ratings = []
            for letter, heard in options:
                score = next((s for k, s in self.scores.items() if heard.startswith(k)), 2)
                ratings.append({"option": letter, "score": score, "grabs": "x"})
            return {"ratings": ratings}
        return self.read


def read(**fields) -> dict:
    return {
        "what_happens": "He bets he can win a race, then crashes into a wall.",
        "crazy": True, "crazy_kind": "wild_reaction", "crazy_what": "he crashes and screams",
        "payoff": "L4", "openings": ["L2", "L1"], "end": "L5", "payoff_score": 8,
        "stands_alone": 7, "flash": "", "tease": "whether he wins the race", **fields,
    }  # fmt: skip


def test_what_is_said_is_cut_into_lines_at_pauses_and_sentence_ends():
    lines = hooks.lines_of(said(*ROWS))
    assert [x.text for x in lines][:3] == [
        "yeah so okay chat listen up", "you really think you can beat me in a race?", "watch this",
    ]  # fmt: skip
    long = hooks.lines_of(said((0, " ".join(["word"] * 30))))
    assert all(len(x.words) <= hooks.LINE_WORDS for x in long)


def test_the_clip_opens_on_the_opening_rated_strongest_cold_and_keeps_the_moment():
    look = a_look(said(*ROWS))
    model = FakeModel(read(), {"you really": 8, "yeah so": 2})
    judged = hooks.judge(model, look, "xQc", "GTA 6")
    assert judged is not None
    start, end = judged.opening.start, judged.end
    assert start == pytest.approx(PEAK - 14 - hooks.LEAD_IN)  # just before its first word
    assert judged.opening.said.startswith("you really think")
    assert start <= PEAK - hooks.LATEST and end >= PEAK + hooks.END_LEAST  # the moment is in
    assert moments.SHORTEST <= end - start <= moments.LONGEST
    assert not any(w.start < end < w.end for w in look.words)  # never ends mid-word
    # The cold look saw only the first seconds, and filler was called filler.
    cold = model.asked[1]
    assert "beat me in a race" in cold and "HE CRASHED" not in cold
    assert "(opens on filler)" in cold


def test_a_weak_opening_or_a_weak_payoff_is_not_made():
    look = a_look(said(*ROWS))
    assert hooks.judge(FakeModel(read(), {"you really": 5}), look, "x", "t") is None
    # Only a deep impact: a payoff of 7 is not enough.
    assert hooks.judge(FakeModel(read(payoff_score=7), {"you really": 9}), look, "x", "t") is None


def test_only_a_moment_where_someone_does_or_says_something_crazy_is_made():
    look = a_look(said(*ROWS))
    # Plain gameplay or chatting, however busy the chat: not read further.
    model = FakeModel(read(crazy=False, crazy_kind="none", crazy_what=""), {"you really": 9})
    assert hooks.judge(model, look, "x", "t") is None and len(model.asked) == 1
    model = FakeModel(read(crazy_kind="none"), {"you really": 9})
    assert hooks.judge(model, look, "x", "t") is None
    # A second look at the clip as it will be cut must agree.
    model = FakeModel(read(), {"you really": 9}, crazy=False)
    assert hooks.judge(model, look, "x", "t") is None
    crazy = model.asked[-1]
    assert "Is the streamer" in crazy and "OH MY GOD HE CRASHED" in crazy
    assert "yeah so okay chat" not in crazy  # only what the clip holds
    judged = hooks.judge(FakeModel(read(), {"you really": 9}), look, "x", "t")
    m = hooks.moment_of(judged)
    assert m.hook["crazy"] == "he crashes and screams" and "wild reaction" in m.why
    assert "What makes it wild: he crashes and screams." in hunt.notes_for(m)


def test_an_opening_too_close_to_the_moment_or_too_early_is_not_taken():
    look = a_look(said(*ROWS))
    # L4 starts a second before the peak: the moment itself would be the opening.
    model = FakeModel(read(openings=["L4"]), {"OH MY GOD": 9, "yeah so": 3, "you really": 3})
    judged = hooks.judge(model, look, "x", "t")
    # The model's only pick doesn't fit: the loudest line that may open is rated instead,
    # and it isn't strong enough.
    assert judged is None
    assert "OH MY GOD" not in model.asked[1]
    # An opening on a word or two (or a game's line, or silence) isn't one.
    look = a_look(said((PEAK - 14, "watch this"), (PEAK - 1, "OH MY GOD HE CRASHED")))
    model = FakeModel(read(openings=["L1"]), {"watch this": 9})
    assert hooks.judge(model, look, "x", "t") is None and len(model.asked) == 1


def test_a_strong_payoff_with_no_strong_opening_opens_cold_on_a_flash_of_it():
    look = a_look(said(*ROWS))
    model = FakeModel(read(flash="L4", payoff_score=9), {"you really": 5, "OH MY GOD": 9})
    judged = hooks.judge(model, look, "x", "t")
    assert judged is not None and judged.flash is not None
    a, b = judged.flash
    assert b - a == pytest.approx(hooks.FLASH) and a <= PEAK + 1 <= b
    assert judged.opening.start == pytest.approx(PEAK - 14 - hooks.LEAD_IN)  # then the build-up
    m = hooks.moment_of(judged)
    assert hooks.flash_of(m) == pytest.approx(judged.flash, abs=0.01) and "cold open" in m.why
    assert judged.end - judged.opening.start + hooks.FLASH <= moments.LONGEST
    # Without a strong enough payoff, no cold open rescues it.
    model = FakeModel(read(flash="L4", payoff_score=7), {"you really": 5, "OH MY GOD": 9})
    assert hooks.judge(model, look, "x", "t") is None


def test_the_best_hooks_are_kept_none_overlapping_each_other_or_earlier_clips():
    def j(start, end, hook, payoff):
        look = a_look(peak=start + 15)
        r = hooks.Read(what_happens="w", payoff_score=payoff, stands_alone=5)
        return hooks.Judged(look, r, hooks.Opening(start, "s", hook, "", "L1"), end, None)

    a, b, c, d = j(100, 120, 9, 9), j(110, 130, 8, 8), j(200, 220, 7, 7), j(300, 320, 10, 10)
    kept = hooks.chosen([c, a, b, d], 3, avoid=[(305, 330)])
    assert kept == [a, c]  # d overlaps a clip made before, b overlaps a
    assert hooks.chosen([c, a, b], 1, avoid=[]) == [a]


def test_notes_carry_the_hook_as_content_the_instruction_reader_leaves_alone():
    m = moments.Moment(990, 1010, 1000, 3.0, ['"kekw" ×9'])
    m.hook = {"what": "He bets he can win a race, then crashes.", "said": 'you "really" think',
              "tease": "whether he wins", "score": 8, "payoff": 8}  # fmt: skip
    notes = hunt.notes_for(m, "Kanel")
    assert notes.startswith("A clip from Kanel's Kick stream; the streamer is Kanel. It happens")
    assert "What happens: He bets he can win a race, then crashes." in notes
    assert "First seconds: you really think." in notes  # no quotes: not taken as an example
    assert "The viewer waits to see: whether he wins." in notes
    assert not re.search(agent.START_AT, notes, re.IGNORECASE)
    fixed = notes.replace("you really think", "").replace("He bets he can win a race", "")
    assert not re.search(agent.TOPIC["one_liner"], fixed.lower())
    assert "What happens: He bets" in hunt.about("xQc", "GTA 6", m)
    # A cold open moves the moment later in the video.
    m.hook["flash"] = [1001.0, 1003.5]
    assert "around 0:12 into" in hunt.notes_for(m)


def test_what_is_said_around_every_moment_is_written_down_in_one_go(tmp_path):
    looks = [a_look(peak=p) for p in (500.0, 900.0)]
    for look in looks:
        look.audio = np.zeros(int((look.end - look.start) * hooks.RATE), dtype=np.float32)
    heard = []

    def transcribe(path: Path):
        heard.append(path)
        second = (looks[0].end - looks[0].start) + hooks.SILENCE
        return [
            Word(text="first", start=1.0, end=1.4),
            Word(text="second", start=second + 2, end=second + 2.5),
        ]

    hooks.write_down(looks, transcribe, tmp_path)
    assert len(heard) == 1
    assert [w.text for w in looks[0].words] == ["first"] and looks[0].words[0].start == 469.0
    assert [w.text for w in looks[1].words] == ["second"]
    assert looks[1].words[0].start == pytest.approx(looks[1].start + 2)


class FakeRouter:
    def __init__(self, planner, words=(), fail_transcribe=False):
        self.planner_model, self.words, self.fail = planner, list(words), fail_transcribe

    def transcribe(self, path):
        if self.fail:
            raise RuntimeError("no whisper")
        return type("T", (), {"words": self.words})()

    @contextmanager
    def _open(self, model):
        yield model

    def planner(self):
        return self._open(self.planner_model)

    def vision(self):
        class Vision:
            def chat_json(self, messages, schema, **kwargs):
                return {"frames": ["a man in a car"] * 4, "sudden": "a crash"}

        return self._open(Vision())


def fake_gather(looks, fetch, folder, ffmpeg):
    for look in looks:
        fetch(look.start, look.end, folder / "x.ts")
        look.audio = np.zeros(int((look.end - look.start) * hooks.RATE), dtype=np.float32)
        look.loud = np.full(int((look.end - look.start) / hooks.STEP), -30.0)
    return looks


def test_a_hunt_s_likely_moments_are_judged_and_only_strong_hooks_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "gather", fake_gather)
    found = [moments.Moment(PEAK - 18, PEAK + 9, PEAK, 3.0, []),
             moments.Moment(3000 - 18, 3009, 3000, 2.0, [])]  # fmt: skip
    # Words only around the first moment (times in the one written-down sound).
    offset = found[0].peak - hooks.LEAD
    words = [Word(text=w.text, start=w.start - offset, end=w.end - offset) for w in said(*ROWS)]
    fetched = []

    def fetch(a, b, dest):
        fetched.append((a, b))
        return a

    settings = Settings(media_dir=tmp_path)
    model = FakeModel(read(), {"you really": 8})
    again = []
    monkeypatch.setattr(hooks, "heard_again", lambda judged, *a: again.append(judged) or judged)
    kept, note = hooks.choose(found, 2, fetch, (0, 4000), [], ("xQc", "GTA"), settings,
                              router=FakeRouter(model, words))  # fmt: skip
    assert len(again) == 1 and len(again[0]) == 1  # each strong opening listened to again
    assert len(kept) == 1 and kept[0].peak == PEAK and kept[0].hook["score"] == 8
    assert kept[0].hook["what"].startswith("He bets") and "hook 8/10" in kept[0].why
    assert note == "1 of 2 likely moments were crazy, with a strong hook"
    assert fetched == [
        (PEAK - hooks.LEAD, PEAK + hooks.TAIL),
        (3000 - hooks.LEAD, 3000 + hooks.TAIL),
    ]


def test_without_its_models_a_hunt_cuts_its_moments_as_found(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "gather", fake_gather)
    found = [moments.Moment(PEAK - 18, PEAK + 9, PEAK, 3.0, []) for _ in range(3)]
    settings = Settings(media_dir=tmp_path)
    no_whisper = FakeRouter(None, fail_transcribe=True)
    kept, note = hooks.choose(found, 2, lambda a, b, d: a, (0, 4000), [], ("x", "t"), settings,
                              router=no_whisper)  # fmt: skip
    assert kept == found[:2] and "cut as found" in note

    def no_backends():
        raise RuntimeError("ffmpeg has no subtitles filter")

    monkeypatch.setattr("agentic_editor.backends.get_backends", no_backends)
    kept, note = hooks.choose(found, 2, lambda a, b, d: a, (0, 4000), [], ("x", "t"), settings)
    assert kept == found[:2] and note == "no models to judge the hooks with; cut as found"

    class Away:
        def chat_json(self, *a, **k):
            raise LLMError("qwen could not be reached")

    kept, note = hooks.choose(found, 2, lambda a, b, d: a, (0, 4000), [], ("x", "t"), settings,
                              router=FakeRouter(Away(), said(*ROWS)))  # fmt: skip
    assert kept == found[:2] and note == "the hooks couldn't be judged; cut as found"


def test_more_moments_are_looked_at_than_clips_wanted():
    assert hooks.wanted(1) == 4 and hooks.wanted(5) == 20
    assert hooks.wanted(8) == hooks.MOST_LOOKED == 30 and hooks.wanted(40) == 40


def test_a_cold_open_is_cut_as_the_flash_then_the_clip(monkeypatch, tmp_path):
    from agentic_editor.clipper import kick

    full = kick.Playlist(
        [kick.Piece(t, 10.0, None, f"{int(t // 10)}.ts") for t in range(0, 200, 10)]
    )
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"x")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(hunt.subprocess, "run", run)
    hunt.cut(full, 100.0, 120.0, tmp_path / "a.mp4", flash=(125.0, 127.5))
    cmd = " ".join(ran[0])
    assert cmd.count(" -i ") == 3 and "concat=n=2:v=1:a=1" in cmd
    assert "whoosh.wav" in cmd and "adelay=2250|2250" in cmd  # over the cut back
    # Each part decoded from a piece before it (its key frame) and trimmed: flash, then clip.
    assert "[0:v:0]trim=start=5.000:duration=2.500" in cmd
    assert "[1:v:0]trim=start=10.000:duration=20.000" in cmd and "-ss" not in ran[0]
    hunt.cut(full, 100.0, 120.0, tmp_path / "b.mp4")
    plain = ran[1]
    assert " ".join(plain).count(" -i ") == 1 and "concat" not in " ".join(plain)
    # Seeking happens after the input (decoded, then dropped), never in the playlist.
    assert plain.index("-ss") > plain.index("-i") and plain[plain.index("-ss") + 1] == "10.000"


def test_the_whoosh_is_a_short_stereo_sound_that_fades(tmp_path):
    import wave

    path = hooks.whoosh(tmp_path / "w.wav")
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 2 and w.getnframes() == int(hooks.WHOOSH * w.getframerate())
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[::2] / 32767
    assert np.abs(samples).max() == pytest.approx(hooks.WHOOSH_LEVEL, abs=0.01)
    assert np.abs(samples[-1000:]).max() < 0.2 * np.abs(samples).max()  # it fades out


def test_openings_are_rated_in_both_orders_and_averaged():
    """A model that favours whatever comes first is evened out; a rating named loosely
    ("Option B: ...") still finds its option."""

    class FirstFavoured(FakeModel):
        def chat_json(self, messages, schema, **kwargs):
            prompt = messages[-1]["content"]
            self.asked.append(prompt)
            if "You are scrolling" not in prompt:
                return self.read
            letters = re.findall(r"Option ([A-Z]):", prompt)
            return {"ratings": [{"option": f"Option {x}: what you hear", "score": 9 - 3 * n}
                                for n, x in enumerate(letters)]}  # fmt: skip

    look = a_look(said(*ROWS))
    model = FirstFavoured(read(openings=["L2", "L1"]), {})
    rated = hooks.rate_openings(model, look, ["L2", "L1"], None)
    assert [o.key for o in rated] == ["L2", "L1"] and [o.score for o in rated] == [7.5, 7.5]
    assert len([p for p in model.asked if "You are scrolling" in p]) == 2


def test_an_opening_that_joins_a_sentence_already_going_is_called_so():
    look = a_look(said((PEAK - 20, "and then he said you really think you can beat me")))
    you = next(w for w in look.words if w.text == "you")
    assert hooks.mid_sentence(look, you.start - 0.02)
    fresh = a_look(said((PEAK - 20, "Okay."), (PEAK - 14, "You really think so?")))
    second = fresh.lines[1]
    assert not hooks.mid_sentence(fresh, hooks.opening_start(fresh, second))


def test_a_vision_model_that_gives_no_answer_for_six_frames_is_asked_about_three(tmp_path):
    look = a_look()
    look.frames = [(PEAK + t, tmp_path / f"{n}.jpg") for n, t in enumerate(hooks.FRAMES_AT)]
    for _, path in look.frames:
        path.write_bytes(b"jpg")
    asked = []

    class Vision:
        def chat_json(self, messages, schema, images=(), **kwargs):
            asked.append(len(images))
            if len(images) > 3:
                raise LLMError("qwen did not return JSON")
            return {"frames": ["a car"] * len(images), "sudden": "a crash"}

    hooks.see(Vision(), look)
    assert asked == [6, 3] and len(look.frames) == 3 and look.seen == ["a car"] * 3


def test_an_opening_not_heard_again_on_its_own_is_left_out(tmp_path):
    def judged(peak, said_text):
        look = a_look(peak=peak)
        look.audio = np.zeros(int((look.end - look.start) * hooks.RATE), dtype=np.float32)
        r = hooks.Read(what_happens="w", payoff_score=8, stands_alone=7)
        opening = hooks.Opening(peak - 10, said_text, 8, "", "L1")
        return hooks.Judged(look, r, opening, peak + 8, None)

    real = judged(1000.0, "you really think you can beat me")
    ghost = judged(2000.0, "he said I want to kill you")
    heard = []

    def transcribe(path):
        heard.append(path)
        # The first opening's words in its own stretch of the file, nothing in the second.
        return [Word(text=w, start=0.4 + 0.2 * n, end=0.5 + 0.2 * n)
                for n, w in enumerate(["really", "think", "you", "can", "beat"])]  # fmt: skip

    assert hooks.heard_again([real, ghost], transcribe, tmp_path) == [real]
    assert len(heard) == 1


def test_moments_are_written_down_a_few_at_a_time_each_a_short_gpu_turn(tmp_path):
    looks = [a_look(peak=500.0 + 100 * n) for n in range(hooks.WRITTEN_AT_ONCE + 1)]
    for look in looks:
        look.audio = np.zeros(int((look.end - look.start) * hooks.RATE), dtype=np.float32)
    calls = []
    hooks.write_down(looks, lambda path: calls.append(path) or [], tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("handle", "fans_say"),
    [("therealjuliannewman", "Julian Newman"), ("KanelJoseph", "Kanel"), ("n3on", "N3on"),
     ("sheismichaela", "Michaela"), ("iamtiagz", "Tiagz"), ("xqc", "xQc"),
     ("theRealSomebody", "Somebody"), ("iamjohn_smith", "John Smith"), ("bob-official", "Bob"),
     ("plainname", "Plainname")],
)  # fmt: skip
def test_a_streamer_is_named_the_way_fans_write_them(handle, fans_say):
    assert hunt.fan_name(handle) == fans_say


def test_the_model_is_asked_for_plain_words_and_the_streamer_by_name():
    prompt = hooks.read_prompt(a_look(said(*ROWS)), "Kanel", "IRL GTA 6")
    assert "Kanel by name" in prompt and 'never "the streamer"' in prompt
    assert "chaotic" in prompt  # named as a word not to use
