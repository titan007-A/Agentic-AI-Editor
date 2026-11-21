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


def name_of(prompt: str, seed: int, model: str) -> str:
    """The file a picture is kept in, by everything that makes it."""
    raw = json.dumps([model, STEPS, SIZE, prompt, seed], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24] + ".png"


def paint(
    pictures: list[Picture], style: str, settings: Settings, progress=None, run=subprocess.Popen
) -> list[str]:
    """The file names of `pictures`, painted where not kept already; "" for one that
    couldn't be (the scene is drawn without it)."""
    progress = progress or (lambda *a: None)
    out = folder(settings)
    out.mkdir(parents=True, exist_ok=True)
    names = [name_of(full_prompt(p, style), p.seed, settings.image_model) for p in pictures]
    todo = [
        {"prompt": full_prompt(p, style), "seed": p.seed, "dest": str(out / name)}
        for p, name in zip(pictures, names, strict=True)
        if p.prompt.strip() and not (out / name).exists()
    ]
    todo = list({job["dest"]: job for job in todo}.values())  # one painting per picture
    if todo:
        request = {
            "model": settings.image_model, "steps": STEPS, "width": SIZE[0], "height": SIZE[1],
            "jobs": todo,
        }  # fmt: skip
        with slots.gpu.turn("paint the pictures", progress):
            _run(request, len(todo), settings, progress, run)
    return [name if (out / name).exists() else "" for name in names]


def _run(request: dict, count: int, settings: Settings, progress, run) -> None:
    # The model's own chatter (progress bars) goes to a file: a pipe nobody reads fills up
    # and stops it.
    log_file = folder(settings) / "paint.log"
    with log_file.open("w", encoding="utf-8") as chatter:
        proc = run(
            [str(python_of(settings)), "-I", str(RUNNER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=chatter, text=True,
        )  # fmt: skip
        _follow(proc, request, count, progress)
    if proc.returncode:
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-500:]
        log.warning("painting failed (%s): %s", proc.returncode, tail)


def _follow(proc, request: dict, count: int, progress) -> None:
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
                progress("paint", f"painted {said['done']} of {count} pictures")
                log.info("picture %s of %s in %s s", said["done"], count, said.get("seconds"))
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
