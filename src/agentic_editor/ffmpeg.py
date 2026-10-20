"""Thin wrappers around the ffmpeg and ffprobe binaries."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agentic_editor.settings import get_settings

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    has_video: bool


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [get_settings().ffmpeg_bin, "-hide_banner", "-nostdin", *args]
    log.debug("ffmpeg %s", " ".join(args))
    result = subprocess.run(cmd, capture_output=capture, text=True, errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or "")[-2000:]
        raise FFmpegError(f"ffmpeg exited with {result.returncode}:\n{tail}")
    return result


def probe(path: Path) -> MediaInfo:
    cmd = [
        get_settings().ffprobe_bin,
        "-v", "error",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:"
        "stream_side_data=rotation:format=duration",
        "-of", "json",
        str(path),
    ]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path}: {result.stderr.strip()}")
    return parse_probe(json.loads(result.stdout))


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if video is None:
        return MediaInfo(duration, 0, 0, 0.0, has_audio, has_video=False)

    width, height = int(video["width"]), int(video["height"])
    rotation = next(
        (int(sd["rotation"]) for sd in video.get("side_data_list", []) if "rotation" in sd), 0
    )
    # Phone footage stores portrait video as landscape frames plus a rotation flag,
    # and ffmpeg applies that rotation automatically when decoding.
    if abs(rotation) % 180 == 90:
        width, height = height, width
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    return MediaInfo(duration, width, height, fps, has_audio, has_video=True)


def _parse_rate(rate: str | None) -> float:
    if not rate or rate == "0/0":
        return 0.0
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def read_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode an audio track to mono float32 samples."""
    cmd = [
        get_settings().ffmpeg_bin, "-nostdin", "-v", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise FFmpegError(f"could not decode audio from {path}: {result.stderr.decode()[-500:]}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def extract_audio(src: Path, dest: Path, sample_rate: int = 44100) -> Path:
    run(["-y", "-i", str(src), "-vn", "-ac", "2", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", str(dest)])  # fmt: skip
    return dest


def escape_filter_path(path: Path) -> str:
    """Escape a path for use as an option value inside a filtergraph."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
