"""Real frames: the player in headless Chromium, encoded by ffmpeg. Skipped where there is
no browser (`playwright install chromium`) or no ffmpeg."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agentic_editor.animate import render
from agentic_editor.animate.timeline import build, player_data
from agentic_editor.settings import ROOT
from tests.test_animate import sample_plan, story_plan, tone_wav, voices_for


def _browser_works() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    async def launch() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=render.chromium())
            await browser.close()

    try:
        asyncio.run(launch())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _browser_works(), reason="needs Chromium (playwright install chromium) and ffmpeg"
)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,nb_frames:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def frame_at(video: Path, t: float, dest: Path) -> Image.Image:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
    )
    return Image.open(dest).convert("RGB")


@pytest.mark.parametrize("style", ["glow", "paper", "story", "kinetic"])
def test_the_video_is_the_preview_frame_for_frame(tmp_path, style):
    if style == "story":  # characters acting
        plan = story_plan(scenes=[s.model_dump() for s in story_plan().scenes[1:3]])
    else:
        plan = sample_plan(style=style, scenes=[s.model_dump() for s in sample_plan().scenes[:2]])
    data = player_data(plan, build(plan, voices_for(plan)))
    data["duration"] = 3.0  # the first scenes are enough
    opts = render.options(ROOT / "fonts", ROOT / "models")
    video = tmp_path / "picture.mp4"
    problems = render.frames_to_video(data, opts, video, fps=10)
    assert problems == []
    info = probe(video)
    (stream,) = info["streams"]
    assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 1080, 1920)
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    for t in (0.55, 2.05):  # mid-animation, and the next scene's way in
        shown = Image.open(render.frame(data, opts, t, tmp_path / f"shown-{t}.jpg")).convert("RGB")
        made = frame_at(video, t, tmp_path / f"made-{t}.png")
        diff = np.abs(np.asarray(shown, dtype=float) - np.asarray(made, dtype=float)).mean()
        assert diff < 4.0, f"frame at {t}s differs from the preview by {diff:.1f}"


def test_a_whole_reel_has_picture_sound_and_a_thumbnail(tmp_path):
    plan = sample_plan(scenes=[s.model_dump() for s in sample_plan().scenes[:2]])
    voices = voices_for(plan)
    tl = build(plan, voices)
    files = [tone_wav(tmp_path / f"v{i}.wav", v.duration) for i, v in enumerate(voices)]
    video, thumb, problems = render.render(
        plan, tl, files, tmp_path / "out", ROOT / "fonts", ROOT / "models"
    )
    kinds = {s["codec_type"]: s["codec_name"] for s in probe(video)["streams"]}
    assert kinds == {"video": "h264", "audio": "aac"}
    assert float(probe(video)["format"]["duration"]) == pytest.approx(tl.duration, abs=0.2)
    assert thumb.exists() and Image.open(thumb).size == (1080, 1920)
    assert problems == []
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "animation.mp4",
        "thumbnail.jpg",
    ]
