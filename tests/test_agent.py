from agentic_editor.agent import (
    Request,
    apply,
    confidence,
    grounded,
    interpret,
    lengths,
    normalise,
    seconds,
)
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset

PRESET = Preset(name="v", label="v", min_duration=20, max_duration=120)


def ask(setting, quote, value=""):
    return Request(setting=setting, value=value, quote=quote)


def test_grounded_requests_change_only_what_they_ask_for():
    text = "Spanish subtitles, warm look, two versions"
    changed, notes, _ = apply(
        PRESET,
        [
            ask("captions_language", "Spanish subtitles"),
            ask("look", "warm look"),
            ask("versions", "two versions"),
        ],
        text,
    )
    assert (changed.captions.mode, changed.captions.language) == ("translated", "Spanish")
    assert changed.grade.look == "warm" and changed.hook.variants == 2
    assert changed.audio == PRESET.audio and changed.framing == PRESET.framing
    assert "from your instruction: captions in Spanish" in notes


def test_a_change_the_instruction_never_asked_for_is_dropped():
    text = "Add Spanish subtitles"
    changed, notes, _ = apply(PRESET, [ask("remove_instruments", "Add Spanish subtitles")], text)
    assert changed == PRESET
    assert notes == [
        "ignored a reading of the instruction (remove_instruments): its quote isn't about that"
    ]


def test_a_quote_that_is_not_in_the_instruction_is_dropped():
    assert grounded(ask("whole_picture", "show the full frame"), "make it warm") is not None


def test_turning_something_off_needs_words_that_say_so():
    assert grounded(ask("captions_off", "hindi subtitles"), "hindi subtitles") is not None
    assert grounded(ask("captions_off", "no captions"), "no captions please") is None


def test_values_left_out_are_read_from_the_quote():
    assert normalise(ask("captions_language", "Add Spanish subtitles")).value == "Spanish"
    assert normalise(ask("captions_off", "hindi subtitles")).setting == "captions_language"
    changed, *_ = apply(PRESET, [ask("look", "black and white")], "black and white please")
    assert changed.grade.look == "mono"


def test_lengths_are_read_in_seconds_or_minutes():
    assert seconds("under 30 seconds") == 30
    assert seconds("under a minute") == 60
    assert seconds("2 minutes max") == 120


def test_a_change_that_contradicts_another_is_dropped_on_its_own():
    text = "Spanish subtitles, at least 90 seconds but at most 30 seconds"
    changed, notes, failed = apply(
        PRESET,
        [
            ask("captions_language", "Spanish subtitles"),
            ask("min_seconds", "at least 90 seconds"),
            ask("max_seconds", "at most 30 seconds"),
        ],
        text,
    )
    assert changed.captions.language == "Spanish"
    assert (changed.min_duration, changed.max_duration) == (90, 120)
    assert failed == [
        'couldn\'t apply "at most 30 seconds" from your instruction '
        "(min_duration is longer than max_duration)"
    ]
    assert failed[0] in notes


def test_a_limit_outside_the_preset_moves_its_other_end():
    text = "Spanish subtitles, warm look, keep it under 15 seconds"
    changed, notes, failed = apply(
        PRESET,
        [
            ask("captions_language", "Spanish subtitles", "Spanish"),
            ask("look", "warm look", "warm"),
            ask("max_seconds", "under 15 seconds", "15"),
        ],
        text,
    )
    assert not failed
    assert (changed.captions.language, changed.grade.look) == ("Spanish", "warm")
    assert (changed.min_duration, changed.max_duration) == (15, 15)
    assert "from your instruction: minimum lowered to 15 s to fit" in notes
    changed, _, failed = apply(
        PRESET, [ask("min_seconds", "at least 3 minutes")], "at least 3 minutes"
    )
    assert not failed and (changed.min_duration, changed.max_duration) == (180, 180)


def test_a_range_uses_both_of_its_ends():
    text = "make it between 30 and 50 seconds"
    quote = "between 30 and 50 seconds"
    changed, *_ = apply(
        PRESET, [ask("min_seconds", quote, "30"), ask("max_seconds", quote, "50")], text
    )
    assert (changed.min_duration, changed.max_duration) == (30, 50)
    # Without the model's values, each end of the range is read from the quote.
    changed, *_ = apply(PRESET, [ask("max_seconds", quote), ask("min_seconds", quote)], text)
    assert (changed.min_duration, changed.max_duration) == (30, 50)
    # A value the quote doesn't name isn't trusted.
    changed, *_ = apply(PRESET, [ask("max_seconds", "under 40 seconds", "400")], "under 40 seconds")
    assert changed.max_duration == 40


def test_lengths_are_read_from_ranges_halves_and_clock_times():
    assert lengths("between 30 and 50 seconds") == [30, 50]
    assert lengths("under a minute and a half") == [90]
    assert lengths("one and a half minutes") == [90]
    assert lengths("half a minute") == [30]
    assert lengths("1:30 tops") == [90]
    assert lengths("30-45s") == [30, 45]
    assert lengths("two versions, under 40 seconds") == [40]
    assert lengths("make it short") == []


def test_interpret_sends_the_instruction_and_reads_the_requests():
    class Model:
        def chat_json(self, messages, schema, **kwargs):
            self.prompt = messages[-1]["content"]
            limit = {"setting": "max_seconds", "value": "40", "quote": "under 40 seconds"}
            return {"requests": [limit], "content_notes": "start on the chorus"}

    model = Model()
    wanted = interpret(model, "keep it under 40 seconds and start on the chorus")
    assert wanted.requests[0].setting == "max_seconds"
    assert wanted.content_notes == "start on the chorus" and "under 40 seconds" in model.prompt


def test_a_clean_render_is_fully_confident():
    edl = EDL(clips=[Clip(start=0, end=30)], decisions=["captions translated into Spanish"])
    assert confidence(edl, [], "qwen3.5:9b", PRESET) == (1.0, [])


def test_problems_lower_confidence_and_say_why():
    failed = "translation failed (timeout); showing the speech"
    edl = EDL(clips=[Clip(start=0, end=12)], decisions=[failed], doubts=[failed])
    score, doubts = confidence(edl, ["loudness: -18.0 LUFS"], "heuristic", PRESET)
    assert score < 0.5
    assert "loudness: -18.0 LUFS" in doubts and "planned without the language model" in doubts
    assert any("under the 20s minimum" in d for d in doubts) and failed in doubts


def test_words_quoted_in_a_decision_are_not_doubts():
    edl = EDL(
        clips=[Clip(start=10, end=35)],
        decisions=[
            'found where you asked to start: 10.0s, "the night the deal failed"',
            "opened 1.2s later, on a stronger line: a confession that he couldn't stop",
        ],
    )
    assert confidence(edl, [], "qwen3.5:9b", PRESET) == (1.0, [])


def test_saying_where_to_start_keeps_that_opening():
    text = "start where he talks about the FCC"
    changed, notes, _ = apply(PRESET, [ask("start_point", text)], text)
    assert changed.hook.optimize is False
    assert notes == ["from your instruction: the opening stays where you asked it to start"]


def test_an_opening_the_model_missed_is_still_kept():
    class Model:
        def chat_json(self, messages, schema, **kwargs):
            return {"requests": [], "content_notes": ""}

    wanted = interpret(Model(), "Warm look. Begin with him saying hello, then the story")
    assert [(r.setting, r.quote) for r in wanted.requests] == [
        ("start_point", "Begin with him saying hello")
    ]


def test_saying_something_is_not_a_request_for_on_screen_text():
    text = "begin with him saying hello"
    assert grounded(ask("one_liner", text), text) is not None


def test_a_long_stretch_without_voice_inside_the_reel_needs_a_look():
    edl = EDL(clips=[Clip(start=60, end=85)])
    score, doubts = confidence(edl, [], "qwen3.5:9b", PRESET, [(70.6, 77.9)])
    assert doubts == ["7s without voice at 11s"] and score < 1.0
