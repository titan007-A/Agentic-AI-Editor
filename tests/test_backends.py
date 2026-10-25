import subprocess
import types

import pytest

from agentic_editor import backends
from agentic_editor.backends import Backends, encoder_args, has_filter, pick_transcriber

# The start of `ffmpeg -filters` from Homebrew's plain ffmpeg, which has no libass.
FILTERS_WITHOUT_LIBASS = """Filters:
  T.. = Timeline support
 ... abench            A->A       Benchmark part of a filtergraph.
 TS. lut3d             V->V       Adjust colors using a 3D LUT.
 ... subtitles_extra   V->V       Not a real filter, only shares a prefix.
"""


def test_apple_silicon_prefers_mlx():
    assert pick_transcriber("auto", apple_silicon=True, cuda_devices=0, has_mlx=True) == "mlx"


def test_cuda_machine_uses_faster_whisper():
    assert pick_transcriber("auto", False, cuda_devices=1, has_mlx=False) == "faster-whisper"


def test_falls_back_to_cpu_without_accelerator():
    assert pick_transcriber("auto", False, cuda_devices=0, has_mlx=False) == "cpu"
    assert pick_transcriber("auto", True, cuda_devices=0, has_mlx=False) == "cpu"


def test_forced_backend_wins():
    assert pick_transcriber("cpu", False, cuda_devices=1, has_mlx=False) == "cpu"


def test_encoder_args_per_backend():
    nvenc = encoder_args(Backends("cuda", "faster-whisper", "nvenc", "h264"))
    assert nvenc[:2] == ["-c:v", "h264_nvenc"]
    vt = encoder_args(Backends("mps", "mlx", "videotoolbox", "hevc"))
    assert vt[:2] == ["-c:v", "hevc_videotoolbox"] and "hvc1" in vt
    x264 = encoder_args(Backends("cpu", "cpu", "x264", "h264"), bitrate_mbps=8)
    assert x264[:2] == ["-c:v", "libx264"] and "12M" in x264
    assert x264[x264.index("-crf") + 1] == "16"
    assert all(args[-2:] == ["-pix_fmt", "yuv420p"] for args in (nvenc, vt, x264))


def fake_ffmpeg(monkeypatch, stdout):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=stdout, returncode=0)
    )


def test_a_filter_is_found_by_its_exact_name(monkeypatch):
    fake_ffmpeg(monkeypatch, FILTERS_WITHOUT_LIBASS)
    assert has_filter("lut3d", "ffmpeg")
    assert not has_filter("subtitles", "ffmpeg")


def test_an_ffmpeg_without_libass_is_refused_at_startup(monkeypatch):
    fake_ffmpeg(monkeypatch, FILTERS_WITHOUT_LIBASS)
    monkeypatch.setattr(backends, "cuda_device_count", lambda: 0)
    settings = types.SimpleNamespace(transcribe_backend="cpu", ffmpeg_bin="/usr/bin/ffmpeg")
    with pytest.raises(RuntimeError, match="no subtitles filter"):
        backends.detect(settings)
