import numpy as np

from agentic_editor.analysis.separation import is_instrument_free

rng = np.random.default_rng(0)
mix = rng.normal(0, 0.2, 22050).astype(np.float32)


RATE = 22050


def test_full_mix_is_not_instrument_free():
    instrumental = mix * 0.7
    assert not is_instrument_free(mix, instrumental, RATE)


def test_clean_vocal_is_instrument_free():
    residue = mix * 0.01
    assert is_instrument_free(mix, residue, RATE)


def test_quiet_stem_with_loud_hits_is_not_instrument_free():
    residue = mix * 0.01
    hit = slice(5000, 5000 + RATE // 10)
    residue[hit] = mix[hit] * 0.9
    assert not is_instrument_free(mix, residue, RATE)


def test_single_click_in_an_empty_stem_is_ignored():
    residue = mix * 0.01
    residue[1000] = 0.9
    assert is_instrument_free(mix, residue, RATE)
