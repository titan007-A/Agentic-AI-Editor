import shutil
import subprocess

import pytest
from PIL import Image, ImageChops

from agentic_editor.analysis import subjects

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture
def sound_runs_on(tmp_path):
    """A second of picture whose sound runs on for another second, like reels whose
    audio track is longer than their video track."""
    video = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:rate=25:duration=1", "-f", "lavfi", "-i", "sine=duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        check=True,
    )  # fmt: skip
    return video


@needs_ffmpeg
def test_a_time_after_the_picture_ends_gets_the_last_frame(sound_runs_on, tmp_path):
    last = subjects.grab(sound_runs_on, 0.96, tmp_path / "last.jpg", "ffmpeg")
    late = subjects.grab(sound_runs_on, 1.5, tmp_path / "late.jpg", "ffmpeg")
    with Image.open(last) as a, Image.open(late) as b:
        assert b.width == subjects.FRAME_WIDTH
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


@needs_ffmpeg
def test_a_time_inside_the_picture_gets_its_own_frame(sound_runs_on, tmp_path):
    early = subjects.grab(sound_runs_on, 0.2, tmp_path / "early.jpg", "ffmpeg")
    last = subjects.grab(sound_runs_on, 0.96, tmp_path / "last.jpg", "ffmpeg")
    with Image.open(early) as a, Image.open(last) as b:
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


@needs_ffmpeg
def test_an_old_frame_is_not_passed_off_as_a_new_one(sound_runs_on, tmp_path):
    dest = tmp_path / "frame.jpg"
    dest.write_bytes(b"from an earlier run")
    with pytest.raises(subprocess.CalledProcessError):
        subjects.grab(sound_runs_on, 9.0, dest, "ffmpeg")
    assert not dest.exists()
