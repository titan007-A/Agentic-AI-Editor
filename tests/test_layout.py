import numpy as np

from agentic_editor.analysis import layout

H, W = 192, 108


def padded_reel(frames: int = 12, seed: int = 1) -> np.ndarray:
    """Black padding, a moving picture in rows 30-70% and columns 10-90%, and a white
    tagline on the padding above it."""
    rng = np.random.default_rng(seed)
    reel = np.zeros((frames, H, W, 3), np.uint8)
    reel[:, 58:134, 11:97] = rng.integers(30, 255, (frames, 76, 86, 3))
    reel[:, 30:36, 20:88:3] = 255  # tagline strokes
    return reel


def test_padding_colour_is_read_from_the_top_and_bottom_edges():
    assert layout.background(padded_reel()) == (0, 0, 0)
    full = np.random.default_rng(2).integers(0, 255, (6, H, W, 3)).astype(np.uint8)
    assert layout.background(full) is None


def test_the_picture_box_leaves_out_the_padding_and_the_text_on_it():
    x0, y0, x1, y1 = layout.picture_box(padded_reel(), (0, 0, 0))
    assert abs(x0 - 0.1) < 0.02 and abs(x1 - 0.9) < 0.02
    assert abs(y0 - 0.3) < 0.02 and abs(y1 - 0.7) < 0.02


def test_a_dark_stretch_of_the_picture_still_counts_once_it_lights_up():
    reel = padded_reel()
    reel[:9, 58:90] = 0  # the top of the stage is black for most of the reel
    _, y0, _, _ = layout.picture_box(reel, (0, 0, 0))
    assert abs(y0 - 0.3) < 0.02


def test_a_repost_caption_right_above_the_picture_is_left_out():
    reel = padded_reel()
    reel[:, 44:58, 11:97:2] = 255  # bold caption lines touching the picture's top edge
    _, y0, _, _ = layout.picture_box(reel, (0, 0, 0))
    assert abs(y0 - 0.3) < 0.02


def test_a_still_shot_on_a_narrow_picture_is_still_picture():
    reel = padded_reel()
    reel[:, 58:134, 40:68] = np.random.default_rng(3).integers(30, 255, (76, 28, 3))
    reel[:, :, :40] = 0
    reel[:, :, 68:] = 0  # a narrow picture that never moves
    _, y0, _, y1 = layout.picture_box(reel, (0, 0, 0))
    assert abs(y0 - 0.3) < 0.02 and abs(y1 - 0.7) < 0.02


def test_no_padding_means_the_picture_fills_the_frame():
    assert layout.picture_box(padded_reel(), None) == (0.0, 0.0, 1.0, 1.0)


def test_a_black_and_white_picture_has_no_saturation():
    grey = np.repeat(np.random.default_rng(3).integers(0, 255, (4, H, W, 1)), 3, axis=3)
    assert layout.saturation(grey.astype(np.uint8)) == 0
    assert layout.saturation(padded_reel()) > 5


def test_the_letters_colour_is_the_named_one_not_the_background():
    crop = np.zeros((40, 200, 3), np.uint8)
    crop[10:30, 20:180:4] = (252, 252, 251)  # white letters, a step off after compression
    crop[10:30, 22:60:4] = (255, 222, 4)  # one yellow word
    assert layout.text_colour(crop, "white") == (255, 255, 255)
    assert layout.text_colour(crop, "Yellow") == (255, 222, 0)
    assert layout.text_colour(crop, "black") == (0, 0, 0)


def test_lines_are_split_where_the_ink_stops():
    mask = np.zeros((100, 300), bool)
    mask[10:30, 20:280] = True
    mask[6:8, 40:44] = True  # an accent just above the first line
    mask[45:62, 60:240] = True
    mask[90:91, 0:5] = True  # a speck
    assert layout.lines(mask) == [(6, 30, 20, 280), (45, 62, 60, 240)]


def test_shapes_that_cannot_be_letters_are_dropped():
    mask = np.zeros((100, 300), bool)
    mask[40:60, 50:60] = True  # a letter
    mask[0:30, 100:200] = True  # a bright patch running off the top of the window
    mask[20:95, 250:260] = True  # a lamp post, taller than the text
    kept = layout.letters(mask, tallest=30)
    assert kept[50, 55] and not kept[10, 150] and not kept[50, 255]


def test_a_picture_on_a_blurred_copy_of_itself_is_found():
    rng = np.random.default_rng(4)
    reel = np.zeros((10, H, W, 3), np.uint8)
    for i in range(10):
        blur = np.full((H, W, 3), 60 + 10 * i, np.uint8)  # smooth, and changing
        blur[:, :, 0] += np.linspace(0, 40, W, dtype=np.uint8)
        reel[i] = blur
        reel[i, 66:126] = 180 + rng.integers(0, 40, (60, W, 3))  # the sharp picture
    x0, y0, x1, y1 = layout.blurred_fill(reel)
    assert (x0, x1) == (0.0, 1.0) and abs(y0 - 66 / H) < 0.01 and abs(y1 - 126 / H) < 0.01
    assert layout.background(reel) is None
    sharp = rng.integers(0, 255, (10, H, W, 3)).astype(np.uint8)
    assert layout.blurred_fill(sharp) is None


def _photo_over_clip(still_rows: int, rows: int = 192, frames: int = 12) -> np.ndarray:
    rng = np.random.default_rng(1)
    clip = rng.integers(0, 255, (frames, rows, 108, 3), dtype=np.uint8)
    clip[:, :still_rows] = rng.integers(0, 255, (1, still_rows, 108, 3), dtype=np.uint8)
    return clip


def test_the_moving_part_of_a_picture_that_is_partly_a_photo():
    # A photo over the top 80 rows, the clip under it: the clip's rows.
    assert layout.moving_rows(_photo_over_clip(80), 0, 192) == (80, 192)
    # Inside the picture only: padding outside it doesn't count.
    assert layout.moving_rows(_photo_over_clip(80), 40, 192) == (80, 192)


def test_a_normal_video_or_a_still_image_has_no_moving_part():
    assert layout.moving_rows(_photo_over_clip(0), 0, 192) is None  # it all moves
    assert layout.moving_rows(_photo_over_clip(20), 0, 192) is None  # a sliver is still
    assert layout.moving_rows(_photo_over_clip(192), 0, 192) is None  # nothing moves
    # A thin strip of changing lyrics on a still image isn't a clip.
    assert layout.moving_rows(_photo_over_clip(170), 0, 192) is None


def test_spare_rows_go_to_the_plainer_side_of_the_moving_part():
    reel = _photo_over_clip(80)
    reel[:, 180:] = 0  # a dark strip under the clip; the photo above is busy
    assert layout.plainer_side(reel, (80, 180), 0, 192) == "below"
    assert layout.plainer_side(reel, (80, 192), 0, 192) == "above"  # nothing below


def action_reel(frames: int = 10, blurred_sides: bool = False) -> np.ndarray:
    """A still, detailed scene with a square falling down columns 60-80 of 200: the
    action. With `blurred_sides`, the outer 40 columns each side change too, smoothly, as
    a blurred copy of a picture does."""
    rng = np.random.default_rng(3)
    scene = rng.integers(60, 200, (100, 200, 3)).astype(np.uint8)
    reel = np.repeat(scene[None], frames, axis=0).copy()
    for k in range(frames):
        reel[k, 10 + 6 * k : 25 + 6 * k, 60:80] = 255
        if blurred_sides:
            for side in (slice(0, 40), slice(160, 200)):
                reel[k, :, side] = 40 + 12 * k  # flat, no detail, but it changes
    return reel


def test_the_action_is_where_the_picture_moves():
    x0, y0, x1, y1 = layout.action_box(action_reel())
    assert 55 <= x0 <= 60 and 80 <= x1 <= 85  # the falling square's columns
    assert y0 <= 12 and y1 >= 75  # the whole fall, top to bottom
    still = np.repeat(action_reel()[:1], 5, axis=0)
    assert layout.action_box(still) is None
    assert layout.action_box(action_reel()[:1]) is None


def test_a_blurred_fill_moving_with_the_picture_isnt_action():
    x0, _, x1, _ = layout.action_box(action_reel(blurred_sides=True))
    assert x0 >= 40 and x1 <= 160


def test_a_cut_or_a_swinging_camera_says_nothing_about_the_action():
    reel = action_reel()
    reel[5:] = 255 - reel[5:]  # the whole picture changes at frame 5: a cut
    x0, _, x1, _ = layout.action_box(reel)
    assert x0 >= 55 and x1 <= 85


def test_a_pages_still_branding_on_the_padding_is_cut_out_but_not_the_text_or_picture():
    rng = np.random.default_rng(1)
    frames = np.zeros((6, 400, 240, 3), np.uint8)
    frames[:, 150:260] = rng.integers(0, 255, (6, 110, 240, 3))  # the moving picture
    frames[:, 20:60, 20:60] = (230, 60, 30)  # an avatar
    frames[:, 30:50, 64:200] = 255  # the page's name just beside it
    frames[:, 90:110, 30:210] = 255  # a tagline, learned as text
    found = layout.branding(frames, (0, 0, 0), (0.0, 150 / 400, 1.0, 260 / 400), [(90, 110)])
    assert len(found) == 1  # avatar and name close together are one piece
    (x0, y0, x1, y1), cut = found[0]
    assert x0 <= 20 and y0 <= 20 and x1 >= 200 and y1 >= 60 and y1 < 90
    assert cut.shape == (y1 - y0, x1 - x0, 4)
    assert cut[25 - y0, 25 - x0, 3] == 255 and tuple(cut[25 - y0, 25 - x0, :3]) == (230, 60, 30)
    assert cut[55 - y0, 100 - x0, 3] == 0  # background between them is see-through


def _sky(count=6, height=400, width=200, seed=1):
    rng = np.random.default_rng(seed)
    frames = np.zeros((count, height, width, 3), np.uint8)
    for y, x in zip(rng.integers(0, height, 200), rng.integers(0, width, 200), strict=True):
        frames[:, y, x] = 220  # stars that stay
    frames[:, 150:250] = rng.integers(0, 255, (count, 100, width, 3))  # the moving picture
    return frames


def test_text_drawn_all_reel_long_stays_and_captions_do_not():
    frames = _sky()
    frames[:, 40:60, 40:160] = 255  # a page's line, on every frame
    assert layout.steady(frames, (0, 0, 0), (30, 30, 170, 70))
    for i in range(len(frames)):
        frames[i, 300:320] = 0
        frames[i, 300:320, 20 * i : 20 * i + 60] = 255  # captions coming and going
    assert not layout.steady(frames, (0, 0, 0), (0, 290, 200, 330))


def test_a_starry_background_is_learned_without_the_picture_or_text():
    frames = _sky()
    frames[:, 40:60, 40:160] = 255
    sky = layout.backdrop(frames, (0, 0, 0), (0.0, 150 / 400, 1.0, 250 / 400), [(40, 40, 160, 60)])
    assert sky is not None and sky.shape == (400, 200, 3)
    assert sky[40:60, 40:160].max() < 255 or (sky[40:60, 40:160] == 255).mean() < 0.05
    assert (sky[150:250] > 30).mean() < 0.05  # stars, not the picture, where the picture was
    plain = np.zeros((6, 400, 200, 3), np.uint8)
    assert layout.backdrop(plain, (0, 0, 0), (0.0, 0.4, 1.0, 0.6), []) is None
