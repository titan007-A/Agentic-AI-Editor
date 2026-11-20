import json
import subprocess
from pathlib import Path

import pytest

from agentic_editor import download, pipeline
from agentic_editor.presets import Preset
from agentic_editor.settings import get_settings


def make(path: Path, video: list[str], audio: list[str]) -> Path:
    subprocess.run(
        [get_settings().ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=160x120:rate=15:duration=1.5", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1.5", *video, *audio, "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def streams(path: Path) -> dict[str, str]:
    out = subprocess.run(
        [get_settings().ffprobe_bin, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return {s["codec_type"]: s for s in json.loads(out.stdout)["streams"]}


def only_download(name="dl") -> Preset:
    return Preset(name=name, label=name, min_duration=1, max_duration=60, download_only=True)


@pytest.mark.parametrize(
    ("video", "audio", "copied"),
    [
        (["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:a", "aac"], True),
        (["-c:v", "libvpx-vp9", "-b:v", "200k"], ["-c:a", "libopus"], False),
    ],
)
def test_a_download_only_preset_gives_the_whole_video_unedited_as_an_mp4(
    monkeypatch, tmp_path, video, audio, copied
):
    def never(*args, **kwargs):
        raise AssertionError("a download-only job edits nothing")

    monkeypatch.setattr(pipeline, "edit_presets", never)
    source = make(tmp_path / ("in.mp4" if copied else "in.mkv"), video, audio)
    settings = type("S", (), {"media_dir": tmp_path / "media", "models_dir": tmp_path})
    got = download.Source(path=source, title="A clip", url="https://kick.com/yera/clips/clip_1")
    result = pipeline.edit(got, tmp_path / "cache" / "abc", [only_download()], settings,
                           None, None, lambda *args: None, "run1")  # fmt: skip
    (out,) = result.outputs
    assert out.preset == "dl" and out.path.suffix == ".mp4" and out.thumbnail.exists()
    kept = streams(out.path)
    assert (kept["video"]["codec_name"], kept["audio"]["codec_name"]) == ("h264", "aac")
    assert (kept["video"]["width"], kept["video"]["height"]) == (160, 120)  # its own size
    assert out.edl.clips[0].start == 0 and out.edl.clips[0].end == pytest.approx(1.5, abs=0.15)
    assert out.edl.decisions[0].startswith("only downloaded: the original")
    assert ("copied" in out.edl.decisions[0]) is copied
    meta = json.loads((out.path.parent / "source.json").read_text())
    assert meta["url"] == "https://kick.com/yera/clips/clip_1"
