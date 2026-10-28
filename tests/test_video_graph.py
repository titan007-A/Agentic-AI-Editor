from pathlib import Path

from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.edl import EDL, Clip, Shot, Zoom
from agentic_editor.presets import Preset
from agentic_editor.render import text
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
        crop_mode="center",
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
    monkeypatch.setattr(compose, "padded_picture", lambda *args: None)
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


def test_a_silent_intro_gets_silence_and_joins_the_reel_in_one_graph():
    from agentic_editor.ffmpeg import MediaInfo
    from agentic_editor.render.compose import bookend_graph

    silent = MediaInfo(1.5, 1920, 1080, 25, has_audio=False, has_video=True)
    sound = MediaInfo(2.0, 1080, 1920, 30, has_audio=True, has_video=True)
    parts, video, audio = bookend_graph(FRAME, 30, [(5, silent)], [(6, sound)], "out", 4)
    graph = ";".join(parts)
    assert "[5:a]" not in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=1.500[ba0]" in graph
    assert "[bv0][ba0][out][reel_a][bv1][ba1]concat=n=3:v=1:a=1[joined_v][joined_a]" in graph
    assert (video, audio) == ("joined_v", "joined_a")


def test_zooms_never_cut_into_a_picture_shown_whole_to_keep_everything():
    from agentic_editor.edl import Shot

    zooms = [Zoom(time=1, scale=1.2, duration=0.5), Zoom(time=6, scale=1.2, duration=0.5)]
    # Letterboxed at the frame's full width: no room at all.
    edl = EDL(clips=[Clip(start=0, end=10)], crop_mode="none", zooms=zooms)
    graph, _ = build_graph(edl, VERTICAL, (1920, 1080), Layers(pulses=[3.0], pulse_scale=1.05))
    assert "between(t" not in graph
    # An inset picture may grow until it reaches the frame's edges, and no further.
    inset = VERTICAL.model_copy(
        update={"framing": VERTICAL.framing.model_copy(update={"picture_width": 0.9})}
    )
    graph, _ = build_graph(edl, inset, (1920, 1080), Layers())
    assert "0.111*between(t,1.000,1.500)" in graph
    # Smart framing: the cropped shot zooms, the shot widened to keep a banner doesn't.
    shots = [Shot(start=0, end=5, x=600, width=606), Shot(start=5, end=10, x=0, width=1248)]
    edl = EDL(clips=[Clip(start=0, end=10)], crop_mode="smart", shots=shots, zooms=zooms)
    graph, _ = build_graph(edl, VERTICAL, (1920, 1080), Layers(pulses=[4.9], pulse_scale=1.05))
    assert "0.200*between(t,1.000,1.500)" in graph and "between(t,6.000" not in graph
    assert "0.050*between(t,4.900,5.000)*(1-(t-4.900)/0.3)" in graph


def test_only_watermarks_on_the_kept_picture_are_blurred():
    from agentic_editor.analysis.watermarks import Watermark
    from agentic_editor.render.compose import on_picture

    def mark(x, y, w, h):
        return Watermark(x=x, y=y, width=w, height=h, spans=[(0, 10)], windows=5)

    repost_caption = mark(32, 344, 1006, 228)  # on the padding above the picture
    handle = mark(102, 648, 184, 68)
    edge = mark(28, 1368, 702, 40)  # the picture's bottom edge at 1392, which never moves
    padded = mark(880, 520, 200, 100)  # its padding runs a few px past the picture
    kept = on_picture([repost_caption, handle, edge, padded], (0, 526, 1080, 866))
    assert [(m.x, m.y, m.width, m.height) for m in kept] == [
        (102, 648, 184, 68),
        (880, 526, 200, 94),
    ]
    assert on_picture([handle], None) == [handle]


def test_the_picture_goes_between_the_text_where_the_preset_puts_it():
    from agentic_editor.presets import load_presets
    from agentic_editor.render import compose
    from agentic_editor.render.video import band_fit
    from agentic_editor.settings import ROOT, get_settings

    preset = load_presets(ROOT / "presets")["vocal-music-it-9x16"]
    face = compose.text_face(get_settings(), "it")
    top, bottom = compose.picture_band(preset, face)
    above, below = preset.framing.text_gap_above, preset.framing.text_gap_below
    placed = [text.place(b, face(b.font, b.text), preset.size, None, 0) for b in preset.text_blocks]
    assert top >= placed[0].band[1] + above and bottom <= placed[1].band[0] - below
    # The whole width: a 9:16 picture keeps the band's rows, a wide one is shown whole.
    assert compose.band_rows(preset, (top, bottom), 1080) == bottom - top
    fit = band_fit(preset.size, "#C8A77D", 1.0, (top, bottom))
    assert fit[0].startswith(f"scale=1080:{bottom - top}:")
    assert f":(ow-iw)/2:{top}+({bottom - top}-ih)/2:" in fit[1]


def test_band_windows_move_at_once_on_a_cut_and_ease_inside_a_shot():
    from agentic_editor.render.video import band_rows_expression

    shots = [
        Shot(start=10, end=14, x=0, width=1080, y=300, height=900),
        Shot(start=14, end=18, x=0, width=1080, y=500, height=900),
        Shot(start=18, end=20, x=0, width=1080, y=420, height=900, glide=True),
    ]
    expression = band_rows_expression(shots, 10)
    assert expression.startswith("300+200*gte(t,4.000)-80*")
    assert "clip((t-7.600)/0.8,0,1)" in expression
