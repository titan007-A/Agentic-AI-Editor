"""Checks a rendered video against its preset and says what to fix."""

import itertools
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agentic_editor import ffmpeg
from agentic_editor.edl import EDL
from agentic_editor.llm import OllamaClient
from agentic_editor.presets import Preset
from agentic_editor.render.audio import integrated_loudness
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

LOUDNESS_TOLERANCE = 1.0
DURATION_TOLERANCE = 0.5
CAPTION_TOLERANCE = 0.3
_DIALOGUE = re.compile(r"^Dialogue: \d+,(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),")


@dataclass(frozen=True)
class Issue:
    kind: str
    detail: str
    measured: float | None = None


class FrameCheck(BaseModel):
    face_visible: bool
    face_cut_off: bool


class FrameChecks(BaseModel):
    frames: list[FrameCheck]


class ThumbnailPick(BaseModel):
    frame: int
    reason: str


def check_format(info: ffmpeg.MediaInfo, preset: Preset, source_duration: float) -> list[Issue]:
    issues = []
    width, height = preset.size
    if (info.width, info.height) != (width, height):
        issues.append(
            Issue("format", f"output is {info.width}x{info.height}, not {preset.resolution}")
        )
    if abs(info.fps - preset.fps) > 0.5:
        issues.append(Issue("format", f"output runs at {info.fps:.2f} fps, not {preset.fps}"))
    if not info.has_audio:
        issues.append(Issue("format", "output has no audio track"))
    shortest = min(preset.min_duration, source_duration)
    if info.duration < shortest - DURATION_TOLERANCE:
        issues.append(Issue("duration", f"{info.duration:.1f}s is under the {shortest:g}s minimum"))
    if info.duration > preset.max_duration + DURATION_TOLERANCE:
        issues.append(
            Issue("duration", f"{info.duration:.1f}s is over the {preset.max_duration:g}s maximum")
        )
    return issues


def check_loudness(measured: float, preset: Preset) -> list[Issue]:
    target = preset.audio.loudness_lufs
    if abs(measured - target) > LOUDNESS_TOLERANCE:
        return [Issue("loudness", f"{measured:.1f} LUFS, target is {target:g}", measured)]
    return []


def caption_times(ass: str) -> list[tuple[float, float]]:
    times = []
    for line in ass.splitlines():
        if m := _DIALOGUE.match(line):
            v = [int(x) for x in m.groups()]
            times.append(
                (
                    v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 100,
                    v[4] * 3600 + v[5] * 60 + v[6] + v[7] / 100,
                )
            )
    return times


def check_captions(ass: str, words: list[Word], duration: float) -> list[Issue]:
    """Captions must start with the first word and stay inside the video."""
    times = caption_times(ass)
    if not words:
        return []
    if not times:
        return [Issue("captions", "captions are enabled but none were written")]
    issues = []
    if abs(times[0][0] - words[0].start) > CAPTION_TOLERANCE:
        issues.append(
            Issue(
                "captions",
                f"first caption at {times[0][0]:.2f}s, first word at {words[0].start:.2f}s",
            )
        )
    if any(end > duration + CAPTION_TOLERANCE for _, end in times):
        issues.append(Issue("captions", "captions run past the end of the video"))
    if any(b[0] < a[0] for a, b in itertools.pairwise(times)):
        issues.append(Issue("captions", "caption events are out of order"))
    return issues


def sample_frames(video: Path, duration: float, dest: Path, count: int = 4) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        path = dest / f"frame-{i}.jpg"
        ffmpeg.run(["-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=512:-2", "-q:v", "4", str(path)])  # fmt: skip
        frames.append(path)
    return frames


def check_faces(vision: OllamaClient, frames: list[Path]) -> list[Issue]:
    """Ask the vision model whether the person on screen is properly framed."""
    reply = vision.chat_json(
        [
            {
                "role": "user",
                "content": (
                    f"These are {len(frames)} frames from a vertical video, in order. For each "
                    "frame say whether a human face is visible and whether a visible face is cut "
                    "off by the edge of the frame."
                ),
            }
        ],
        FrameChecks.model_json_schema(),
        images=frames,
        temperature=0.0,
    )
    checks = FrameChecks.model_validate(reply).frames
    cut = [i for i, c in enumerate(checks) if c.face_visible and c.face_cut_off]
    if cut:
        return [Issue("framing", f"face cut off by the frame edge in sampled frame(s) {cut}")]
    if checks and not any(c.face_visible for c in checks):
        return [Issue("framing", "no face visible in any sampled frame")]
    return []


def review(
    video: Path,
    edl: EDL,
    preset: Preset,
    source_duration: float,
    captions: Path | None,
    words: list[Word],
    vision: OllamaClient | None,
    work_dir: Path,
) -> list[Issue]:
    info = ffmpeg.probe(video)
    issues = check_format(info, preset, source_duration)
    issues += check_loudness(integrated_loudness(video), preset)
    if captions is not None:
        issues += check_captions(captions.read_text(encoding="utf-8"), words, info.duration)
    if vision is not None and edl.crop_mode in ("face", "center"):
        issues += check_faces(vision, sample_frames(video, info.duration, work_dir / "frames"))
    for issue in issues:
        log.info("review: %s: %s", issue.kind, issue.detail)
    return issues


def pick_thumbnail(vision: OllamaClient, video: Path, duration: float, work_dir: Path) -> Path:
    """Let the vision model choose the most eye-catching of a few sampled frames."""
    frames = sample_frames(video, duration, work_dir / "thumbs", count=6)
    reply = vision.chat_json(
        [
            {
                "role": "user",
                "content": (
                    f"These are {len(frames)} frames from a short video, numbered from 0. Pick the "
                    "one that would make the best thumbnail: sharp, well lit, a clear face or "
                    "subject, an expressive moment."
                ),
            }
        ],
        ThumbnailPick.model_json_schema(),
        images=frames,
        temperature=0.0,
    )
    index = min(max(ThumbnailPick.model_validate(reply).frame, 0), len(frames) - 1)
    dest = video.with_suffix(".jpg")
    t = duration * (index + 0.5) / len(frames)
    ffmpeg.run(["-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2",
                str(dest)])  # fmt: skip
    return dest
