"""Turns a source video into the facts the planner works from. Expensive results are
cached in the source's work directory, so re-running a video skips straight to planning."""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agentic_editor import ffmpeg
from agentic_editor.analysis import audio, beats, scenes, separation, watermarks
from agentic_editor.analysis.beats import Beats
from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.router import ModelRouter
from agentic_editor.transcript import Transcript

log = logging.getLogger(__name__)

AudioSource = Literal["original", "vocals"]
Progress = Callable[[str, str], None]
ENERGY_HOP = 0.5
SPARSE_WORDS = 20  # words a minute below which a full mix is worth hearing without the band


class Analysis(BaseModel):
    duration: float
    width: int
    height: int
    fps: float
    audio_source: AudioSource
    audio_path: Path
    instrument_free: bool = False
    transcript: Transcript
    scenes: list[float]
    energy: list[float]
    energy_hop: float = ENERGY_HOP
    silences: list[tuple[float, float]] = []
    pauses: list[tuple[float, float]] = []
    watermarks: list[Watermark] = []
    beats: Beats = Beats()

    def energy_between(self, start: float, end: float) -> float:
        """Mean level in dB over [start, end)."""
        lo = int(start / self.energy_hop)
        hi = max(lo + 1, int(end / self.energy_hop))
        window = self.energy[lo:hi]
        return sum(window) / len(window) if window else -90.0


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()[:20]


def transcript_file(work_dir: Path, audio: str, router: ModelRouter) -> Path:
    """Where the transcript of `audio` is kept. Another whisper model or backend hears it
    differently, so each keeps its own."""
    backend = router.backends.transcriber
    s = router.settings
    model = s.mlx_whisper_model if backend == "mlx" else s.whisper_model
    name = re.sub(r"[^\w.-]+", "_", f"{backend}-{model}")
    return work_dir / f"transcript-{audio}-{name}.json"


def cached(path: Path, compute: Callable[[], object], dump=json.dumps, load=json.loads):
    if path.exists():
        return load(path.read_text(encoding="utf-8"))
    value = compute()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(value), encoding="utf-8")
    tmp.replace(path)
    return value


def analyze(
    video: Path,
    work_dir: Path,
    router: ModelRouter,
    audio_source: AudioSource,
    progress: Progress = lambda stage, message: None,
) -> Analysis:
    work_dir.mkdir(parents=True, exist_ok=True)
    info = ffmpeg.probe(video)
    if not info.has_audio:
        raise ValueError(f"{video.name} has no audio track")

    original = work_dir / "audio.wav"
    if not original.exists():
        progress("analyze", "extracting audio")
        ffmpeg.extract_audio(video, original)

    timeline_audio, instrument_free = original, False
    if audio_source == "vocals":
        timeline_audio, instrument_free = isolate_vocals(original, work_dir, router, progress)

    def run_transcription():
        progress("transcribe", "transcribing")
        return router.transcribe(timeline_audio).model_dump()

    transcript = Transcript.model_validate(
        cached(transcript_file(work_dir, audio_source, router), run_transcription)
    )
    sparse = len(transcript.words) < SPARSE_WORDS * info.duration / 60
    if audio_source == "original" and sparse:
        # Whisper hears little of singing over a full band; the isolated vocals give the
        # planner and the captions the words. The timing is the same, so they fit as is.
        try:
            vocals, _ = isolate_vocals(original, work_dir, router, progress)
        except separation.SeparationFailed as exc:
            # No voice to isolate (an instrumental, ambience): the full mix's words stand.
            log.info("no vocals to transcribe on their own: %s", exc)
        else:
            sung = Transcript.model_validate(
                cached(
                    transcript_file(work_dir, "vocals", router),
                    lambda: router.transcribe(vocals).model_dump(),
                )
            )
            if len(sung.words) > len(transcript.words):
                log.info(
                    "using the vocals' %d words; the full mix gave %d",
                    len(sung.words),
                    len(transcript.words),
                )
                transcript = sung

    def run_scene_detection():
        progress("analyze", "detecting scene changes")
        return scenes.detect_cuts(video)

    cuts = cached(work_dir / "scenes.json", run_scene_detection)

    def run_watermark_search():
        progress("analyze", "looking for watermarks")
        found = watermarks.detect(
            video, (info.width, info.height), info.duration, router.settings.ffmpeg_bin
        )
        return [m.model_dump() for m in found]

    def run_beat_tracking():
        progress("analyze", "finding the beat")
        return beats.detect(original).model_dump()

    rhythm = Beats.model_validate(cached(work_dir / "beats.json", run_beat_tracking))

    marks = [
        Watermark.model_validate(m)
        for m in cached(work_dir / f"watermarks-v{watermarks.VERSION}.json", run_watermark_search)
    ]
    samples = ffmpeg.read_audio(timeline_audio, 16000)

    return Analysis(
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        audio_source=audio_source,
        audio_path=timeline_audio,
        instrument_free=instrument_free,
        transcript=transcript,
        scenes=cuts,
        energy=audio.energy_curve(samples, 16000, ENERGY_HOP),
        pauses=audio.pauses(samples, 16000),
        watermarks=marks,
        beats=rhythm,
    )


def isolate_vocals(original: Path, work_dir: Path, router: ModelRouter, progress: Progress):
    vocals = work_dir / "vocals.wav"
    meta = work_dir / "vocals.json"
    model = router.settings.separation_model
    if meta.exists():
        stored = json.loads(meta.read_text())
        if stored.get("model") == model:
            # The same model on the same audio rejects its stems again; don't spend
            # minutes of GPU finding that out.
            if stored.get("failed"):
                raise separation.SeparationFailed(stored["failed"])
            if vocals.exists():
                return vocals, stored["instrument_free"]
        # Separated with another model: redo it, and the transcripts that were made from it.
        for made in work_dir.glob("transcript-vocals*.json"):
            made.unlink()

    progress("separate", "isolating vocals")
    stems_dir = work_dir / "stems"
    stems_dir.mkdir(exist_ok=True)
    stems = router.separate_vocals(original, stems_dir)
    try:
        track = separation.accept_stems(original, stems, vocals)
    except separation.SeparationFailed as exc:
        meta.write_text(json.dumps({"failed": str(exc), "model": model}))
        stems.vocals.unlink(missing_ok=True)
        stems.instrumental.unlink(missing_ok=True)
        raise
    meta.write_text(json.dumps({"instrument_free": track.instrument_free, "model": model}))
    return vocals, track.instrument_free


def with_silences(analysis: Analysis, threshold_db: float, min_duration: float) -> Analysis:
    found = audio.detect_silences(analysis.audio_path, threshold_db, min_duration)
    return analysis.model_copy(update={"silences": found})
