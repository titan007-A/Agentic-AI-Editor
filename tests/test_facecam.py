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
    monkeypatch.setattr(pipeline.camera_finder, "in_stream", lambda *a, **k: found)
    cut = pipeline.facecam_cuts(edl, facecam_preset(), analysis, settings.media_dir, settings)
    assert cut.facecam is not None and not cut.doubts
    assert any("386x318 box at 0,0" in d for d in cut.decisions)
    # The preset's own box wins; the face found is kept when it is inside it.
    boxed = facecam_preset(box=(0.0, 0.0, 0.25, 0.4))
    cut = pipeline.facecam_cuts(edl, boxed, analysis, settings.media_dir, settings)
    assert any("480x432 box at 0,0" in d for d in cut.decisions)
    assert cut.facecam.face == (210, 72, 95, 126)
    # All camera, or none: the face is followed over the whole frame (none is a doubt).
    all_camera = Camera((1, 1, 9, 9), None)
    monkeypatch.setattr(pipeline.camera_finder, "in_stream", lambda *a, **k: all_camera)
    cut = pipeline.facecam_cuts(edl, facecam_preset(), analysis, settings.media_dir, settings)
    assert cut.facecam is None and not cut.doubts and "all camera" in cut.decisions[-1]
    monkeypatch.setattr(pipeline.camera_finder, "in_stream", lambda *a, **k: None)
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
    assert preset.facecam.enabled and preset.facecam.gap > 0
    jpeg, notes = preview.render_checked(preset, settings, "16:9")
    assert notes == []
    # A black band between the panels, Kick's logo at its left and the channel at its right,
    # the one-liner under it.
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    camera, game = preset.facecam.rows(1920)
    band = camera[1], game[0]
    middle = (band[0] + band[1]) // 2
    assert max(image.getpixel((500, middle))) < 40
    row = [image.getpixel((x, middle)) for x in range(1080)]
    green = [x for x, (r, g, _) in enumerate(row) if g > 200 > r]
    white = [x for x, rgb in enumerate(row) if min(rgb) > 200]
    assert green and max(green) < 500 and white and min(white) > 500
    assert preset.one_liner.offset >= band[1]


def test_the_kick_facecam_preset_is_laid_out_like_the_clips_page_reel():
    """Where @kickclipsonly's reel (DgBUOQgxuBa, 1080x1920) puts each thing, measured from
    its frames: camera 0-620, band 621-694, Kick's logo 56-308 x 595-676 rising into the
    camera on a black backing, the channel's capitals 532-767 x 645-669, the white title
    card 25-1055 from 695 over the game, the game down to 1892."""
    from agentic_editor.presets import load_preset

    settings = get_settings()
    preset = load_preset(settings.presets_dir / "kick-facecam-9x16.yaml")
    assert preset.facecam.rows(1920) == ((0, 621), (695, 1892))
    jpeg, _ = preview.render_checked(preset, settings, "16:9")
    image = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")).astype(int)
    r, g, b = image[..., 0], image[..., 1], image[..., 2]
    ys, xs = np.nonzero((g > 200) & (r < 120) & (b < 100))
    assert abs(xs.min() - 56) <= 3 and abs(xs.max() - 308) <= 3
    assert abs(ys.min() - 595) <= 3 and abs(ys.max() - 676) <= 3
    assert image[588, 100].max() < 40  # the logo's backing, over the camera
    channel = image[621:688, 400:1080].min(axis=2) > 200
    ys, xs = np.nonzero(channel)
    assert abs(xs.min() + 400 - 532) <= 4 and abs(ys.min() + 621 - 645) <= 3
    assert 22 <= ys.max() - ys.min() + 1 <= 28  # capitals about 25 px tall, not a headline
    card = image[:, :, :].min(axis=2) > 240
    across = np.nonzero(card[800])[0]
    assert abs(across.min() - 25) <= 3 and abs(across.max() - 1054) <= 3
    down = np.nonzero(card[:, 540])[0]
    assert abs(down[down > 690].min() - 695) <= 6


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


def test_a_banner_box_can_span_the_frame_whatever_the_words(tmp_path):
    settings = get_settings()
    font = text.font_file(settings.fonts_dir, "Montserrat-SemiBold")
    across = text.BoxShape(margin=25, radius=35, pad=42)
    spans = {}
    for words in ("Short", "A much longer line of words here"):
        for shape in (None, across):
            dest = text.draw_banner(words, font, 60, "#000000", "#FFFFFF", (1080, 1920), 700,
                                    tmp_path / "b.png", shape)  # fmt: skip
            alpha = np.asarray(Image.open(dest))[..., 3]
            rows = np.flatnonzero((alpha > 128).any(axis=1))
            middle = (rows[0] + rows[-1]) // 2
            columns = np.flatnonzero(alpha[middle] > 128)
            spans[words, shape is None] = (columns[0], columns[-1], rows[0], rows[-1])
            above, below = text.banner_extent(words, font, 60, "#FFFFFF", 1080, shape)
            assert abs(700 + above - rows[0]) <= 2 and abs(700 + below - rows[-1]) <= 2
    # Across the frame: the same box for few words and many; as wide as the words otherwise.
    assert spans["Short", False][:2] == spans["A much longer line of words here", False][:2]
    assert abs(spans["Short", False][0] - 25) <= 1 and abs(spans["Short", False][1] - 1054) <= 1
    assert spans["Short", True][1] - spans["Short", True][0] < 400
    # The room inside sets the box's height: 42 px over and under the ink.
    hugging, wide = spans["Short", True], spans["Short", False]
    assert abs((wide[3] - wide[2]) - (hugging[3] - hugging[2]) - 2 * (42 - 60 * text.BOX_PAD)) <= 2
    # A line wraps where the box ends, not 80 px in from each side.
    assert text.banner_room(1080, 70, across) == 1080 - 50 - 63
    assert text.banner_room(1080, 70) == 920


def test_a_fixed_banner_stays_close_under_the_preset_s_text():
    from agentic_editor.render import compose

    text_line = [(645.0, 670.0)]  # the channel line's ink, 23 px over the card
    assert compose.banner_top("top", 693, 217, 1920, [], 10.0, text_line, fixed=True) == 693
    # Not fixed, it keeps a banner's gap from other text, as before.
    assert compose.banner_top("top", 693, 217, 1920, [], 10.0, text_line) != 693
    # Fixed, it still never covers another's ink.
    covering = compose.banner_top("top", 660, 217, 1920, [], 10.0, text_line, fixed=True)
    assert covering + 217 <= 645 or covering >= 670


def test_text_can_stand_at_the_left_or_the_right(tmp_path):
    settings = get_settings()
    font = text.font_file(settings.fonts_dir, "Anton-Regular")
    block = Preset.model_fields["text_blocks"].annotation.__args__[0](
        text="KICK.COM/XQC", font="Anton-Regular", size=60, anchor="top", offset=100,
    )  # fmt: skip
    spans = {}
    for align in ("left", "center", "right"):
        placed = text.place(block.model_copy(update={"align": align, "inset": 40}), font,
                            (1080, 1920), None, None)  # fmt: skip
        layer = np.asarray(Image.open(text.draw([placed], (1080, 1920), tmp_path / "t.png")))
        columns = np.flatnonzero((layer[..., 3] > 128).any(axis=0))
        spans[align] = (columns[0], columns[-1])
        assert abs(placed.columns(1080)[0] - columns[0]) < 8
    assert abs(spans["left"][0] - 40) < 8 and abs(spans["right"][1] - 1040) < 8
    middle = (spans["center"][0] + spans["center"][1]) / 2
    assert abs(middle - 540) < 8


def test_a_hunts_clips_share_the_surest_camera_found_in_them(tmp_path, monkeypatch):
    from agentic_editor.clipper import hunt

    videos = []
    for n in range(3):
        (tmp_path / f"kick-abc123-{n}").mkdir()
        videos.append(tmp_path / f"kick-abc123-{n}" / "source.mp4")
        videos[-1].write_bytes(b"v")
    (tmp_path / "kick-other-0").mkdir()
    (tmp_path / "kick-other-0" / "source.mp4").write_bytes(b"v")
    assert hunt.fellow_moments(videos[0]) == videos[1:]
    assert hunt.fellow_moments(tmp_path / "upload.mp4") == []

    face = (220, 450, 110, 140)
    cam = (0, 370, 490, 316)
    looks = {
        videos[0]: Camera(face, (0, 370, 1156, 710), 0.58),  # a game's line taken for its edge
        videos[1]: Camera(face, None, 0.4, sure=False),  # no box sure
        videos[2]: Camera((210, 452, 108, 140), cam, 0.8),
    }
    seen = []

    def look(video, *args):
        seen.append(video)
        return looks[video]

    monkeypatch.setattr(finder, "look", look)
    monkeypatch.setattr(finder.ffmpeg, "probe", lambda v: type("I", (), {
        "duration": 30.0, "width": 1920, "height": 1080})())  # fmt: skip

    def found(video):
        fellows = hunt.fellow_moments(video)
        return finder.in_stream(video, 0, 30, SOURCE, tmp_path, "ffmpeg", fellows)

    assert found(videos[0]) == Camera(face, cam, 0.8)
    assert found(videos[1]) == Camera(face, cam, 0.8)
    assert found(videos[2]) == looks[videos[2]]  # its own is the surest
    assert seen.count(videos[2]) == 2  # once as its own, once kept for the others
    # A face the surer box can't be the camera round (elsewhere, or far too big) keeps its own.
    looks[videos[0]] = Camera((1500, 100, 100, 130), (1350, 0, 570, 330), 0.6)
    assert found(videos[0]) == looks[videos[0]]
    looks[videos[0]] = Camera((60, 300, 400, 520), None, 0.7)  # all camera now
    assert found(videos[0]).full
    # Nothing sure anywhere: no camera, as find says.
    assert finder.in_stream(videos[1], 0, 30, SOURCE, tmp_path, "ffmpeg", []) is None
