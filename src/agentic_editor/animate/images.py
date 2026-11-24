"""Painted pictures for a reel's scenes: an image model behind the animation.

The planner writes what each scene's picture shows (Visual.art); here it is painted by
Z-Image-Turbo (Apache-2.0, 6B, 4-bit) through mflux, which runs on Apple Silicon in its
own environment (models/image-venv) so its pinned packages never meet the app's. The
model loads once per reel in paint_runner.py and takes the GPU slot like every other
model. Pictures are kept by what they show, so a preview, a change elsewhere and the
render never paint one twice. Where the model isn't installed (the PC, CI) nothing is
painted and the reel is drawn as before.
"""

import hashlib
import json
import logging
import platform
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from agentic_editor import slots
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

RUNNER = Path(__file__).parent / "paint_runner.py"
STEPS = 9
SIZE = (720, 1280)  # 9:16, shown at 1080x1920 (the player scales it to cover)
# A World map card's picture: the card's own 4:5 shape, sized for a card 376 px wide (a
# tour has thirty of them, so smaller paints faster). A gold horror card is a landscape
# photo, 4:3, shown 420 px wide.
CARD = (512, 640)
HORROR_CARD = (640, 480)
# Pictures painted in one turn of the GPU; between turns a job waiting for it goes first.
BATCH = 8
# The most the model may take to load, and then each picture, before the painting is given
# up (the scenes are drawn instead): a stuck model never holds the GPU for long.
LOAD_SECONDS = 150
SECONDS_EACH = 90
# What every picture of a style looks like, added to what the scene's picture shows.
LOOKS = {
    "story": "soft anime watercolor storybook background painting, hand-painted, warm gentle "
    "light, lush detail, calm colours",
    "glow": "cinematic dramatic digital painting, moody deep blue and teal light, volumetric "
    "light, highly detailed",
    "paper": "vintage editorial illustration, muted earthy colours, textured paper, finely "
    "detailed",
    # A World map card: the thing alone on white, so it sits in the white card like a cut-out.
    "map": "one single subject centred and filling most of the frame, isolated on a plain pure "
    "white background, bright clean studio product photo, vivid natural colours, soft shadow",
    # A gold horror card: the place itself, as a dark and eerie photograph.
    "map-horror": "a realistic photograph of the whole place, eerie and dark, dusk, fog, cold "
    "moonlight, moody desaturated colours, cinematic wide shot",
}
# Styles that are their own look: drawn, never painted.
UNPAINTED = ("stick", "kinetic")
NEVER = "no text, no letters, no words, no watermark, no logo"
EMPTY = "an empty scene with nobody in it, no people, no faces"


@dataclass
class Picture:
    prompt: str  # what the scene's picture shows (Visual.art)
    seed: int
    empty: bool = False  # characters stand in front of it: nobody painted in


def python_of(settings: Settings) -> Path:
    return settings.image_python or settings.models_dir / "image-venv" / "bin" / "python"


def available(settings: Settings) -> bool:
    """Whether pictures can be painted here: Apple Silicon with the model's environment."""
    return (
        sys.platform == "darwin" and platform.machine() == "arm64" and python_of(settings).exists()
    )


def folder(settings: Settings) -> Path:
    return settings.cache_dir / "animation-images"


def full_prompt(picture: Picture, style: str) -> str:
    parts = [picture.prompt.strip().rstrip("."), LOOKS.get(style, LOOKS["story"])]
    if picture.empty:
        parts.append(EMPTY)
    parts.append(NEVER)
    return ", ".join(p for p in parts if p)


def name_of(prompt: str, seed: int, model: str, size: tuple[int, int] = SIZE) -> str:
    """The file a picture is kept in, by everything that makes it."""
    raw = json.dumps([model, STEPS, list(size), prompt, seed], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24] + ".png"


def card_style(look: str) -> tuple[str, tuple[int, int]]:
    """The LOOKS entry and size a World map reel's cards are painted in, by its look."""
    return ("map-horror", HORROR_CARD) if look == "horror" else ("map", CARD)


def card_key(style: str, text: str, country: str, seed: int = 0) -> str:
    """A card picture's key across reels: what it shows by name (any case, accents or
    articles), its country, its look, and the number asked to paint it again."""
    import unicodedata

    def plain(value: str) -> str:
        flat = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
        words = re.findall(r"[a-z0-9]+", flat)
        return " ".join(w for w in words if w not in ("the", "a", "an"))

    return f"{style}|{plain(country)}|{plain(text)}|{int(seed or 0)}"


class CardIndex:
    """Card pictures painted for earlier reels, by card_key: a thing that comes again
    (the Eiffel Tower, sushi) is never painted twice, whatever words its picture was asked
    in this time. Kept next to the pictures (cards.json)."""

    _lock = threading.Lock()
    MAX = 20000

    def __init__(self, folder: Path):
        self.path = folder / "cards.json"
        self.folder = folder
        try:
            self.found = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.found = {}

    def get(self, key: str) -> str:
        name = self.found.get(key, "")
        return name if name and (self.folder / name).exists() else ""

    def put(self, key: str, name: str) -> None:
        self.found[key] = name

    def save(self) -> None:
        with CardIndex._lock:
            try:  # others may have added theirs meanwhile
                kept = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                kept = {}
            kept.update(self.found)
            while len(kept) > self.MAX:
                kept.pop(next(iter(kept)))
            self.folder.mkdir(parents=True, exist_ok=True)
            part = self.path.with_suffix(".part")
            part.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
            part.replace(self.path)


# A neon card's dish sits on the map itself: the white it was painted on is cut away.
WHITE = 236  # a pixel this light in every channel, joined to the border, is background
FEATHER = 1.2  # the edge's softness, in pixels


def cut_name(name: str) -> str:
    return hashlib.sha256(f"{name}|cut".encode()).hexdigest()[:24] + ".png"


def cut_out(settings: Settings, name: str) -> str:
    """The picture `name` with the white background around its subject made see-through
    (only white joined to the border: white inside the dish stays), kept once; "" when the
    picture isn't there."""
    import numpy as np
    from PIL import Image, ImageFilter
    from scipy import ndimage

    out = folder(settings)
    dest = out / cut_name(name)
    if dest.exists():
        return dest.name
    src = out / name
    if not src.exists():
        return ""
    picture = Image.open(src).convert("RGB")
    pixels = np.asarray(picture)
    light = (pixels >= WHITE).all(axis=2)
    parts, _ = ndimage.label(light)
    edge = set(np.unique(np.concatenate([parts[0], parts[-1], parts[:, 0], parts[:, -1]])))
    edge.discard(0)
    background = np.isin(parts, list(edge))
    alpha = Image.fromarray(np.where(background, 0, 255).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.GaussianBlur(FEATHER))
    cut = picture.convert("RGBA")
    cut.putalpha(alpha)
    part = dest.with_suffix(".part.png")
    cut.save(part)
    part.replace(dest)
    return dest.name


def paint(
    pictures: list[Picture],
    style: str,
    settings: Settings,
    progress=None,
    run=subprocess.Popen,
    size: tuple[int, int] = SIZE,
) -> list[str]:
    """The file names of `pictures`, painted where not kept already; "" for one that
    couldn't be (the scene is drawn without it)."""
    progress = progress or (lambda *a: None)
    out = folder(settings)
    out.mkdir(parents=True, exist_ok=True)
    names = [name_of(full_prompt(p, style), p.seed, settings.image_model, size) for p in pictures]
    todo = [
        {"prompt": full_prompt(p, style), "seed": p.seed, "dest": str(out / name)}
        for p, name in zip(pictures, names, strict=True)
        if p.prompt.strip() and not (out / name).exists()
    ]
    todo = list({job["dest"]: job for job in todo}.values())  # one painting per picture
    for start in range(0, len(todo), BATCH):
        part = todo[start : start + BATCH]
        request = {
            "model": settings.image_model, "steps": STEPS, "width": size[0], "height": size[1],
            "jobs": part,
        }  # fmt: skip
        with slots.gpu.turn("paint the pictures", progress):
            _run(request, len(part), settings, progress, run, (start, len(todo)))
    return [name if (out / name).exists() else "" for name in names]


def _run(
    request: dict, count: int, settings: Settings, progress, run, of: tuple[int, int] = (0, 0)
) -> None:
    # The model's own chatter (progress bars) goes to a file: a pipe nobody reads fills up
    # and stops it.
    log_file = folder(settings) / "paint.log"
    with log_file.open("w", encoding="utf-8") as chatter:
        proc = run(
            [str(python_of(settings)), "-I", str(RUNNER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=chatter, text=True,
        )  # fmt: skip
        _follow(proc, request, count, progress, of)
    if proc.returncode:
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-500:]
        log.warning("painting failed (%s): %s", proc.returncode, tail)


def _follow(proc, request: dict, count: int, progress, of: tuple[int, int] = (0, 0)) -> None:
    before, total = of[0], of[1] or count
    # A watchdog: reading the runner's lines blocks, so a stuck runner is killed on time.
    deadline = LOAD_SECONDS + SECONDS_EACH * count
    watchdog = threading.Timer(deadline, proc.kill)
    watchdog.daemon = True
    watchdog.start()
    try:
        proc.stdin.write(json.dumps(request))
        proc.stdin.close()
        for line in proc.stdout:
            try:
                said = json.loads(line)
            except ValueError:
                continue
            if "done" in said:
                done = before + said["done"]
                progress("paint", f"painted {done} of {total} pictures")
                log.info("picture %s of %s in %s s", done, total, said.get("seconds"))
            elif "loaded" in said:
                log.info("image model loaded in %s s", said["loaded"])
        proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        if not watchdog.is_alive() and proc.returncode not in (0, None):
            log.warning("painting took over %s s; the rest is drawn without pictures", deadline)
        watchdog.cancel()
