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
    # Too long, it was asked once more for a complete shorter line before the cut.
    assert len(model.prompts) == 2 and "at most 6" in model.prompts[1]


def test_a_long_one_liner_is_asked_again_then_cut_at_a_sentence_end():
    long = "Conducción de un coche convertible al atardecer en la carretera. ¿Quién? Un conductor"

    class Shorter:
        def __init__(self):
            self.prompts = []

        def chat_json(self, messages, schema, **kwargs):
            self.prompts.append(messages[-1]["content"])
            first = len(self.prompts) == 1
            return {"line": long if first else "Un conductor en un convertible al atardecer"}

    model = Shorter()
    assert one_line(model, ["a.jpg"], 12, "", "", None, "Spanish") == (
        "Un conductor en un convertible al atardecer"
    )
    assert "had 13 words" in model.prompts[1]
    # Still too long the second time: cut at its last sentence end, not mid-sentence.
    line = one_line(Model({"line": long}), ["a.jpg"], 12, "", "", None)
    assert line == "Conducción de un coche convertible al atardecer en la carretera. ¿Quién?"
    # No sentence end in the first half: cut at the limit, no dangling comma.
    line = one_line(
        Model({"line": "one two three, four five six seven"}), ["a.jpg"], 3, "", "", None
    )
    assert line == "one two three"


def test_a_one_liner_never_goes_to_the_other_end_only_a_little_nearer_its_own():
    from agentic_editor.subtitles import overlap

    faces = [(t, [(330.0, 500.0)]) for t in range(10)]
    top = banner_top("top", 260, 90, 1920, faces)
    assert 150 <= top < 260 and overlap((top, top + 90), [(330.0, 500.0)]) == 0
    assert banner_top("top", 260, 90, 1920, []) == 260
    # faces it can't get clear of by a small move: it stays where it is put, at the top
    everywhere = [(t, [(0.0, 900.0)]) for t in range(10)]
    assert banner_top("top", 260, 90, 1920, everywhere) == 260
    bottom = banner_top("bottom", 400, 90, 1920, [(t, [(1300.0, 1450.0)]) for t in range(10)])
    assert bottom > 1920 - 400 - 90 and bottom + 90 <= 1920 - 330  # down a little, not up
    assert banner_top("bottom", 400, 90, 1920, everywhere) == 1920 - 400 - 90


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
    assert first == 260  # can't get clear by a small move: stays at the top
    assert second + 90 <= first or second >= first + 90
    assert second + 90 <= 960  # stacked beside it on its own half


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
        "Fue uno de los primeros",
        "artículos sobre eso",
    ]
    assert (cues[0].start, cues[0].end) == (0.0, 2.0)
    assert cues[1].start == 2.0 and cues[-1].end == 6.5
    assert all(a.end == b.start for a, b in itertools.pairwise(cues))


def test_captions_break_after_a_comma_near_the_middle():
    from agentic_editor.subtitles import even_parts

    text = "los 90 trabajaba para la FCC, la Comisión Federal de Comunicaciones"
    first, second = even_parts(text.split(" "), 2)
    assert first[-1] == "FCC," and second[0] == "la"


def test_a_long_caption_is_shared_out_instead_of_leaving_a_word_alone():
    from agentic_editor.subtitles import fit_lines

    cue = Cue(start=0, end=4, text="Comisión Federal de Comunicaciones en Estados Unidos.")
    fitted = fit_lines([cue], lambda s: 10 * len(s), max_width=200)
    assert len(fitted) == 2
    assert all(len(c.text.replace("\n", " ").split()) >= 3 for c in fitted)


def test_a_word_wider_than_the_frame_does_not_stop_the_captions():
    from agentic_editor.edl import Cue
    from agentic_editor.subtitles import fit_lines

    cue = Cue(start=0, end=2, text="Die Geschwindigkeitsbegrenzung gilt")
    out = fit_lines([cue], lambda line: 40.0 * len(line), max_width=950)
    assert " ".join(c.text.replace("\n", " ") for c in out) == cue.text
    assert out[0].start == 0 and out[-1].end == 2


def test_a_one_liner_that_copies_the_guidance_s_example_is_written_again():
    example = "Una demostración en primera persona muestra el manejo de un rifle equipado"

    class Copier:
        def __init__(self):
            self.prompts = []

        def chat_json(self, messages, schema, images=None, temperature=0.0):
            self.prompts.append(messages[0]["content"])
            if len(self.prompts) == 1:
                return {"line": example}
            return {"line": "Marines suben a un buque de guerra en el puerto"}

    model = Copier()
    line = one_line(model, ["a.jpg"], 12, f'Like "{example}"', "", None, "Spanish")
    assert line == "Marines suben a un buque de guerra en el puerto"
    assert len(model.prompts) == 2 and "don't copy" in model.prompts[1]
