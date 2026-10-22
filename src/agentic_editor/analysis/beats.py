"""Where the beats and bar lines fall, measured on the original mix: a vocal stem has no
drums to find them in."""

from pathlib import Path

from pydantic import BaseModel

PER_BAR = 4


class Beats(BaseModel):
    tempo: float = 0.0
    beats: list[float] = []
    downbeats: list[float] = []

    @property
    def interval(self) -> float:
        """Seconds between beats."""
        return 60.0 / self.tempo if self.tempo else 0.0


def downbeat_phase(strengths: list[float], per_bar: int = PER_BAR) -> int:
    """Which beat of each group starts the bar: the one with the strongest onsets."""
    if len(strengths) < per_bar:
        return 0
    means = [sum(strengths[p::per_bar]) / len(strengths[p::per_bar]) for p in range(per_bar)]
    return max(range(per_bar), key=lambda p: means[p])


def detect(audio: Path) -> Beats:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr)
    times = librosa.frames_to_time(frames, sr=sr)
    if len(times) == 0:
        return Beats()
    phase = downbeat_phase([float(v) for v in onset[frames]])
    return Beats(
        tempo=round(float(np.atleast_1d(tempo)[0]), 2),
        beats=[round(float(t), 3) for t in times],
        downbeats=[round(float(t), 3) for t in times[phase::PER_BAR]],
    )
