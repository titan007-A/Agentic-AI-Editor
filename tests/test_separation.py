import importlib.util

import numpy as np
import pytest

from agentic_editor.analysis.separation import (
    MIN_FINGERPRINT_CORRELATION,
    fingerprint_correlation,
    is_instrument_free,
    loudest_stretch,
)

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


# The MFCCs come from librosa, which only the ml extra installs.
needs_librosa = pytest.mark.skipif(
    importlib.util.find_spec("librosa") is None, reason="librosa is in the ml extra"
)


def sung_notes(seconds: int) -> np.ndarray:
    """A voice-like line: a new harmonic note every half second."""
    t = np.arange(RATE // 2) / RATE
    notes = []
    for pitch in rng.uniform(150, 500, seconds * 2):
        note = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 6))
        notes.append(note * np.hanning(len(t)))
    return (0.2 * np.concatenate(notes)).astype(np.float32)


@needs_librosa
def test_vocals_after_an_instrumental_intro_are_compared_where_they_sing():
    intro = rng.normal(0, 0.1, RATE * 18).astype(np.float32)
    voice = sung_notes(20)
    source = np.concatenate([intro, voice + rng.normal(0, 0.02, len(voice)).astype(np.float32)])
    vocals = np.concatenate([np.full(len(intro), 1e-6, np.float32), voice])
    assert loudest_stretch(vocals, RATE, 15) >= len(intro)
    assert fingerprint_correlation(source, vocals, RATE) >= MIN_FINGERPRINT_CORRELATION


@needs_librosa
def test_a_stem_of_noise_still_fails():
    source = sung_notes(30) + rng.normal(0, 0.02, RATE * 30).astype(np.float32)
    noise = rng.normal(0, 0.1, RATE * 30).astype(np.float32)
    assert fingerprint_correlation(source, noise, RATE) < MIN_FINGERPRINT_CORRELATION


def test_loudest_stretch_of_a_clip_shorter_than_the_window_starts_at_zero():
    assert loudest_stretch(sung_notes(4), RATE, 15) == 0
