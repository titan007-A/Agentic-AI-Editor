from agentic_editor.edl import EDL, Clip
from agentic_editor.pipeline import fix
from agentic_editor.presets import Preset
from agentic_editor.review import Issue
from tests.conftest import words_from

PRESET = Preset(name="t", label="t", min_duration=4, max_duration=6, framing={"mode": "center"})
WORDS = words_from("a@0-0.5 b@1-1.5 c@3-3.5 d@5-5.5 e@7-7.5 f@9-9.5")


def test_loudness_target_is_moved_by_the_measured_error(make_analysis):
    issue = Issue("loudness", "too quiet", measured=-15.5)
    _, preset, changed = fix(
        [issue], EDL(clips=[Clip(start=0, end=5)]), PRESET, make_analysis(WORDS, 10), -14.0
    )
    assert changed and preset.audio.loudness_lufs == -12.5


def test_cut_off_face_escalates_from_center_to_face_tracking(make_analysis):
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="center")
    edl, _, changed = fix([Issue("framing", "cut")], edl, PRESET, make_analysis(WORDS, 10), -14)
    assert changed and edl.crop_mode == "face"
    edl, preset, _ = fix([Issue("framing", "cut")], edl, PRESET, make_analysis(WORDS, 10), -14)
    assert preset.framing.smoothing == 0.5
    edl, _, _ = fix([Issue("framing", "cut")], edl, preset, make_analysis(WORDS, 10), -14)
    assert edl.crop_mode == "none"


def test_too_long_plan_is_trimmed_to_a_line_boundary(make_analysis):
    edl = EDL(clips=[Clip(start=0, end=9.5)])
    edl, _, changed = fix([Issue("duration", "long")], edl, PRESET, make_analysis(WORDS, 10), -14)
    assert changed and edl.duration <= PRESET.max_duration


def test_issues_without_a_fix_leave_the_plan_alone(make_analysis):
    edl = EDL(clips=[Clip(start=0, end=5)])
    assert fix([Issue("captions", "late")], edl, PRESET, make_analysis(WORDS, 10), -14)[2] is False
