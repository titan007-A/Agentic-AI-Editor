from agentic_editor.edl import EDL, Clip
from agentic_editor.hooks import options, sharpen
from agentic_editor.planner import build_lines
from agentic_editor.presets import Preset
from tests.test_planner import sentence

PRESET = Preset(name="t", label="t", min_duration=10, max_duration=60)


class Reader:
    def __init__(self, option):
        self.option = option
        self.prompts = []

    def chat_json(self, messages, schema, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return {"option": self.option, "reason": "opens on a question"}


def talk():
    return (
        sentence(0, "so um where was I")
        + sentence(4, "why did nobody see this coming")
        + sentence(8, "the whole room went silent")
        + sentence(12, "and then everyone started laughing")
        + sentence(16, "anyway thanks for watching")
    )


def test_speech_opens_on_the_line_the_model_finds_strongest(make_analysis):
    analysis = make_analysis(talk(), 30.0)
    edl = sharpen(Reader(1), EDL(clips=[Clip(start=0, end=20)]), analysis, PRESET, None)
    assert edl.clips[0].start == 4.0 - 0.25
    assert edl.decisions[-1].startswith("opened 3.8s later") and "question" in edl.decisions[-1]


def test_keeping_the_first_line_is_noted_but_changes_nothing(make_analysis):
    edl = EDL(clips=[Clip(start=0, end=20)])
    kept = sharpen(Reader(0), edl, make_analysis(talk(), 30.0), PRESET, None)
    assert kept.clips == edl.clips
    assert kept.decisions == ["kept the planned opening; it was the strongest of 3"]


def test_openings_that_would_leave_the_reel_too_short_are_not_offered(make_analysis):
    lines = build_lines(make_analysis(talk(), 30.0))
    starts = [ln.start for ln in options(EDL(clips=[Clip(start=0, end=20)]), lines, PRESET)]
    assert starts == [0.0, 4.0, 8.0]  # 12.0 would leave under 10 s


def test_music_opens_on_a_clearly_louder_entrance(make_analysis):
    # Quiet verse for 6 s, then the chorus comes in loud.
    energy = [-34.0] * 12 + [-14.0] * 48
    analysis = make_analysis(
        [], 30.0, energy, audio_source="vocals", silences=[(5.5, 6.0), (20.0, 30.0)]
    )
    edl = sharpen(None, EDL(clips=[Clip(start=0, end=20)]), analysis, PRESET, None)
    assert edl.clips[0].start == 6.0 - 0.25
    assert "dB stronger" in edl.decisions[-1]


def test_music_keeps_its_start_when_nothing_is_clearly_stronger(make_analysis):
    analysis = make_analysis(
        [], 30.0, [-20.0] * 60, audio_source="vocals", silences=[(5.5, 6.0), (20.0, 30.0)]
    )
    edl = EDL(clips=[Clip(start=0, end=20)])
    assert sharpen(None, edl, analysis, PRESET, None).clips == edl.clips


def test_optimizing_can_be_switched_off(make_analysis):
    preset = PRESET.model_copy(update={"hook": PRESET.hook.model_copy(update={"optimize": False})})
    edl = EDL(clips=[Clip(start=0, end=20)])
    assert sharpen(Reader(1), edl, make_analysis(talk(), 30.0), preset, None) == edl


def test_a_later_opening_lands_back_on_the_beat(make_analysis):
    from agentic_editor.analysis.beats import Beats

    rhythm = Beats(
        tempo=120.0,
        beats=[i * 0.5 for i in range(60)],
        downbeats=[0.5 + i * 2.0 for i in range(15)],
    )
    preset = PRESET.model_copy(
        update={"beat_sync": PRESET.beat_sync.model_copy(update={"snap_trims": True})}
    )
    analysis = make_analysis(talk(), 30.0, beats=rhythm)
    edl = sharpen(Reader(1), EDL(clips=[Clip(start=0, end=20)]), analysis, preset, None)
    assert edl.clips[0].start == 3.5  # the beat before 3.75; the bar line is too far back
    assert edl.decisions[-1].startswith("start") and "beat" in edl.decisions[-1]
