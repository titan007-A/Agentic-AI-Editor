from pathlib import Path

import numpy as np
import pytest

from agentic_editor import analyze, ffmpeg
from agentic_editor.analysis import beats, scenes, separation, watermarks
from agentic_editor.analysis.beats import Beats
from agentic_editor.transcript import Transcript

RATE = 22050
MUSIC = (0.3 * np.random.default_rng(0).standard_normal(RATE * 60)).astype(np.float32)


class Router:
    """Transcribes to `words`, separates into stems whose content `stems` decides."""

    def __init__(self, words=(), stems="empty", model="whisper-a", transcriber="cpu"):
        self.words = list(words)
        self.stems = stems
        self.settings = type(
            "S", (), {"separation_model": "m.ckpt", "whisper_model": model, "ffmpeg_bin": "ffmpeg"}
        )
        self.backends = type("B", (), {"transcriber": transcriber})
        self.transcribed = 0
        self.separated = 0

    def transcribe(self, audio, language=None):
        self.transcribed += 1
        return Transcript(words=self.words)

    def separate_vocals(self, audio, out_dir):
        self.separated += 1
        vocals, rest = out_dir / "a_(Vocals).wav", out_dir / "a_(Instrumental).wav"
        vocals.write_text(self.stems)
        rest.write_text("music")
        return separation.Stems(vocals, rest)


@pytest.fixture
def offline(monkeypatch):
    """analyze() without ffmpeg, scene detection or librosa: a 60 s source of music."""

    def read_audio(path, sample_rate=16000):
        if Path(path).name.startswith("a_(") and Path(path).read_text() == "empty":
            return np.full(sample_rate * 60, 1e-5, dtype=np.float32)
        return MUSIC if sample_rate == RATE else np.zeros(sample_rate * 60, dtype=np.float32)

    monkeypatch.setattr(
        ffmpeg, "probe", lambda p: ffmpeg.MediaInfo(60.0, 1920, 1080, 30, True, True)
    )
    monkeypatch.setattr(ffmpeg, "extract_audio", lambda src, dest, **kw: dest.write_bytes(b"x"))
    monkeypatch.setattr(ffmpeg, "read_audio", read_audio)
    monkeypatch.setattr(scenes, "detect_cuts", lambda video: [])
    monkeypatch.setattr(watermarks, "detect", lambda *args: [])
    monkeypatch.setattr(beats, "detect", lambda path: Beats())


def test_a_source_without_a_voice_keeps_the_full_mix_words(offline, tmp_path):
    router = Router(stems="empty")
    found = analyze.analyze(Path("source.mp4"), tmp_path, router, "original")
    assert found.transcript.words == [] and router.separated == 1
    # The rejected stems are remembered, so the next run doesn't separate again.
    analyze.analyze(Path("source.mp4"), tmp_path, router, "original")
    assert router.separated == 1


def test_a_vocal_preset_still_fails_when_there_is_no_voice(offline, tmp_path):
    with pytest.raises(separation.SeparationFailed):
        analyze.analyze(Path("source.mp4"), tmp_path, Router(stems="empty"), "vocals")
