from agentic_editor.ffmpeg import MediaInfo
from agentic_editor.presets import Preset
from agentic_editor.review import check_captions, check_faces, check_format, check_loudness
from tests.conftest import words_from

PRESET = Preset(name="t", label="t", min_duration=20, max_duration=60)


def info(**overrides):
    values = {
        "duration": 30.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_audio": True,
        "has_video": True,
    }
    return MediaInfo(**{**values, **overrides})


def test_matching_output_passes():
    assert check_format(info(), PRESET, source_duration=200) == []


def test_wrong_size_and_length_are_reported():
    kinds = [i.kind for i in check_format(info(width=720, duration=75), PRESET, 200)]
    assert kinds == ["format", "duration"]


def test_short_source_lowers_the_minimum():
    assert check_format(info(duration=12), PRESET, source_duration=12.2) == []


def test_loudness_tolerance():
    assert check_loudness(-14.6, PRESET) == []
    assert check_loudness(-17.0, PRESET)[0].kind == "loudness"


ASS = """[Events]
Dialogue: 0,0:00:00.12,0:00:00.50,Caption,,0,0,0,,HI
Dialogue: 0,0:00:00.50,0:00:01.00,Caption,,0,0,0,,THERE
"""


def test_captions_in_sync_pass():
    assert check_captions(ASS, words_from("hi@0.1-0.5 there@0.5-1.0"), duration=5) == []


def test_late_captions_are_reported():
    issues = check_captions(ASS, words_from("hi@0.9-1.2"), duration=0.5)
    assert {i.detail.split(" ")[0] for i in issues} == {"first", "captions"}


class FakeVision:
    def __init__(self, frames):
        self.reply = {"frames": frames}

    def chat_json(self, messages, schema, **kwargs):
        assert kwargs["images"]
        return self.reply


def test_cut_off_face_is_a_framing_issue():
    vision = FakeVision(
        [
            {"face_visible": True, "face_cut_off": False},
            {"face_visible": True, "face_cut_off": True},
        ]
    )
    issues = check_faces(vision, ["a.jpg", "b.jpg"])
    assert issues[0].kind == "framing" and "[1]" in issues[0].detail


def test_well_framed_faces_pass():
    vision = FakeVision([{"face_visible": True, "face_cut_off": False}] * 2)
    assert check_faces(vision, ["a.jpg", "b.jpg"]) == []
