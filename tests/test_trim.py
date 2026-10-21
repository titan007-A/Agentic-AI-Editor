from agentic_editor.edl import EDL, Clip
from agentic_editor.planner import build_lines
from agentic_editor.presets import Preset
from agentic_editor.trim import apply, voice_bounds

VOCALS = Preset(name="v", label="v", min_duration=20, max_duration=45)


def energy(duration: float, loud: tuple[float, float] | None = None) -> list[float]:
    """-30 dB everywhere, -12 dB inside `loud`, sampled every 0.5 s."""
    return [
        -12.0 if loud and loud[0] <= i * 0.5 < loud[1] else -30.0 for i in range(int(duration * 2))
    ]


def plan(start: float, end: float) -> EDL:
    return EDL(clips=[Clip(start=start, end=end)])


def test_silence_left_by_vocal_separation_is_trimmed_from_both_ends(make_analysis):
    analysis = make_analysis([], 40.0, audio_source="vocals", silences=[(0.0, 6.0), (31.0, 40.0)])
    edl = apply(plan(0.0, 40.0), analysis, VOCALS.model_copy(update={"min_duration": 10}))
    clip = edl.clips[0]
    assert (clip.start, clip.end) == (5.75, 31.6)
    assert edl.decisions[:2] == [
        "trimmed 6.0s of silence at the start",
        "trimmed 9.0s of silence at the end",
    ]


def test_a_short_quiet_lead_in_is_left_alone(make_analysis):
    analysis = make_analysis([], 30.0, audio_source="vocals", silences=[(0.0, 0.4)])
    edl = apply(plan(0.0, 30.0), analysis, VOCALS)
    assert edl.clips[0].start == 0.0 and edl.decisions == []


def test_silence_inside_the_reel_is_never_cut_out(make_analysis):
    analysis = make_analysis([], 40.0, audio_source="vocals", silences=[(12.0, 16.0)])
    edl = apply(plan(2.0, 38.0), analysis, VOCALS)
    assert len(edl.clips) == 1
    assert (edl.clips[0].start, edl.clips[0].end) == (2.0, 38.0)


def test_too_long_keeps_one_continuous_line_aligned_window(make_analysis):
    analysis = make_analysis(
        [],
        120.0,
        energy(120.0, loud=(60.0, 100.0)),
        audio_source="vocals",
        silences=[(10.0, 10.8), (30.0, 30.8), (55.0, 55.8), (75.0, 75.8), (100.0, 100.8)],
    )
    edl = apply(plan(0.0, 120.0), analysis, VOCALS)
    clip = edl.clips[0]
    assert len(edl.clips) == 1
    assert 20 <= clip.duration <= 45
    # Starts just before a line begins and ends just after one ends, over the loud part.
    lines = build_lines(analysis)
    assert round(clip.start + 0.25, 3) in {ln.start for ln in lines}
    assert round(clip.end - 0.6, 3) in {ln.end for ln in lines}
    loud = min(clip.end, 100.0) - max(clip.start, 60.0)
    assert loud >= 0.8 * clip.duration
    assert edl.decisions[-1].startswith(f"kept {clip.start:.1f}-{clip.end:.1f}s")


def test_window_choice_steers_around_a_long_silent_gap(make_analysis):
    analysis = make_analysis(
        [],
        90.0,
        audio_source="vocals",
        silences=[(20.0, 26.0), (44.5, 45.0), (89.0, 90.0)],
    )
    edl = apply(plan(0.0, 89.0), analysis, VOCALS)
    clip = edl.clips[0]
    assert clip.start >= 26.0 - 0.25 or clip.end <= 20.6


def test_too_short_after_trimming_grows_into_neighbouring_lines(make_analysis):
    analysis = make_analysis(
        [], 60.0, audio_source="vocals", silences=[(0.0, 5.0), (15.0, 15.5), (40.0, 60.0)]
    )
    edl = apply(plan(15.5, 39.0), analysis, VOCALS.model_copy(update={"min_duration": 30}))
    clip = edl.clips[0]
    assert clip.start == 5.0 and clip.duration >= 30
    assert any(d.startswith("extended to") for d in edl.decisions)


def test_voice_bounds_skip_silence_at_both_edges():
    assert voice_bounds(0, 30, [(0, 4), (25, 30)]) == (4, 25)
    assert voice_bounds(5, 20, [(0, 4), (25, 30)]) == (5, 20)
