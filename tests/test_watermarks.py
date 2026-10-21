import numpy as np

from agentic_editor.analysis.watermarks import find, spans

FPS, HEIGHT, WIDTH = 2.0, 270, 480
rng = np.random.default_rng(1)
# Black and white stripes: hard edges that never move, like the strokes of a logo.
LOGO = np.tile(np.array([[0, 255]], np.uint8).repeat(3, axis=1), (14, 12))


def frames(seconds: float, logo_at=lambda t: None, moving=True):
    for i in range(int(seconds * FPS)):
        t = i / FPS
        if moving:
            frame = rng.integers(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)
        else:
            frame = np.full((HEIGHT, WIDTH), 90, np.uint8)
            frame[100:180, 150:330] = 200
        if (corner := logo_at(t)) is not None:
            y, x = corner
            frame[y : y + LOGO.shape[0], x : x + LOGO.shape[1]] = LOGO
        yield frame


def test_finds_a_fixed_corner_logo_for_the_whole_video():
    found = find(lambda: frames(20, lambda t: (10, 20)), (1920, 1080), 20.0)
    assert len(found) == 1
    mark = found[0]
    assert mark.x <= 20 * 4 and mark.x + mark.width >= (20 + LOGO.shape[1]) * 4
    assert mark.y <= 10 * 4 and mark.y + mark.height >= (10 + LOGO.shape[0]) * 4
    assert mark.spans == [(0.0, 20.0)]


def test_follows_a_logo_that_jumps_corners():
    def jumping():
        return frames(24, lambda t: (10, 20) if t < 12 else (230, 380))

    found = sorted(find(jumping, (1920, 1080), 24.0), key=lambda m: m.y)
    assert len(found) == 2
    assert found[0].spans == [(0.0, 12.0)]
    assert found[1].spans == [(12.0, 24.0)]


def test_a_still_shot_says_nothing():
    assert find(lambda: frames(12, lambda t: (10, 20), moving=False), (1920, 1080), 12.0) == []


def test_moving_picture_without_overlay_has_no_candidates():
    assert find(lambda: frames(12), (1920, 1080), 12.0) == []


def test_spans_bridge_a_single_missing_window():
    assert spans([0, 1, 3, 4, 8], 4.0, 40.0) == [(0.0, 20.0), (32.0, 36.0)]
