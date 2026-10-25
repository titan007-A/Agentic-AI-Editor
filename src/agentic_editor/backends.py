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
from pathlib import Path
from typing import Literal

from agentic_editor.settings import Settings, get_settings

log = logging.getLogger(__name__)

Accelerator = Literal["cuda", "mps", "cpu"]
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
    accelerator: Accelerator
    transcriber: Transcriber
    encoder: Encoder
    video_codec: str

    @property
    def encoder_name(self) -> str:
        return _ENCODER_NAMES[(self.encoder, self.video_codec)]

    def describe(self) -> str:
        return (
            f"platform={sys.platform} accelerator={self.accelerator} "
            f"transcription={self.transcriber} encoder={self.encoder_name}"
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


def preload_cuda_libraries() -> None:
    """Load cuBLAS and cuDNN from the nvidia-* wheels so CTranslate2 can find them.

    CTranslate2 opens these by name. Inside torch they are already loaded, but a fresh
    process that only runs whisper has nothing on the loader path."""
    import ctypes

    for package, names in (
        ("nvidia.cublas", ("libcublasLt.so.12", "libcublas.so.12")),
        ("nvidia.cudnn", ("libcudnn.so.9",)),
    ):
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue
        lib_dir = Path(next(iter(spec.submodule_search_locations))) / "lib"
        for name in names:
            if (lib_dir / name).exists():
                ctypes.CDLL(str(lib_dir / name), mode=ctypes.RTLD_GLOBAL)


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


def has_filter(name: str, ffmpeg_bin: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == [name] for line in result.stdout.splitlines())


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

    # Found out at startup rather than in the middle of the first job with captions.
    if not has_filter("subtitles", settings.ffmpeg_bin):
        hint = " (on macOS: brew install ffmpeg-full)" if sys.platform == "darwin" else ""
        raise RuntimeError(
            f"{settings.ffmpeg_bin} has no subtitles filter, which captions and titles need; "
            f"use an ffmpeg built with libass{hint}"
        )

    codec = settings.video_codec
    if settings.encoder == "auto":
        # libx264 is the default even with a GPU: measured against lossless renders it
        # matched the hardware encoders' VMAF at 30-57% of the file size. The hardware
        # encoders stay available for speed.
        hardware = "videotoolbox" if sys.platform == "darwin" else "nvenc"
        encoder = "x264"
        if not encoder_works(_ENCODER_NAMES[(encoder, codec)], settings.ffmpeg_bin):
            log.warning("libx264 failed a test encode, falling back to %s", hardware)
            encoder = hardware
    else:
        encoder = settings.encoder
        if not encoder_works(_ENCODER_NAMES[(encoder, codec)], settings.ffmpeg_bin):
            raise RuntimeError(f"ENCODER={encoder} was forced but a test encode with it failed")

    accelerator = "cuda" if cuda else "mps" if apple else "cpu"
    backends = Backends(accelerator, transcriber, encoder, codec)
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
        args = [
            "-c:v",
            name,
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "19",
            "-b:v",
            rate,
            "-maxrate",
            peak,
            "-bufsize",
            buf,
        ]
    elif backends.encoder == "videotoolbox":
        args = ["-c:v", name, "-b:v", rate, "-maxrate", peak, "-bufsize", buf, "-allow_sw", "0"]
    elif name == "libx264":
        # CRF 16 with the slower preset held VMAF within 0.2 of the old NVENC output on
        # average (worst 1% of frames stayed above 95) while cutting size 30-57%. The
        # cap keeps busy shots under Instagram's 25 Mbps upload limit.
        args = ["-c:v", name, "-preset", "slower", "-crf", "16"]
        args += ["-maxrate", peak, "-bufsize", f"{bitrate_mbps * 3:g}M"]
    else:
        args = ["-c:v", name, "-preset", "medium", "-crf", "18", "-maxrate", peak, "-bufsize", buf]
    if backends.video_codec == "h264":
        args += ["-profile:v", "high"]
    else:
        args += ["-tag:v", "hvc1"]
    return [*args, "-pix_fmt", "yuv420p"]
