"""Singer edit (style "singer"): a song's hook made into a lyric edit around the singer.

The owner's reference (docs/animation-studio.md, "Singer edit") is a 12 s square reel of
KiD Boi's "Maa, I Tried": one scene a sung line, each a warm, gritty picture with the
singer in it, and every sung word slapped on as a torn-paper sticker the moment it is
sung, the line's key words in red handwriting, small side notes and red doodles around.

Here the owner gives the song's name and the singer's photos:

1. the hook: the song found, downloaded, its catchy part cut and every sung word timed
   (animate/songhook.py, the Song story's song finder);
2. the photos: each cut out (ISNet, see-through around the singer) and their face found;
3. the writer gives every line a shot (how the singer is shown), its key words, side
   notes, a doodle and a picture, plus the post's caption;
4. the pictures are painted where the image model is (the Mac); elsewhere the player
   draws a gritty backdrop from the photo itself;
5. the player (player/singer.js) draws it, the sound is the hook's own, brought to the
   presets' loudness.
"""

import hashlib
import itertools
import json
import logging
import re
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from agentic_editor import ffmpeg, logs
from agentic_editor.animate import images
from agentic_editor.animate.plan import (
    SHOTS,
    Item,
    Plan,
    Scene,
    SingerEdit,
    SingerPhoto,
    Song,
    SungLine,
    SungWord,
    Visual,
    tidy,
)
from agentic_editor.animate.timeline import SceneTime, Timeline
from agentic_editor.db import Animation
from agentic_editor.settings import Settings
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

STYLE = "singer"
NAME = "Singer edit"
MAX_PHOTOS = 6
PHOTO_SIDE = 1600  # a photo is kept this big at most (the stage is 1080 wide)
# Beyond any camera's photo: refused before it is decoded (a decompression bomb).
MAX_PIXELS = 200_000_000
MIN_SIDE = 120  # a smaller photo has too little of the singer to show
# A photo smaller than this on its short side (a search result's thumbnail) is enlarged to
# about this, by Real-ESRGAN and GFPGAN where they are, as the upscale preset does.
SMALL_SIDE = 400
ENLARGE_SIDE = 800
ENLARGE_WITH_MODELS = True  # tests turn the upscale models off
PREPARE_VERSION = 3
HEAD_ONLY = 0.45  # a cutout whose face is more of its height than this is a face alone
# Lines: a scene each. Words further apart than this start a new line when the hook
# doesn't say where its lines are; so do long ones.
GAP = 0.42
LINE_WORDS = 7
LINE_SECONDS = 3.2
SHORT_LINE = 0.75  # a line sung quicker than this joins the next
LONG_LINE = 4.6  # a line sung longer than this is two scenes
LEAD = 0.07  # a scene's picture comes this much before its first word
FADE_OUT = 0.35
LOUDNESS = -14.0
TRUE_PEAK = -1.5
# The doodles the player draws, by the emoji a writer gives for them.
DOODLES = {"❓": "question", "⭐": "star", "❤️": "heart", "➡️": "arrow", "❌": "cross",
           "👑": "crown", "✨": "spark"}  # fmt: skip
PHOTO_SHOTS = ("hero", "close", "far", "print", "mirror", "echo", "silhouette")
# The shots a reel cycles through when the writer gives none (or the same twice running).
ROTATION = ("hero", "close", "far", "print", "silhouette", "mirror", "echo", "words")
CUT_MODEL = "isnet-general-use.onnx"
CUT_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
CUT_SIZE = 1024
CUT_VERSION = 3
_AUDIO = re.compile(r"[0-9a-f]{24}\.wav")
_PICTURE = re.compile(r"[0-9a-f]{24}\.png")


class SingerError(RuntimeError):
    """Something the owner can act on (no song found, no singer in the photos)."""


def songs_dir(settings: Settings) -> Path:
    return settings.cache_dir / "animation-songs"


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


# ---- photos --------------------------------------------------------------------------


class PhotoRefused(ValueError):
    """An upload that can't be used, in plain words, with what is known of the file (for
    the log: never the picture itself)."""

    def __init__(self, reason: str, data: bytes, picture=None, kind: str = ""):
        super().__init__(reason)
        self.details = {
            "format": kind or (getattr(picture, "format", None) or "unknown"),
            "bytes": len(data),
            "pixels": f"{picture.width}x{picture.height}" if picture is not None else "unknown",
        }


@dataclass
class Upload:
    name: str  # the photo kept with the pictures (images.folder)
    notes: list[str] = field(default_factory=list)


_heif = [False]


def _open_heif() -> None:
    """HEIC/HEIF (iPhones) read by Pillow through pillow-heif, once; without it they are
    refused with a reason."""
    if _heif[0]:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - a dependency, here only if broken
        log.warning("pillow-heif isn't installed: HEIC photos can't be read")
    _heif[0] = True


def _rgb(picture):
    """Any photo's pixels as 8-bit RGB: 16-bit and float scaled down (not clipped),
    see-through parts on white, CMYK and palettes converted."""
    import numpy as np
    from PIL import Image

    if picture.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        values = np.asarray(picture, dtype=np.float32)
        top = float(values.max()) or 1.0
        scale = 255.0 / (65535.0 if top > 255 and picture.mode != "F" else max(top, 1.0))
        return Image.fromarray(np.clip(values * scale, 0, 255).astype(np.uint8)).convert("RGB")
    if picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info:
        picture = picture.convert("RGBA")
        white = Image.new("RGBA", picture.size, (255, 255, 255, 255))
        white.alpha_composite(picture)
        return white.convert("RGB")
    return picture.convert("RGB")


def _decode(data: bytes):
    """The uploaded file as a picture, its first frame, turned as the camera held it."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    _open_heif()
    try:
        picture = Image.open(BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoRefused(
            "that file isn't a photo this tool can read (JPEG, PNG, WebP, HEIC, AVIF or GIF work)",
            data,
        ) from exc
    except Image.DecompressionBombError as exc:
        raise PhotoRefused("that file is far too big to be a photo", data) from exc
    if picture.width * picture.height > MAX_PIXELS:
        raise PhotoRefused("that file is far too big to be a photo", data, picture)
    kind = picture.format or "unknown"
    try:
        if getattr(picture, "n_frames", 1) > 1:
            picture.seek(0)  # a GIF or an animated WebP: its first frame
        if kind == "JPEG":
            picture.draft("RGB", (PHOTO_SIDE * 2, PHOTO_SIDE * 2))  # a big JPEG decodes smaller
        picture = ImageOps.exif_transpose(picture)
        picture.load()
    except (OSError, ValueError, SyntaxError) as exc:
        raise PhotoRefused(
            "that photo's file is damaged; send it again", data, picture, kind
        ) from exc
    return picture, kind


def enlarge(picture, settings: Settings, short: int = ENLARGE_SIDE):
    """A small photo made bigger, its short side `short`: by Real-ESRGAN with the faces
    redrawn by GFPGAN (the upscale preset's models, on the CPU: a thumbnail is quick and the
    GPU stays free) where they are here, else smoothly. And how it was done."""
    import numpy as np
    from PIL import Image

    scale = short / min(picture.size)
    size = (round(picture.width * scale), round(picture.height * scale))
    folder = settings.models_dir / "upscale"
    wanted = ("realesr-general-x4v3.pth", "realesr-general-wdn-x4v3.pth", "GFPGANv1.4.pth",
              "face_detection_yunet_2023mar.onnx")  # fmt: skip
    if ENLARGE_WITH_MODELS and all((folder / name).exists() for name in wanted):
        try:
            import torch

            from agentic_editor import upscale

            cpu = torch.device("cpu")
            frame = upscale.Upscaler(folder, 0.5, cpu)(np.asarray(picture), size)
            frame = upscale.FaceFixer(folder, 0.5, cpu)(frame)
            return Image.fromarray(frame.round().clip(0, 255).astype(np.uint8)), "Real-ESRGAN"
        except Exception:
            log.exception("Real-ESRGAN couldn't enlarge a photo; enlarging it smoothly")
    return picture.resize(size, Image.LANCZOS), "smoothly"


def take_photo(data: bytes, settings: Settings) -> Upload:
    """An uploaded photo, whatever a phone or a browser saved it as, kept with the
    painted pictures (served and drawn like them) as a PNG named by its content: turned
    upright, at most PHOTO_SIDE on its long side, a small one enlarged. PhotoRefused for
    a file that can't be used, saying why."""
    from PIL import Image

    picture, kind = _decode(data)
    width, height = picture.size
    if min(width, height) < MIN_SIDE:
        raise PhotoRefused(
            f"that photo is only {width}x{height} pixels; send one at least {MIN_SIDE} pixels "
            "on its short side",
            data, picture, kind,
        )  # fmt: skip
    picture = _rgb(picture)
    notes = []
    name = hashlib.sha256(data).hexdigest()[:24] + ".png"
    out = images.folder(settings)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / name
    if min(width, height) < SMALL_SIDE:
        notes.append(f"a small photo ({width}x{height}): enlarged, it may look soft")
    if dest.exists():
        return Upload(name, notes)
    if min(width, height) < SMALL_SIDE:
        picture, how = enlarge(picture, settings)
        log.info("singer photo %s: %dx%d enlarged %s", name, width, height, how)
    picture.thumbnail((PHOTO_SIDE, PHOTO_SIDE), Image.LANCZOS)
    part = dest.with_suffix(".part.png")
    picture.save(part, optimize=True)
    part.replace(dest)
    return Upload(name, notes)


def save_photo(data: bytes, settings: Settings) -> str:
    """take_photo's kept file."""
    return take_photo(data, settings).name


# ---- the photo made ready ------------------------------------------------------------------


def bars_of(picture) -> tuple[int, int, int, int]:
    """The photo inside its borders: plain rows and columns at its edges taken off (black
    or white letterboxing, a phone screenshot's status and navigation bars, a frame), as
    long as most of the picture is left. (left, top, right, bottom)."""
    import numpy as np

    pixels = np.asarray(picture.convert("RGB"), dtype=np.int16)
    height, width = pixels.shape[:2]

    def plain(line) -> bool:
        middle = np.median(line, axis=0)
        return float((np.abs(line - middle).max(axis=1) < 24).mean()) >= 0.8

    def run(lines) -> int:
        count = 0
        for line in lines:
            if not plain(line):
                break
            count += 1
        return count

    top = run(pixels[i] for i in range(height // 2))
    bottom = run(pixels[height - 1 - i] for i in range(height // 2))
    left = run(pixels[:, i] for i in range(width // 2))
    right = run(pixels[:, width - 1 - i] for i in range(width // 2))
    box = (left, top, width - right, height - bottom)
    kept = (box[2] - box[0]) * (box[3] - box[1])
    if (
        box[2] - box[0] < width * 0.3
        or box[3] - box[1] < height * 0.3
        or kept < 0.25 * width * height
    ):
        return (0, 0, width, height)  # all plain: nothing that looks like a border
    return box


def _faces_in(picture, settings: Settings) -> list[tuple[float, float, float, float]]:
    """Faces YuNet finds (x, y, w, h in pixels); [] when its model isn't here."""
    import numpy as np

    from agentic_editor.analysis import facecam

    model = settings.models_dir / "upscale" / facecam.YUNET
    if not model.exists():
        try:
            model = facecam.model(settings.models_dir)
        except Exception:
            log.info("no face finder for preparing a photo", exc_info=True)
            return []
    return facecam.faces(np.asarray(picture.convert("RGB"))[None], model)[0]


def main_person(solid, faces) -> tuple[int, int, int, int] | None:
    """The box of the photo's main person: around the biggest face, from the top of their
    head to the bottom of their outline, as wide as the outline between them and the
    nearest faces beside them (a group photo); the whole outline when there is one person.
    None when nobody stands out."""
    import numpy as np

    rows = np.where(solid.any(axis=1))[0]
    if not len(rows):
        return None
    height, width = solid.shape
    cols = np.where(solid.any(axis=0))[0]
    left, right = int(cols[0]), int(cols[-1]) + 1
    if faces:
        main = max(faces, key=lambda f: f[2] * f[3])
        middle = main[0] + main[2] / 2
        others = [f[0] + f[2] / 2 for f in faces if f is not main and f[2] > 0.4 * main[2]]
        lefts = [x for x in others if x < middle]
        rights = [x for x in others if x > middle]
        if lefts:
            left = max(left, int((max(lefts) + middle) / 2))
        if rights:
            right = min(right, int((min(rights) + middle) / 2))
        band = solid[:, left:right]
        rows = np.where(band.any(axis=1))[0]
        if not len(rows):
            return None
        # the whole head (hair, a turban) above the face, room on both sides of it
        top = min(int(rows[0]), int(main[1] - 0.9 * main[3]))
        left = min(left, int(main[0] - 0.6 * main[2]))
        right = max(right, int(main[0] + 1.6 * main[2]))
        bottom = max(int(rows[-1]) + 1, int(main[1] + 1.6 * main[3]))
        margin_x = int(0.08 * (right - left))
        margin_y = int(0.05 * (bottom - top))
    else:
        # no face seen (a stylised cover, a face half in shadow): the outline may be the
        # face alone, so more room around it for the hair and shoulders
        top, bottom = int(rows[0]), int(rows[-1]) + 1
        margin_x = int(0.3 * (right - left))
        margin_y = int(0.25 * (bottom - top))
    return (max(0, left - margin_x), max(0, top - margin_y),
            min(width, right + margin_x), min(height, bottom + margin_y))  # fmt: skip


def without_others(solid, faces):
    """The outline without the heads of smaller faces away from the biggest (a drummer
    behind the singer, a crowd): they would be cut out and painted with them."""
    if len(faces) < 2:
        return solid
    solid = solid.copy()
    main = max(faces, key=lambda f: f[2] * f[3])
    height, width = solid.shape
    for x, y, w, h in faces:
        if (x, y, w, h) == tuple(main) or w >= 0.6 * main[2]:
            continue
        if abs((x + w / 2) - (main[0] + main[2] / 2)) < (w + main[2]) / 2:
            continue  # over or under the main face: part of the same person
        x0, y0 = max(0, int(x - 0.4 * w)), max(0, int(y - 0.6 * h))
        x1, y1 = min(width, int(x + 1.4 * w)), min(height, int(y + 1.5 * h))
        solid[y0:y1, x0:x1] = False
    return solid


def prepare_photo(
    name: str, settings: Settings, read=None, mask_of=None, faces_of=None, detect=None
) -> tuple[str, list[str]]:
    """The photo made ready for a singer edit, so whatever was sent works as it is: its
    borders and screenshot bars trimmed, every bit of text painted out (a watermark, a
    page's handle, an album cover's title), cropped to the main person (the biggest face
    in a group, the face kept in), enlarged again when the crop is small. Kept once; the
    photo itself when nothing needed doing. And what was done, in words."""
    from PIL import Image

    out = images.folder(settings)
    dest_name = _key(name, "singer-prepared", PREPARE_VERSION) + ".png"
    meta = out / (dest_name + ".json")
    if meta.exists():
        known = json.loads(meta.read_text())
        if (out / known.get("name", name)).exists():
            return known.get("name", name), list(known.get("notes", []))
    picture = Image.open(out / name).convert("RGB")
    notes: list[str] = []
    changed = False
    box = bars_of(picture)
    if box != (0, 0, *picture.size):
        picture = picture.crop(box)
        notes.append("borders trimmed")
        changed = True
    try:
        solid = keep_subject((mask_of or person_mask)(picture, settings.models_dir)) > 0.5
        faces = (faces_of or _faces_in)(picture, settings)
    except Exception:
        log.exception("couldn't find the singer in %s", name)
        solid, faces = None, []
    if read is None and detect is None:
        from agentic_editor.analysis import ocr

        if ocr.available():
            read = lambda pic: ocr.read(pic, settings.models_dir)  # noqa: E731
            detect = ocr.boxes
    lines = read(picture) if read else []
    boxes = stamps(lines, None)
    if detect is not None and solid is not None:
        # letters OCR sees but can't read (a cover's script title), off the person only:
        # on them it could be a pattern
        for x0, y0, x1, y1 in detect(picture):
            area = solid[max(0, y0) : y1, max(0, x0) : x1]
            if area.size and float(area.mean()) < 0.2:
                pad = max(3, (y1 - y0) // 2)
                boxes.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    if boxes:
        picture = wipe(picture, boxes)
        notes.append(f"text painted out ({len(boxes)} places)")
        changed = True
    person = None
    if solid is not None and 0.02 < float(solid.mean()) < 0.97:
        person = main_person(without_others(solid, faces), faces)
    if (
        person
        and (person[2] - person[0]) * (person[3] - person[1])
        < 0.85 * picture.width * picture.height
    ):
        picture = picture.crop(person)
        notes.append("cropped to the singer")
        changed = True
    if changed and min(picture.size) < SMALL_SIDE:
        picture = picture.resize(
            (round(picture.width * SMALL_SIDE / min(picture.size)),
             round(picture.height * SMALL_SIDE / min(picture.size))), Image.LANCZOS,
        )  # fmt: skip
    kept = name
    if changed:
        kept = dest_name
        part = out / (dest_name + ".part.png")
        picture.save(part)
        part.replace(out / dest_name)
    meta.write_text(json.dumps({"name": kept, "notes": notes}))
    return kept, notes


def cut_model(models_dir: Path) -> Path:
    """ISNet (Apache-2.0, 176 MB), fetched the first time a singer edit needs it."""
    path = models_dir / "segmentation" / CUT_MODEL
    if not path.exists():
        with logs.download_progress("the background removal model", path.parent):
            partial = path.with_suffix(".part")
            urllib.request.urlretrieve(CUT_URL, partial)
            partial.replace(path)
    return path


_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def _session(model: Path):
    import onnxruntime as ort

    with _session_lock:
        if str(model) not in _sessions:
            # On the CPU: a second a photo, and the GPU stays free for the models that need it.
            _sessions[str(model)] = ort.InferenceSession(
                str(model), providers=["CPUExecutionProvider"]
            )
        return _sessions[str(model)]


def person_mask(picture, models_dir: Path):
    """How sure ISNet is that each pixel is the photo's subject, 0..1, at the photo's size."""
    import numpy as np
    from PIL import Image

    session = _session(cut_model(models_dir))
    x = np.asarray(picture.resize((CUT_SIZE, CUT_SIZE), Image.BILINEAR), dtype=np.float32)
    x = (x / 255.0 - 0.5).transpose(2, 0, 1)[None]
    out = session.run(None, {session.get_inputs()[0].name: x})[0][0, 0]
    out = (out - out.min()) / max(1e-6, float(out.max() - out.min()))
    mask = Image.fromarray((out * 255).astype(np.uint8)).resize(picture.size, Image.BILINEAR)
    return np.asarray(mask, dtype=np.float32) / 255.0


def keep_subject(mask):
    """The mask with stray blobs gone: the biggest part and any part a sixth its size."""
    import numpy as np
    from scipy import ndimage

    solid = mask > 0.5
    parts, count = ndimage.label(solid)
    if count == 0:
        return np.zeros_like(mask)
    sizes = ndimage.sum(solid, parts, range(1, count + 1))
    biggest = float(max(sizes))
    kept = [i + 1 for i, s in enumerate(sizes) if s >= biggest / 6]
    near = ndimage.binary_dilation(np.isin(parts, kept), iterations=6)
    return mask * near


@dataclass
class Cutout:
    name: str  # the PNG in the pictures folder
    face: list[float]  # [x, y, w, h] in the cutout, 0..1
    aspect: float


def cut_out(
    name: str, settings: Settings, mask_of=person_mask, face_of=None, faces_of=None
) -> Cutout | None:
    """The singer cut out of the photo `name` (see-through around them, cropped to them),
    kept once; None when nobody stands out in it (the photo is then shown whole)."""
    import numpy as np
    from PIL import Image

    out = images.folder(settings)
    picture = Image.open(out / name).convert("RGB")
    dest_name = _key(name, "singer-cut", CUT_VERSION) + ".png"
    meta = out / (dest_name + ".json")
    if (out / dest_name).exists() and meta.exists():
        known = json.loads(meta.read_text())
        return Cutout(dest_name, known["face"], known["aspect"]) if known.get("ok") else None
    mask = keep_subject(mask_of(picture, settings.models_dir))
    try:
        others = (faces_of or _faces_in)(picture, settings)
    except Exception:
        others = []
    if len(others) > 1:
        kept = without_others(mask > 0.5, others)
        mask = keep_subject(mask * kept)
    solid = mask > 0.5
    share = float(solid.mean())
    if share < 0.02 or share > 0.96:
        meta.write_text(json.dumps({"ok": False, "share": share}))
        return None
    rows, cols = np.where(solid)
    pad = int(0.02 * max(picture.size))
    top, bottom = max(0, rows.min() - pad), min(picture.height, rows.max() + pad + 1)
    left, right = max(0, cols.min() - pad), min(picture.width, cols.max() + pad + 1)
    cut = picture.convert("RGBA")
    cut.putalpha(Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)))
    cut = cut.crop((left, top, right, bottom))
    face = (face_of or _face)(picture, settings)
    w, h = right - left, bottom - top
    head = head_of(solid[top:bottom, left:right])
    face_box = head
    if face:
        fx, fy, fw, fh = face
        found = [(fx - left) / w, (fy - top) / h, fw / w, fh / h]
        # The face finder is made for faces near the camera: on a full-length photo it
        # can take a jacket for a face. Trusted only where the silhouette has its head.
        sized = 0.5 * head[3] <= found[3] <= 1.6 * head[3] and found[2] <= 1.6 * head[2]
        if found[1] + found[3] / 2 < 0.45 and sized:
            face_box = found
    face_box = [round(min(1.0, max(0.0, v)), 4) for v in face_box]
    part = out / (dest_name + ".part.png")
    cut.save(part)
    part.replace(out / dest_name)
    meta.write_text(json.dumps({"ok": True, "face": face_box, "aspect": round(w / h, 4)}))
    return Cutout(dest_name, face_box, round(w / h, 4))


def head_of(solid) -> list[float]:
    """Where an upright person's head is in their cutout, from its outline alone: from the
    top of the silhouette down to its neck (the narrowest point above the waist), as wide as
    the silhouette mostly is there. [x, y, w, h], 0..1."""
    import numpy as np
    from scipy.ndimage import uniform_filter1d

    height, width = solid.shape
    rows = np.where(solid.any(axis=1))[0]
    if len(rows) < 10:
        return [0.34, 0.02, 0.32, 0.2]
    top, bottom = int(rows[0]), int(rows[-1])
    tall = bottom - top + 1
    spans, middles = [], []
    for row in solid[top : bottom + 1]:
        cols = row.nonzero()[0]
        spans.append(cols[-1] - cols[0] + 1 if len(cols) else 0)
        middles.append((cols[0] + cols[-1]) / 2 if len(cols) else np.nan)
    smooth = uniform_filter1d(np.asarray(spans, dtype=float), size=max(3, tall // 50))
    low, high = int(0.08 * tall), max(int(0.08 * tall) + 2, int(0.65 * tall))
    neck = low + int(np.argmin(smooth[low:high]))
    head = [s for s in spans[:neck] if s > 0] or [width * 0.3]
    head_w = float(np.median(head))
    middle = float(np.nanmedian(middles[:neck])) if neck else width / 2
    return [(middle - head_w / 2) / width, top / height, head_w / width, neck / height]


def _face(picture, settings: Settings) -> list[float] | None:
    """The biggest face in the photo, [x, y, w, h] in pixels; None when none is found."""
    try:
        import mediapipe as mp
        import numpy as np
        from mediapipe.tasks.python import BaseOptions, vision

        from agentic_editor.analysis import faces

        options = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(faces.model_file(settings.models_dir))),
            min_detection_confidence=0.45,
        )
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(picture))
        with vision.FaceDetector.create_from_options(options) as detector:
            found = detector.detect(image).detections
    except Exception:  # no face finder here: the head is guessed
        log.info("no face finder for the singer's photo", exc_info=True)
        return None
    if not found:
        return None
    box = max((d.bounding_box for d in found), key=lambda b: b.width * b.height)
    # The detector's box is the face alone; hair and chin make the head.
    grow = 0.25
    return [box.origin_x - box.width * grow, box.origin_y - box.height * grow * 1.6,
            box.width * (1 + 2 * grow), box.height * (1 + 2.6 * grow)]  # fmt: skip


def photos_of(
    names: list[str], settings: Settings, cutter=None, cleaner=None
) -> tuple[list[SingerPhoto], int]:
    """The photos made ready: stamped-on text painted out, the singer cut out of each;
    and how many marks were taken off them."""
    cutter = cutter or cut_out
    cleaner = cleaner or clean_photo
    out = []
    wiped = 0
    for name in names[:MAX_PHOTOS]:
        if not _PICTURE.fullmatch(name) or not (images.folder(settings) / name).exists():
            continue
        try:
            name, marks = cleaner(name, settings)
            wiped += marks
        except Exception:
            log.exception("couldn't look for marks on %s", name)
        try:
            cut = cutter(name, settings)
        except Exception:
            log.exception("couldn't cut the singer out of %s", name)
            cut = None
        if cut and len(cut.face) == 4 and cut.face[3] > HEAD_ONLY:
            # a face alone (a close portrait, an album cover): cut out it floats; it is
            # shown whole, as a print, and painted from whole
            cut = None
        out.append(
            SingerPhoto(image=name, cutout=cut.name, face=cut.face, aspect=cut.aspect)
            if cut
            else SingerPhoto(image=name)
        )
    return out, wiped


# Text an agency or a page stamps on a photo: a web address, a handle, a copyright sign, a
# photo agency's name. Never the singer's: it would show on every scene and every painting.
STAMP = re.compile(
    r"www\.|\.(com|in|net|org|co|tv|me|io)\b|@\w|©|\(c\)|\b(photos?|pics?|getty|shutterstock"
    r"|alamy|stock|hungama|pinkvilla|bhayani|filmfare|instagram|tiktok|youtube|copyright"
    r"|watermark|images)\b",
    re.I,
)
CLEAN_VERSION = 1


def clean_photo(name: str, settings: Settings, read=None, mask_of=None) -> tuple[str, int]:
    """The photo with text stamped on it painted out (an agency's watermark, a page's
    handle), kept once; the photo itself when it has none. Any text on the singer counts,
    stamp or not: lyric stickers go over them, and a painting would copy it into every
    scene. Text in the background stays unless it reads like a stamp (it goes with the
    background when the singer is cut out)."""
    from PIL import Image

    out = images.folder(settings)
    dest_name = _key(name, "singer-clean", CLEAN_VERSION) + ".png"
    meta = out / (dest_name + ".json")
    if meta.exists():
        known = json.loads(meta.read_text())
        kept = known.get("name", name)
        if (out / kept).exists():
            return kept, int(known.get("marks", 0))
    if read is None:
        from agentic_editor.analysis import ocr

        if not ocr.available():
            return name, 0
        read = lambda picture: ocr.read(picture, settings.models_dir)  # noqa: E731
    picture = Image.open(out / name).convert("RGB")
    lines = read(picture)
    solid = None
    if any(not STAMP.search(line.text) for line in lines):
        solid = keep_subject((mask_of or person_mask)(picture, settings.models_dir)) > 0.5
    marks = stamps(lines, solid)
    kept = name
    if marks:
        kept = dest_name
        part = out / (dest_name + ".part.png")
        wipe(picture, marks).save(part)
        part.replace(out / dest_name)
        log.info("singer photo %s: %d marks painted out", name, len(marks))
    meta.write_text(json.dumps({"name": kept, "marks": len(marks)}))
    return kept, len(marks)


def stamps(lines, solid) -> list[tuple[int, int, int, int]]:
    """The boxes to paint out: stamp-like lines, lines mostly on the singer, and the lines
    beside them (the rest of a logo), each with a margin of half its height."""

    def on_person(line) -> float:
        if solid is None:
            return 0.0
        area = solid[line.y0 : line.y1, line.x0 : line.x1]
        return float(area.mean()) if area.size else 0.0

    # Every line read goes when there's no outline to tell the singer apart (a photo made
    # ready: a cover's title is no watermark but has no place in the reel either).
    marked = [
        line for line in lines if solid is None or STAMP.search(line.text) or on_person(line) > 0.5
    ]
    boxes = []
    for line in lines:
        near = any(
            line is m
            or (
                abs((line.y0 + line.y1) / 2 - (m.y0 + m.y1) / 2) < 1.6 * (m.y1 - m.y0)
                and line.x0 < m.x1 + (m.y1 - m.y0)
                and line.x1 > m.x0 - (m.y1 - m.y0)
            )
            for m in marked
        )
        if near:
            pad = max(3, (line.y1 - line.y0) // 2)
            boxes.append((line.x0 - pad, line.y0 - pad, line.x1 + pad, line.y1 + pad))
    return boxes


def wipe(picture, boxes):
    """`picture` with the letters inside `boxes` painted over from around them: only the
    strokes (what stands out from the box's own smoothed look), so a jacket under a
    watermark keeps its folds."""
    import cv2
    import numpy as np
    from PIL import Image

    rgb = np.asarray(picture.convert("RGB")).copy()
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    strokes = np.zeros_like(gray)
    for x0, y0, x1, y1 in boxes:
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(width, int(x1)), min(height, int(y1))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        region = gray[y0:y1, x0:x1]
        size = max(3, (min(y1 - y0, 101) // 2) * 2 + 1)
        smooth = cv2.medianBlur(region, size)
        strokes[y0:y1, x0:x1] = (cv2.absdiff(region, smooth) > 18).astype(np.uint8) * 255
    strokes = cv2.dilate(strokes, np.ones((3, 3), np.uint8), iterations=2)
    mended = cv2.inpaint(rgb, strokes, 5, cv2.INPAINT_TELEA)
    return Image.fromarray(mended)


LOOK_SCHEMA = {
    "type": "object",
    "properties": {"person": {"type": "boolean"}, "look": {"type": "string"}},
    "required": ["person", "look"],
}


def describe(llm, photo: Path) -> str:
    """How the singer looks, for pictures that paint them ("a young man with curly dark
    hair, a short beard and glasses, in a brown hoodie"); "" when it can't be told."""
    try:
        reply = llm.chat_json(
            [{"role": "user", "content": (
                "Is there one main person in this photo? Describe how they look in one short "
                "phrase for a painter: gender and age group, hair, beard, glasses, skin tone, "
                "clothes. No names, nothing about the background."
            )}],
            LOOK_SCHEMA,
            images=[photo],
            temperature=0.1,
        )  # fmt: skip
    except Exception:
        log.info("couldn't describe the singer", exc_info=True)
        return ""
    look = " ".join(str(reply.get("look", "")).split())[:160]
    return look if reply.get("person") else ""


# ---- the hook --------------------------------------------------------------------------


@dataclass
class Hook:
    """A song's catchy part: its sound and every sung word, times from its own start."""

    audio: Path
    duration: float
    title: str
    artist: str
    words: list[Word]
    lines: list = field(default_factory=list)  # the lyric lines, when the finder knows them


def _get(found, *names, default=None):
    for name in names:
        if isinstance(found, dict) and name in found:
            return found[name]
        if hasattr(found, name):
            return getattr(found, name)
    return default


def _word(raw) -> Word | None:
    text = str(_get(raw, "text", "word", default="")).strip()
    if not text:
        return None
    start = float(_get(raw, "start", default=0.0) or 0.0)
    end = float(_get(raw, "end", default=start) or start)
    return Word(text=text, start=start, end=max(end, start))


def _length(path: Path) -> float:
    return float(ffmpeg.probe(path).duration or 0.0)


def _romanised(line) -> list:
    """A line's timed words, written as its lyric was (romanised Hindi, say) when the song
    shows them in another script and the two have as many words: lyric edits are read in
    the lyric's own spelling."""
    words = list(_get(line, "words", default=[]) or [])
    said = str(_get(line, "said", default="") or "").split()
    if not said or len(said) != len(words) or not words:
        return words
    if all(str(_get(w, "text", default="")).isascii() for w in words):
        return words
    return [
        {"text": text, "start": _get(w, "start", default=0.0), "end": _get(w, "end", default=0.0)}
        for text, w in zip(said, words, strict=True)
    ]


def hook_of(found, settings: Settings, audio: Path | None = None) -> Hook:
    """Whatever the hook finder gave, as a Hook: its sound cut to the hook and kept as a
    WAV named by what it is, its words timed from the hook's start. The finder may give
    the whole song with the hook's place in it (start, end) or the hook alone, times in
    the song or in the hook, and the words by themselves or inside its lines."""
    source = audio or Path(str(_get(found, "audio", "path", "audio_path", default="")))
    if not source.is_file():
        raise SingerError("the song's sound wasn't found")
    start = float(_get(found, "start", default=0.0) or 0.0)
    end = float(_get(found, "end", default=0.0) or 0.0)
    raw_lines = list(_get(found, "lines", default=[]) or [])
    raw_words = list(_get(found, "words", default=[]) or [])
    if not raw_words and raw_lines and not isinstance(raw_lines[0], str):
        raw_words = [w for line in raw_lines for w in _romanised(line)]
        raw_lines = [
            {"text": " ".join(str(_get(w, "text", default="")) for w in _romanised(line)),
             "start": _get(line, "start", default=0.0), "end": _get(line, "end", default=0.0),
             "words": _romanised(line)}
            for line in raw_lines
        ]  # fmt: skip
    words = [w for w in (_word(x) for x in raw_words) if w]
    length = _length(source)
    whole = end > start and length > (end - start) + 1.5
    span = (end - start) if end > start else length
    absolute = bool(words) and start > 0.5 and words[0].start >= start - 0.5
    if absolute:
        words = [Word(text=w.text, start=w.start - start, end=w.end - start) for w in words]
        raw_lines = [_shift_line(line, start) for line in raw_lines]
    words = [w for w in words if w.end > 0 and w.start < span]
    folder = songs_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    name = _key(str(source), stat.st_size, stat.st_mtime, start if whole else 0, span) + ".wav"
    dest = folder / name
    if not dest.exists():
        part = dest.with_suffix(".part.wav")
        cut = ["-ss", f"{start:.3f}", "-t", f"{span:.3f}"] if whole else ["-t", f"{span:.3f}"]
        ffmpeg.run(["-y", *cut, "-i", str(source), "-vn", "-ac", "2", "-ar", "48000",
                    "-c:a", "pcm_s16le", str(part)])  # fmt: skip
        part.replace(dest)
    return Hook(
        audio=dest,
        duration=round(min(span, _length(dest)), 3),
        title=" ".join(str(_get(found, "title", default="")).split())[:120],
        artist=" ".join(str(_get(found, "artist", default="")).split())[:120],
        words=words,
        lines=raw_lines,
    )


def _shift_line(line, by: float):
    if isinstance(line, str):
        return line
    words = [w for w in (_word(x) for x in _get(line, "words", default=[]) or []) if w]
    return {
        "text": str(_get(line, "text", default="")),
        "start": float(_get(line, "start", default=0.0) or 0.0) - by,
        "end": float(_get(line, "end", default=0.0) or 0.0) - by,
        "words": [{"text": w.text, "start": w.start - by, "end": w.end - by} for w in words],
    }


def find_hook(query: str, settings: Settings, router=None, say=None, finder=None) -> Hook:
    """The song named `query` (a "|" and words of the part wanted may follow), found and
    its hook chosen and timed by the song finder (animate/songhook.py), as a Hook; `finder`
    stands in for it."""
    from agentic_editor.animate import songhook

    say = say or (lambda message: None)
    try:
        if finder is not None:
            found = _call(finder, query, settings)
        else:
            found = songhook.find(query, settings, router, say)
    except songhook.SongError as exc:
        raise SingerError(str(exc)) from exc
    if found is None:
        raise SingerError(f"no song found for {query!r}")
    # A Song names its sound by the songs cache's own file name.
    audio = songhook.audio_path(settings, found) if isinstance(found, Song) else None
    hook = hook_of(found, settings, audio)
    if len(hook.words) < 3:
        raise SingerError("the song's hook has no words to show (is it an instrumental part?)")
    return hook


def _call(finder, query: str, settings: Settings):
    try:
        return finder(query, settings=settings)
    except TypeError:
        return finder(query)


# ---- lines -----------------------------------------------------------------------------


def _plain(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.lower())


def sung_lines(hook: Hook) -> list[SungLine]:
    """The hook's lines, one a scene: as the finder knows them, else by the singer's
    breaths; a line sung very quickly joins the next, a long one is cut at its widest gap."""
    words = sorted(hook.words, key=lambda w: w.start)
    groups = _given_lines(hook.lines, words) or _by_breath(words)
    groups = _split_long(_join_short(groups))
    out = []
    for group in groups:
        text = " ".join(w.text for w in group)
        out.append(
            SungLine(
                text=text,
                start=round(group[0].start, 3),
                end=round(group[-1].end, 3),
                words=[
                    SungWord(text=w.text, start=round(w.start, 3), end=round(w.end, 3))
                    for w in group
                ],
            )
        )
    return out


def _given_lines(lines: list, words: list[Word]) -> list[list[Word]]:
    if not lines or not words:
        return []
    if all(isinstance(x, str) for x in lines):
        counts = [len(x.split()) for x in lines if x.split()]
        if sum(counts) != len(words):
            return []  # the lyric and the timed words disagree: breaths decide
        out, at = [], 0
        for n in counts:
            out.append(words[at : at + n])
            at += n
        return out
    out = []
    for line in lines:
        start = float(_get(line, "start", default=0.0) or 0.0)
        end = float(_get(line, "end", default=0.0) or 0.0)
        group = [w for w in words if start <= (w.start + w.end) / 2 < end]
        if group:
            out.append(group)
    placed = sum(len(g) for g in out)
    return out if placed >= 0.8 * len(words) else []


def _by_breath(words: list[Word]) -> list[list[Word]]:
    out: list[list[Word]] = []
    for w in words:
        if not out:
            out.append([w])
            continue
        line = out[-1]
        breath = w.start - line[-1].end >= GAP
        full = len(line) >= LINE_WORDS or w.end - line[0].start > LINE_SECONDS
        stop = bool(re.search(r"[.,!?;:]$", line[-1].text))
        if breath or full or stop:
            out.append([w])
        else:
            line.append(w)
    return out


def _join_short(groups: list[list[Word]]) -> list[list[Word]]:
    out: list[list[Word]] = []
    for group in groups:
        quick = out and (out[-1][-1].end - out[-1][0].start) < SHORT_LINE
        if quick and len(out[-1]) + len(group) <= LINE_WORDS + 2:
            out[-1] = out[-1] + group
            continue
        out.append(group)
    if len(out) > 1 and (out[-1][-1].end - out[-1][0].start) < SHORT_LINE * 0.6:
        out[-2] = out[-2] + out.pop()
    return out


def _split_long(groups: list[list[Word]]) -> list[list[Word]]:
    out: list[list[Word]] = []
    for group in groups:
        if group[-1].end - group[0].start > LONG_LINE and len(group) >= 4:
            middle = range(max(1, len(group) // 4), min(len(group) - 1, len(group) * 3 // 4) + 1)
            cut = max(middle, key=lambda i: group[i].start - group[i - 1].end)
            out += _split_long([group[:cut]]) + _split_long([group[cut:]])
        else:
            out.append(group)
    return out


# ---- the writer -------------------------------------------------------------------------

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "shot": {"type": "string", "enum": list(SHOTS)},
                    "art": {"type": "string"},
                    "highlight": {"type": "string"},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "doodle": {"type": "string", "enum": ["", *DOODLES]},
                },
                "required": ["line", "shot", "art", "highlight", "notes", "doodle"],
            },
        },
    },
    "required": ["title", "caption", "scenes"],
}

SHOT_GUIDE = (
    "hero: the singer big, from the waist up, standing in the place; "
    "close: the singer's face, close; "
    "far: the singer small, far off in a big place (alone, lost, a crowd); "
    "print: their photo as a torn print taped on a wall (memories, the past); "
    "mirror: they face their own reflection (talking to oneself, who am I); "
    "echo: three copies of them fading (many people, repeating, the crowd); "
    "silhouette: their dark outline against a bright light (hope, the sun, dreams); "
    "words: no singer, only the line's words, huge (one word that hits hard)"
)


def _prompt(hook: Hook, lines: list[SungLine], look: str, paints: bool, request: str = "",
            current: Plan | None = None, only: int | None = None) -> list[dict]:  # fmt: skip
    numbered = "\n".join(f"{i + 1}. {line.text}" for i, line in enumerate(lines))
    painted = (
        "; painted: the singer painted into the picture, seen from behind or the side "
        "(only where their face doesn't matter)"
        if paints and look
        else ""
    )
    system = (
        "You plan Instagram lyric edits for singers: the song's catchy part, one scene for each "
        "sung line, the singer's own photo cut out and standing in each scene, every sung word "
        "slapped on as a torn paper sticker the moment it is sung, the line's key words in red "
        "handwriting, small side notes and red doodles around them, on warm, gritty, cinematic "
        "pictures (amber light, deep brown shadows, film grain). You answer in JSON."
    )
    user = (
        f"Song: {hook.title or 'unknown'}"
        + (f" by {hook.artist}" if hook.artist else "")
        + "\n"
        + (f"The singer looks like: {look}\n" if look else "")
        + f"The sung lines (number. words as sung):\n{numbered}\n\n"
        "For each line give:\n"
        f"- line: its number\n- shot: how the singer is shown. {SHOT_GUIDE}{painted}.\n"
        "- art: the picture behind it, in English: one real place that shows what the line "
        "means (its feeling, not its words), the time and the light, e.g. 'a rain-soaked "
        "rooftop at night, city lights below'. Not the shot, the singer's pose or the words: "
        "those are drawn by themselves. "
        + ("For a painted shot say what the singer does in it. " if painted else "")
        + "Otherwise nobody is in it (the singer's photo is put in front). Never writing, signs, "
        "logos or names. Max 30 words.\n"
        "- highlight: ONE word of the line that carries it (two in a long line), copied exactly "
        "from the line; never the whole line (for 'mere jaisa nahi hai koi': 'jaisa').\n"
        "- notes: 0-3 tiny side notes, max 4 words each, in the song's own language and script, "
        "like thoughts, questions or taunts around the line (for 'kiska beta kya karta hai': "
        "'Kya achieve kiya?', 'Future plan kya hai?'). Most lines have none.\n"
        "- doodle: a red doodle by the words, or ''. Only 2-3 lines of the whole reel get one: "
        + " ".join(DOODLES)
        + "\n"
        "Also: title: the song and singer ('Song - Singer'); caption: the post's caption, a "
        "short line about the song's feeling in its own language, then 'Song: <song> by "
        "<singer>', then 5-8 hashtags.\n"
        "Rules: line 1 is hero or close (the reel opens on the singer). Never the same shot on "
        "two lines in a row. words at most twice. Every line gets a scene."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if current is not None:
        shown = [
            {
                "line": i + 1,
                "shot": s.visual.shot,
                "art": s.visual.art,
                "highlight": s.visual.highlight,
                "notes": [x.text for x in s.visual.items],
                "doodle": s.visual.emoji,
            }
            for i, s in enumerate(current.scenes)
        ]
        what = (
            f"Write line {only + 1}'s scene again"
            + (f": {request}" if request else " in a fresh way")
            + ". Give every line, the others as they are."
            if only is not None
            else f"Change it as asked: {request}"
        )
        messages.append(
            {"role": "user", "content": "The edit as it is now:\n"
             + json.dumps({"title": current.title, "caption": current.caption, "scenes": shown},
                          ensure_ascii=False)
             + f"\n\n{what}"}
        )  # fmt: skip
    return messages


def write(llm, hook: Hook, lines: list[SungLine], look: str, paints: bool, request: str = "",
          current: Plan | None = None, only: int | None = None) -> dict:  # fmt: skip
    """The writer's reply ({} when it gives none: the edit is then planned by rule)."""
    try:
        return llm.chat_json(
            _prompt(hook, lines, look, paints, request, current, only), WRITE_SCHEMA,
            temperature=0.5,
        )  # fmt: skip
    except Exception:
        log.exception("the writer gave no singer edit")
        return {}


def _words_of(text: str) -> list[str]:
    return [w for w in text.split() if _plain(w)]


# Words that carry nothing alone, never a line's key word by rule (Hinglish and English).
_SMALL_WORDS = (
    "a an the of to in on is am are be i you me my we it and or but so se ka ki ke ko hai hain "
    "ho hu hoon hun main mai mein me ne na ya to bhi jo wo woh ye yeh tu tum kya kyu kyun"
)
SMALL = frozenset(_SMALL_WORDS.split())


def _highlight(given: str, line: str) -> str:
    """The line's key words (one, two in a long line) as the line writes them: the
    writer's when it picked a few of the line's words, else the longest words that carry
    something (a writer that gives back the whole line has picked nothing)."""
    words = _words_of(line)
    have = {_plain(w): w for w in words}
    asked = [w for w in str(given).split() if _plain(w)]
    picked = list(dict.fromkeys(have[_plain(w)] for w in asked if _plain(w) in have))
    if picked and len(asked) <= 2 and len(picked) < len(words):
        return " ".join(picked[:2])
    content = [w for w in words if _plain(w) not in SMALL] or words
    if not content:
        return ""
    count = 2 if len(words) >= 6 and len(content) >= 2 else 1
    best = sorted(content, key=lambda w: -len(_plain(w)))[:count]
    return " ".join(dict.fromkeys(w for w in words if w in best))


def _notes(given) -> list[str]:
    out = []
    for note in given if isinstance(given, list) else []:
        note = " ".join(str(note).split())
        if note and len(note.split()) <= 6 and len(note) <= 40:
            out.append(note)
    return out[:3]


def _shots(wanted: list[str], allowed: set[str]) -> list[str]:
    """The writer's shots held to the rules: allowed here, the reel opening on the singer,
    never the same twice running (while another is allowed), words at most twice."""
    out: list[str] = []
    words = 0
    turn = 0
    opening = next((s for s in ("hero", "close", "print") if s in allowed), "words")

    def fits(shot: str) -> bool:
        return shot in allowed and not (shot == "words" and words >= 2)

    for i, shot in enumerate(wanted):
        if i == 0 and shot not in ("hero", "close"):
            shot = opening
        if not fits(shot) or (out and shot == out[-1]):
            shot = ""
            for _ in range(len(ROTATION)):
                pick = ROTATION[turn % len(ROTATION)]
                turn += 1
                if fits(pick) and (not out or pick != out[-1]):
                    shot = pick
                    break
            shot = shot or (out[-1] if out else opening)
        words += shot == "words"
        out.append(shot)
    return out


def allowed_shots(photos: list[SingerPhoto], look: str, paints: bool) -> set[str]:
    cut = any(p.cutout for p in photos)
    shots = set(PHOTO_SHOTS if cut else ("print",)) | {"words"}
    if paints and look:
        shots.add("painted")
    return shots


def plan_of(reply: dict, hook: Hook, lines: list[SungLine], edit: SingerEdit,
            paints: bool) -> Plan:  # fmt: skip
    """The Plan of a singer edit from the writer's reply, anything missing or wrong put
    right by rule, so a reply that is half there still makes a whole edit."""
    by_line: dict[int, dict] = {}
    for s in reply.get("scenes", []) if isinstance(reply.get("scenes"), list) else []:
        if isinstance(s, dict) and isinstance(s.get("line"), int) and 1 <= s["line"] <= len(lines):
            by_line.setdefault(s["line"] - 1, s)
    allowed = allowed_shots(edit.photos, edit.look, paints)
    shots = _shots([str(by_line.get(i, {}).get("shot", "")) for i in range(len(lines))], allowed)
    scenes = []
    doodled = False
    for i, line in enumerate(lines):
        given = by_line.get(i, {})
        doodle = str(given.get("doodle", ""))
        # a doodle on every line is noise: never two lines running
        doodle = "" if doodled else doodle
        doodled = doodle in DOODLES
        art = " ".join(str(given.get("art", "")).split())[:300] or FALLBACK_ART
        visual = Visual(
            kind="verse",
            text=line.text,
            highlight=_highlight(str(given.get("highlight", "")), line.text),
            items=[Item(text=n) for n in _notes(given.get("notes"))],
            emoji=doodle if doodle in DOODLES else "",
            shot=shots[i],
            art=art,
        )
        scenes.append(Scene(narration=line.text, visual=visual, hold=0))
    title = " ".join(str(reply.get("title", "")).split())[:70] or _title(hook)
    caption = str(reply.get("caption", "")).strip()[:2200] or _caption(hook)
    plan = Plan(
        title=title, kind="story", style=STYLE, voice="", mood="emotional", scenes=scenes,
        cta="", caption=caption, singer=edit.model_copy(update={"lines": lines}),
    )  # fmt: skip
    return tidy(plan)


def _title(hook: Hook) -> str:
    return " - ".join(p for p in (hook.title, hook.artist) if p) or "Singer edit"


def _caption(hook: Hook) -> str:
    tags = ["#lyrics", "#song", "#music", "#lyricsedit", "#reels"]
    artist_tag = re.sub(r"\W+", "", hook.artist.lower())
    if artist_tag:
        tags.insert(0, f"#{artist_tag}")
    who = f" by {hook.artist}" if hook.artist else ""
    return f"Song: {hook.title or 'this one'}{who}\n\n" + " ".join(tags)


FALLBACK_ART = (
    "an empty city street at dusk, warm street lights, long shadows, dust in the air, a lonely mood"
)


# ---- timeline, player and sound ----------------------------------------------------------


def timeline_of(plan: Plan) -> Timeline:
    """When each line's scene shows and each word lands: the song's own times."""
    edit = plan.singer or SingerEdit()
    lines = edit.lines
    duration = edit.duration or (lines[-1].end + 0.5 if lines else 1.0)
    scenes: list[SceneTime] = []
    for i, line in enumerate(lines):
        start = 0.0 if i == 0 else max(lines[i - 1].end, line.start - LEAD)
        if i + 1 < len(lines):
            end = max(start + 0.2, max(line.end, lines[i + 1].start - LEAD))
        else:
            end = duration
        words = [Word(text=w.text, start=w.start, end=w.end) for w in line.words]
        beats = [w.start for w in words]
        scenes.append(
            SceneTime("verse", round(start, 3), round(end, 3), line.start, line.end, words, beats)
        )
    for a, b in itertools.pairwise(scenes):  # no gap, no overlap between scenes
        a.end = b.start
    return Timeline(scenes, round(duration, 3))


def player_of(plan: Plan, timeline: Timeline) -> dict:
    """What player/singer.js draws (the shape timeline.player_data gives the other styles,
    with the singer's photos)."""
    word = lambda w: {"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}  # noqa: E731
    edit = plan.singer or SingerEdit()
    return {
        "style": STYLE,
        "look": plan.look,
        "title": plan.title,
        "duration": round(timeline.duration, 3),
        "header": {"start": 9999},
        "cast": [],
        "scenes": [
            {
                "kind": s.kind,
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "voiceStart": round(s.voice_start, 3),
                "voiceEnd": round(s.voice_end, 3),
                "beats": [round(b, 3) for b in s.beats],
                "talkFrom": None,
                "words": [word(w) for w in s.words],
                "visual": scene.visual.model_dump(),
            }
            for s, scene in zip(timeline.scenes, plan.scenes, strict=False)
        ],
        "captions": [],
        "singer": {
            "photos": [p.model_dump() for p in edit.photos],
            "title": edit.title,
            "artist": edit.artist,
        },
    }


def sound(plan: Plan, dest: Path, settings: Settings) -> Path:
    """The hook's own sound for the reel: a quick fade out at its end, at the presets'
    loudness."""
    from agentic_editor.render import audio

    edit = plan.singer
    if edit is None or not _AUDIO.fullmatch(edit.audio or ""):
        raise SingerError("the edit has no song")
    source = songs_dir(settings) / edit.audio
    if not source.exists():
        raise SingerError("the song's sound is gone from this machine; make the edit again")
    duration = edit.duration or _length(source)
    raw = dest.with_suffix(".raw.wav")
    fade = max(0.0, duration - FADE_OUT)
    fades = f"afade=t=in:d=0.02,afade=t=out:st={fade:.3f}:d={FADE_OUT}"
    ffmpeg.run([
        "-y", "-i", str(source), "-t", f"{duration:.3f}", "-af", fades, "-ac", "2",
        "-ar", str(audio.SAMPLE_RATE), "-c:a", "pcm_s16le", str(raw),
    ])  # fmt: skip
    audio.normalize_loudness(raw, dest, LOUDNESS, TRUE_PEAK)
    raw.unlink(missing_ok=True)
    return dest


_mixing = threading.Lock()


def preview_sound(plan: Plan, settings: Settings) -> Path:
    edit = plan.singer or SingerEdit()
    key = _key(edit.audio, edit.duration, LOUDNESS, 1)
    dest = settings.cache_dir / "animation-sound" / f"{key}.wav"
    if dest.exists():
        return dest
    with _mixing:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_suffix(".part.wav")
            sound(plan, part, settings)
            part.replace(dest)
    return dest


# ---- pictures ----------------------------------------------------------------------------


# How each shot paints the singer into its scene, where the likeness model is.
POSES = {
    "hero": "seen from the waist up, standing in the place, gazing past the camera",
    "close": "a close-up of their face and shoulders in three-quarter view, warm light on the face",
    "far": "a wide shot, the person small in the distance, seen from behind",
    "mirror": "standing before a tall old mirror, seen over the shoulder, their reflection "
    "looking back at them",
    "echo": "standing in a crowd of grey faceless people, the only one in colour",
    "silhouette": "seen from behind as a dark silhouette against a bright sunset, a glowing rim "
    "of light around them",
    "painted": "seen from behind or from the side",
}
REFERENCE_SIDE = 1024
REFERENCE_VERSION = 1


def likeness_prompt(shot: str, art: str, look: str = "") -> str:
    """What the likeness model paints for a scene with the singer in it; `look` (the
    vision model's words for them) settles what a face alone leaves open, a man or a
    woman, their age, their hair."""
    who = f" ({look.strip().rstrip('.')})" if look.strip() else ""
    return (
        f"The person from the reference photo{who}, {POSES.get(shot, POSES['hero'])}. "
        f"{place_of(art)}. Keep their face, hairstyle, headwear, skin tone, build "
        f"and clothes exactly as in the photo. {images.LOOKS[STYLE]}. {images.NEVER}"
    )


# A painter asked for "huge words" or "a torn photo" paints letters and a photo: the parts
# of a picture's description that name writing are left out of what it is asked.
WRITING = re.compile(
    r"\b(words?|letters?|text|typography|font|caption|title|sign|signs|signage|poster|logo|"
    r"writing|written|graffiti|photo|photograph|print|visible)\b",
    re.I,
)


def place_of(art: str) -> str:
    """The writer's picture for a line, without the parts that ask for writing or a
    photo; the plain fallback place when little is left."""
    kept = [
        part.strip()
        for part in re.split(r"[.,;]", art)
        if part.strip() and not WRITING.search(part)
    ]
    place = ", ".join(kept)
    return place if len(place.split()) >= 3 else FALLBACK_ART


def place_prompt(art: str) -> str:
    """What the likeness model paints for a scene without the singer (words, a print)."""
    return f"{place_of(art)}, {images.LOOKS[STYLE]}, {images.EMPTY}, {images.NEVER}"


def reference_of(photo: SingerPhoto, settings: Settings) -> Path:
    """The picture a scene paints the singer from: their cutout on plain grey, so nothing
    behind them in the photo is copied, else the (cleaned) photo itself; kept once."""
    from PIL import Image

    folder = images.folder(settings)
    if not photo.cutout or not (folder / photo.cutout).exists():
        return folder / photo.image
    if len(photo.face) == 4 and photo.face[3] > HEAD_ONLY:
        # the cutout is a face alone (a close portrait, a cover): the photo says more of
        # them (hair against the background, the neck and shoulders)
        return folder / photo.image
    dest = folder / (_key(photo.cutout, "singer-reference", REFERENCE_VERSION) + ".png")
    if not dest.exists():
        cut = Image.open(folder / photo.cutout).convert("RGBA")
        scale = min(1.0, REFERENCE_SIDE / (max(cut.size) * 1.12))
        cut = cut.resize((max(1, int(cut.width * scale)), max(1, int(cut.height * scale))))
        side_w, side_h = int(cut.width * 1.12) + 1, int(cut.height * 1.12) + 1
        canvas = Image.new("RGBA", (side_w, side_h), (128, 128, 128, 255))
        canvas.alpha_composite(cut, ((side_w - cut.width) // 2, (side_h - cut.height) // 2))
        part = dest.with_suffix(".part.png")
        canvas.convert("RGB").save(part)
        part.replace(dest)
    return dest


def _seed(prompt: str, again: int) -> int:
    return (int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16) + 7919 * again) % 2**31


def paint_likenesses(plan: Plan, settings: Settings, likeness, progress) -> tuple[Plan, list[str]]:
    """Every scene painted by the likeness model: the singer painted in from their photos
    (in turn) as the shot says, or the place alone for words and prints. A scene whose
    picture couldn't be painted shows the singer cut out, as without the model."""
    photos = [
        p
        for p in (plan.singer.photos if plan.singer else [])
        if (images.folder(settings) / p.image).exists()
    ]
    wanted = [(i, s) for i, s in enumerate(plan.scenes) if s.visual.art.strip()]
    pictures = []
    turn = 0
    for _, s in wanted:
        v = s.visual
        if v.shot in POSES and photos:
            reference = reference_of(photos[turn % len(photos)], settings)
            turn += 1
            prompt = likeness_prompt(v.shot, v.art, plan.singer.look if plan.singer else "")
            pictures.append(
                images.Picture(prompt, _seed(prompt, v.art_seed), refs=(str(reference),))
            )
        else:
            prompt = place_prompt(v.art)
            pictures.append(images.Picture(prompt, _seed(prompt, v.art_seed)))
    names = likeness(pictures, settings, progress) if pictures else []
    scenes = list(plan.scenes)
    for (i, scene), picture, name in zip(wanted, pictures, names, strict=True):
        with_singer = bool(name and picture.refs)
        face = painted_face(images.folder(settings) / name, settings) if with_singer else []
        visual = scene.visual.model_copy(
            update={"image": name, "with_singer": with_singer, "face": face}
        )
        scenes[i] = scene.model_copy(update={"visual": visual})
    missing = sum(1 for n in names if not n)
    note = (
        [f"{missing} of {len(names)} pictures couldn't be painted; those show the photo cut out"]
        if missing
        else []
    )
    return plan.model_copy(update={"scenes": scenes}), note


def painted_face(path: Path, settings: Settings, face_of=None) -> list[float]:
    """Where the singer's face is in a picture painted with them, [x, y, w, h], 0..1; []
    when none is seen (from behind, far off): the words then take the shot's usual place.
    The biggest face YuNet finds (the singer stands nearest; others in a crowd are small):
    MediaPipe's short-range finder misses a face a tenth of the picture wide."""
    from PIL import Image

    try:
        picture = Image.open(path).convert("RGB")
        found = (face_of or _yunet_face)(picture, settings)
    except Exception:
        log.info("no face looked for in %s", path, exc_info=True)
        return []
    if not found:
        return []
    x, y, w, h = found
    box = [x / picture.width, y / picture.height, w / picture.width, h / picture.height]
    return [round(min(1.0, max(0.0, v)), 4) for v in box]


def _yunet_face(picture, settings: Settings) -> list[float] | None:
    import numpy as np

    from agentic_editor.analysis import facecam

    pixels = np.asarray(picture)[None]
    found = facecam.faces(pixels, facecam.model(settings.models_dir))[0]
    return list(max(found, key=lambda b: b[2] * b[3])) if found else None


def paint_plan(
    plan: Plan, settings: Settings, paint, progress, likeness=None
) -> tuple[Plan, list[str]]:
    """Each scene's picture painted (kept ones reused). Where the likeness model is, the
    singer is painted into them from their photos; else nobody is in them (the photo cut
    out stands in front), but for a painted shot, painted from the vision model's words."""
    if likeness is None and paint is images.paint and images.identity_available(settings):
        likeness = images.paint_likeness
    if likeness is not None:
        return paint_likenesses(plan, settings, likeness, progress)
    wanted = [(i, s) for i, s in enumerate(plan.scenes) if s.visual.art.strip()]
    if not wanted:
        return plan, []
    if paint is images.paint and not images.available(settings):
        return plan, ["painted pictures need the image model (installed on the Mac); the "
                      "scenes are drawn from the photos"]  # fmt: skip
    look = plan.singer.look if plan.singer else ""
    pictures = []
    for _, s in wanted:
        painted = s.visual.shot == "painted"
        prompt = (
            f"{s.visual.art}, the person is {look}" if painted and look else place_of(s.visual.art)
        )
        seed = (int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
                + 7919 * s.visual.art_seed) % 2**31  # fmt: skip
        pictures.append(images.Picture(prompt, seed, empty=not painted))
    names = paint(pictures, STYLE, settings, progress)
    scenes = list(plan.scenes)
    for (i, scene), name in zip(wanted, names, strict=True):
        scenes[i] = scene.model_copy(
            update={
                "visual": scene.visual.model_copy(
                    update={"image": name, "with_singer": False, "face": []}
                )
            }
        )
    missing = sum(1 for n in names if not n)
    note = [f"{missing} of {len(names)} pictures couldn't be painted"] if missing else []
    return plan.model_copy(update={"scenes": scenes}), note


def _unpainted(plan: Plan) -> Plan:
    scenes = [
        s.model_copy(
            update={
                "visual": s.visual.model_copy(
                    update={"image": "", "with_singer": False, "face": []}
                )
            }
        )
        for s in plan.scenes
    ]
    return plan.model_copy(update={"scenes": scenes})


# ---- the studio's work -------------------------------------------------------------------


def skeleton(idea: str, photos: list[str]) -> dict:
    """What a new singer edit holds before it is made: the song's name and the photos
    (so a retry has them)."""
    edit = SingerEdit(query=idea, photos=[SingerPhoto(image=n) for n in photos])
    return Plan(title=idea, style=STYLE, kind="story", voice="", mood="emotional",
                cta="", singer=edit).model_dump(mode="json")  # fmt: skip


def edited(stored: dict | None, plan: Plan) -> Plan:
    """A singer edit changed by hand: what is sung, when, and the photos stay the studio's
    (one scene a line, in order); the rest (shots, key words, notes, pictures) is the
    person's."""
    before = Plan.model_validate(stored) if stored else None
    if before is None or before.singer is None or not before.scenes:
        raise ValueError("this singer edit isn't made yet")
    if len(plan.scenes) != len(before.scenes):
        raise ValueError("a singer edit has one scene for each sung line; scenes can't be "
                         "added, removed or moved")  # fmt: skip
    scenes = []
    for old, new in zip(before.scenes, plan.scenes, strict=True):
        visual = new.visual.model_copy(update={"kind": "verse", "text": old.visual.text})
        scenes.append(new.model_copy(update={"narration": old.narration, "visual": visual,
                                             "hold": 0}))  # fmt: skip
    plan = plan.model_copy(
        update={"style": STYLE, "scenes": scenes, "singer": before.singer, "cta": "", "voice": ""}
    )
    return tidy(plan)


def _set(Session: sessionmaker, animation_id: str, **values) -> None:
    with Session() as db:
        a = db.get(Animation, animation_id)
        if a is None:
            return
        for k, v in values.items():
            setattr(a, k, v)
        db.commit()


def run_task(
    animation_id: str, Session: sessionmaker, settings: Settings, router=None, paint=None,
    finder=None, cutter=None, likeness=None,
) -> None:  # fmt: skip
    """A singer edit's queued operation: make it (find the hook, cut out the photos,
    write), change it, write a scene again, or put it together after an edit by hand.
    Like every studio task, whatever happens is stored."""
    from agentic_editor.backends import get_backends
    from agentic_editor.router import ModelRouter

    try:
        with Session() as db:
            a = db.get(Animation, animation_id)
            if a is None:
                return
            task = dict(a.task or {"op": "draft"})
            wants_pictures = bool(a.images)
            idea = a.idea
            stored = a.plan
        current = Plan.model_validate(stored) if stored else None
        op = task.get("op", "draft")
        if current is None or current.singer is None or not current.scenes:
            op = "draft"  # a retry of an edit that was never made
        if op == "auto":
            raise SingerError("an autopilot can't make singer edits (they need photos)")
        paint = paint or images.paint
        if likeness is None and paint is images.paint and images.identity_available(settings):
            likeness = images.paint_likeness
        likeness = likeness if wants_pictures else None
        paints = wants_pictures and (
            likeness is not None or paint is not images.paint or images.available(settings)
        )
        say = lambda message: _set(Session, animation_id, message=message)  # noqa: E731
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        notes: list[str] = []
        if op == "draft":
            _set(Session, animation_id, status="drafting", message="finding the song", error=None)
            edit = current.singer if current and current.singer else SingerEdit(query=idea)
            hook = find_hook(edit.query or idea, settings, router, say, finder)
            say("cutting the singer out of the photos")
            photos, wiped = photos_of([p.image for p in edit.photos], settings, cutter)
            if wiped:
                notes.append(f"{wiped} watermark or stamped text marks painted out of the photos")
            if not photos:
                raise SingerError("the edit needs at least one photo of the singer")
            if not any(p.cutout for p in photos):
                notes.append("nobody stood out in the photos: they are shown whole, as prints")
            look = edit.look
            if paints and not look:
                say("looking at the singer")
                with router.vision() as llm:
                    # a whole person says more than a face alone (a cover's made-up face
                    # was taken for a woman's)
                    seen = next((p.image for p in photos if p.cutout), photos[0].image)
                    look = describe(llm, images.folder(settings) / seen)
            lines = sung_lines(hook)
            edit = edit.model_copy(update={
                "title": hook.title, "artist": hook.artist, "audio": hook.audio.name,
                "duration": hook.duration, "photos": photos, "look": look,
            })  # fmt: skip
            say(f"writing the edit ({len(lines)} lines)")
            with router.planner() as llm:
                reply = write(llm, hook, lines, look, paints)
            if not reply:
                notes.append("the writer didn't answer: shots, key words and pictures were "
                             "chosen by rule")  # fmt: skip
            plan = plan_of(reply, hook, lines, edit, paints)
        elif op in ("change", "scene"):
            assert current is not None and current.singer is not None
            _set(Session, animation_id, status="changing", message="changing it as asked",
                 error=None)  # fmt: skip
            edit = current.singer
            hook = Hook(songs_dir(settings) / edit.audio, edit.duration, edit.title, edit.artist,
                        [])  # fmt: skip
            only = int(task.get("scene", 0)) if op == "scene" else None
            with router.planner() as llm:
                reply = write(llm, hook, edit.lines, edit.look, paints,
                              task.get("request", ""), current, only)  # fmt: skip
            if not reply:
                raise SingerError("the writer didn't answer; try again")
            plan = plan_of(reply, hook, edit.lines, edit, paints)
            if only is not None:  # only that scene changes
                scenes = list(current.scenes)
                if 0 <= only < len(scenes):
                    scenes[only] = plan.scenes[only]
                plan = current.model_copy(update={"scenes": scenes})
            plan = plan.model_copy(update={"title": current.title if only is not None
                                           else plan.title})  # fmt: skip
        else:  # "voice": put it together as it is
            assert current is not None
            plan = tidy(current)
        if paints:
            _set(Session, animation_id, status="painting", message="painting the pictures")
            plan, painted = paint_plan(plan, settings, paint, lambda stage, m: say(m), likeness)
            notes += painted
        else:
            plan = _unpainted(plan)
        _set(Session, animation_id, status="voicing", message="putting it together")
        timeline = timeline_of(plan)
        data = player_of(plan, timeline)
        try:
            preview_sound(plan, settings)
        except Exception:
            log.exception("singer edit %s: no preview sound", animation_id)
        notes.append(f"{timeline.duration:.0f} s, {len(plan.scenes)} lines")
        with Session() as db:
            a = db.get(Animation, animation_id)
            if a is None:
                return
            if op != "voice" and a.plan and Plan.model_validate(a.plan).scenes:
                a.history = [a.plan, *(a.history or [])][:20]
            a.plan = plan.model_dump(mode="json")
            a.notes = notes
            a.player, a.status, a.message, a.task, a.error = data, "ready", "ready", None, None
            a.updated_at = datetime.now(UTC)
            db.commit()
    except Exception as exc:
        log.exception("singer edit %s failed", animation_id)
        _set(
            Session, animation_id, status="failed", task=None, message="failed",
            error=str(exc) if isinstance(exc, SingerError) else f"{type(exc).__name__}: {exc}",
        )  # fmt: skip


def render(plan: Plan, out_dir: Path, settings: Settings, progress=None):
    """The finished edit (MP4), its thumbnail and any text that didn't fit."""
    from agentic_editor import slots
    from agentic_editor.animate import render as drawing

    progress = progress or (lambda *a: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline = timeline_of(plan)
    data = player_of(plan, timeline)
    opts = drawing.options(settings.fonts_dir, settings.models_dir, images.folder(settings))
    progress("animate", "bringing the song to the reel's loudness")
    mixed = sound(plan, out_dir / "sound.wav", settings)
    silent = out_dir / "picture.mp4"
    with slots.encoder.turn("draw the singer edit", progress):
        problems = drawing.frames_to_video(data, opts, silent, drawing.FPS, progress)
    dest = out_dir / "animation.mp4"
    ffmpeg.run(["-y", "-i", str(silent), "-i", str(mixed), "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                "-movflags", "+faststart", str(dest)])  # fmt: skip
    silent.unlink(missing_ok=True)
    mixed.unlink(missing_ok=True)
    thumb = out_dir / "thumbnail.jpg"
    at = timeline.scenes[0].voice_end if timeline.scenes else 0.5
    ffmpeg.run(["-y", "-ss", f"{max(0.3, at):.2f}", "-i", str(dest), "-frames:v", "1",
                "-q:v", "3", str(thumb)])  # fmt: skip
    return dest, thumb, problems


def render_job(animation_id: str, plan: Plan, settings: Settings, progress, run_id: str):
    """studio.render_job for a singer edit: the video and its EDL as a pipeline.Result."""
    from agentic_editor import pipeline
    from agentic_editor.edl import EDL, Clip

    progress("title", plan.title or NAME)
    out_dir = settings.media_dir / "outputs" / f"animation-{animation_id}" / run_id
    video, thumb, problems = render(plan, out_dir, settings, progress)
    duration = timeline_of(plan).duration
    edit = plan.singer or SingerEdit()
    edl = EDL(
        clips=[Clip(start=0, end=round(duration, 3))],
        title=plan.title,
        crop_mode="none",
        decisions=[f"animation: {NAME}, {edit.title} - {edit.artist}, {len(plan.scenes)} lines"],
    )
    issues = [f"text shrunk to fit: {p}" for p in problems]
    output = pipeline.Output(
        f"animation-{STYLE}",
        video,
        edl,
        "animation",
        thumbnail=thumb,
        issues=issues,
        song=(edit.title, edit.artist) if edit.title and edit.artist else None,
    )
    progress("done", f"animation -> {video}")
    return pipeline.Result(plan.title or NAME, [output]), plan
