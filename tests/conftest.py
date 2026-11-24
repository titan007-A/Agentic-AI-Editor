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


@pytest.fixture(autouse=True)
def no_map_download(monkeypatch):
    """The world map is 33 MB from other people's servers: never fetched by a test (one
    that wants it gives its own `download`)."""
    from agentic_editor.animate import maps

    def offline(url, dest):
        raise ConnectionError(f"no map download in tests ({url})")

    monkeypatch.setattr(maps, "DOWNLOAD", offline)


@pytest.fixture(autouse=True)
def no_web_search(monkeypatch):
    """Web search is third parties' servers: in a test every engine fails (a test that wants
    results gives its own engines or `find`), and nothing is kept in the cache folder."""
    from agentic_editor import websearch

    def offline(query, count, page=1):
        raise ConnectionError("no web search in tests")

    websearch.reset()
    monkeypatch.setattr(websearch, "_engines", lambda: [("offline", offline)])
    monkeypatch.setattr(websearch, "_cache_dir", lambda: None)
    yield
    websearch.reset()


@pytest.fixture(autouse=True)
def no_ocr(request, monkeypatch):
    """OCR runs models on real frames: in a test it isn't there, so the vision model's text
    boxes are used, unless the test turns it on itself."""
    from agentic_editor.analysis import ocr

    if request.module.__name__.endswith("test_ocr"):
        return
    monkeypatch.setattr(ocr, "available", lambda: False)


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


@pytest.fixture
def all_instagram(monkeypatch):
    """Instagram's share of a research lifted (research.INSTAGRAM_SHOWN, _LOOKED_UP), for
    tests of other things whose fake engines find only Instagram reels."""
    from agentic_editor import research

    monkeypatch.setattr(research, "INSTAGRAM_SHOWN", 1.0)
    monkeypatch.setattr(research, "INSTAGRAM_LOOKED_UP", 1.0)
