import numpy as np
import pytest

from agentic_editor.analysis.audio import energy_curve, levels, parse_silencedetect
from agentic_editor.render.audio import MAX_LIMITING_DB, Loudness, loudness_gain, parse_loudnorm

SILENCE_LOG = """
[silencedetect @ 0x1] silence_start: 1.25
[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.25
[silencedetect @ 0x1] silence_start: -0.01
[silencedetect @ 0x1] silence_end: 0.4 | silence_duration: 0.4
[silencedetect @ 0x1] silence_start: 9.1
"""


def test_parse_silencedetect_pairs_and_clamps():
    assert parse_silencedetect(SILENCE_LOG, duration=10.0) == [
        (1.25, 2.5),
        (0.0, 0.4),
        (9.1, 10.0),
    ]


def test_energy_curve_measures_each_window():
    rate = 1000
    loud = np.full(500, 0.5, dtype=np.float32)
    quiet = np.full(500, 0.005, dtype=np.float32)
    curve = energy_curve(np.concatenate([loud, quiet, loud[:100]]), rate, hop=0.5)
    assert len(curve) == 3
    assert curve[0] == pytest.approx(-6.02, abs=0.01)
    assert curve[1] == pytest.approx(-46.02, abs=0.01)


def test_levels_of_full_scale_sine():
    t = np.linspace(0, 1, 48000, endpoint=False)
    rms, peak = levels(np.sin(2 * np.pi * 440 * t).astype(np.float32))
    assert rms == pytest.approx(-3.01, abs=0.05)
    assert peak == pytest.approx(0.0, abs=0.01)


def test_parse_loudnorm_takes_last_block():
    log = 'noise {"input_i" : "-30.0"}\nmore\n{"input_i" : "-23.5", "input_tp" : "-4.0"}'
    assert parse_loudnorm(log)["input_i"] == "-23.5"


def test_loudness_gain_reaches_target_when_peaks_allow():
    gain, limiting = loudness_gain(Loudness(-20.0, -9.0), target=-14.0, ceiling=-1.5)
    assert (gain, limiting) == (6.0, 0.0)


def test_loudness_gain_limits_a_little_to_reach_target():
    gain, limiting = loudness_gain(Loudness(-22.5, -9.0), target=-14.0, ceiling=-1.5)
    assert gain == 8.5
    assert limiting == pytest.approx(1.0)


def test_loudness_gain_stays_quieter_rather_than_squash():
    gain, limiting = loudness_gain(Loudness(-19.0, -1.0), target=-14.0, ceiling=-1.5)
    assert limiting == MAX_LIMITING_DB
    assert gain == pytest.approx(2.5)
