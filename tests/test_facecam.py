import io
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from agentic_editor import pipeline
from agentic_editor.analysis import facecam as finder
from agentic_editor.analysis.facecam import Camera, Picture
from agentic_editor.edl import EDL, Clip
from agentic_editor.edl import Facecam as Cuts
from agentic_editor.presets import Preset
from agentic_editor.render import facecam, preview, text
from agentic_editor.render.video import Layers, build_graph
from agentic_editor.settings import get_settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
SOURCE = (1920, 1080)


def facecam_preset(**fields) -> Preset:
    return Preset(
        name="f", label="f", min_duration=10, max_duration=60,
        facecam={"enabled": True, **fields},
        framing={"mode": "none", "picture_y": 100, "picture_center": True},
    )  # fmt: skip


def test_a_facecam_preset_follows_the_face_when_there_is_no_camera_box():
    preset = facecam_preset()
    assert preset.framing.mode == "face"  # the fallback fills the frame, text stays put
    assert (preset.framing.picture_y, preset.framing.picture_center) == (0, False)
    assert preset.facecam.rows(1920) == ((0, 760), (760, 1920))
    assert facecam_preset(top=100, camera=600, gap=20, game=900).facecam.rows(1920) == (
        (100, 700), (720, 1620),
    )  # fmt: skip


def test_the_panels_must_fit_the_frame_and_not_clash_with_a_split():
    with pytest.raises(ValueError, match="run off the 1920 px frame"):
        facecam_preset(camera=1000, game=1000)
    with pytest.raises(ValueError, match="facecam and split"):
        Preset(name="f", label="f", min_duration=10, max_duration=60,
               facecam={"enabled": True}, split={"enabled": True})  # fmt: skip
    with pytest.raises(ValueError, match="shares of the picture"):
        facecam_preset(box=(0.9, 0.0, 0.3, 0.3))
    assert facecam_preset(box=(0.7, 0.0, 0.3, 0.3)).facecam.box == (0.7, 0.0, 0.3, 0.3)


def test_the_streamer_is_the_face_that_stays_put():
    steady = (100.0, 40.0, 40.0, 50.0)
    found = []
    for i in range(20):
        faces = [(steady[0] + i % 3, steady[1], steady[2], steady[3])]  # a little head movement
        if i % 2:
            faces.append((300.0 + 20 * i, 300.0, 30.0, 36.0))  # someone in the game, moving
        found.append(faces if i % 5 else [])  # missed now and then
    face, share = finder.steady_face(found)
    assert face[0] == pytest.approx(101, abs=1.5) and face[1] == 40
    assert share == pytest.approx(16 / 20)
    assert finder.steady_face([[(300.0 + 30 * i, 300.0, 30.0, 36.0)] for i in range(20)]) is None


def synthetic(box, face, size=(960, 540), border=True, room_lines=()):
    """A stream at the read size: the game outside the camera box moves a lot, the room
    inside little; the box's border is a lasting edge."""
    width, height = size
    left, top, right, bottom = box
    across = np.zeros((height, width - 1), np.float32)
    down = np.zeros((height - 1, width), np.float32)
    rng = np.random.default_rng(1)
    across += rng.uniform(0, 4, across.shape)  # the game's edges don't last
    down += rng.uniform(0, 4, down.shape)
    if border:
        if left > 0:
            across[top:bottom, left - 1] = 30
        if right < width:
            across[top:bottom, right - 1] = 30
        if top > 0:
            down[top - 1, left:right] = 30
        if bottom < height:
            down[bottom - 1, left:right] = 30
    for x in room_lines:  # a door frame in the room: a lasting line, the same both sides
        across[top + 10 : bottom - 30, x] = 30
    motion = np.full((height, width), 12.0, np.float32)
    motion[top:bottom, left:right] = 1.0
    colour = np.zeros((height, width, 3), np.float32)
    colour[...] = (40, 90, 40)
    colour[top:bottom, left:right] = (120, 100, 90)
    return Picture(across=across, down=down, motion=motion, colour=colour)


def test_the_camera_box_is_the_lasting_rectangle_round_the_face():
    picture = synthetic((60, 80, 260, 200), None)
    box, score = finder.camera_box(picture, (140.0, 110.0, 30.0, 38.0))
    assert box == (60, 80, 260, 200) and score > finder.SURE


def test_a_camera_in_a_corner_uses_the_frames_own_edges():
    picture = synthetic((0, 0, 220, 150), None)
    box, score = finder.camera_box(picture, (90.0, 40.0, 30.0, 38.0))
    assert box == (0, 0, 220, 150) and score > finder.SURE


def test_no_face_or_no_box_finds_no_camera(tmp_path, monkeypatch):
    frames = np.zeros((10, 54, 96, 3), np.uint8)
    monkeypatch.setattr(finder.layout, "sample", lambda *a, **k: frames)
    monkeypatch.setattr(finder, "model", lambda models_dir: tmp_path / "yunet.onnx")
    monkeypatch.setattr(finder, "faces", lambda f, p: [[] for _ in f])
    assert finder.find(tmp_path / "v.mp4", 0, 10, SOURCE, tmp_path, "ffmpeg") is None
    # A big face with no box round it: the picture is all camera.
    monkeypatch.setattr(finder, "faces", lambda f, p: [[(30.0, 5.0, 20.0, 26.0)] for _ in f])
    camera = finder.find(tmp_path / "v.mp4", 0, 10, SOURCE, tmp_path, "ffmpeg")
    assert camera is not None and camera.full and camera.face == (600, 100, 400, 520)


def test_the_camera_panel_shows_the_box_round_the_face_in_its_shape():
    box, face = (0, 162, 416, 262), (207, 283, 53, 68)
    x, y, w, h = facecam.camera_cut(box, face, 1080 / 760)
    assert w / h == pytest.approx(1080 / 760, rel=0.01)
    assert box[0] <= x and x + w <= box[0] + box[2] and box[1] <= y and y + h <= box[1] + box[3]
    assert x <= face[0] and face[0] + face[2] <= x + w  # the face is in it
    assert all(v % 2 == 0 for v in (x, y, w, h))


def test_a_camera_far_from_the_panels_shape_is_shown_whole():
    preset = facecam_preset()
    narrow = Camera((1763, 243, 105, 134), (1636, 158, 284, 324))
    cuts = facecam.layout(narrow, SOURCE, preset)
    assert cuts.fit and cuts.camera[2] <= 284 and cuts.camera[3] <= 324
    x, y, w, h = facecam.shown_camera(cuts, (1080, 760))
    assert h == 760 and w < 1080 and x == (1080 - w) // 2
    joined = ";".join(facecam.graph_parts("c0", cuts, preset, "0x000000", "fc0"))
    assert "boxblur" in joined and f"overlay={x}:{y}[fc0cam]" in joined
    wide = Camera((207, 283, 53, 68), (0, 162, 416, 262))
    assert not facecam.layout(wide, SOURCE, preset).fit


def test_the_game_panel_moves_off_the_camera():
    shape = 1080 / 1160
    assert facecam.game_cut(SOURCE, shape, 0.5, None) == (458, 0, 1004, 1080)
    left = facecam.game_cut(SOURCE, shape, 0.5, (0, 0, 504, 284))
    assert left[0] >= 504  # moved right, clear of a camera on the left
    right = facecam.game_cut(SOURCE, shape, 0.5, (1286, 0, 634, 356))
    assert right[0] + right[2] <= 1286
    assert facecam.game_cut(SOURCE, shape, 0.0, None)[0] == 0


def test_the_graph_cuts_both_panels_from_the_picture_and_never_zooms():
    preset = facecam_preset().model_copy(update={"zoom_punch_ins": True})
    cuts = Cuts(camera=(6, 24, 370, 260), game=(456, 0, 1004, 1080), face=(210, 72, 95, 126))
    edl = EDL(clips=[Clip(start=5, end=25)], crop_mode="face", facecam=cuts)
    graph, out = build_graph(edl, preset, SOURCE, Layers(facecam=cuts, pulses=[1.0, 2.0]))
    assert "[fc0a]crop=370:260:6:24,scale=1080:760:flags=lanczos" in graph
    assert "[fc0b]crop=1004:1080:456:0,scale=1080:1160:flags=lanczos" in graph
    assert "[fc0bg][fc0cam]overlay=0:0:shortest=1[fc0one]" in graph
    assert "[fc0one][fc0game]overlay=0:760:shortest=1[fc0]" in graph
    assert "zoomed" not in graph and out == "out"


def test_captions_and_banners_keep_off_the_face_in_the_camera_panel():
    preset = facecam_preset()
    cuts = Cuts(camera=(6, 24, 370, 260), game=(456, 0, 1004, 1080), face=(210, 72, 95, 126))
    top, bottom = facecam.face_rows(cuts, preset)
    scale = 760 / 260
    assert top == pytest.approx((72 - 24) * scale) and bottom == pytest.approx((198 - 24) * scale)
    assert facecam.face_rows(cuts.model_copy(update={"face": None}), preset) is None


def test_the_edit_cuts_the_panels_round_the_camera_found(monkeypatch, make_analysis):
    analysis = make_analysis([], 20.0)
    edl = EDL(clips=[Clip(start=0, end=20)], crop_mode="face")
    settings = get_settings()
    found = Camera((210, 72, 95, 126), (0, 0, 386, 318), 0.7)
    monkeypatch.setattr(pipeline.camera_finder, "find", lambda *a, **k: found)
    cut = pipeline.facecam_cuts(edl, facecam_preset(), analysis, settings.media_dir, settings)
    assert cut.facecam is not None and not cut.doubts
    assert any("386x318 box at 0,0" in d for d in cut.decisions)
    # The preset's own box wins; the face found is kept when it is inside it.
    boxed = facecam_preset(box=(0.0, 0.0, 0.25, 0.4))
    cut = pipeline.facecam_cuts(edl, boxed, analysis, settings.media_dir, settings)
    assert any("480x432 box at 0,0" in d for d in cut.decisions)
    assert cut.facecam.face == (210, 72, 95, 126)
    # All camera, or none: the face is followed over the whole frame (none is a doubt).
    monkeypatch.setattr(pipeline.camera_finder, "find", lambda *a, **k: Camera((1, 1, 9, 9), None))
    cut = pipeline.facecam_cuts(edl, facecam_preset(), analysis, settings.media_dir, settings)
    assert cut.facecam is None and not cut.doubts and "all camera" in cut.decisions[-1]
    monkeypatch.setattr(pipeline.camera_finder, "find", lambda *a, **k: None)
    cut = pipeline.facecam_cuts(edl, facecam_preset(), analysis, settings.media_dir, settings)
    assert cut.facecam is None and cut.doubts
    # A preset without the layout is left alone.
    plain = Preset(name="p", label="p", min_duration=10, max_duration=60)
    assert pipeline.facecam_cuts(edl, plain, analysis, settings.media_dir, settings) == edl


def test_text_over_the_video_can_have_an_outline(tmp_path):
    settings = get_settings()
    font = text.font_file(settings.fonts_dir, "Anton-Regular")
    block = Preset.model_fields["text_blocks"].annotation.__args__[0](
        text="KICK", font="Anton-Regular", size=80, anchor="top", offset=100,
        outline=4, outline_color="#000000",
    )  # fmt: skip
    for outline, name in ((4, "with.png"), (0, "without.png")):
        placed = text.place(block.model_copy(update={"outline": outline}), font, (1080, 1920),
                            None, None)  # fmt: skip
        layer = Image.open(text.draw([placed], (1080, 1920), tmp_path / name)).convert("RGBA")
        pixels = np.asarray(layer)
        opaque = pixels[..., 3] > 200
        black = opaque & (pixels[..., :3].max(axis=2) < 40)
        white = opaque & (pixels[..., :3].min(axis=2) > 215)
        assert white.sum() > 1000
        assert (black.sum() > 500) == bool(outline)


def test_the_preview_draws_both_panels_and_warns_of_text_on_the_face():
    settings = get_settings()
    preset = facecam_preset()
    jpeg, notes = preview.render_checked(preset, settings, "16:9")
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    sky = image.getpixel((540, 900))  # the game panel's sky
    assert sky[2] > 180 and sky[2] > sky[0]
    assert notes == []
    covering = preset.model_copy(
        update={"one_liner": preset.one_liner.model_copy(update={"enabled": True, "offset": 260})}
    )
    _, notes = preview.render_checked(covering, settings, "16:9")
    assert any("streamer's face" in n for n in notes)


def test_the_kick_facecam_preset_loads_and_previews_clean():
    from agentic_editor.presets import load_preset

    settings = get_settings()
    preset = load_preset(settings.presets_dir / "kick-facecam-9x16.yaml")
    assert preset.facecam.enabled and preset.text_blocks[0].outline == 4
    _, notes = preview.render_checked(preset, settings, "16:9")
    assert notes == []


@needs_ffmpeg
def test_a_render_puts_the_camera_on_top_and_the_game_below(tmp_path):
    preset = facecam_preset()
    cuts = Cuts(camera=(0, 0, 398, 280), game=(458, 0, 1004, 1080))
    edl = EDL(clips=[Clip(start=0, end=1)], crop_mode="face", facecam=cuts)
    graph, out = build_graph(edl, preset, SOURCE, Layers(facecam=cuts))
    source = "color=c=blue:s=1920x1080:d=1:r=30,drawbox=x=0:y=0:w=400:h=282:color=red:t=fill"
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-filter_complex", graph,
         "-map", f"[{out}]", "-frames:v", "1", str(frame)], check=True,
    )  # fmt: skip
    image = Image.open(frame).convert("RGB")
    assert image.size == (1080, 1920)
    camera, game = image.getpixel((540, 380)), image.getpixel((540, 1300))
    assert camera[0] > 200 and camera[2] < 60  # the red camera fills the top panel
    assert game[2] > 200 and game[0] < 60  # the blue game the bottom one
