import numpy as np

from agentic_editor.analysis.onscreen import track
from agentic_editor.edl import Clip, Shot
from agentic_editor.framing import box_rows

BANNER = (40, 60, 200, 90)  # source pixels, at the 240 px sample width


def frames(banner_on: range, stripes_on: range, count: int = 12) -> np.ndarray:
    """A softly varying picture; a lettered banner on some frames, a striped shirt filling
    the same box on others."""
    rng = np.random.default_rng(1)
    out = np.empty((count, 120, 240), np.uint8)
    for i in range(count):
        frame = (100 + rng.integers(0, 8, size=(120, 240))).astype(np.uint8)
        x0, y0, x1, y1 = BANNER
        if i in banner_on:
            frame[y0:y1, x0:x1] = 30
            for x in range(x0 + 8, x1 - 8, 12):  # letters
                frame[y0 + 8 : y1 - 8, x : x + 4] = 230
        if i in stripes_on:
            frame[y0:y1, x0:x1] = np.where((np.arange(y1 - y0) // 2 % 2)[:, None], 220, 40)
        out[i] = frame
    return out


def test_text_is_followed_while_on_screen_and_stripes_in_its_place_are_not_it():
    times = [i * 0.25 for i in range(12)]
    shown = track(
        times, frames(banner_on=range(2, 6), stripes_on=range(8, 11)), [(1.0, BANNER)], 240
    )
    assert [t for t, boxes in shown if boxes] == [0.5, 0.75, 1.0, 1.25]


def test_text_maps_to_the_rows_it_covers_in_the_reel():
    clip = Clip(start=10.0, end=20.0)
    banner = (0.0, 795.0, 1918.0, 1002.0)
    widened = Shot(start=10.0, end=15.0, x=0, width=1920)
    cropped = Shot(start=15.0, end=20.0, x=1300, width=608)
    rows = box_rows(
        [(12.0, [banner]), (17.0, [(100.0, 300.0, 400.0, 360.0)])],
        clip, [widened, cropped], (0, 0, 1920, 1080), (1920, 1080), (1080, 1920), 608,
    )  # fmt: skip
    # The whole width scaled to 1080 and centred: 1080/1920 of the source's rows.
    (t, [(top, bottom)]), (later, outside) = rows
    assert t == 2.0 and round(top) == 1103 and round(bottom) == 1220
    assert later == 7.0 and outside == []  # cropped out of that shot
