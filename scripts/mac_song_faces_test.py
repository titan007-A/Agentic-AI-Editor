"""Paint one Song story both ways on the Mac: its moments from words alone (Z-Image, as
before) and its people as portraits with every moment painted from them (the likeness
model), and save the two side by side.

The story is found and written as the studio does (the song's name -> its part -> the
writer), in a test folder of its own: the song, the pictures and the sheet never touch the
live app. Prints the seconds of each step and picture and the painter's peak memory, and
saves:

    <folder>/out/sheet.jpg     the portraits, then each moment before | after
    <folder>/out/refs.jpg      one moment painted from 1, 2, 3... portraits (--probe)
    <folder>/out/times.json    every time measured, and the story's people and pictures

Run it only through the polite runner (it waits for an empty live queue and gives way to
live work):

    ~/aiedit-test/run-branch.sh faces song-same-faces scripts/mac_song_faces_test.py \\
        --song "Some Song Some Singer" --probe

--plan paints a story written elsewhere (a Plan's JSON) instead of finding and writing one.
"""

import argparse
import json
import logging
import os
import re
import resource
import sys
import time
from pathlib import Path

MODEL = "mflux-community/flux2-klein-4b-mflux-q8"


class Times(logging.Handler):
    """The painter's own lines: each picture's seconds and each model load, in order."""

    def __init__(self):
        super().__init__()
        self.pictures: list[tuple[str, float]] = []
        self.loads: list[float] = []

    def emit(self, record):
        text = record.getMessage()
        painted = re.match(r"painted \d+ of \d+ (\w+), ([\d.]+) s", text)
        if painted:
            self.pictures.append((painted.group(1), float(painted.group(2))))
        loaded = re.match(r"image model loaded in ([\d.]+) s", text)
        if loaded:
            self.loads.append(float(loaded.group(1)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--song", help="the song's name, as typed in the studio")
    ap.add_argument("--plan", type=Path, help="a story written already (a Plan's JSON)")
    ap.add_argument("--folder", type=Path, default=None, help="default: CACHE_DIR/../faces-out")
    ap.add_argument("--model", default=MODEL, help="the likeness model (FLUX.2 klein 4B)")
    ap.add_argument("--probe", action="store_true", help="one moment from 1..4 portraits")
    ap.add_argument("--turn", type=int, default=1,
                    help="pictures a GPU turn (live work waits for at most one turn)")  # fmt: skip
    args = ap.parse_args()
    if not args.song and not args.plan:
        ap.error("give --song or --plan")
    os.environ["IDENTITY_MODEL"] = args.model
    times = Times()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger().addHandler(times)

    from agentic_editor.animate import images, planner, songhook, songpaint, songwriter
    from agentic_editor.animate.plan import Plan, tidy
    from agentic_editor.backends import get_backends
    from agentic_editor.router import ModelRouter
    from agentic_editor.settings import get_settings

    images.BATCH = max(1, args.turn)  # a live job waits a picture or so, never a reel
    settings = get_settings()
    folder = (args.folder or settings.cache_dir.parent / "faces-out").expanduser().resolve()
    out = folder / "out"
    out.mkdir(parents=True, exist_ok=True)
    if not images.identity_available(settings):
        sys.exit("the likeness painter isn't available here (Apple Silicon, the image model)")
    measured: dict = {"song": args.song or str(args.plan), "model": args.model}

    if args.plan:
        plan = tidy(Plan.model_validate_json(args.plan.expanduser().read_text()))
    else:
        started = time.time()
        router = ModelRouter(settings, get_backends(), lambda *a: None)
        song = songhook.find(args.song, settings, router, lambda m: print(f"  {m}", flush=True))
        measured["find_s"] = round(time.time() - started, 1)
        started = time.time()
        with router.planner() as llm:
            planner.roomy(llm)
            plan, wrote = songwriter.draft(llm, song)
        plan = tidy(plan)
        measured["write_s"] = round(time.time() - started, 1)
        measured["writer_notes"] = wrote
    measured["title"] = plan.song.title
    measured["artist"] = plan.song.artist
    measured["part"] = [round(plan.song.start, 1), round(plan.song.end, 1)]
    measured["moments"] = len(plan.scenes)
    measured["pictures"] = len({s.visual.art for s in plan.scenes})
    print(f"story written: {len(plan.scenes)} moments, {len(plan.song.people)} people", flush=True)

    def say(stage, message):
        print(f"  {message}", flush=True)

    asked: list[list[int]] = []

    def likeness(pictures, settings_, progress, **kw):
        asked.append([len(p.refs) for p in pictures])
        return images.paint_likeness(pictures, settings_, progress, **kw)

    # after: portraits, then every moment from them
    times.pictures.clear()
    times.loads.clear()
    started = time.time()
    after, after_notes = songpaint.paint(plan, settings, images.paint, likeness, say)
    measured["after_s"] = round(time.time() - started, 1)
    measured["after_notes"] = after_notes
    measured["after_loads_s"] = list(times.loads)
    portraits = [s for what, s in times.pictures if what == "portraits"]
    moments = [s for what, s in times.pictures if what == "pictures"]
    measured["portrait_s"] = portraits
    measured["moment_s"] = moments
    refs = [n for call in asked[1:] for n in call] if len(asked) > 1 else []
    by_refs: dict[int, list[float]] = {}
    for n, s in zip(refs, moments, strict=False):
        by_refs.setdefault(n, []).append(s)
    measured["moment_s_by_refs"] = {
        str(n): round(sum(v) / len(v), 1) for n, v in sorted(by_refs.items())
    }
    measured["painter_peak_gb"] = round(
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1e9, 1
    )

    # before: each moment from words alone, as today (Z-Image)
    times.pictures.clear()
    times.loads.clear()
    started = time.time()
    before, before_notes = songpaint.paint(plan, settings, images.paint, None, say)
    measured["before_s"] = round(time.time() - started, 1)
    measured["before_notes"] = before_notes
    measured["before_loads_s"] = list(times.loads)
    measured["before_moment_s"] = [s for _, s in times.pictures]
    measured["painter_peak_gb_all"] = round(
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1e9, 1
    )

    people = {p.name: p for p in after.song.people}
    measured["people"] = [{"name": p.name, "look": p.look, "portrait": p.image}
                          for p in after.song.people]  # fmt: skip
    measured["scenes"] = [
        {"art": s.visual.art, "who": [p.name for p in songwriter.people_in(plan, s.visual.art)],
         "before": b.visual.image, "after": a.visual.image}
        for s, b, a in zip(plan.scenes, before.scenes, after.scenes, strict=True)
    ]  # fmt: skip
    sheet(out / "sheet.jpg", images.folder(settings), list(people.values()), measured["scenes"],
          settings.fonts_dir / "Inter-Bold.ttf")  # fmt: skip

    if args.probe:
        probe(plan, after, settings, out, measured, times)
    (out / "times.json").write_text(json.dumps(measured, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in measured.items() if k not in ("scenes", "people")},
                     indent=1))  # fmt: skip
    print(f"saved {out}")


def probe(plan, after, settings, out: Path, measured: dict, times: Times) -> None:
    """The moment with the most people in it, painted from words, then from 1, 2, 3... of
    their portraits (as moments are, made smaller), and from all of them whole: what each
    costs and keeps."""
    from PIL import Image

    from agentic_editor.animate import images, songpaint, songwriter

    folder = images.folder(settings)
    portraits = {p.name: p.image for p in after.song.people if p.image}
    best = max(
        plan.scenes,
        key=lambda s: len(
            [p for p in songwriter.people_in(plan, s.visual.art) if p.name in portraits]
        ),
    )
    who = [p for p in songwriter.people_in(plan, best.visual.art) if p.name in portraits]
    if len(who) < 2:
        print("no moment has two people with portraits: no probe")
        return
    seed = images.seed_of(plan.song.video, best.visual.art_seed)
    pictures = [images.Picture(songpaint.moment_prompt(plan, best.visual.art, []), seed + 1)]
    asked = ["words"]
    for n in range(1, min(4, len(who)) + 1):
        refs = who[:n]
        small = tuple(songpaint.reference_of(folder, portraits[p.name]) for p in refs)
        prompt = songpaint.moment_prompt(plan, best.visual.art, refs)
        pictures.append(images.Picture(prompt, seed + 1, refs=small))
        asked.append(f"{n} small")
    pictures.append(images.Picture(
        songpaint.moment_prompt(plan, best.visual.art, who[:4]), seed + 1,
        refs=tuple(str(folder / portraits[p.name]) for p in who[:4])))  # fmt: skip
    asked.append(f"{min(4, len(who))} whole")
    times.pictures.clear()
    names = images.paint_likeness(pictures, settings, lambda *a: None)
    measured["probe"] = {
        "who": [p.name for p in who],
        "asked": asked,
        "seconds": [s for _, s in times.pictures],
        "pictures": names,
    }
    tiles = [Image.open(folder / portraits[p.name]).convert("RGB") for p in who]
    tiles += [Image.open(folder / n).convert("RGB") for n in names if n]
    height = 640
    tiles = [t.resize((int(t.width * height / t.height), height)) for t in tiles]
    board = Image.new("RGB", (sum(t.width + 12 for t in tiles), height), "white")
    x = 0
    for t in tiles:
        board.paste(t, (x, 0))
        x += t.width + 12
    board.save(out / "refs.jpg", quality=88)


def sheet(dest: Path, folder: Path, people: list, scenes: list[dict], font_file: Path) -> None:
    """The portraits along the top, then each moment as before | after, four to a row."""
    from PIL import Image, ImageDraw, ImageFont

    w, h, gap, label = 216, 384, 14, 34
    try:
        font = ImageFont.truetype(str(font_file), 20)
    except OSError:
        font = ImageFont.load_default()

    def tile(name: str, size: tuple[int, int]) -> Image.Image:
        if name and (folder / name).exists():
            return Image.open(folder / name).convert("RGB").resize(size)
        return Image.new("RGB", size, (60, 60, 60))

    per_row = 4
    rows = (len(scenes) + per_row - 1) // per_row
    width = per_row * (2 * w + 3 * gap)
    pw, ph = int(h * 576 / 1024), h
    height = (ph + label + gap) + rows * (h + label + gap) + gap
    board = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(board)
    x = gap
    for p in people:
        board.paste(tile(p.image, (pw, ph)), (x, gap))
        draw.text((x, gap + ph + 4), p.name[:18], fill="black", font=font)
        x += pw + gap
    y0 = ph + label + 2 * gap
    for k, s in enumerate(scenes):
        col, row = k % per_row, k // per_row
        x = gap + col * (2 * w + 3 * gap)
        y = y0 + row * (h + label + gap)
        board.paste(tile(s["before"], (w, h)), (x, y))
        board.paste(tile(s["after"], (w, h)), (x + w + gap // 2, y))
        who = ", ".join(s["who"]) or "nobody"
        draw.text((x, y + h + 4), f"{k + 1}: {who}"[:40], fill="black", font=font)
    draw.text((width - 520, 4), "each moment: before (words) | after (portraits)",
              fill="black", font=font)  # fmt: skip
    board.save(dest, quality=88)


if __name__ == "__main__":
    main()
