import pytest

from agentic_editor.analyze import Analysis
from agentic_editor.transcript import Transcript, Word


def words_from(spec: str) -> list[Word]:
    """Build words from 'text@start-end' tokens, e.g. 'hello@0.0-0.4 world@0.5-0.9'."""
    words = []
    for token in spec.split():
        text, _, span = token.partition("@")
        start, _, end = span.partition("-")
        words.append(Word(text=text, start=float(start), end=float(end)))
    return words


@pytest.fixture(autouse=True)
def shazam_hears_nothing(request, monkeypatch):
    """Shazam is a network service: in a test it recognises nothing, unless the test says
    what it hears (presets fall back to it by default, see Preset.auto_name). Its own tests
    fake the network beneath it instead."""
    from agentic_editor import shazam

    if request.module.__name__.endswith("test_shazam"):
        return
    monkeypatch.setattr(shazam, "song", lambda *args, **kwargs: None)


@pytest.fixture
def make_analysis():
    def build(words: list[Word], duration: float, energy: list[float] | None = None, **extra):
        hop = 0.5
        return Analysis(
            duration=duration,
            width=1920,
            height=1080,
            fps=30.0,
            audio_source=extra.pop("audio_source", "original"),
            audio_path="audio.wav",
            transcript=Transcript(language="en", words=words),
            scenes=[],
            energy=energy if energy is not None else [-30.0] * int(duration / hop),
            **extra,
        )

    return build
