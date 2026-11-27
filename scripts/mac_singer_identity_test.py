"""Try the singer edit's likeness painter on the Mac before turning it on.

Paints three lines of a singer edit with the singer painted in from their photos
(FLUX.2 [klein] 4B through mflux), all in a test folder of its own: the model download,
the image model's environment (when the live one's mflux is too old), the pictures and the
frames. Nothing of the live app is changed. Prints the model's load time, the seconds each
picture took and the most memory the painter used, and saves:

    <folder>/out/sheet.jpg      the photo and the three paintings side by side
    <folder>/out/frame-N.jpg    each painting as the reel shows it, words and all

Run it from the repo, only while the live queue is empty, through the polite wrapper:

    .venv/bin/python run/polite.py scripts/mac_singer_identity_test.py \\
        --photos ~/Desktop/singer1.jpg ~/Desktop/singer2.jpg --folder ~/aiedit-test/identity

When the pictures look right, set IDENTITY_MODEL in .env to the model it used (printed at
the end) and restart the service. Unset, singer edits stand the cut-out photo in the scenes.
"""

import argparse
import json
import logging
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

MODEL = "mflux-community/flux2-klein-4b-mflux-q8"
MFLUX = "mflux==0.24.0"
LINES = [
    ("Mere jaisa nahi hai koi", "hero", "a crowded night street of an old city, neon signs off"),
    ("Khud se baatein kar sakta hu", "mirror", "a dim dressing room with one warm bulb"),
    ("Khud mein khush reh sakta hu", "far", "a rooftop over the city at sunset, a cat nearby"),
]


def environment(folder: Path, live_python: Path) -> Path:
    """An image model environment with mflux's FLUX.2: the live one when it has it, else a
    new one in the test folder (the live one is never upgraded here)."""
    check = "from mflux.models.flux2.variants import Flux2KleinEdit"
    if live_python.exists() and subprocess.run([str(live_python), "-c", check]).returncode == 0:
        print(f"using the live image environment ({live_python})")
        return live_python
    venv = folder / "image-venv"
    python = venv / "bin" / "python"
    if not python.exists():
        print(f"the live image environment has no FLUX.2: making {venv} with {MFLUX}")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-q", MFLUX], check=True)
    if subprocess.run([str(python), "-c", check]).returncode:
        sys.exit(f"{MFLUX} in {venv} has no Flux2KleinEdit")
    return python


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--photos", nargs="+", required=True, type=Path, help="photos of a singer")
    ap.add_argument("--folder", type=Path, default=Path.home() / "aiedit-test" / "identity")
    ap.add_argument("--model", default=MODEL, help="the likeness model (an mflux FLUX.2 klein 4B)")
    args = ap.parse_args()
    folder = args.folder.expanduser().resolve()
    out = folder / "out"
    out.mkdir(parents=True, exist_ok=True)
    # Everything the test fetches or makes stays in its folder.
    os.environ["HF_HOME"] = str(folder / "huggingface")
    os.environ["CACHE_DIR"] = str(folder / "cache")
    os.environ["MEDIA_DIR"] = str(folder / "media")
    os.environ["MODELS_DIR"] = str(folder / "models")
    # The app's own image environment: this checkout's, else the live app's (a test
    # checkout has no models folder of its own).
    repo = Path(__file__).resolve().parents[1]
    live_python = next(
        (
            p
            for p in (
                repo / "models" / "image-venv" / "bin" / "python",
                Path.home() / "Agentic-AI-Editor" / "models" / "image-venv" / "bin" / "python",
            )
            if p.exists()
        ),
        repo / "models" / "image-venv" / "bin" / "python",
    )
    os.environ["IMAGE_PYTHON"] = str(environment(folder, live_python))
    os.environ["IDENTITY_MODEL"] = args.model
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)

    from PIL import Image

    from agentic_editor.animate import images, singer
    from agentic_editor.animate import render as drawing
    from agentic_editor.animate.plan import SingerEdit
    from agentic_editor.settings import get_settings
    from agentic_editor.transcript import Word

    settings = get_settings()
    if not images.identity_available(settings):
        sys.exit("the likeness painter isn't available here (Apple Silicon and IMAGE_PYTHON)")

    started = time.time()
    # as the upload does: taken in (any kind, a small one enlarged), then made ready
    names = []
    for path in args.photos:
        taken = singer.take_photo(path.expanduser().read_bytes(), settings)
        ready, done = singer.prepare_photo(taken.name, settings)
        print(f"{path.name}: {'; '.join(taken.notes + done) or 'used as sent'}")
        names.append(ready)
    photos, wiped = singer.photos_of(names, settings)
    print(f"photos ready in {time.time() - started:.1f} s ({wiped} marks painted out)")
    from agentic_editor.router import ModelRouter

    # the vision model alone: no transcriber or encoder is checked for it
    with ModelRouter(settings, None, lambda *a: None).vision() as llm:
        look = singer.describe(llm, images.folder(settings) / photos[0].image)
    print(f"the singer, as the vision model sees them: {look or '(nothing said)'}")

    words, at = [], 0.3
    for text, _, _ in LINES:
        for word in text.split():
            words.append(Word(text=word, start=round(at, 2), end=round(at + 0.3, 2)))
            at += 0.34
        at += 0.6
    hook = singer.Hook(Path("/dev/null"), round(at + 0.5, 2), "Test", "Singer", words,
                       [t for t, _, _ in LINES])  # fmt: skip
    reply = {"title": "Test", "caption": "test", "scenes": [
        {"line": i + 1, "shot": shot, "art": art, "highlight": "", "notes": [], "doodle": ""}
        for i, (_, shot, art) in enumerate(LINES)
    ]}  # fmt: skip
    edit = SingerEdit(
        title="Test", artist="Singer", duration=hook.duration, photos=photos, look=look
    )
    plan = singer.plan_of(reply, hook, singer.sung_lines(hook), edit, paints=True)

    painting = time.time()
    plan, notes = singer.paint_plan(plan, settings, images.paint, lambda *a: print(*a))
    took = time.time() - painting
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1e9  # bytes on macOS
    painted = [s.visual.image for s in plan.scenes]
    print(json.dumps({
        "model": args.model, "pictures": len(painted), "painted": sum(1 for n in painted if n),
        "seconds_all": round(took, 1), "seconds_each": round(took / max(1, len(painted)), 1),
        "painter_peak_memory_gb": round(peak, 1), "notes": notes,
    }, indent=1))  # fmt: skip
    print(
        f"(per picture and the load time: the 'picture N of M' and 'loaded' lines above; "
        f"the runner's own log is {images.folder(settings) / 'paint.log'})"
    )

    folder_images = images.folder(settings)
    tiles = [Image.open(folder_images / photos[0].image).convert("RGB")]
    tiles += [Image.open(folder_images / n).convert("RGB") for n in painted if n]
    height = 640
    tiles = [t.resize((int(t.width * height / t.height), height)) for t in tiles]
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 10 * len(tiles), height), "white")
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.width + 10
    sheet.save(out / "sheet.jpg", quality=90)

    timeline = singer.timeline_of(plan)
    data = singer.player_of(plan, timeline)
    opts = drawing.options(settings.fonts_dir, settings.models_dir, folder_images)
    for i, scene in enumerate(timeline.scenes):
        drawing.frame(data, opts, round(scene.end - 0.1, 2), out / f"frame-{i + 1}.jpg")
    print(f"saved {out / 'sheet.jpg'} and {len(timeline.scenes)} frames in {out}")
    print(f"if they look right: IDENTITY_MODEL={args.model} in .env, then restart the service")


if __name__ == "__main__":
    main()
