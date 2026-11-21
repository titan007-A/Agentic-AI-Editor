"""Frames from the player in headless Chromium, sound from sound.py, one MP4.

The player (player/player.js) draws any frame from the time alone, so the frames are
split into ranges, each drawn by its own page and encoded to its own file, and the files
are joined: the pages work at once. The browser is Playwright's Chromium (`playwright
install chromium` once per machine; ANIMATION_CHROMIUM names another).
"""

import asyncio
import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path

from agentic_editor import ffmpeg, slots
from agentic_editor.animate import sound
from agentic_editor.animate.plan import Plan
from agentic_editor.animate.timeline import Timeline, player_data
from agentic_editor.render import fonts
from agentic_editor.settings import get_settings

log = logging.getLogger(__name__)

PLAYER = Path(__file__).parent / "player"
FPS = 30
SIZE = (1080, 1920)
CRF = "17"
# Frames leave the browser as JPEG: half the time of PNG, and at this quality nothing the
# H.264 encode after it keeps (both are 4:2:0).
JPEG_QUALITY = 95


class RenderError(RuntimeError):
    pass


def emoji_font(models_dir: Path) -> Path:
    return models_dir / "fonts" / fonts.COLOR_EMOJI


def chromium() -> str | None:
    """The browser to draw with: ANIMATION_CHROMIUM if set, else Playwright's own."""
    return os.environ.get("ANIMATION_CHROMIUM") or None


def workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 3))


def options(fonts_dir: Path, models_dir: Path, images_dir: Path | None = None) -> dict:
    """player.js load() options for a page opened from disk. The colour emoji font is the
    captions' (fetched the first time); without it the browser's own emoji are drawn."""
    found = fonts.colored(models_dir / "fonts")
    return {
        "fontBase": fonts_dir.resolve().as_uri() + "/",
        "emojiFont": found.resolve().as_uri() if found else None,
        "imageBase": images_dir.resolve().as_uri() + "/" if images_dir else None,
    }


def _encoder(dest: Path, fps: int) -> subprocess.Popen:
    settings = get_settings()
    cmd = [
        settings.ffmpeg_bin, "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps),
        "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
        "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart", str(dest),
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


async def _range(
    browser, data: dict, opts: dict, frames: range, dest: Path, fps: int, done
) -> list[str]:
    page = await browser.new_page(
        viewport={"width": SIZE[0], "height": SIZE[1]}, device_scale_factor=1
    )
    try:
        await page.goto((PLAYER / "player.html").as_uri())
        info = await page.evaluate("([d, o]) => window.Player.load(d, o)", [data, opts])
        encoder = _encoder(dest, fps)
        try:
            for f in frames:
                await page.evaluate("t => window.Player.seek(t)", f / fps)
                shot = await page.screenshot(type="jpeg", quality=JPEG_QUALITY)
                await asyncio.to_thread(encoder.stdin.write, shot)
                done()
        finally:
            encoder.stdin.close()
            err = (await asyncio.to_thread(encoder.stderr.read)).decode(errors="replace")
            code = await asyncio.to_thread(encoder.wait)
        if code:
            raise RenderError(f"encoding frames failed: {err[-400:]}")
        return info.get("problems", [])
    finally:
        await page.close()


async def _frames(
    data: dict, opts: dict, dest_dir: Path, fps: int, count: int, progress
) -> tuple[list[Path], list[str]]:
    from playwright.async_api import async_playwright

    total = max(1, math.ceil(data["duration"] * fps))
    n = max(1, min(count, total // (fps * 2) or 1))
    size = math.ceil(total / n)
    parts = [dest_dir / f"part{i:02d}.mp4" for i in range(n)]
    made = [0]

    def done() -> None:
        made[0] += 1
        if made[0] % (fps * 2) == 0 or made[0] == total:
            progress("animate", f"drew {made[0]} of {total} frames")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=chromium(), args=["--disable-lcd-text", "--font-render-hinting=none"]
        )
        try:
            results = await asyncio.gather(
                *(
                    _range(
                        browser,
                        data,
                        opts,
                        range(i * size, min(total, (i + 1) * size)),
                        parts[i],
                        fps,
                        done,
                    )
                    for i in range(n)
                )
            )
        finally:
            await browser.close()
    problems = sorted({p for r in results for p in r})
    return parts, problems


def frames_to_video(data: dict, opts: dict, dest: Path, fps: int = FPS, progress=None) -> list[str]:
    """The player's frames for `data` as a silent H.264 video at `dest`; returns the texts
    that had to shrink past their smallest size (player.js fit)."""
    progress = progress or (lambda *a: None)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        parts, problems = asyncio.run(_frames(data, opts, Path(tmp), fps, workers(), progress))
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        ffmpeg.run(
            ["-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)]
        )
    return problems


def frame(data: dict, opts: dict, t: float, dest: Path) -> Path:
    """One frame as a picture (thumbnails, tests)."""

    async def shoot() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=chromium(), args=["--disable-lcd-text"]
            )
            page = await browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]})
            await page.goto((PLAYER / "player.html").as_uri())
            await page.evaluate("([d, o]) => window.Player.load(d, o)", [data, opts])
            await page.evaluate("t => window.Player.seek(t)", t)
            await page.screenshot(path=str(dest), type="jpeg", quality=90)
            await browser.close()

    asyncio.run(shoot())
    return dest


def render(
    plan: Plan,
    timeline: Timeline,
    voices: list[Path],
    out_dir: Path,
    fonts_dir: Path,
    models_dir: Path,
    progress=None,
    images_dir: Path | None = None,
) -> tuple[Path, Path, list[str]]:
    """The finished reel (MP4), its thumbnail and any text that didn't fit."""
    progress = progress or (lambda *a: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = player_data(plan, timeline)
    opts = options(fonts_dir, models_dir, images_dir)
    progress("animate", "mixing the voice, music and sound effects")
    mixed = sound.mix(timeline, voices, plan.mood, out_dir / "sound.wav", seed=seed_of(plan.title))
    silent = out_dir / "picture.mp4"
    # Drawing and encoding take every core: one at a time with the other jobs' encodes.
    with slots.encoder.turn("draw the animation", progress):
        problems = frames_to_video(data, opts, silent, FPS, progress)
    dest = out_dir / "animation.mp4"
    ffmpeg.run([
        "-y", "-i", str(silent), "-i", str(mixed), "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(dest),
    ])  # fmt: skip
    silent.unlink(missing_ok=True)
    mixed.unlink(missing_ok=True)
    hook_end = timeline.scenes[0].end - 0.2 if timeline.scenes else 1.0
    thumb = out_dir / "thumbnail.jpg"
    ffmpeg.run(
        [
            "-y",
            "-ss",
            f"{max(0.5, hook_end):.2f}",
            "-i",
            str(dest),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumb),
        ]
    )
    return dest, thumb, problems


def seed_of(text: str) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(text)) % 100000 or 1
