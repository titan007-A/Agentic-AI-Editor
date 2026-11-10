"""framing.only_with_text: a video with text of its own is smart-cropped inside the rows its
text leaves, one without is shown whole (pipeline.crop_or_whole)."""

from pathlib import Path

import pytest

from agentic_editor import pipeline
from agentic_editor.analysis.subjects import Subject
from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Framing, Preset

SIZE = (1080, 1920)


def line(text: str, x0: int, y0: int, x1: int, y1: int) -> Subject:
    return Subject(kind="text", label=text, x0=x0, y0=y0, x1=x1, y1=y1, keep=True)


CAPTION = line("Soldiers train with heavy guns", 60, 1500, 1020, 1560)
TITLE = line("Follow for part two", 120, 200, 960, 260)


def test_text_counts_only_when_it_is_words_wide_and_stays():
    times = [1.0, 3.0, 5.0]
    every = {t: [CAPTION] for t in times}
    assert pipeline.own_text(times, every, SIZE, []) == [(1481, 1579)]
    once = {1.0: [CAPTION], 3.0: [], 5.0: []}
    assert pipeline.own_text(times, once, SIZE, []) == []  # a sign passing by
    handle = line("@nlt_31photography", 800, 1700, 1050, 1730)
    assert pipeline.own_text(times, {t: [handle] for t in times}, SIZE, []) == []
    narrow = line("STOP NOW", 500, 900, 600, 940)
    assert pipeline.own_text(times, {t: [narrow] for t in times}, SIZE, []) == []
    mark = Watermark(x=40, y=1480, width=1000, height=100, spans=[(0, 6)], windows=3)
    assert pipeline.own_text(times, every, SIZE, [mark]) == []  # blurred, not cropped
    assert pipeline.own_text([2.0], {2.0: [CAPTION]}, SIZE, []) == [(1481, 1579)]
    both = {t: [TITLE, CAPTION] for t in times}
    assert pipeline.own_text(times, both, SIZE, []) == [(181, 279), (1481, 1579)]


def test_the_crop_keeps_the_tallest_rows_without_text():
    whole = (0, 0, *SIZE)
    assert pipeline.clear_band(whole, [(1481, 1579)]) == (0, 0, 1080, 1480)
    assert pipeline.clear_band(whole, [(181, 279), (1481, 1579)]) == (0, 280, 1080, 1200)
    assert pipeline.clear_band(whole, [(100, 1700)]) is None  # too little left
    assert pipeline.clear_band(whole, [(900, 960)]) is None  # lyrics across a face
    landscape = (0, 0, 1416, 1080)  # a real one: sung words at rows 509-575
    assert pipeline.clear_band(landscape, [(509, 575)]) is None
    assert pipeline.clear_band(landscape, [(960, 1040)]) == (0, 0, 1416, 960)
    band = (0, 470, 1080, 608)  # a picture between padding: text on the padding is outside
    assert pipeline.clear_band(band, [(181, 279), (1481, 1579)]) == band


def preset(**framing) -> Preset:
    return Preset(name="p", label="p", min_duration=5, max_duration=30,
                  framing=Framing(mode="smart", **framing))  # fmt: skip


@pytest.fixture
def reel(make_analysis, monkeypatch):
    analysis = make_analysis([], 10).model_copy(update={"width": 1080, "height": 1920})
    monkeypatch.setattr(pipeline.compose, "picture_area", lambda *a: (0, 0, *SIZE))
    edl = EDL(clips=[Clip(start=0, end=6)], crop_mode="smart")

    def decide(found, chosen: Preset | None = None, vision=None):
        chosen = chosen or preset(only_with_text=True)
        monkeypatch.setattr(pipeline, "text_lines", lambda video, times, *a: found(times))
        return pipeline.crop_or_whole(edl, chosen, analysis, Path("v.mp4"), None, Path("w"),
                                      vision)  # fmt: skip

    return decide


def test_a_video_without_text_goes_whole(reel):
    out = reel(lambda times: {t: [] for t in times})
    assert out.crop_mode == "none" and out.picture is None
    assert out.decisions[-1] == "no text of its own on the video: shown whole"


def test_a_video_with_text_is_cropped_inside_the_rows_without_it(reel):
    out = reel(lambda times: {t: [CAPTION] for t in times})
    assert out.crop_mode == "smart" and out.picture == (0, 0, 1080, 1480)
    assert "cropped away" in out.decisions[-1]


def test_text_everywhere_is_framed_smartly_on_the_whole_picture(reel):
    big = line("words all over the picture", 0, 100, 1080, 1800)
    out = reel(lambda times: {t: [big] for t in times})
    assert out.crop_mode == "smart" and out.picture == (0, 0, *SIZE)
    assert "crosses the middle" in out.decisions[-1]


def test_presets_without_the_switch_are_left_as_they_were(reel):
    def never(times):
        raise AssertionError("looked for text")

    assert reel(never, preset()).crop_mode == "smart"


def test_without_ocr_or_a_vision_model_it_stays_smart(reel):
    out = reel(lambda times: None)
    assert out.crop_mode == "smart" and "couldn't look" in out.decisions[-1]


def test_smart_framing_leaves_text_out_of_what_must_stay():
    assert pipeline.drops_text(preset(only_with_text=True))
    assert pipeline.drops_text(preset(keep_action=True))
    assert not pipeline.drops_text(preset())
