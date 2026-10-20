"""Picks transcription and encoding backends for the machine we're running on.

This is the only module that knows about CUDA, Apple Silicon or specific encoders;
everything else asks it which backend to use.
"""

import importlib.util
import logging
import platform
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from agentic_editor.settings import Settings, get_settings

log = logging.getLogger(__name__)

Transcriber = Literal["faster-whisper", "mlx", "cpu"]
Encoder = Literal["nvenc", "videotoolbox", "x264"]

_ENCODER_NAMES = {
    ("nvenc", "h264"): "h264_nvenc",
    ("nvenc", "hevc"): "hevc_nvenc",
    ("videotoolbox", "h264"): "h264_videotoolbox",
    ("videotoolbox", "hevc"): "hevc_videotoolbox",
    ("x264", "h264"): "libx264",
    ("x264", "hevc"): "libx265",
}


@dataclass(frozen=True)
class Backends:
    system: str
    transcriber: Transcriber
    encoder: Encoder
    video_codec: str

    @property
    def encoder_name(self) -> str:
        return _ENCODER_NAMES[(self.encoder, self.video_codec)]

    def describe(self) -> str:
        return (
            f"system={self.system} transcription={self.transcriber} "
            f"encoder={self.encoder_name}"
        )


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def cuda_device_count() -> int:
    if importlib.util.find_spec("ctranslate2") is None:
        return 0
    import ctranslate2

    try:
        return ctranslate2.get_cuda_device_count()
    except RuntimeError:
        return 0


def encoder_works(encoder_name: str, ffmpeg_bin: str) -> bool:
    """Encode one frame with the given encoder; listing it in -encoders isn't enough."""
    # NVENC rejects frames below its minimum size, so the test frame can't be tiny.
    cmd = [
        ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
        "-frames:v", "1", "-c:v", encoder_name, "-f", "null", "-",
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def pick_transcriber(requested: str, apple_silicon: bool, cuda_devices: int, has_mlx: bool):
    if requested != "auto":
        return requested
    if apple_silicon and has_mlx:
        return "mlx"
    if cuda_devices > 0:
        return "faster-whisper"
    return "cpu"


def detect(settings: Settings) -> Backends:
    apple = is_apple_silicon()
    cuda = cuda_device_count()
    has_mlx = importlib.util.find_spec("mlx_whisper") is not None
    transcriber = pick_transcriber(settings.transcribe_backend, apple, cuda, has_mlx)
    if transcriber == "faster-whisper" and cuda == 0:
        raise RuntimeError(
            "TRANSCRIBE_BACKEND=faster-whisper needs a CUDA device, but CTranslate2 sees none"
        )
    if transcriber == "mlx" and not has_mlx:
        raise RuntimeError("TRANSCRIBE_BACKEND=mlx but mlx-whisper is not installed")
    if transcriber == "cpu":
        log.warning("no GPU transcription backend available, whisper will run on the CPU")

    codec = settings.video_codec
    if settings.encoder == "auto":
        candidate = "videotoolbox" if sys.platform == "darwin" else "nvenc"
        if encoder_works(_ENCODER_NAMES[(candidate, codec)], settings.ffmpeg_bin):
            encoder = candidate
        else:
            log.warning("%s failed a test encode, falling back to libx264", candidate)
            encoder = "x264"
    else:
        encoder = settings.encoder
        if not encoder_works(_ENCODER_NAMES[(encoder, codec)], settings.ffmpeg_bin):
            raise RuntimeError(f"ENCODER={encoder} was forced but a test encode with it failed")

    if apple:
        system = "macos-apple-silicon"
    elif cuda:
        system = f"{sys.platform}-cuda"
    else:
        system = f"{sys.platform}-cpu"
    backends = Backends(system, transcriber, encoder, codec)
    log.info("backends: %s", backends.describe())
    return backends


@lru_cache
def get_backends() -> Backends:
    return detect(get_settings())


def encoder_args(backends: Backends, bitrate_mbps: float = 12.0) -> list[str]:
    """Video encoder arguments tuned for short-form uploads."""
    rate, peak, buf = f"{bitrate_mbps:g}M", f"{bitrate_mbps * 1.5:g}M", f"{bitrate_mbps * 2:g}M"
    name = backends.encoder_name
    if backends.encoder == "nvenc":
        args = ["-c:v", name, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19",
                "-b:v", rate, "-maxrate", peak, "-bufsize", buf]  # fmt: skip
    elif backends.encoder == "videotoolbox":
        args = ["-c:v", name, "-b:v", rate, "-maxrate", peak, "-bufsize", buf, "-allow_sw", "0"]
    else:
        args = ["-c:v", name, "-preset", "medium", "-crf", "18", "-maxrate", peak, "-bufsize", buf]
    if backends.video_codec == "h264":
        args += ["-profile:v", "high"]
    else:
        args += ["-tag:v", "hvc1"]
    return [*args, "-pix_fmt", "yuv420p"]
