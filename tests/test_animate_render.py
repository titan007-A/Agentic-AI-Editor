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
from tests.test_animate import sample_plan, scene, story_plan, tone_wav, voices_for


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


@pytest.mark.parametrize("style", ["glow", "paper", "story", "kinetic", "stick"])
def test_the_video_is_the_preview_frame_for_frame(tmp_path, style):
    if style in ("story", "stick"):  # characters acting
        scenes = [s.model_dump() for s in story_plan().scenes[1:3]]
        scenes[0]["visual"] |= {
            "text": "Hisashi Ouchi",
            "detail": "83 days",
            "crowd": 3,
            "emoji": "☢️",
        }
        plan = story_plan(style=style, scenes=scenes)
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


def test_a_painted_picture_is_behind_its_scene_in_the_video(tmp_path):
    folder = tmp_path / "pictures"
    folder.mkdir()
    name = "a" * 24 + ".png"
    Image.new("RGB", (720, 1280), (200, 30, 40)).save(folder / name)
    scenes = [s.model_dump() for s in story_plan().scenes[1:3]]
    scenes[0]["visual"]["image"] = name
    plan = story_plan(scenes=scenes)
    data = player_data(plan, build(plan, voices_for(plan)))
    data["duration"] = 2.0
    opts = render.options(ROOT / "fonts", ROOT / "models", folder)
    shown = Image.open(render.frame(data, opts, 1.0, tmp_path / "shown.jpg")).convert("RGB")
    r, g, b = shown.getpixel((40, 700))  # the painting, not the drawn street
    assert r > 150 and g < 90 and b < 90, (r, g, b)
    video = tmp_path / "picture.mp4"
    assert render.frames_to_video(data, opts, video, fps=10) == []
    made = frame_at(video, 1.0, tmp_path / "made.png")
    diff = np.abs(np.asarray(shown, dtype=float) - np.asarray(made, dtype=float)).mean()
    assert diff < 4.0


def boxes_at(data: dict, opts: dict, t: float, texts: list[str]) -> dict:
    """Where each of `texts` is drawn at `t` (its box and opacity), from the player itself."""

    async def look() -> dict:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=render.chromium())
            page = await browser.new_page(viewport={"width": 1080, "height": 1920})
            await page.goto((render.PLAYER / "player.html").as_uri())
            await page.evaluate("([d, o]) => window.Player.load(d, o)", [data, opts])
            await page.evaluate("t => window.Player.seek(t)", t)
            found = await page.evaluate(
                """texts => Object.fromEntries(texts.map((text) => {
                    const node = [...document.querySelectorAll("div")].find(
                        (n) => n.textContent === text && n.children.length === 0);
                    if (!node) return [text, null];
                    const r = node.getBoundingClientRect();
                    return [text, { x: r.x, y: r.y, w: r.width, h: r.height,
                                    o: Number(getComputedStyle(node).opacity) }];
                }))""",
                texts,
            )
            await browser.close()
            return found

    return asyncio.run(look())


def overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and \
        a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]  # fmt: skip


@pytest.mark.parametrize(
    "actors",
    [
        [{"name": "Elias", "action": "shocked", "mood": "surprised", "says": "It's a map!"}],
        [{"name": "Elias", "action": "idle", "mood": "neutral", "says": "It's a map!"},
         {"name": "Leo", "action": "idle", "mood": "happy"}],
        [{"name": "Elias", "action": "idle", "mood": "neutral"},
         {"name": "Leo", "action": "wave", "mood": "happy", "says": "It's a map!"}],
    ],
    ids=["alone", "speaks-beside-another", "the-other-speaks"],
)  # fmt: skip
def test_a_scene_note_never_sits_on_a_speech_bubble(actors):
    plan = story_plan(scenes=[
        scene("Elias opens the bottle with trembling hands and reads the note inside.", "scene",
              setting="room", text="The Discovery", detail="Trembling hands", actors=actors),
    ])  # fmt: skip
    data = player_data(plan, build(plan, voices_for(plan)))
    opts = render.options(ROOT / "fonts", ROOT / "models")
    first = data["scenes"][0]
    late = boxes_at(data, opts, first["end"] - 0.1, ["Trembling hands", "It's a map!"])
    note, bubble = late["Trembling hands"], late["It's a map!"]
    assert bubble["o"] > 0.9
    if len(actors) == 1:  # one actor always leaves room beside it: both shown, apart
        assert first["talkFrom"] is None and note["o"] > 0.9
        assert not overlap(note, bubble), (note, bubble)
    else:  # two: the note first, the line after it
        assert first["talkFrom"] == pytest.approx(first["start"] + 2.0, abs=0.01)
        assert note["o"] < 0.05
        early = boxes_at(data, opts, first["start"] + 1.2, ["Trembling hands", "It's a map!"])
        assert early["Trembling hands"]["o"] > 0.9 and early["It's a map!"]["o"] < 0.05


def test_a_world_map_reel_flies_lights_the_country_and_pins_its_cards(tmp_path, monkeypatch):
    from agentic_editor.animate import maps
    from tests.test_animate_map import WORLD, map_plan

    # A small world: the countries' table and an Earth of one land colour, one tile level.
    monkeypatch.setattr(maps, "LEVELS", 1)
    models = tmp_path / "models"
    source = tmp_path / "world.geojson"
    source.write_text(json.dumps(WORLD))
    maps.table_path(models).parent.mkdir(parents=True)
    maps.build_table(source, maps.table_path(models))
    earth = tmp_path / "earth.jpg"
    Image.new("RGB", (3000, 1500), (120, 130, 80)).save(earth)
    maps.build_tiles(earth, maps.tiles_dir(models))
    (models / "fonts").mkdir()
    plan = map_plan()
    data = maps.attach(player_data(plan, build(plan, voices_for(plan))), plan, models)
    assert data["map"]["tiles"] and data["map"]["levels"] == 1
    usa = data["scenes"][1]
    card_at = usa["beats"][1] + 0.6  # the first card has popped and the camera is on it
    data["duration"] = round(card_at + 0.5, 2)
    opts = render.options(ROOT / "fonts", models)
    shown = Image.open(render.frame(data, opts, card_at, tmp_path / "shown.jpg")).convert("RGB")
    r, g, b = shown.getpixel((540, 700))  # the card, white
    assert min(r, g, b) > 225, (r, g, b)
    r, g, b = shown.getpixel((90, 1650))  # the lit country: the land under purple
    assert b > g + 20 and r > g, (r, g, b)
    caption = boxes_at(data, opts, card_at, ["Apple Pie"])["Apple Pie"]
    assert caption is not None and 1280 < caption["y"] < 1420
    video = tmp_path / "picture.mp4"
    assert render.frames_to_video(data, opts, video, fps=10) == []
    made = frame_at(video, card_at, tmp_path / "made.png")
    diff = np.abs(np.asarray(shown, dtype=float) - np.asarray(made, dtype=float)).mean()
    assert diff < 4.0, f"the video differs from the preview by {diff:.1f}"
