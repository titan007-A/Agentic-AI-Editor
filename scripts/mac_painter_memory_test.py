"""How much memory the image painter takes on the Mac, and how fast it paints, over a whole
batch in one runner process, as the live code calls it.

Paints the first moments of a Song story (a Plan's JSON) once for each runner variant, each
variant into a folder of its own so nothing is taken from another's: FLUX.2 [klein] from the
people's portraits (made smaller, as songpaint does) and Z-Image from words. While a runner
paints, its memory is read every 2 s as top shows it (MEM, and CMPRS: compressed), with the
swap in use; each picture's seconds and, where the runner tells them, its own memory and peak
come from its lines. Then every variant's pictures are compared with the first's, pixel by
pixel (the same seed must give the same picture).

Variants: `main` is the runner of origin/main; `NAME=KEY:VALUE,...` is this checkout's with
its CACHE_LIMIT / CLEAR_EACH / FREE_TEXT_ENCODER set so; `branch` is this checkout's as it is.
Run it only through the polite runner:

    ~/aiedit-test/run-branch.sh painter BRANCH scripts/mac_painter_memory_test.py \\
        --plan ~/aiedit-test/faces/story2.json --likeness main branch --zimage main branch
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

MODEL = "mflux-community/flux2-klein-4b-mflux-q8"
REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "src" / "agentic_editor" / "animate" / "paint_runner.py"


class Lines(logging.Handler):
    """The painter's lines: each picture's seconds and memory, and the model's load."""

    def __init__(self):
        super().__init__()
        self.pictures: list[dict] = []
        self.loads: list[float] = []

    def emit(self, record):
        text = record.getMessage()
        m = re.match(r"painted \d+ of \d+ \w+, ([\d.]+) s(?:, ([\d.]+) GB \(most ([\d.]+) GB\))?",
                     text)  # fmt: skip
        if m:
            seconds, memory, peak = (float(g) if g else None for g in m.groups())
            self.pictures.append({"seconds": seconds, "memory_gb": memory, "peak_gb": peak})
        m = re.match(r"image model loaded in ([\d.]+) s", text)
        if m:
            self.loads.append(float(m.group(1)))


def gb(text: str) -> float:
    """top's 17G, 5123M, 800K as GB."""
    m = re.match(r"([\d.]+)([BKMGT])", text.strip())
    if not m:
        return 0.0
    return float(m.group(1)) * {"B": 1e-9, "K": 1e-6, "M": 1e-3, "G": 1.0, "T": 1e3}[m.group(2)]


def swap_used() -> float:
    out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    m = re.search(r"used = ([\d.]+)M", out)
    return round(float(m.group(1)) / 1024, 2) if m else 0.0


class Watch:
    """The runners this process starts, read every 2 s with top: the most MEM and CMPRS."""

    def __init__(self):
        self.mem = self.cmprs = 0.0
        self.swap = swap_used()
        self.swap_max = self.swap
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def _run(self):
        me = str(os.getpid())
        while not self._stop.wait(2):
            kids = subprocess.run(["pgrep", "-P", me], capture_output=True, text=True).stdout
            for pid in kids.split():
                top = subprocess.run(
                    ["top", "-l", "1", "-pid", pid, "-stats", "pid,mem,cmprs"],
                    capture_output=True, text=True,
                ).stdout.strip().splitlines()  # fmt: skip
                row = top[-1].split() if top else []
                if len(row) >= 3 and row[0] == pid:
                    self.mem = max(self.mem, gb(row[1]))
                    self.cmprs = max(self.cmprs, gb(row[2]))
            self.swap_max = max(self.swap_max, swap_used())


def variant(spec: str, folder: Path) -> tuple[str, Path]:
    """A runner file for a variant (see the module's doc)."""
    if spec == "branch":
        return spec, RUNNER
    folder.mkdir(parents=True, exist_ok=True)
    if spec == "main":
        text = subprocess.run(
            ["git", "-C", str(REPO), "show", f"origin/main:{RUNNER.relative_to(REPO)}"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        name = "main"
    else:
        name, _, sets = spec.partition("=")
        text = RUNNER.read_text()
        for item in filter(None, sets.split(",")):
            key, _, value = item.partition(":")
            text, n = re.subn(rf"^{key} = .*$", f"{key} = {value}", text, count=1, flags=re.M)
            if not n:
                sys.exit(f"the runner has no {key}")
    dest = folder / f"{name}.py"
    dest.write_text(text)
    return name, dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", type=Path, required=True, help="a Song story (a Plan's JSON)")
    ap.add_argument("--count", type=int, default=12, help="moments painted by the likeness model")
    ap.add_argument("--zcount", type=int, default=6, help="moments painted by Z-Image")
    ap.add_argument("--likeness", nargs="*", default=["main", "branch"], help="variants")
    ap.add_argument("--zimage", nargs="*", default=["main", "branch"], help="variants")
    ap.add_argument("--against", default="",
                    help="compare with this variant's pictures of an earlier run")  # fmt: skip
    args = ap.parse_args()
    os.environ.setdefault("IDENTITY_MODEL", MODEL)
    lines = Lines()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger().addHandler(lines)

    from agentic_editor.animate import images, songpaint, songwriter
    from agentic_editor.animate.plan import Plan, tidy
    from agentic_editor.settings import get_settings

    settings = get_settings()
    out = settings.cache_dir.parent / "painter-out"
    out.mkdir(parents=True, exist_ok=True)
    plan = tidy(Plan.model_validate_json(args.plan.expanduser().read_text()))
    base = plan.song.video
    seed = images.seed_of(base, 0)
    arts = list(dict.fromkeys(s.visual.art for s in plan.scenes if s.visual.art.strip()))

    # The people's portraits, painted once for every variant (with this checkout's runner).
    shared = settings.model_copy(update={"cache_dir": out / "portraits"})
    who = {art: songwriter.people_in(plan, art) for art in arts[: args.count]}
    cast = list({p.name: p for found in who.values() for p in found}.values())
    made = images.paint_likeness(
        [images.Picture(songpaint.portrait_prompt(p), images.seed_of(base, p.art_seed))
         for p in cast], shared, size=songpaint.PORTRAIT_SIZE, what="portraits",
    )  # fmt: skip
    folder = images.folder(shared)
    portraits = {p.name: name for p, name in zip(cast, made, strict=True) if name}
    likeness = [
        images.Picture(
            songpaint.moment_prompt(plan, art, who[art][: songpaint.MOST_REFS]), seed,
            refs=tuple(songpaint.reference_of(folder, portraits[p.name])
                       for p in who[art][: songpaint.MOST_REFS] if p.name in portraits),
        )
        for art in arts[: args.count]
    ]  # fmt: skip
    words = [images.Picture(songwriter.painted(plan, art), seed) for art in arts[: args.zcount]]

    results: dict = {"plan": str(args.plan), "portraits": len(portraits), "runs": []}
    for kind, specs, pictures in (("likeness", args.likeness, likeness),
                                  ("zimage", args.zimage, words)):  # fmt: skip
        first = None
        if args.against:  # an earlier run's pictures, when it painted them all
            there = settings.model_copy(update={"cache_dir": out / f"{kind}-{args.against}"})
            if kind == "likeness":
                kept = [images.likeness_name(p, settings.identity_model) for p in pictures]
            else:
                kept = [images.name_of(images.full_prompt(p, "song"), p.seed,
                                       settings.image_model) for p in pictures]  # fmt: skip
            if all((images.folder(there) / n).exists() for n in kept):
                first = (there, kept)
        for spec in specs:
            name, runner = variant(spec, out / "runners")
            here = settings.model_copy(update={"cache_dir": out / f"{kind}-{name}"})
            shutil.rmtree(images.folder(here), ignore_errors=True)  # painted afresh
            images.RUNNER, images.BATCH = runner, len(pictures)  # one runner for the batch
            lines.pictures.clear()
            lines.loads.clear()
            started = time.time()
            with Watch() as watch:
                if kind == "likeness":
                    names = images.paint_likeness(pictures, here, what="pictures")
                else:
                    names = images.paint(pictures, "song", here)
            run = {
                "kind": kind, "variant": name, "pictures": len(pictures),
                "painted": sum(1 for n in names if n), "all_s": round(time.time() - started, 1),
                "load_s": lines.loads, "seconds": [p["seconds"] for p in lines.pictures],
                "memory_gb": [p["memory_gb"] for p in lines.pictures],
                "peak_gb": max((p["peak_gb"] or 0 for p in lines.pictures), default=0),
                "top_mem_gb": round(watch.mem, 1), "top_cmprs_gb": round(watch.cmprs, 1),
                "swap_gb": [watch.swap, round(watch.swap_max, 2)],
            }  # fmt: skip
            if first is None:
                first = (here, names)
            else:
                run["same_as_first"] = same(images.folder(first[0]), images.folder(here),
                                            first[1], names)  # fmt: skip
            results["runs"].append(run)
            print(json.dumps(run), flush=True)
            (out / "memory.json").write_text(json.dumps(results, indent=1))
    print(f"saved {out / 'memory.json'}")


def same(a: Path, b: Path, names_a: list[str], names_b: list[str]) -> dict:
    """How far one variant's pictures are from the first's: the largest difference of any
    pixel's channel (0-255) and the share of pixels that differ at all."""
    import numpy as np
    from PIL import Image

    worst, differ = 0, []
    for x, y in zip(names_a, names_b, strict=True):
        if not x or not y:
            continue
        pa = np.asarray(Image.open(a / x).convert("RGB"), dtype=np.int16)
        pb = np.asarray(Image.open(b / y).convert("RGB"), dtype=np.int16)
        diff = np.abs(pa - pb)
        worst = max(worst, int(diff.max()))
        differ.append(float((diff.max(axis=2) > 0).mean()))
    return {"max_diff": worst, "share_differing": round(max(differ, default=0.0), 4)}


if __name__ == "__main__":
    main()
