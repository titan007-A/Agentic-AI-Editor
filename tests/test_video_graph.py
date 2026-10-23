from pathlib import Path

from agentic_editor.analysis.watermarks import Watermark
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
    assert chain == [
        "crop=606:1080:657:0",
        "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos",
        "crop=1080:1920",
    ]


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


def test_graph_layers_overlays_in_order():
    edl = EDL(
        clips=[Clip(start=4, end=12)],
        crop_mode="none",
        zooms=[Zoom(time=11, scale=1.2, duration=0.5)],
    )
    layers = Layers(
        text_overlay=2,
        title_overlay=3,
        title_duration=3.0,
        captions=Path("/w/captions.ass"),
        fonts_dir=Path("/repo/fonts"),
    )
    graph, out = build_graph(edl, VERTICAL, (1920, 1080), layers)
    assert "[v0]concat=n=1:v=1:a=0[base]" in graph
    assert "0.200*between(t,7.000,7.500)" in graph
    order = [graph.index(s) for s in ("[2:v]overlay", "subtitles=", "[3:v]overlay=0:0:enable")]
    assert order == sorted(order)
    assert out == "out"


def test_watermark_is_blurred_only_while_visible_in_clip_time():
    mark = Watermark(x=100, y=20, width=200, height=60, spans=[(0, 8), (16, 24)], windows=4)
    edl = EDL(clips=[Clip(start=6, end=20)], crop_mode="none")
    graph, _ = build_graph(edl, VERTICAL, (1920, 1080), Layers(watermarks=[mark]))
    assert "crop=200:60:100:20,gblur=sigma=24:steps=3" in graph
    # Source 6-8 s and 16-20 s are 0-2 s and 10-14 s of an input seeked to 6 s.
    assert "enable='between(t,0.000,2.000)+between(t,10.000,14.000)'" in graph
    assert "[w0_0]" in graph and graph.index("[w0_0]") < graph.index("[v0]")


def test_clip_without_a_visible_watermark_is_left_alone():
    mark = Watermark(x=100, y=20, width=200, height=60, spans=[(0, 4)], windows=1)
    edl = EDL(clips=[Clip(start=10, end=20)], crop_mode="none")
    graph, _ = build_graph(edl, VERTICAL, (1920, 1080), Layers(watermarks=[mark]))
    assert "gblur" not in graph and graph.startswith("[0:v]")


def test_watermark_blur_is_on_by_default():
    assert VERTICAL.watermarks.blur


def test_an_inset_picture_is_drawn_narrower_and_centred():
    chain = frame_chain("none", (1920, 1080), FRAME, "#000000", "c0", None, None, 0.74)
    assert chain[0].startswith("scale=798:1920:force_original_aspect_ratio=decrease")
    assert chain[1] == "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x000000"
    top, bottom = video_band("none", (1920, 1080), FRAME, 0.74)
    assert round(bottom - top) == round(798 * 1080 / 1920)
    assert round(top + bottom) == 1920


def test_no_shot_is_stretched_to_the_frame():
    from agentic_editor.edl import Shot
    from agentic_editor.render.video import shot_chain

    # A window narrower than the frame's shape is fitted and padded at the sides.
    narrow = Shot(start=0, end=5, x=0, width=1080)
    chain = shot_chain(narrow, 0, (0, 0, 1080, 1920), (1080, 1080), "#000000", None)
    assert chain[0] == "crop=1080:1920:0:0"
    assert "force_original_aspect_ratio=decrease" in chain[1] and chain[2].startswith("pad=")
    # A shot with its own height is cropped top and bottom to exactly that window.
    tall = Shot(start=0, end=5, x=0, width=1080, y=200, height=1080)
    chain = shot_chain(tall, 0, (0, 0, 1080, 1920), (1080, 1080), "#000000", None)
    assert chain[0] == "crop=1080:1080:0:200"
    assert "force_original_aspect_ratio=increase" in chain[1]


def test_baked_in_bars_are_measured_across_the_clip_not_only_at_its_start(monkeypatch):
    from agentic_editor.render import compose

    # A dark opening shows only a lit spot; later probes see the whole 558 px picture.
    found = {0.0: (80, 100, 280, 160), 5.0: (558, 426, 40, 0), 10.0: (558, 426, 40, 0)}
    monkeypatch.setattr(compose, "detect_black_bars", lambda video, at: found.get(at))
    assert compose.baked_bars(Path("v.mp4"), 0.0, 20.0, (638, 426)) == (558, 426, 40, 0)
    monkeypatch.setattr(compose, "detect_black_bars", lambda video, at: (638, 426, 0, 0))
    assert compose.baked_bars(Path("v.mp4"), 0.0, 20.0, (638, 426)) is None


def test_shipped_letterboxed_presets_take_off_baked_in_bars():
    from agentic_editor.presets import load_presets
    from agentic_editor.settings import ROOT

    letterboxed = [p for p in load_presets(ROOT / "presets").values() if p.framing.mode == "none"]
    vocal = [p for p in letterboxed if p.name.startswith("vocal-music")]
    assert len(vocal) == 4 and all(p.framing.crop_black_bars for p in vocal)
    italian = next(p for p in vocal if p.name == "vocal-music-it-9x16")
    edl = EDL(clips=[Clip(start=0, end=10)], crop_mode="none")
    graph, _ = build_graph(edl, italian, (558, 426), Layers(black_bars=(558, 426, 40, 0)))
    assert "crop=558:426:40:0" in graph and "color=0xC8A77D" in graph
