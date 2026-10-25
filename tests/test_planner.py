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


def test_out_of_range_candidates_are_dropped(make_analysis):
    llm = FakeLLM(candidates((3, 99)), candidates((4, 99)))
    with pytest.raises(LLMError, match="no usable segments"):
        plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)


def test_an_empty_answer_is_asked_again(make_analysis):
    llm = FakeLLM({"candidates": []}, candidates((1, 3)), final(0), {"words": []})
    edl = plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    assert edl.clips[0].start == 4.0
    assert "at least one segment" in llm.prompts[1][-1]["content"]


def test_rule_violations_are_fed_back_and_retried(make_analysis):
    llm = FakeLLM(candidates((1, 1), (3, 3)), final(7), final(0), {"words": []})
    edl = plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    assert len(edl.clips) == 1
    feedback = llm.prompts[2][-1]["content"]
    assert "C7 is not one of the candidates" in feedback


def test_a_pick_of_no_candidate_falls_back_to_the_best_scored_one(make_analysis):
    llm = FakeLLM(candidates((1, 3)), final(-1), final(-1), final(-1), {"words": []})
    edl = plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    assert edl.clips[0].start == 4.0 and edl.hook == "Nobody saw this coming"


def test_only_real_candidates_can_be_picked(make_analysis):
    class Recording(FakeLLM):
        def chat_json(self, messages, schema, **kwargs):
            self.schemas = [*getattr(self, "schemas", []), schema]
            return super().chat_json(messages, schema, **kwargs)

    llm = Recording(candidates((1, 3), (2, 4)), final(1), {"words": []})
    plan_with_llm(llm, make_analysis(transcript(), 20.0), PRESET)
    assert llm.schemas[1]["properties"]["candidate"]["enum"] == [0, 1]


def test_gives_up_after_two_retries(make_analysis):
    llm = FakeLLM(candidates((1, 2)), {"pick": 0}, {"pick": 0}, {"pick": 0})
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


def test_segments_without_dead_air_are_preferred(make_analysis):
    words = (
        sentence(0, "this opening line is strong")
        + sentence(8, "after a long quiet stretch")
        + sentence(11, "the story carries on here")
        + sentence(14, "and ends on a high note")
    )
    analysis = make_analysis(words, 20.0, silences=[(2.0, 7.8)])
    # The model lists the segment with the six quiet seconds first.
    llm = FakeLLM(candidates((0, 1), (2, 3)), final(0))
    plain = PRESET.model_copy(update={"zoom_punch_ins": False})
    edl = plan_with_llm(llm, analysis, plain)
    # The clean segment, grown by a line to the minimum, still clear of the silence.
    assert (edl.clips[0].start, edl.clips[0].end) == (8.0, 16.3)
    assert "C1" not in llm.prompts[1][-1]["content"]  # only the clean one was offered


def test_a_source_shorter_than_the_minimum_is_still_planned(make_analysis):
    from agentic_editor import pipeline

    words = [w for k in range(3) for w in sentence(k * 4, f"this is sentence {k} here.")]
    preset = PRESET.model_copy(
        update={"min_duration": 15, "max_duration": 60, "zoom_punch_ins": False}
    )
    llm = FakeLLM(candidates((0, 2)), final(0), {"option": 0, "reason": "x"})
    llm.model = "fake"
    (edl, planner), *_ = pipeline.plan(llm, make_analysis(words, 12.0), preset, None)
    assert planner == "fake" and edl.hook == "Nobody saw this coming"
    assert len(llm.prompts) <= 3  # no retries over a length the source doesn't have


def test_a_requested_opening_near_the_end_is_kept_and_flagged(make_analysis):
    from agentic_editor import trim
    from agentic_editor.planner import plan_variants

    words = [w for k in range(10) for w in sentence(k * 4, f"this is sentence {k} here.")]
    words += sentence(40, "and finally the FCC part.")
    analysis = make_analysis(words, 44.0)
    preset = PRESET.model_copy(
        update={"min_duration": 15, "max_duration": 30, "zoom_punch_ins": False}
    )
    preset = preset.model_copy(update={"hook": preset.hook.model_copy(update={"optimize": False})})
    fcc = next(ln for ln in build_lines(analysis) if "FCC" in ln.text)
    llm = FakeLLM({"line": fcc.index, "words": "the FCC part"}, final(0))
    (edl,) = plan_variants(llm, analysis, preset, None, 1, start_at="start at the FCC part")
    edl = trim.apply(edl, analysis, preset)
    assert edl.clips[0].start >= fcc.start - 0.25
    assert edl.doubts and "after the chosen opening" in edl.doubts[0]


def test_vocal_lines_past_the_end_of_the_audio_are_still_split(make_analysis):
    # The video runs 60 s but its audio stops at 40 s, in the middle of a sung line.
    analysis = make_analysis([], 60.0, [-20.0] * 80, audio_source="vocals", silences=[(0.0, 20.0)])
    lines = build_lines(analysis)
    assert lines[-1].end == 60.0 and all(ln.end - ln.start <= 12.0 for ln in lines)


def test_a_vocal_stem_that_never_falls_silent_is_planned_from_its_sound(make_analysis):
    from agentic_editor.planner import build_lines

    # Call and response with a crowd: voice all the way, a few words in the transcript.
    words = [
        Word(text=t, start=7.8 + 0.2 * i, end=7.95 + 0.2 * i)
        for i, t in enumerate(["all", "over", "the", "world"])
    ]
    energy = [-20.0] * 60
    energy[30] = -45.0  # the quietest moment, at 15 s
    analysis = make_analysis(words, 30.0, energy, audio_source="vocals", silences=[])
    lines = build_lines(analysis)
    assert lines[0].start == 0.0 and lines[-1].end == 30.0
    assert sum(ln.end - ln.start for ln in lines) == 30.0
