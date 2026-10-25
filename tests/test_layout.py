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
