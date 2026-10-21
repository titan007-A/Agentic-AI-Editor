"""Face detection on sampled frames and the smoothed crop path that follows them."""

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
SWITCH_RATIO = 1.6


@dataclass(frozen=True)
class Face:
    cx: float
    cy: float
    width: float
    height: float
    score: float

    @property
    def area(self) -> float:
        return self.width * self.height


Sample = tuple[float, list[Face]]


def model_file(models_dir: Path) -> Path:
    path = models_dir / "mediapipe" / "blaze_face_short_range.tflite"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading face detection model")
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


def detect(
    video: Path, start: float, end: float, models_dir: Path, fps: float = 5.0
) -> list[Sample]:
    """Faces found in frames sampled `fps` times a second between start and end."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    options = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_file(models_dir))),
        min_detection_confidence=0.5,
    )
    capture = cv2.VideoCapture(str(video))
    capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    samples: list[Sample] = []
    next_sample = start
    with vision.FaceDetector.create_from_options(options) as detector:
        while True:
            if not capture.grab():
                break
            t = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if t > end:
                break
            if t + 1e-3 < next_sample:
                continue
            next_sample += 1 / fps
            ok, frame = capture.retrieve()
            if not ok:
                continue
            height, width = frame.shape[:2]
            image = mp.Image(
                image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            )
            faces = []
            for detection in detector.detect(image).detections:
                box = detection.bounding_box
                faces.append(
                    Face(
                        cx=(box.origin_x + box.width / 2) / width,
                        cy=(box.origin_y + box.height / 2) / height,
                        width=box.width / width,
                        height=box.height / height,
                        score=detection.categories[0].score if detection.categories else 0.0,
                    )
                )
            samples.append((t - start, faces))
    capture.release()
    return samples


def choose(faces: list[Face], previous: Face | None) -> Face | None:
    """The face to frame: stay on the one we were following unless another is much bigger."""
    if not faces:
        return None
    largest = max(faces, key=lambda f: f.area)
    if previous is None:
        return largest
    nearest = min(faces, key=lambda f: abs(f.cx - previous.cx) + abs(f.cy - previous.cy))
    return largest if largest.area > nearest.area * SWITCH_RATIO else nearest


def smooth(values: list[float], smoothing: float) -> list[float]:
    """Zero-lag exponential smoothing (forward then backward pass)."""
    if not values:
        return []
    alpha = 1 - smoothing
    forward = [values[0]]
    for v in values[1:]:
        forward.append(forward[-1] + alpha * (v - forward[-1]))
    backward = [forward[-1]]
    for v in reversed(forward[:-1]):
        backward.append(backward[-1] + alpha * (v - backward[-1]))
    return backward[::-1]


def crop_path(
    samples: list[Sample], smoothing: float, src_width: int, crop_width: int
) -> list[tuple[float, int]]:
    """Left edge of the crop window over time, in source pixels."""
    centers = []
    previous = None
    for _, faces in samples:
        face = choose(faces, previous) or previous
        previous = face
        centers.append(face.cx if face else None)
    known = [c for c in centers if c is not None]
    if not known:
        return [(0.0, (src_width - crop_width) // 2)]
    first = known[0]
    filled = []
    for c in centers:
        filled.append(c if c is not None else (filled[-1] if filled else first))
    limit = src_width - crop_width
    return [
        (t, int(min(max(c * src_width - crop_width / 2, 0), limit)))
        for (t, _), c in zip(samples, smooth(filled, smoothing), strict=True)
    ]


def write_commands(path: list[tuple[float, int]], target: str, dest: Path) -> Path:
    dest.write_text("".join(f"{t:.3f} {target} x {x};\n" for t, x in path), encoding="utf-8")
    return dest
