import pytest
from pydantic import ValidationError

from agentic_editor.edl import EDL, Clip, problems
from agentic_editor.presets import Preset

PRESET = Preset(name="t", label="t", min_duration=20, max_duration=60)


def edl(*spans):
    return EDL(clips=[Clip(start=s, end=e) for s, e in spans])


def test_valid_edl_has_no_problems():
    assert problems(edl((10, 45)), PRESET, source_duration=100) == []


def test_a_reel_is_never_spliced_from_several_pieces():
    with pytest.raises(ValidationError):
        edl((0, 15), (30, 45))


def test_reports_rule_violations():
    text = " | ".join(problems(edl((0, 70)), PRESET, source_duration=65))
    assert "after the source" in text
    assert "longer than the 60s maximum" in text


def test_long_segment_can_be_left_for_the_trimmer():
    assert problems(edl((0, 90)), PRESET, 100, allow_long=True) == []


def test_too_short_total():
    assert "shorter" in problems(edl((0, 10)), PRESET, 100)[0]


def test_clip_must_end_after_it_starts():
    with pytest.raises(ValidationError):
        Clip(start=5, end=5)


def test_maps_source_time_onto_output_timeline():
    plan = edl((10, 40))
    assert plan.to_output_time(15) == 5
    assert plan.to_output_time(39) == 29
    assert plan.to_output_time(5) is None
    assert plan.to_output_time(40) is None
