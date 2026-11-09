"""The upscale-only preset (upscale/ and pipeline.upscaled), without its models: a fake
upscaler stands in for Real-ESRGAN and the face fixer is tested with a stand-in network."""

import json
import shutil
import subprocess
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import pytest

from agentic_editor import download, ffmpeg, pipeline, upscale
from agentic_editor.backends import Backends
from agentic_editor.presets import Preset, Upscale
from agentic_editor.settings import get_settings

BACKENDS = Backends("cpu", "cpu", "x264", "h264")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
needs_ml = pytest.mark.skipif(
    find_spec("torch") is None or find_spec("cv2") is None, reason="needs the ml extras"
)


def test_the_output_has_the_short_side_asked_for_and_the_model_sees_half_of_it_at_most():
    small = upscale.plan(480, 270, 30, "1080p")
    assert small.out == (1920, 1080) and small.model_in == (480, 270)
    reel = upscale.plan(720, 1280, 30, "1080p")
    assert reel.out == (1080, 1920) and reel.model_in == (540, 960)
    big = upscale.plan(1080, 1920, 25, "1080p")  # already 1080p: drawn at 2x and brought down
    assert big.out == (1080, 1920) and big.model_in == (540, 960)
    four_k = upscale.plan(1280, 720, 0, "4k")
    assert four_k.out == (3840, 2160) and four_k.model_in == (1280, 720) and four_k.fps == 30
    odd = upscale.plan(321, 241, 30, "1440p")
    assert all(n % 2 == 0 for n in (*odd.out, *odd.model_in))
    with pytest.raises(upscale.UpscaleError):
        upscale.plan(0, 0, 30, "1080p")


def make_video(path: Path, seconds: float = 1.5, audio: str = "aac") -> Path:
    sound = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    codec = ["-c:a", audio] if audio == "aac" else ["-c:a", "libopus"]
    subprocess.run(
        [get_settings().ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size=160x120:rate=15:duration={seconds}", *sound, "-c:v", "libx264",
         "-pix_fmt", "yuv420p", *codec, "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


class Nearest:
    """Stands in for Real-ESRGAN: the frames made bigger by repeating pixels."""

    def __init__(self, folder, denoise, dev):
        self.denoise = denoise

    def __call__(self, frames, out):
        import cv2

        width, height = out
        return np.stack([cv2.resize(f, (width, height), cv2.INTER_NEAREST) for f in frames])


@pytest.fixture
def no_models(monkeypatch, tmp_path):
    monkeypatch.setattr(upscale, "fetch_models", lambda models_dir: tmp_path)
    monkeypatch.setattr(upscale, "Upscaler", Nearest)
    monkeypatch.setattr(upscale, "FaceFixer", lambda folder, strength, dev: lambda frame: frame)
    monkeypatch.setattr(upscale, "device", lambda: None)
    monkeypatch.setattr(upscale, "_free", lambda dev: None)


@needs_ml
@needs_ffmpeg
@pytest.mark.parametrize("audio", ["aac", "opus"])
def test_a_video_is_written_bigger_with_its_sound_length_and_frame_rate(no_models, tmp_path, audio):
    source = make_video(tmp_path / f"in-{audio}.mkv", audio=audio)
    dest = tmp_path / "out" / "up.mp4"
    said = []
    sizes = upscale.upscale(source, dest, tmp_path, BACKENDS, "1080p", faces=0,
                            progress=lambda stage, message: said.append(message))  # fmt: skip
    info = ffmpeg.probe(dest)
    assert (info.width, info.height) == sizes.out == (1440, 1080)
    assert info.has_audio and info.fps == pytest.approx(15)
    assert info.duration == pytest.approx(1.5, abs=0.15)
    assert upscale.audio_codec(dest) == "aac"  # opus doesn't go in an MP4 as it is
    assert said[0] == "upscaling 160x120 to 1440x1080"


@needs_ml
@needs_ffmpeg
def test_a_silent_video_stays_silent(no_models, tmp_path):
    source = tmp_path / "silent.mp4"
    subprocess.run(
        [get_settings().ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=120x160:rate=10:duration=1", "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )  # fmt: skip
    upscale.upscale(source, tmp_path / "up.mp4", tmp_path, BACKENDS, "1080p", faces=0)
    info = ffmpeg.probe(tmp_path / "up.mp4")
    assert (info.width, info.height) == (1080, 1440) and not info.has_audio


@needs_ml
@needs_ffmpeg
def test_a_failure_mid_video_raises_instead_of_waiting_forever(no_models, monkeypatch, tmp_path):
    class Broken(Nearest):
        def __call__(self, frames, out):
            raise RuntimeError("the GPU ran out of memory")

    monkeypatch.setattr(upscale, "Upscaler", Broken)
    source = make_video(tmp_path / "in.mp4")
    with pytest.raises(RuntimeError, match="out of memory"):
        upscale.upscale(source, tmp_path / "up.mp4", tmp_path, BACKENDS, "1080p", faces=0)


def fixer(strength: float, redraw) -> upscale.FaceFixer:
    """A FaceFixer without its models: `redraw` stands in for GFPGAN on the 512 px faces."""
    import cv2
    import torch

    class Net(torch.nn.Module):
        def forward(self, x, return_rgb=False, randomize_noise=False):
            return redraw(x), None

    fix = upscale.FaceFixer.__new__(upscale.FaceFixer)
    fix.cv2, fix.strength, fix.half = cv2, strength, False
    fix.net, fix.device, fix.last = Net(), torch.device("cpu"), []
    mask = np.zeros((512, 512), np.float32)
    mask[32:-32, 32:-32] = 1
    fix.mask = cv2.GaussianBlur(mask, (0, 0), 16)
    return fix


# A face's five points in a 400x400 frame: eyes 60 px apart.
POINTS = np.array([[170, 180], [230, 180], [200, 215], [178, 245], [222, 245]], np.float32)


@needs_ml
def test_a_face_is_pasted_back_only_where_it_is_and_by_the_strength_asked():
    import torch

    frame = np.full((400, 400, 3), 100, np.uint8)
    white = fixer(0.5, lambda x: torch.ones_like(x))  # GFPGAN draws the face all white
    white.points = lambda image: [POINTS]
    out = white(frame)
    assert out[210, 200].tolist() == [178, 178, 178]  # halfway from 100 to 255
    assert out[5, 5].tolist() == [100, 100, 100]  # far from the face: untouched
    same = fixer(1.0, lambda x: x)  # a redraw that changes nothing leaves the frame as it was
    same.points = lambda image: [POINTS]
    assert np.abs(same(frame).astype(int) - 100).max() <= 1


@needs_ml
def test_no_face_or_no_strength_leaves_the_frame_untouched():
    import torch

    frame = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8)
    none = fixer(0.5, lambda x: torch.ones_like(x))
    none.points = lambda image: []
    assert none(frame) is frame
    off = fixer(0.0, lambda x: torch.ones_like(x))
    off.points = lambda image: [POINTS / 2]
    assert off(frame) is frame


def preset(name: str, **upscaling) -> Preset:
    return Preset(name=name, label=name, min_duration=1, max_duration=60,
                  upscale=Upscale(**upscaling))  # fmt: skip


@needs_ml
@needs_ffmpeg
def test_an_upscale_preset_is_made_from_the_whole_video_without_editing(
    no_models, monkeypatch, tmp_path
):
    def never(*args, **kwargs):
        raise AssertionError("an upscale-only job edits nothing")

    monkeypatch.setattr(pipeline, "edit_presets", never)
    video = make_video(tmp_path / "in.mp4")
    settings = type("S", (), {"media_dir": tmp_path / "media", "models_dir": tmp_path})
    source = download.Source(path=video, title="Old clip", url="https://x.test/v")
    result = pipeline.edit(source, tmp_path / "cache" / "abc", [preset("up", enabled=True)],
                           settings, BACKENDS, None, lambda *args: None, "run1")  # fmt: skip
    (out,) = result.outputs
    assert out.preset == "up" and out.path.exists() and out.thumbnail.exists()
    assert out.edl.clips[0].start == 0 and out.edl.clips[0].end == pytest.approx(1.5, abs=0.15)
    assert out.edl.decisions == ["upscaled 160x120 to 1440x1080, faces 50%, denoise 50%"]
    meta = json.loads((out.path.parent / "source.json").read_text())
    assert meta["title"] == "Old clip" and meta["url"] == "https://x.test/v"


@needs_ml
@needs_ffmpeg
def test_upscale_and_edit_presets_in_one_job_each_get_their_own_and_one_failing_keeps_the_other(
    no_models, monkeypatch, tmp_path
):
    edited = []

    def edit_presets(source, work_dir, presets, *args):
        edited.append([p.name for p in presets])
        raise RuntimeError("the planner is down")

    monkeypatch.setattr(pipeline, "edit_presets", edit_presets)
    video = make_video(tmp_path / "in.mp4")
    settings = type("S", (), {"media_dir": tmp_path / "media", "models_dir": tmp_path})
    source = download.Source(path=video, title="Clip")
    presets = [preset("up", enabled=True, faces=0), preset("reel")]
    result = pipeline.edit(source, tmp_path / "cache" / "abc", presets, settings, BACKENDS,
                           None, lambda *args: None, "run1")  # fmt: skip
    assert edited == [["reel"]]
    assert [o.preset for o in result.outputs] == ["up"]
    assert result.failures == ["RuntimeError: the planner is down"]
    assert result.outputs[0].edl.decisions[0].endswith("faces left as they were, denoise 50%")


def test_a_preset_without_upscale_still_goes_through_the_editor(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(pipeline, "edit_presets", lambda *args: called.append(args) or "edited")
    source = download.Source(path=tmp_path / "v.mp4", title="t")
    assert pipeline.edit(source, tmp_path, [preset("reel")], None, None, None, None, "r") == (
        "edited"
    )


def test_the_shared_upscale_preset_loads():
    from agentic_editor.presets import load_presets

    shipped = load_presets(Path(__file__).parent.parent / "presets")["upscale-hd"]
    assert shipped.upscale.enabled and shipped.upscale.size == "1080p"
    assert not shipped.post_caption.enabled


def test_a_model_downloaded_with_the_wrong_contents_is_thrown_away(monkeypatch, tmp_path):
    import urllib.request

    def download(url, path):
        Path(path).write_bytes(b"not the model")

    monkeypatch.setattr(urllib.request, "urlretrieve", download)
    with pytest.raises(upscale.UpscaleError, match="wrong contents"):
        upscale.fetch_models(tmp_path)
    assert list((tmp_path / "upscale").iterdir()) == []


def test_models_already_here_are_not_downloaded_again(monkeypatch, tmp_path):
    import urllib.request

    folder = tmp_path / "upscale"
    folder.mkdir()
    for name in upscale.MODELS:
        (folder / name).write_bytes(b"x")
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda *a: pytest.fail("downloaded"))
    assert upscale.fetch_models(tmp_path) == folder
