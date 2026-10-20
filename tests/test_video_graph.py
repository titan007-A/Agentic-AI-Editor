from pathlib import Path

from agentic_editor.edl import EDL, Clip, Zoom
from agentic_editor.presets import Preset
from agentic_editor.render.video import Layers, build_graph, frame_chain, video_band

VERTICAL = Preset(name="v", label="v", min_duration=1, max_duration=60)
FRAME = (1080, 1920)


def test_letterbox_pads_with_background_color():
    chain = frame_chain("none", (1920, 1080), FRAME, "#C8A77D", "c0", None, None)
    assert chain[-1] == "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0xC8A77D"


def test_center_crop_keeps_target_aspect():
    chain = frame_chain("center", (1920, 1080), FRAME, "#000000", "c0", None, None)
    assert chain == ["crop=606:1080:657:0", "scale=1080:1920:flags=lanczos"]


def test_face_mode_drives_crop_from_command_file():
    chain = frame_chain("face", (1920, 1080), FRAME, "#000000", "c1", Path("/w/c1.cmd"), None)
    assert chain[0] == "sendcmd=f='/w/c1.cmd'"
    assert chain[1].startswith("crop@c1=606:1080:")


def test_black_bars_are_cropped_before_scaling():
    chain = frame_chain("none", (1920, 1080), FRAME, "#C8A77D", "c0", None, (1440, 1080, 240, 0))
    assert chain[0] == "crop=1440:1080:240:0"


def test_video_band_for_letterboxed_landscape_source():
    top, bottom = video_band("none", (1920, 1080), FRAME)
    assert (round(top), round(bottom)) == (656, 1264)
    assert video_band("center", (1920, 1080), FRAME) is None
    assert video_band("none", (720, 1280), FRAME) is None


def test_graph_concats_clips_and_layers_overlays_in_order():
    edl = EDL(clips=[Clip(start=0, end=5), Clip(start=10, end=12)], crop_mode="none",
              zooms=[Zoom(time=11, scale=1.2, duration=0.5)])  # fmt: skip
    layers = Layers(text_overlay=2, title_overlay=3, title_duration=3.0,
                    captions=Path("/w/captions.ass"), fonts_dir=Path("/repo/fonts"))  # fmt: skip
    graph, out = build_graph(edl, VERTICAL, (1920, 1080), layers)
    assert "[v0][v1]concat=n=2:v=1:a=0[base]" in graph
    assert "0.200*between(t,6.000,6.500)" in graph
    order = [graph.index(s) for s in ("[2:v]overlay", "subtitles=", "[3:v]overlay=0:0:enable")]
    assert order == sorted(order)
    assert out == "out"
