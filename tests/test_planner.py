import pytest

from agentic_editor.edl import Clip
from agentic_editor.llm import LLMError
from agentic_editor.planner import (
    Line,
    build_lines,
    chunk_lines,
    describe_lines,
    fit_duration,
    plan_with_llm,
)
from agentic_editor.presets import Preset
from agentic_editor.transcript import Word

PRESET = Preset(name="t", label="t", min_duration=6, max_duration=12, zoom_punch_ins=True)


class FakeLLM:
    """Returns canned replies in order and records the prompts it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def chat_json(self, messages, schema, **kwargs):
        self.prompts.append(messages)
        return self.replies.pop(0)


def sentence(start: float, text: str) -> list[Word]:
    return [
        Word(text=w, start=start + i * 0.4, end=start + i * 0.4 + 0.3)
        for i, w in enumerate(text.split())
    ]


def transcript():
    return (
        sentence(0, "so this is the intro part")
        + sentence(4, "nobody believed what happened next")
        + sentence(8, "the whole room went silent")
        + sentence(12, "and then everyone started laughing")
        + sentence(16, "anyway thanks for watching")
    )


def candidates(*spans):
    return {
        "candidates": [
            {"start_line": a, "end_line": b, "hook": 8, "completeness": 7, "peak": 7, "reason": "x"}
            for a, b in spans
        ]
    }


def final(pick, hook="Nobody saw this coming"):
    return {
        "candidate": pick,
        "reason": "best",
        "hook": hook,
        "title": "The room went silent",
        "caption_position": "middle",
    }


def test_lines_split_at_pauses(make_analysis):
    lines = build_lines(make_analysis(transcript(), 20.0))
    assert [line.text for line in lines][:2] == [
        "so this is the intro part",
        "nobody believed what happened next",
    ]
    assert lines[1].start == 4.0


def test_chunks_respect_length():
    lines = [Line(i, i * 40, i * 40 + 1.1, sentence(i * 40, "a b c"), "normal") for i in range(10)]
    assert [len(c) for c in chunk_lines(lines, seconds=150)] == [4, 4, 2]


def test_plans_from_candidates_and_maps_lines_to_times(make_analysis):
    llm = FakeLLM(candidates((1, 3)), final(0), {"words": ["Silent!", "unicorn"]})
    edl = plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    clip = edl.clips[0]
    assert (clip.start, clip.end) == (
        4.0,
        sentence(12, "and then everyone started laughing")[-1].end,
    )
    assert edl.hook == "Nobody saw this coming"
    assert edl.caption_overrides.position == "middle"
    assert [z.time for z in edl.zooms] == [8 + 4 * 0.4]


def test_out_of_range_candidates_are_dropped(make_analysis):
    llm = FakeLLM(candidates((3, 99)), final(0))
    with pytest.raises(LLMError, match="no usable segments"):
        plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)


def test_rule_violations_are_fed_back_and_retried(make_analysis):
    llm = FakeLLM(candidates((1, 1), (3, 3)), final(7), final(0), {"words": []})
    edl = plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    assert len(edl.clips) == 1
    feedback = llm.prompts[2][-1]["content"]
    assert "C7 is not one of the candidates" in feedback


def test_gives_up_after_two_retries(make_analysis):
    llm = FakeLLM(candidates((1, 2)), final(5), final(5), final(5))
    with pytest.raises(LLMError, match="no valid plan"):
        plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)


def test_short_plan_is_extended_by_whole_lines(make_analysis):
    lines = build_lines(make_analysis(transcript(), 20.0))
    clips = fit_duration([Clip(start=lines[1].start, end=lines[1].end)], lines, PRESET)
    assert clips[0].duration >= PRESET.min_duration
    assert clips[0].start in {ln.start for ln in lines}
    assert clips[0].end in {ln.end for ln in lines}


def test_extension_grows_toward_the_closer_neighbour():
    lines = [
        Line(0, 0.0, 3.0, [], "normal"),
        Line(1, 3.5, 6.0, [], "normal"),
        Line(2, 60.0, 70.0, [], "normal"),
    ]
    clips = fit_duration([Clip(start=3.5, end=6.0)], lines, PRESET)
    assert (clips[0].start, clips[0].end) == (0.0, 6.0)


def test_vocal_lines_follow_where_the_singer_is_audible(make_analysis):
    words = sentence(2.0, "hold me now")
    analysis = make_analysis(
        words, 30.0, audio_source="vocals", silences=[(0.0, 1.5), (5.0, 9.0), (24.0, 30.0)]
    )
    lines = build_lines(analysis)
    assert (lines[0].start, lines[0].end) == (1.5, 5.0)
    assert lines[0].text == "hold me now"
    assert lines[1].start == 9.0 and lines[1].text == "(no clear words)"
    assert all(ln.end - ln.start <= 12.0 for ln in lines)
    assert "4s with no voice" in describe_lines(lines)


def test_variants_come_from_different_segments(make_analysis):
    from agentic_editor.planner import plan_variants

    llm = FakeLLM(candidates((0, 1), (1, 2), (3, 4)), final(0), final(0), final(0))
    preset = PRESET.model_copy(update={"zoom_punch_ins": False})
    edls = plan_variants(llm, make_analysis(transcript(), 20.0), preset, None, count=3)
    spans = [(e.clips[0].start, e.clips[0].end) for e in edls]
    assert [e.variant for e in edls] == [0, 1]
    # The second candidate lies inside the first plan, so the next version is built from
    # the third, grown back to the minimum length; the two share under half their time.
    assert spans == [(0.0, 9.9), (8.0, 17.5)]


def test_a_requested_opening_is_found_and_the_plan_starts_there(make_analysis):
    from agentic_editor.planner import plan_variants

    plain = PRESET.model_copy(update={"zoom_punch_ins": False})
    llm = FakeLLM({"line": 2, "words": "the whole room"}, final(0))
    edls = plan_variants(
        llm, make_analysis(transcript(), 20.0), plain, None, 1, start_at="start when it goes quiet"
    )
    assert len(edls) == 1 and edls[0].clips[0].start == 8.0
    assert edls[0].decisions == ['found where you asked to start: 8.0s, "the whole room"']
    assert 'The editor said: "start when it goes quiet"' in llm.prompts[0][-1]["content"]


def test_an_opening_whose_words_are_not_in_that_line_is_not_trusted(make_analysis):
    from agentic_editor.planner import find_start

    lines = build_lines(make_analysis(transcript(), 20.0))
    assert find_start(FakeLLM({"line": 0, "words": "the whole room"}), lines, "x") is None
    assert find_start(FakeLLM({"line": -1, "words": ""}), lines, "x") is None
