import numpy as np

from agentic_editor.analysis import watermarks
from agentic_editor.analysis.watermarks import Watermark, confirm, find, spans

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
    # Spans may run a little past the jump, never short of it: a blur held a moment too
    # long is better than a mark left showing.
    (a0, a1), (b0, b1) = found[0].spans[0], found[1].spans[0]
    assert len(found[0].spans) == len(found[1].spans) == 1
    assert a0 == 0.0 and 12.0 <= a1 <= 14.0
    assert 10.0 <= b0 <= 12.0 and b1 == 24.0


def test_follows_a_mark_that_jumps_every_five_seconds():
    # Off the 4 s window grid, so no fixed window ever saw one place for its whole length.
    def jumping():
        return frames(45, lambda t: (10, 20) if t % 10 < 5 else (120, 380))

    found = sorted(find(jumping, (1920, 1080), 45.0), key=lambda m: m.y)
    assert len(found) == 2
    top, side = found
    for start in (0, 10, 20, 30, 40):
        assert any(a <= start and start + 5 <= b for a, b in top.spans), (start, top.spans)
        assert not any(a <= start + 7 <= b for a, b in top.spans)
    for start in (5, 15, 25, 35):
        assert any(a <= start and start + 5 <= b for a, b in side.spans), (start, side.spans)
    # The vision model is shown a moment when the mark is really there.
    assert top.seen_at % 10 < 5 and side.seen_at % 10 >= 5


def test_a_still_shot_says_nothing():
    assert find(lambda: frames(12, lambda t: (10, 20), moving=False), (1920, 1080), 12.0) == []


def test_moving_picture_without_overlay_has_no_candidates():
    assert find(lambda: frames(12), (1920, 1080), 12.0) == []


def test_the_edge_of_the_picture_against_a_black_bar_is_not_an_overlay():
    # One sharp row that never moves, like where a letterboxed picture meets its bar, above
    # a smooth picture that does.
    def lined():
        for i in range(int(20 * FPS)):
            frame = np.tile(((np.arange(WIDTH) + 37 * i) % 256).astype(np.uint8), (HEIGHT, 1))
            frame[:30] = 0
            frame[30:33, 100:380] = 255
            yield frame

    assert find(lined, (1920, 1080), 20.0) == []


class Vision:
    """Reads `text` off the close-up and says `overlay` about the frame."""

    def __init__(self, text, overlay):
        self.text, self.overlay, self.asked = text, overlay, []

    def chat_json(self, messages, schema, images, **kwargs):
        self.asked.append(images)
        if "text" in schema["properties"]:
            return {"text": self.text}
        return {"overlay": self.overlay}


def test_only_a_mark_the_vision_model_can_read_is_an_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(watermarks, "_evidence", lambda *args: ["marked.jpg", "close.jpg"])
    mark = Watermark(x=0, y=0, width=40, height=20, spans=[(0.0, 5.0)], windows=4)

    def verdict(vision):
        return confirm(vision, tmp_path / "v.mp4", [mark], "ffmpeg", tmp_path)[0].confirmed

    assert verdict(Vision("@audit.channel", True))
    assert not verdict(Vision("GENROUTER H3000", False))
    # Nothing to read in the close-up: not asked about the frame, where a caption outside
    # the box would be read instead.
    empty = Vision("", True)
    assert not verdict(empty) and empty.asked == [["close.jpg"]]


def test_spans_bridge_a_single_missing_window():
    assert spans([0, 4, 12, 16, 32], 4.0, 40.0) == [(0.0, 20.0), (32.0, 36.0)]
    assert spans([0, 1, 2, 4, 5, 9], 4.0, 40.0, step=1.0) == [(0.0, 13.0)]
    assert spans([0, 0.5, 2.5, 6.0], 1.5, 40.0, step=0.5) == [(0.0, 4.0), (6.0, 7.5)]
