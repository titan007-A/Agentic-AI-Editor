import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from agentic_editor import pipeline
from agentic_editor.analysis import ocr, subjects, watermarks
from agentic_editor.analysis.watermarks import Watermark


def _vision_finds(monkeypatch, items):
    monkeypatch.setattr(subjects, "find", lambda vision, video, times, *a: [items for _ in times])


def _frames(monkeypatch, tmp_path):
    def grab(video, at, dest, ffmpeg_bin):
        Image.new("RGB", (640, 360)).save(dest)
        return dest

    monkeypatch.setattr(subjects, "grab", grab)


def _settings(tmp_path):
    return SimpleNamespace(ffmpeg_bin="ffmpeg", models_dir=tmp_path / "models")


HEAD = subjects.Subject(kind="head", label="singer", x0=800, y0=100, x1=1000, y1=400, keep=True)
LOOSE = subjects.Subject(kind="text", label="lyric", x0=300, y0=500, x1=1500, y1=560, keep=True)


def test_ocr_text_replaces_the_vision_models_and_is_scaled_to_the_source(
    monkeypatch, tmp_path, make_analysis
):
    _vision_finds(monkeypatch, [HEAD, LOOSE])
    _frames(monkeypatch, tmp_path)
    monkeypatch.setattr(ocr, "available", lambda: True)
    line = ocr.Line(x0=100, y0=300, x1=400, y1=320, text="SABES BIEN", score=0.9)
    monkeypatch.setattr(ocr, "read", lambda image, root, whole_frame=False: [line])
    analysis = make_analysis([], 10).model_copy(update={"width": 1920, "height": 1080})
    vision = SimpleNamespace(model="m:4b")
    ((_, items),) = pipeline.subject_samples(
        vision, tmp_path / "v.mp4", [2.0], analysis, _settings(tmp_path), tmp_path
    )
    assert [s.kind for s in items] == ["head", "text"]
    assert (items[1].x0, items[1].y0, items[1].x1, items[1].y1) == (300, 900, 1200, 960)
    assert items[1].label == "SABES BIEN" and items[1].keep
    assert "2.000" in json.loads((tmp_path / f"texts-ocr-v{pipeline.OCR_VERSION}.json").read_text())


def test_without_ocr_the_vision_models_text_stays(monkeypatch, tmp_path, make_analysis):
    _vision_finds(monkeypatch, [HEAD, LOOSE])
    _frames(monkeypatch, tmp_path)
    monkeypatch.setattr(ocr, "available", lambda: True)

    def broken(image, root, whole_frame=False):
        raise RuntimeError("models can't be fetched")

    monkeypatch.setattr(ocr, "read", broken)
    analysis = make_analysis([], 10)
    vision = SimpleNamespace(model="m:4b")
    ((_, items),) = pipeline.subject_samples(
        vision, tmp_path / "v.mp4", [2.0], analysis, _settings(tmp_path), tmp_path
    )
    assert items == [HEAD, LOOSE]


def test_ocr_finds_text_even_without_the_vision_model(monkeypatch, tmp_path, make_analysis):
    _frames(monkeypatch, tmp_path)
    monkeypatch.setattr(ocr, "available", lambda: True)
    line = ocr.Line(x0=10, y0=10, x1=200, y1=30, text="@canciones", score=0.95)
    monkeypatch.setattr(ocr, "read", lambda image, root, whole_frame=False: [line])
    analysis = make_analysis([], 10).model_copy(update={"width": 640, "height": 360})
    ((_, items),) = pipeline.subject_samples(
        None, tmp_path / "v.mp4", [1.0], analysis, _settings(tmp_path), tmp_path
    )
    assert [(s.kind, s.label) for s in items] == [("text", "@canciones")]


class _Vision:
    def __init__(self, overlay):
        self.overlay, self.asked = overlay, []

    def chat_json(self, messages, schema, images=None, temperature=0.0):
        prompt = messages[0]["content"]
        self.asked.append("read" if prompt == watermarks.READ_PROMPT else "confirm")
        if prompt == watermarks.READ_PROMPT:
            return {"text": "www.cnn.com"}  # the vision model reading into an empty strip
        return {"overlay": self.overlay}


@pytest.fixture
def evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        watermarks, "_evidence", lambda *a: [tmp_path / "m.jpg", tmp_path / "c.jpg"]
    )


MARK = Watermark(x=10, y=10, width=100, height=30, spans=[(0, 5)], windows=4)


def test_a_candidate_ocr_reads_nothing_on_is_never_blurred(evidence, tmp_path):
    vision = _Vision(overlay=True)
    (mark,) = watermarks.confirm(
        vision, tmp_path / "v.mp4", [MARK], "ffmpeg", tmp_path, lambda p: ""
    )
    assert mark.confirmed is False and vision.asked == []


def test_text_ocr_reads_still_needs_the_vision_model_to_call_it_an_overlay(evidence, tmp_path):
    vision = _Vision(overlay=False)  # a sign on the stage
    read = lambda p: "@canciones_antiguas"  # noqa: E731
    (mark,) = watermarks.confirm(vision, tmp_path / "v.mp4", [MARK], "ffmpeg", tmp_path, read)
    assert mark.confirmed is False and vision.asked == ["confirm"]
    vision = _Vision(overlay=True)
    (mark,) = watermarks.confirm(vision, tmp_path / "v.mp4", [MARK], "ffmpeg", tmp_path, read)
    assert mark.confirmed is True


def test_when_ocr_fails_the_vision_model_reads(evidence, tmp_path):
    def broken(p):
        raise RuntimeError("no OCR")

    vision = _Vision(overlay=True)
    (mark,) = watermarks.confirm(vision, tmp_path / "v.mp4", [MARK], "ffmpeg", tmp_path, broken)
    assert vision.asked == ["read", "confirm"] and mark.confirmed is True


def test_ocr_reads_rendered_text_when_its_models_are_here(tmp_path):
    from agentic_editor.settings import ROOT

    if not ocr.available():
        pytest.skip("OCR isn't installed here (it comes with the ml extras)")
    image = Image.new("RGB", (640, 200), "black")
    font = ImageFont.truetype(str(ROOT / "fonts" / "OpenSans-Bold.ttf"), 40)
    ImageDraw.Draw(image).text((40, 70), "HELLO WORLD", fill="white", font=font)
    image.save(tmp_path / "f.png")
    (line,) = ocr.read(tmp_path / "f.png", ROOT / "models")
    assert line.text.replace(" ", "") == "HELLOWORLD"
    assert 30 <= line.x0 <= 50 and 60 <= line.y0 <= 90


def test_the_picture_band_hears_of_edge_text_from_ocr_all_through_the_clip(
    monkeypatch, tmp_path, make_analysis
):
    """Lyrics come in after the shot's one look: OCR every couple of seconds catches them."""
    from agentic_editor import framing
    from agentic_editor.edl import EDL, Clip
    from agentic_editor.presets import Preset, TextBlock
    from agentic_editor.render import compose

    preset = Preset(
        name="p", label="p", min_duration=1, max_duration=60,
        text_blocks=[TextBlock(text="top", size=60, anchor="top", offset=200)],
    )  # fmt: skip
    analysis = make_analysis([], 9).model_copy(update={"width": 1080, "height": 1920})
    looked = []

    def text_lines(video, times, analysis, settings, work_dir):
        looked.extend(times)
        return {t: ([LYRIC] if t > 3 else []) for t in times}

    LYRIC = subjects.Subject(kind="text", label="SI CHIAMAN GUAI", x0=200, y0=1450,
                             x1=880, y1=1490, keep=True)  # fmt: skip
    seen = {}
    monkeypatch.setattr(pipeline, "text_lines", text_lines)
    monkeypatch.setattr(pipeline, "subject_samples", lambda *a: [])
    monkeypatch.setattr(compose, "picture_band", lambda preset, face: (300, 1700))
    monkeypatch.setattr(compose, "band_rows", lambda *a: 1400)
    monkeypatch.setattr(compose, "moving_part", lambda *a: None)
    monkeypatch.setattr(compose, "text_face", lambda *a: None)
    monkeypatch.setattr(
        framing, "band_shots", lambda *a: (seen.setdefault("marks", a[7]) and []) or []
    )
    edl = EDL(clips=[Clip(start=0, end=9)], crop_mode="none")
    detections = {(0, 9): []}
    pipeline.band_framing(edl, preset, analysis, tmp_path / "v.mp4", _settings(tmp_path),
                          tmp_path, detections, None)  # fmt: skip
    assert len(looked) >= 4  # every couple of seconds, not once
    assert (1450, 1490) in seen["marks"]


def test_what_cant_be_a_line_of_text_is_left_out(monkeypatch, tmp_path):
    """Legs on a beach read as "LL", 15% of the frame tall; a guitar's fret read as "C"."""
    found = SimpleNamespace(
        boxes=[
            [[100, 519], [140, 519], [140, 710], [100, 710]],
            [[10, 10], [300, 10], [300, 40], [10, 40]],
            [[10, 50], [60, 50], [60, 80], [10, 80]],
            [[10, 90], [60, 90], [60, 120], [10, 120]],
        ],
        txts=["LL", "@secretosmilitares", "OK", "C"],
        scores=[0.93, 0.97, 0.7, 0.99],
    )
    monkeypatch.setattr(ocr, "_engine", lambda: lambda pixels, **kw: found)
    lines = ocr.read(Image.new("RGB", (720, 1280)), tmp_path, whole_frame=True)
    assert [line.text for line in lines] == ["@secretosmilitares"]
    # A close-up of one line is all line: tall is fine there.
    lines = ocr.read(Image.new("RGB", (720, 1280)), tmp_path)
    assert [line.text for line in lines] == ["LL", "@secretosmilitares"]


def test_a_frame_without_text_reads_nothing_and_logs_nothing(tmp_path, capfd, caplog):
    if not ocr.available():
        pytest.skip("OCR isn't installed here (it comes with the ml extras)")
    assert ocr.read(Image.new("RGB", (640, 360)), tmp_path, whole_frame=True) == []
    out, err = capfd.readouterr()
    said = out + err + caplog.text
    assert "detection result is empty" not in said


def test_a_one_liner_alone_holds_the_picture_at_full_width(monkeypatch, tmp_path, make_analysis):
    """No text blocks, only a whole-reel one-liner on top: a tall picture is cut top and
    bottom to the band under it, not shrunk with background at the sides (as the preview
    shows it)."""
    from agentic_editor import framing
    from agentic_editor.edl import EDL, Clip
    from agentic_editor.presets import OneLiner, Preset
    from agentic_editor.render import compose

    preset = Preset(
        name="p", label="p", min_duration=1, max_duration=60,
        one_liner=OneLiner(enabled=True, position="top", offset=412),
    )  # fmt: skip
    analysis = make_analysis([], 9).model_copy(update={"width": 1080, "height": 1920})
    seen = {}
    monkeypatch.setattr(pipeline, "text_lines", lambda *a: None)
    monkeypatch.setattr(pipeline, "subject_samples", lambda *a: [])
    monkeypatch.setattr(compose, "line_rows", lambda preset, face: (412.0, 520.0))
    monkeypatch.setattr(compose, "moving_part", lambda *a: None)
    monkeypatch.setattr(compose, "text_face", lambda *a: None)
    monkeypatch.setattr(framing, "band_shots", lambda *a: seen.setdefault("rows", a[5]) and [])
    edl = EDL(clips=[Clip(start=0, end=9)], crop_mode="none")
    pipeline.band_framing(edl, preset, analysis, tmp_path / "v.mp4", _settings(tmp_path),
                          tmp_path, {(0, 9): []}, None)  # fmt: skip
    top, bottom = compose.picture_band(preset, None)
    assert seen["rows"] == compose.band_rows(preset, (top, bottom), 1080) < 1920


def test_the_text_on_the_video_is_read_top_to_bottom_once_per_text(monkeypatch, tmp_path):
    def line(text, y):
        return subjects.Subject(kind="text", label=text, x0=0, y0=y, x1=100, y1=y + 20, keep=True)

    found = {
        1.0: [line("really that windy?", 540), line("Is Iceland", 500)],
        2.0: [line("Is Iceland", 500), line("really that windy?", 540)],
        3.0: [],
    }
    monkeypatch.setattr(pipeline, "text_lines", lambda video, times, *a: found)
    texts = pipeline.shown_text(tmp_path / "v.mp4", [1.0, 2.0, 3.0], None, None, tmp_path)
    assert texts == ["Is Iceland really that windy?"]
    monkeypatch.setattr(pipeline, "text_lines", lambda *a: None)  # no OCR here
    assert pipeline.shown_text(tmp_path / "v.mp4", [1.0], None, None, tmp_path) == []
