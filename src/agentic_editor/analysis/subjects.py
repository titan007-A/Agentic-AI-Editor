"""What in a frame must survive a vertical crop, according to the vision model.

Faces come from the face detector, which is fast and precise. This asks the vision model
for what it can't see: the microphone or instrument being played, readable text and
graphics, and the people in shot, each marked as must-keep or fine to crop.
"""

import logging
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agentic_editor.llm import LLMError

log = logging.getLogger(__name__)

FRAME_WIDTH = 640  # plenty to place a box, and keeps the vision call quick
PER_CALL = 4

Kind = Literal["head", "person", "instrument", "microphone", "text", "other"]


class Subject(BaseModel):
    """A box in source pixels."""

    kind: Kind
    label: str
    x0: int
    y0: int
    x1: int
    y1: int
    keep: bool


class _Found(BaseModel):
    label: str
    kind: Kind
    box: list[int] = Field(min_length=4, max_length=4)
    keep: bool


class _Frame(BaseModel):
    image: int
    items: list[_Found]


class _Reply(BaseModel):
    frames: list[_Frame]


PROMPT = (
    "Each image is one frame of a video that will be cropped to a narrow vertical 9:16 "
    "frame for phone screens. For every image, list what is in it. For each person give two "
    "entries: their head (kind head) and their whole visible body (kind person). Also list "
    "each musical instrument, microphone or mic stand, and each piece of on-screen text or "
    "graphics. Give a short label, the kind (head, person, instrument, microphone, text, "
    "other), a box [x0, y0, x1, y1] in 0-1000 coordinates relative to that image's width and "
    "height, and keep. Set keep=true when cutting through it would look wrong: the head of a "
    "person the shot is about, the microphone or instrument someone is singing into or "
    "playing, readable text, a logo. Set keep=false for things that can be cropped without "
    "anyone noticing: bodies and shoulders, people in the background, stage equipment, "
    "scenery. Number the images from 0 in the order given."
)


def grab(video: Path, at: float, dest: Path, ffmpeg_bin: str) -> Path:
    subprocess.run(
        [ffmpeg_bin, "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", str(dest)],
        check=True,
    )  # fmt: skip
    return dest


def to_source(found: _Found, size: tuple[int, int]) -> Subject | None:
    x0, y0, x1, y1 = (min(1000, max(0, v)) for v in found.box)
    if x1 <= x0 or y1 <= y0:
        return None
    w, h = size
    return Subject(
        kind=found.kind,
        label=found.label,
        x0=round(x0 / 1000 * w),
        y0=round(y0 / 1000 * h),
        x1=round(x1 / 1000 * w),
        y1=round(y1 / 1000 * h),
        # Cropping through a body is normal in vertical video; what matters is its head.
        keep=found.keep and found.kind != "person",
    )


def find(
    vision, video: Path, times: list[float], size: tuple[int, int], ffmpeg_bin: str, work: Path
) -> list[list[Subject]]:
    """Subjects at each of `times`, asking about a few frames per call."""
    work.mkdir(parents=True, exist_ok=True)
    found: list[list[Subject]] = []
    for batch_start in range(0, len(times), PER_CALL):
        batch = times[batch_start : batch_start + PER_CALL]
        images = [
            grab(video, t, work / f"frame-{batch_start + i}.jpg", ffmpeg_bin)
            for i, t in enumerate(batch)
        ]
        try:
            reply = vision.chat_json(
                [{"role": "user", "content": PROMPT}],
                _Reply.model_json_schema(),
                images=images,
                temperature=0.0,
            )
            by_image = {f.image: f.items for f in _Reply.model_validate(reply).frames}
        except LLMError as exc:
            # Framing falls back to the face detector for these frames.
            log.warning("no subjects for %d frame(s): %s", len(batch), exc)
            by_image = {}
        for i in range(len(batch)):
            subjects = [to_source(item, size) for item in by_image.get(i, [])]
            found.append([s for s in subjects if s is not None])
    return found
