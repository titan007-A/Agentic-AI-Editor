from agentic_editor.edl import EDL, Clip, Cue, Shot
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


def test_a_retrimmed_plan_is_framed_again_and_its_captions_follow(make_analysis):
    edl = EDL(
        clips=[Clip(start=0, end=9.5)],
        shots=[Shot(start=0, end=9.5, x=0, width=608)],
        picture=(0, 0, 1920, 1080),
        cues=[Cue(start=0.2, end=1.0, text="uno"), Cue(start=3.1, end=4.0, text="dos")],
    )
    fixed, _, changed = fix([Issue("duration", "long")], edl, PRESET, make_analysis(WORDS, 10), -14)
    clip = fixed.clips[0]
    assert changed and fixed.shots == [] and fixed.picture is None
    shift = clip.start - edl.clips[0].start
    kept = [c for c in edl.cues if c.end - shift > 0 and c.start - shift < clip.duration]
    assert [c.text for c in fixed.cues] == [c.text for c in kept]
    assert all(0 <= c.start < c.end <= clip.duration for c in fixed.cues)
    assert fixed.cues[-1].start == round(max(0.0, kept[-1].start - shift), 3)
