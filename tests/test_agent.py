from agentic_editor.agent import Request, apply, confidence, grounded, interpret, normalise, seconds
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset

PRESET = Preset(name="v", label="v", min_duration=20, max_duration=120)


def ask(setting, quote, value=""):
    return Request(setting=setting, value=value, quote=quote)


def test_grounded_requests_change_only_what_they_ask_for():
    text = "Spanish subtitles, warm look, two versions"
    changed, notes = apply(
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
    changed, notes = apply(PRESET, [ask("remove_instruments", "Add Spanish subtitles")], text)
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
    changed, _ = apply(PRESET, [ask("look", "black and white")], "black and white please")
    assert changed.grade.look == "mono"


def test_lengths_are_read_in_seconds_or_minutes():
    assert seconds("under 30 seconds") == 30
    assert seconds("under a minute") == 60
    assert seconds("2 minutes max") == 120


def test_an_impossible_combination_keeps_the_preset():
    text = "at least 90 seconds but at most 30 seconds"
    changed, notes = apply(
        PRESET,
        [ask("min_seconds", "at least 90 seconds"), ask("max_seconds", "at most 30 seconds")],
        text,
    )
    assert changed == PRESET and notes[0].startswith("couldn't apply")


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
    edl = EDL(
        clips=[Clip(start=0, end=12)],
        decisions=["translation failed (timeout); showing the speech"],
    )
    score, doubts = confidence(edl, ["loudness: -18.0 LUFS"], "heuristic", PRESET)
    assert score < 0.5
    assert "loudness: -18.0 LUFS" in doubts and "planned without the language model" in doubts
    assert any("under the 20s minimum" in d for d in doubts)


def test_saying_where_to_start_keeps_that_opening():
    text = "start where he talks about the FCC"
    changed, notes = apply(PRESET, [ask("start_point", text)], text)
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
