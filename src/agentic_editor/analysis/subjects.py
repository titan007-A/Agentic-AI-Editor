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

from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

FRAME_WIDTH = 640  # plenty to place a box, and keeps the vision call quick
PER_CALL = 4
MAX_ITEMS = 5  # per frame: a long list is where a small model starts repeating itself

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
    items: list[_Found] = Field(max_length=MAX_ITEMS)


class _Reply(BaseModel):
    frames: list[_Frame] = Field(max_length=PER_CALL)


PROMPT = (
    "Each image is one frame of a video that will be cropped to a narrow vertical 9:16 "
    "frame for phone screens. For every image, list at most 5 things, the most important "
    "first: each person's head (kind head), each musical instrument, microphone or mic stand, "
    "and each piece of on-screen text or graphics. Give a short label, the kind (head, "
    "instrument, microphone, text, other), a box [x0, y0, x1, y1] in 0-1000 coordinates "
    "relative to that image's width and height, and keep. Set keep=true when cutting "
    "through it would look wrong: the head of a "
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
) -> list[list[Subject] | None]:
    """Subjects at each of `times`, asking about a few frames per call; None where the
    model gave no usable answer, so it isn't remembered as an empty frame."""
    work.mkdir(parents=True, exist_ok=True)
    found: list[list[Subject] | None] = []
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
            by_image = {f.image: f.items for f in parse(_Reply, reply).frames}
        except LLMError as exc:
            # Framing falls back to the face detector for these frames.
            log.warning("no subjects for %d frame(s): %s", len(batch), exc)
            found += [None] * len(batch)
            continue
        for i in range(len(batch)):
            subjects = [to_source(item, size) for item in by_image.get(i, [])]
            found.append([s for s in subjects if s is not None])
    return found
