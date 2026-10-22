import numpy as np

from agentic_editor.analysis.audio import pauses
from agentic_editor.edl import EDL, Clip
from agentic_editor.hooks import options
from agentic_editor.planner import build_lines
from agentic_editor.presets import Preset
from agentic_editor.transcript import Word, split_phrase
from agentic_editor.trim import apply

PRESET = Preset(name="v", label="v", min_duration=5, max_duration=12)


def run_on(text: str, start: float = 0.0, step: float = 0.3) -> list[Word]:
    """Words the way whisper times them: each one stretched up to the next."""
    return [
        Word(text=w, start=start + i * step, end=start + (i + 1) * step)
        for i, w in enumerate(text.split())
    ]


def numbered(count: int, end_at: int | None = None) -> str:
    return " ".join(f"w{i}" + ("." if i == end_at else "") for i in range(count))


def test_lines_start_where_a_sentence_starts():
    phrase = run_on(numbered(14, end_at=5))
    assert [len(g) for g in split_phrase(phrase, max_words=24)] == [6, 8]


def test_a_long_run_on_sentence_splits_at_its_longest_pause():
    phrase = run_on(numbered(30))

    def pause(before, after):
        return 0.4 if after.text == "w20" else 0.0

    groups = split_phrase(phrase, max_words=24, pause=pause)
    assert [g[0].text for g in groups] == ["w0", "w20"]


def test_short_breaks_in_the_voice_are_found_in_the_audio():
    rate = 16000
    tone = 0.3 * np.sin(np.arange(rate) * 2 * np.pi * 220 / rate).astype(np.float32)
    quiet = np.zeros(int(rate * 0.3), np.float32)
    blip = np.zeros(int(rate * 0.05), np.float32)
    signal = np.concatenate([tone, quiet, tone, blip, tone])
    assert pauses(signal, rate) == [(1.0, 1.3)]


def test_lines_inside_a_sentence_are_marked_for_the_planner(make_analysis):
    words = run_on(numbered(40, end_at=39))
    analysis = make_analysis(words, 13.0, pauses=[(6.0, 6.4)])
    lines = build_lines(analysis)
    assert [(ln.opens, ln.closes) for ln in lines] == [(True, False), (False, True)]
    assert lines[0].render().endswith("...") and "] (" in lines[1].render()
    assert lines[1].render().split(") ", 1)[1].startswith("...")


def test_a_reel_planned_to_start_mid_sentence_starts_where_the_sentence_does(make_analysis):
    words = run_on(numbered(30, end_at=29)) + run_on(numbered(5, end_at=4), start=9.5)
    analysis = make_analysis(words, 12.0, pauses=[(4.5, 4.9)])
    lines = build_lines(analysis)
    assert not lines[1].opens
    edl = apply(EDL(clips=[Clip(start=lines[1].start - 0.25, end=11.0)]), analysis, PRESET)
    assert edl.clips[0].start == 0.0
    assert "where the sentence begins" in edl.decisions[-1]


def test_the_hook_never_moves_the_start_into_the_middle_of_a_sentence(make_analysis):
    words = run_on(numbered(30, end_at=29)) + run_on(numbered(20, end_at=19), start=9.5)
    analysis = make_analysis(words, 16.0, pauses=[(4.5, 4.9)])
    lines = build_lines(analysis)
    assert [ln.opens for ln in lines] == [True, False, True]
    choices = options(EDL(clips=[Clip(start=0.0, end=15.5)]), lines, PRESET)
    assert [c.start for c in choices] == [0.0, 9.5]
