"""Times each heavy stage on this machine, without any of the analysis caches."""

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from agentic_editor import ffmpeg
from agentic_editor.backends import Backends, encoder_args
from agentic_editor.presets import Preset
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings

RENDER_SECONDS = 20.0
LLM_PROMPT = (
    "Describe, in about 150 words, how you would cut a four-minute live performance down to a "
    "45 second vertical clip for social media."
)


@dataclass(frozen=True)
class Result:
    stage: str
    seconds: float
    detail: str


def realtime(media_seconds: float, took: float) -> str:
    return f"{media_seconds / took:.1f}x realtime" if took > 0 else "instant"


def time_transcription(router: ModelRouter, audio: Path, duration: float) -> Result:
    started = time.monotonic()
    transcript = router.transcribe(audio)
    took = time.monotonic() - started
    words = len(transcript.words)
    return Result(
        "transcription",
        took,
        f"{realtime(duration, took)}, {words} words, {router.backends.transcriber}",
    )


def time_separation(
    router: ModelRouter, audio: Path, duration: float, work: Path
) -> tuple[Result, Path]:
    started = time.monotonic()
    stems = router.separate_vocals(audio, work / "stems")
    took = time.monotonic() - started
    result = Result(
        "vocal separation", took, f"{realtime(duration, took)}, {router.settings.separation_model}"
    )
    return result, stems.vocals


def time_llm(base_url: str, model: str) -> Result:
    """Generation speed as Ollama measures it, after a warm-up call loads the model."""
    with httpx.Client(timeout=600) as http:
        body = {"model": model, "prompt": LLM_PROMPT, "stream": False, "think": False}
        http.post(f"{base_url}/api/generate", json={**body, "options": {"num_predict": 8}})
        started = time.monotonic()
        response = http.post(f"{base_url}/api/generate", json=body)
        took = time.monotonic() - started
        response.raise_for_status()
        data = response.json()
        http.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0})
    tokens, nanos = data.get("eval_count", 0), data.get("eval_duration", 0)
    rate = tokens / (nanos / 1e9) if nanos else 0.0
    return Result(f"llm {model}", took, f"{rate:.1f} tokens/s over {tokens} tokens")


def time_render(
    video: Path, info: ffmpeg.MediaInfo, preset: Preset, backends: Backends, work: Path
) -> Result:
    """Scale and pad a stretch of the source to the preset's frame and encode it for real."""
    width, height = preset.size
    seconds = min(RENDER_SECONDS, info.duration)
    frame = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={preset.framing.background}"
    )
    started = time.monotonic()
    ffmpeg.run([
        "-y", "-t", f"{seconds:.3f}", "-i", str(video), "-vf", frame, "-an",
        *encoder_args(backends, preset.bitrate_mbps), "-r", str(preset.fps),
        str(work / "render.mp4"),
    ])  # fmt: skip
    took = time.monotonic() - started
    fps = seconds * preset.fps / took if took else 0.0
    return Result(
        "render",
        took,
        f"{realtime(seconds, took)}, {fps:.0f} fps at {preset.resolution}, {backends.encoder_name}",
    )


def run(
    video: Path, preset: Preset, settings: Settings, backends: Backends, llm: bool = True
) -> list[Result]:
    info = ffmpeg.probe(video)
    work = settings.cache_dir / "benchmark"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    router = ModelRouter(settings, backends)
    try:
        results = []
        if info.has_audio:
            audio = ffmpeg.extract_audio(video, work / "audio.wav")
            separated, vocals = time_separation(router, audio, info.duration, work)
            # Whisper's voice activity filter drops most of a full music mix, so time it on
            # the isolated vocals, the way the vocal presets use it.
            results.append(time_transcription(router, vocals, info.duration))
            results.append(separated)
        if llm:
            for model in dict.fromkeys([settings.llm_model, settings.vision_model]):
                results.append(time_llm(settings.ollama_url, model))
        results.append(time_render(video, info, preset, backends, work))
        return results
    finally:
        shutil.rmtree(work, ignore_errors=True)


def report(results: list[Result], source: str, duration: float) -> str:
    lines = [f"{source} ({duration:.0f}s)"]
    width = max(len(r.stage) for r in results)
    for r in results:
        lines.append(f"  {r.stage:<{width}}  {r.seconds:7.1f}s  {r.detail}")
    return "\n".join(lines)
