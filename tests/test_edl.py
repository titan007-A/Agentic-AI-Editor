import pytest
from pydantic import ValidationError

from agentic_editor.edl import EDL, Clip, problems
from agentic_editor.presets import Preset

PRESET = Preset(name="t", label="t", min_duration=20, max_duration=60, clip_count=2)


def edl(*spans):
    return EDL(clips=[Clip(start=s, end=e) for s, e in spans])


def test_valid_edl_has_no_problems():
    assert problems(edl((0, 15), (30, 45)), PRESET, source_duration=100) == []


def test_reports_rule_violations():
    found = problems(edl((0, 30), (20, 50), (60, 70)), PRESET, source_duration=65)
    text = " | ".join(found)
    assert "3 clips" in text
    assert "starts before clip 0 ends" in text
    assert "after the source" in text
    assert "longer than the 60s maximum" in text


def test_too_short_total():
    assert "shorter" in problems(edl((0, 10)), PRESET, 100)[0]


def test_clip_must_end_after_it_starts():
    with pytest.raises(ValidationError):
        Clip(start=5, end=5)


def test_maps_source_time_onto_output_timeline():
    plan = edl((10, 20), (40, 50))
    assert plan.to_output_time(15) == 5
    assert plan.to_output_time(42) == 12
    assert plan.to_output_time(30) is None
