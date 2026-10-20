"""Whisper backends. Each function runs in a child process so the model's memory is
released as soon as it returns."""

import logging
import sys
import time
from pathlib import Path

from agentic_editor import ffmpeg
from agentic_editor.backends import preload_cuda_libraries
from agentic_editor.transcript import Transcript, Word

log = logging.getLogger(__name__)


def with_faster_whisper(
    audio: Path, model: str, device: str, download_root: Path, language: str | None
) -> Transcript:
    from faster_whisper import WhisperModel

    if device == "cuda" and sys.platform == "linux":
        preload_cuda_libraries()
    compute_type = "float16" if device == "cuda" else "int8"
    started = time.monotonic()
    whisper = WhisperModel(
        model, device=device, compute_type=compute_type, download_root=str(download_root)
    )
    loaded = time.monotonic()
    # Decoding with our ffmpeg instead of faster-whisper's PyAV path keeps one decoder for
    # the whole pipeline and avoids PyAV API changes.
    segments, info = whisper.transcribe(
        ffmpeg.read_audio(audio, 16000),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
    )
    words = [
        Word(text=w.word.strip(), start=w.start, end=w.end, probability=w.probability)
        for segment in segments
        for w in segment.words or []
        if w.word.strip()
    ]
    elapsed = time.monotonic() - loaded
    log.info(
        "faster-whisper %s on %s/%s: %.0fs of audio in %.1fs (%.1fx realtime, load %.1fs)",
        model, device, compute_type, info.duration, elapsed,
        info.duration / max(elapsed, 1e-6), loaded - started,
    )  # fmt: skip
    return Transcript(language=info.language, words=words)


def with_mlx(audio: Path, model: str, language: str | None) -> Transcript:
    import mlx_whisper

    started = time.monotonic()
    result = mlx_whisper.transcribe(
        str(audio), path_or_hf_repo=model, word_timestamps=True, language=language
    )
    words = [
        Word(text=w["word"].strip(), start=w["start"], end=w["end"],
             probability=w.get("probability", 1.0))
        for segment in result["segments"]
        for w in segment.get("words", [])
        if w["word"].strip()
    ]  # fmt: skip
    log.info("mlx-whisper %s: transcribed in %.1fs", model, time.monotonic() - started)
    return Transcript(language=result.get("language"), words=words)
