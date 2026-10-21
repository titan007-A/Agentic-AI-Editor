import re
from pathlib import Path

import numpy as np

from agentic_editor import ffmpeg

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(
    path: Path, threshold_db: float, min_duration: float
) -> list[tuple[float, float]]:
    result = ffmpeg.run([
        "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])  # fmt: skip
    return parse_silencedetect(result.stderr, ffmpeg.probe(path).duration)


def parse_silencedetect(log: str, duration: float) -> list[tuple[float, float]]:
    silences = []
    start = None
    for line in log.splitlines():
        if m := _SILENCE_START.search(line):
            start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, duration))
    return silences


def to_db(value: float) -> float:
    return 20 * np.log10(max(value, 1e-9))


def energy_curve(samples: np.ndarray, sample_rate: int, hop: float = 0.5) -> list[float]:
    """RMS level in dBFS for consecutive windows of `hop` seconds."""
    size = int(sample_rate * hop)
    if size == 0 or len(samples) == 0:
        return []
    count = int(np.ceil(len(samples) / size))
    padded = np.zeros(count * size, dtype=np.float32)
    padded[: len(samples)] = samples
    rms = np.sqrt(np.mean(padded.reshape(count, size) ** 2, axis=1))
    return [round(to_db(v), 2) for v in rms]


def levels(samples: np.ndarray) -> tuple[float, float]:
    """(rms_db, peak_db) of a signal."""
    if len(samples) == 0:
        return -180.0, -180.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return to_db(rms), to_db(float(np.max(np.abs(samples))))


def loudest_window_db(samples: np.ndarray, sample_rate: int, window: float = 0.05) -> float:
    """RMS of the loudest stretch of `window` seconds: a hit counts, a lone click doesn't."""
    size = max(1, int(sample_rate * window))
    count = len(samples) // size
    if count == 0:
        return levels(samples)[0]
    frames = samples[: count * size].reshape(count, size)
    return to_db(float(np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)).max()))
