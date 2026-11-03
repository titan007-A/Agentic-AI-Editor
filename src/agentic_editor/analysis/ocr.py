"""Text in a picture, found and read by PaddleOCR's models (through RapidOCR, on the CPU).

The vision model is asked for on-screen text too, but its boxes come out a few dozen rows
off, miss more than half the lines (an @handle, a second caption line) and now and then
name a lyric line that isn't there. OCR boxes sit tight on the letters, and what OCR can't
read isn't text: it is also the test of whether a watermark candidate has anything on it.
"""

import logging
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Below this the recogniser is guessing: a guitar's fret marker read as "C".
MIN_SCORE = 0.5
MIN_LETTERS = 2
# Two letters are a word only when it is sure: legs on a beach were read as "LL".
SHORT_SCORE = 0.9
# Of a whole frame's height: no line of on-screen text is taller than this.
MAX_LINE = 0.12

_lock = threading.Lock()


class Line(BaseModel):
    """One line of text: its box in the picture's pixels, what it says, how sure."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str
    score: float


def available() -> bool:
    """Whether OCR is installed here (it comes with the ml extras)."""
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


def models_dir(root: Path) -> Path:
    return root / "ocr"


@lru_cache
def _engine(folder: str):
    from rapidocr import RapidOCR

    # The models download into `folder` on first use (fetch-models does it ahead).
    Path(folder).mkdir(parents=True, exist_ok=True)
    return RapidOCR(params={"Global.model_root_dir": folder, "Global.log_level": "warning"})


def fetch_model(root: Path) -> None:
    _engine(str(models_dir(root)))


def letters(text: str) -> int:
    return sum(c.isalnum() for c in text)


def read(image: Path | Image.Image, root: Path, whole_frame: bool = False) -> list[Line]:
    """The lines of text in `image` that OCR reads with confidence, in its pixels. In a
    `whole_frame` a line taller than MAX_LINE of it is something else read as letters (a
    close-up of one line is all line)."""
    picture = image if isinstance(image, Image.Image) else Image.open(image)
    pixels = np.array(picture.convert("RGB"))
    engine = _engine(str(models_dir(root)))
    with _lock:  # one recogniser shared by the job threads
        result = engine(pixels, use_det=True, use_cls=False, use_rec=True)
    if result.boxes is None:
        return []
    lines = []
    for quad, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
        if score < MIN_SCORE or letters(text) < MIN_LETTERS:
            continue
        if letters(text) == MIN_LETTERS and score < SHORT_SCORE:
            continue
        q = np.asarray(quad)
        if whole_frame and q[:, 1].max() - q[:, 1].min() > MAX_LINE * pixels.shape[0]:
            continue
        lines.append(
            Line(
                x0=int(q[:, 0].min()),
                y0=int(q[:, 1].min()),
                x1=int(np.ceil(q[:, 0].max())),
                y1=int(np.ceil(q[:, 1].max())),
                text=text.strip(),
                score=round(float(score), 3),
            )
        )
    return lines
