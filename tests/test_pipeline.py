from agentic_editor import pipeline, trim
from agentic_editor.edl import EDL, Clip
from agentic_editor.llm import LLMError
from agentic_editor.presets import Preset
from tests.conftest import words_from

WORDS = words_from("a.@0-0.5 b.@1-1.5 c.@3-3.5 d.@5-5.5 e.@7-7.5 f.@9-9.5")


class Down:
    model = "down"

    def chat_json(self, messages, schema, **kwargs):
        raise LLMError("timeout")


def test_a_failed_translation_is_a_doubt(make_analysis):
    preset = Preset(
        name="t",
        label="t",
        min_duration=4,
        max_duration=8,
        captions={"mode": "translated", "language": "Spanish"},
    )
    edl = pipeline.translated(
        Down(), EDL(clips=[Clip(start=0, end=5)]), make_analysis(WORDS, 10), preset, None
    )
    assert edl.doubts == [edl.decisions[-1]] and edl.doubts[0].startswith("translation failed")


def test_a_reel_left_short_is_a_doubt_once(make_analysis):
    preset = Preset(name="t", label="t", min_duration=30, max_duration=60)
    edl = trim.apply(EDL(clips=[Clip(start=0, end=9.5)]), make_analysis(WORDS, 10), preset)
    edl = trim.apply(edl, make_analysis(WORDS, 10), preset)
    assert len(edl.doubts) == 1 and "left shorter" in edl.doubts[0]
