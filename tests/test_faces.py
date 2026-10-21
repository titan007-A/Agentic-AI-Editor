import pytest

from agentic_editor.analysis.faces import Face, choose, crop_path, smooth, write_commands


def face(cx: float, size: float = 0.2) -> Face:
    return Face(cx=cx, cy=0.4, width=size, height=size, score=0.9)


def test_keeps_following_the_same_speaker_when_a_similar_face_appears():
    current = face(0.3)
    assert choose([face(0.31), face(0.7, size=0.22)], current).cx == 0.31


def test_switches_when_another_face_clearly_dominates():
    assert choose([face(0.31), face(0.7, size=0.4)], face(0.3)).cx == 0.7


def test_smoothing_has_no_lag_on_a_constant_signal():
    assert smooth([0.5] * 5, 0.9) == pytest.approx([0.5] * 5)


def test_smoothing_softens_a_jump():
    out = smooth([0.2] * 5 + [0.8] * 5, 0.8)
    assert 0.2 < out[4] < out[5] < 0.8


def test_crop_path_clamps_and_holds_position_without_faces():
    samples = [(0.0, [face(0.95)]), (0.2, []), (0.4, [face(0.95)])]
    path = crop_path(samples, smoothing=0.0, src_width=1920, crop_width=606)
    assert [x for _, x in path] == [1920 - 606] * 3


def test_crop_path_centers_when_no_face_is_ever_seen():
    assert crop_path([(0.0, [])], 0.5, 1920, 606) == [(0.0, 657)]


def test_command_file_format(tmp_path):
    out = write_commands([(0.0, 10), (0.2, 12)], "crop@c0", tmp_path / "c0.cmd")
    assert out.read_text() == "0.000 crop@c0 x 10;\n0.200 crop@c0 x 12;\n"
