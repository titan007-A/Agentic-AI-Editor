import itertools

import pytest

from agentic_editor.edl import Cue
from agentic_editor.llm import LLMError
from agentic_editor.render.compose import banner_top
from agentic_editor.subtitles import one_line, translate, visual_cues


class Model:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def chat_json(self, messages, schema, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return self.reply


CUES = [Cue(start=0, end=2, text="I worked at the FCC"), Cue(start=2, end=5, text="for ten years")]


def test_translation_keeps_the_timing_and_drops_word_highlights():
    model = Model(
        {"lines": [{"n": 0, "text": "Trabajé en la FCC"}, {"n": 1, "text": "durante diez años"}]}
    )
    out = translate(model, CUES, "Spanish", notes="informal")
    assert [(c.start, c.end, c.text) for c in out] == [
        (0, 2, "Trabajé en la FCC"),
        (2, 5, "durante diez años"),
    ]
    assert all(not c.words for c in out)
    assert "Spanish" in model.prompts[0] and "informal" in model.prompts[0]


def test_a_skipped_line_is_asked_for_again_on_its_own():
    class Skipper(Model):
        def chat_json(self, messages, schema, **kwargs):
            self.prompts.append(messages[-1]["content"])
            if len(self.prompts) == 1:
                return {"lines": [{"n": 0, "text": "Trabajé en la FCC"}]}
            return {"lines": [{"n": 1, "text": "durante diez años"}]}

    model = Skipper(None)
    out = translate(model, CUES, "Spanish", None)
    assert [c.text for c in out] == ["Trabajé en la FCC", "durante diez años"]
    assert "1. for ten years" in model.prompts[1] and "0. I worked" not in model.prompts[1]


def test_lines_still_missing_after_asking_twice_are_refused():
    with pytest.raises(LLMError):
        translate(Model({"lines": [{"n": 0, "text": "solo una"}]}), CUES, "Spanish", None)


def test_captions_from_the_picture_are_written_one_frame_at_a_time():
    class Writer(Model):
        def chat_json(self, messages, schema, **kwargs):
            self.prompts.append(messages[-1]["content"])
            assert len(kwargs["images"]) == 1
            return {"line": ["She steps up", "The crowd goes wild"][len(self.prompts) - 1]}

    model = Writer(None)
    cues = visual_cues(model, ["a.jpg", "b.jpg"], 10.0, "", None)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (0, 5.0, "She steps up"),
        (5.0, 10.0, "The crowd goes wild"),
    ]
    assert "the caption before it, which was: She steps up" in model.prompts[1]


def test_the_one_liner_is_cut_to_its_word_limit():
    model = Model({"line": '"A voice that stops the whole room cold tonight"'})
    line = one_line(model, ["a.jpg"], 6, "about the singer", "we sang all night", None)
    assert line == "A voice that stops the whole"
    assert "about the singer" in model.prompts[0] and "we sang all night" in model.prompts[0]


def test_one_liner_moves_to_the_other_end_when_faces_are_under_it():
    faces_at_top = [(t, [(250.0, 420.0)]) for t in range(10)]
    assert banner_top("top", 260, 90, 1920, faces_at_top) == 1920 - 260 - 90
    assert banner_top("top", 260, 90, 1920, []) == 260


def test_long_lines_wrap_into_two_and_split_into_timed_captions():
    from agentic_editor.subtitles import fit_lines

    cue = Cue(start=0, end=6, text="one two three four five six seven eight nine ten eleven twelve")
    fitted = fit_lines([cue], lambda s: 10 * len(s), max_width=100)
    assert all(c.text.count("\n") <= 1 for c in fitted)
    assert all(10 * len(line) <= 100 for c in fitted for line in c.text.split("\n"))
    assert fitted[0].start == 0 and fitted[-1].end == 6
    assert all(a.end == b.start for a, b in itertools.pairwise(fitted))


def test_a_banner_never_covers_one_already_placed():
    faces_at_top = [(t, [(250.0, 420.0)]) for t in range(10)]
    first = banner_top("top", 260, 90, 1920, faces_at_top)
    second = banner_top("top", 260, 90, 1920, faces_at_top, taken=[(first, first + 90)])
    assert first == 1920 - 260 - 90
    assert second + 90 <= first or second >= first + 90
    assert second + 90 <= 250 - 24 or second >= 420 + 24


def test_speech_is_translated_a_sentence_at_a_time_and_keeps_its_timing():
    from agentic_editor.subtitles import translate_speech
    from agentic_editor.transcript import Word

    text = "I wrote this paper. It was one of the first papers on that"
    words = [Word(text=w, start=i * 0.5, end=(i + 1) * 0.5) for i, w in enumerate(text.split())]
    model = Model(
        {
            "lines": [
                {"n": 0, "text": "Escribí este artículo."},
                {"n": 1, "text": "Fue uno de los primeros artículos sobre eso"},
            ]
        }
    )
    cues = translate_speech(model, words, "Spanish", None, per_cue=4)
    assert "0. I wrote this paper.\n1. It was one of the first papers on that" in model.prompts[0]
    assert [c.text for c in cues] == [
        "Escribí este artículo.",
        "Fue uno de los",
        "primeros artículos sobre eso",
    ]
    assert (cues[0].start, cues[0].end) == (0.0, 2.0)
    assert cues[1].start == 2.0 and cues[-1].end == 6.5
    assert all(a.end == b.start for a, b in itertools.pairwise(cues))
