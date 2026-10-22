from agentic_editor.analysis.beats import Beats, downbeat_phase
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset
from agentic_editor.render.video import zoom_expression
from agentic_editor.trim import apply, snap_to_beat

# 120 BPM: a beat every 0.5 s, bars starting on the second beat of each group.
RHYTHM = Beats(
    tempo=120.0,
    beats=[i * 0.5 for i in range(200)],
    downbeats=[0.5 + i * 2.0 for i in range(50)],
)


def test_the_strongest_beat_of_each_group_starts_the_bar():
    assert downbeat_phase([1, 5, 1, 1, 2, 6, 1, 2]) == 1
    assert downbeat_phase([3, 1]) == 0


def test_start_moves_back_to_the_bar_line_and_end_on_to_the_next_beat():
    start, end, note = snap_to_beat(10.8, 40.3, RHYTHM, max_duration=60)
    assert start == 10.5  # the bar line 0.3 s before
    assert end == 40.5  # the next bar line, 0.2 s after
    assert note == "start and end moved onto the beat (120 BPM)"


def test_snapping_only_moves_outward_and_never_far():
    start, end, _ = snap_to_beat(11.6, 40.6, RHYTHM, max_duration=60)
    assert 11.6 - 0.5 <= start <= 11.6
    assert 40.6 <= end <= 40.6 + 1.0


def test_snapping_never_pushes_past_the_maximum_length():
    _, end, _ = snap_to_beat(10.5, 40.3, RHYTHM, max_duration=29.9)
    assert end == 40.3


def test_no_tempo_means_no_change():
    assert snap_to_beat(10.8, 40.3, Beats(), 60) == (10.8, 40.3, None)


def test_trimming_snaps_to_the_beat_when_the_preset_asks(make_analysis):
    preset = Preset(name="v", label="v", min_duration=20, max_duration=60)
    preset = preset.model_copy(
        update={"beat_sync": preset.beat_sync.model_copy(update={"snap_trims": True})}
    )
    analysis = make_analysis([], 100.0, beats=RHYTHM)
    edl = apply(EDL(clips=[Clip(start=10.8, end=40.3)]), analysis, preset)
    assert (edl.clips[0].start, edl.clips[0].end) == (10.5, 40.5)


def test_pulses_ease_back_out_after_each_bar():
    expr = zoom_expression(EDL(clips=[Clip(start=0, end=10)]), [2.0], 1.04)
    assert expr == "1+0.040*between(t,2.000,2.300)*(1-(t-2.000)/0.3)"
