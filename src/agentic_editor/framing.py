"""Shot-by-shot framing for a vertical crop, so nothing that matters gets cut.

What must stay in frame comes from two places: heads from the face detector (fast, every
fifth of a second) and the vision model's must-keep boxes (heads it can see in profile,
the microphone or instrument being used, readable text) on a few frames per shot. Every
shot then gets the plainest framing that keeps all of it:

- fixed: everything fits one full-height 9:16 window for the whole shot;
- follow: at each moment it fits, but the subject moves, so the window tracks the head;
- fit: it is wider than 9:16, so the window widens to hold it and the picture is padded
  above and below, up to showing the whole frame, rather than cutting anything off.

A picture already narrower than the frame keeps its full width instead and is cropped top
and bottom around the same things, or shown whole with the sides padded.

A long shot is only split where it changes between fitting and not fitting, which is
usually a name banner or title animating in or out. Widened shots then share one width,
and when most of the reel needs widening every shot gets it, so the framing doesn't jump
between sizes at each cut.
"""

import itertools
import math
from dataclasses import dataclass

import numpy as np

from agentic_editor.analysis.faces import Sample, smooth
from agentic_editor.analysis.subjects import Subject
from agentic_editor.edl import Clip, Shot

MARGIN = 0.04  # of the crop width, added around what must stay when a window is widened
UNIFORM = 0.5  # share of the reel needing a wider window past which every shot gets it
HEAD_WIDTH = 1.4  # a face box is narrower than the head and hair around it
FULL = 0.85  # a window this close to the whole picture just shows the whole picture
MIN_SHOT = 1.0  # seconds; a cut closer than this to another isn't worth reframing for
VIEW_EVERY = 4.0  # seconds between vision samples inside a long shot
FACE_NEAR = 0.5  # seconds: faces this close to a vision sample count with it
DROPOUT = 2.0  # seconds: a face the detector lost this briefly is still the same face
HEADROOM = 0.12  # of a window's height, left above the heads when cropping top and bottom

Content = tuple[int, int, int, int]  # x, y, width, height of the picture, bars excluded
Span = tuple[float, float]


@dataclass(frozen=True)
class Moving:
    """The rows of a picture that move when part of it is a still image (a photo above a
    clip), and where a window taller than them takes its spare rows: "above" or "below",
    the plainer side."""

    top: int
    bottom: int
    spare: str = "below"


@dataclass(frozen=True)
class View:
    """What must stay in frame at one moment, as a horizontal span in source pixels."""

    time: float
    span: Span | None


def even(value: float) -> int:
    return int(value) // 2 * 2


def crop_width(content: Content, frame: tuple[int, int]) -> int:
    return even(content[3] * frame[0] / frame[1])


def crop_height(content: Content, frame: tuple[int, int]) -> int:
    return even(content[2] * frame[1] / frame[0])


def view_times(clip: Clip, cuts: list[float], every: float | None = VIEW_EVERY) -> list[float]:
    """Where to sample for the vision model: early in each shot, then every few seconds
    (or, with `every` None, only once a shot)."""
    times = []
    for start, end in shot_bounds(clip, cuts):
        t = start + min(0.5, (end - start) / 2)
        while t < end:
            times.append(round(t, 3))
            if every is None:
                break
            t += every
    return times


def shot_bounds(clip: Clip, cuts: list[float]) -> list[Span]:
    inner = [c for c in sorted(cuts) if clip.start + MIN_SHOT < c < clip.end - MIN_SHOT]
    bounds, previous = [], clip.start
    for cut in inner:
        if cut - previous >= MIN_SHOT:
            bounds.append((previous, cut))
            previous = cut
    bounds.append((previous, clip.end))
    return bounds


def union(spans: list[Span]) -> Span | None:
    return (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None


def head_spans(faces: list[Sample], around: float, src_width: int) -> list[Span]:
    """Heads in the face sample closest to `around`. One instant only: a head moving over
    a second is a reason to follow it, not to widen the window."""
    near = [(abs(t - around), found) for t, found in faces if abs(t - around) <= FACE_NEAR]
    if not near:
        return []
    _, found = min(near, key=lambda pair: pair[0])
    spans = []
    for face in found:
        half = face.width * src_width * HEAD_WIDTH / 2
        spans.append((face.cx * src_width - half, face.cx * src_width + half))
    return spans


def keep_spans(
    subjects: list[Subject], faces: list[Sample], at: float, src_width: int
) -> list[Span]:
    """Each thing that must stay whole at one moment: heads from the face detector, plus the
    vision model's must-keep items, minus heads the detector already has."""
    heads = head_spans(faces, at, src_width)
    found = []
    for s in subjects:
        if not s.keep or (s.kind == "head" and _detected(s, heads)):
            continue
        if s.kind == "head":
            # The detector often loses a face for a moment when the head turns; the same
            # face a second or two away is a far better guide than the vision model's
            # loose head box.
            nearby = _nearby_head(s, faces, at, src_width)
            if nearby is not None:
                heads.append(nearby)
                continue
        found.append((s.x0, s.x1))
    return found + heads


def crossing(spans: list[Span], x: float, width: int, slack: float = 4.0) -> list[Span]:
    """Spans that a window's edge really cuts through: more than a tenth of the thing (and
    more than a few pixels) left outside. Head spans include an estimate for the hair, and
    a following crop lags its subject slightly; slivers of either are not a visible cut."""
    left, right = x, x + width
    cut = []
    for a, b in spans:
        outside = max(0.0, min(b, left) - a) + max(0.0, b - max(a, right))
        inside = min(b, right) - max(a, left)
        if inside > 0 and outside > max(slack, 0.1 * (b - a)):
            cut.append((a, b))
    return cut


def check_times(
    shots: list[Shot], sampled: list[float], per_shot: int = 2
) -> list[tuple[int, float]]:
    """Fresh moments to re-check each cropped shot at: between the samples it was framed
    from, where something may have moved in that the first pass didn't see."""
    out = []
    for k, shot in enumerate(shots):
        inside = [t for t in sampled if shot.start <= t < shot.end]
        points = [shot.start, *inside, shot.end]
        gaps = sorted(itertools.pairwise(points), key=lambda p: p[1] - p[0], reverse=True)
        for a, b in gaps[:per_shot]:
            if b - a >= 1.0:
                out.append((k, round((a + b) / 2, 3)))
    return out


def widen_to(shot: Shot, needed: Span, content: Content, width: int) -> Shot:
    """Widen a shot's window just enough to hold `needed` as well, with a margin."""
    left = min(shot.x, needed[0]) - MARGIN * width
    right = max(shot.x + shot.width, needed[1]) + MARGIN * width
    if right - left >= FULL * content[2]:
        x, wide = content[0], even(content[2])
    else:
        wide = even(right - left)
        x = _place((left, right), wide, content)
    return shot.model_copy(
        update={
            "x": x,
            "width": wide,
            "follow": False,
            "reason": "widened after a re-check found something at the edge",
        }
    )


def views(
    samples: list[tuple[float, list[Subject]]],
    faces: list[Sample],
    content: Content,
    src_width: int,
) -> list[View]:
    """Must-keep spans at each vision sample, faces nearby included. No margin is added:
    a window is only widened when something would really be cut, not to keep a gap."""
    left, right = content[0], content[0] + content[2]
    out = []
    for t, subjects in samples:
        whole = union(keep_spans(subjects, faces, t, src_width))
        if whole is not None:
            whole = (max(left, whole[0]), min(right, whole[1]))
        out.append(View(t, whole))
    return out


def _nearby_head(head: Subject, faces: list[Sample], at: float, src_width: int) -> Span | None:
    """The head span of a detected face inside this vision head box, from the face sample
    nearest in time within DROPOUT seconds."""
    best: tuple[float, Span] | None = None
    for t, found in faces:
        if abs(t - at) > DROPOUT:
            continue
        for face in found:
            cx = face.cx * src_width
            if head.x0 <= cx <= head.x1 and (best is None or abs(t - at) < best[0]):
                half = face.width * src_width * HEAD_WIDTH / 2
                best = (abs(t - at), (cx - half, cx + half))
    return best[1] if best else None


def _detected(head: Subject, detected: list[Span]) -> bool:
    """Whether the face detector already found this head. Its boxes are tight; the vision
    model's head boxes run loose, sometimes taking in the shoulders, so the detector wins."""
    return any(head.x0 <= (a + b) / 2 <= head.x1 for a, b in detected)


def _fits(view: View, width: int) -> bool:
    return view.span is None or even(view.span[1] - view.span[0]) <= width


def _place(span: Span, width: int, content: Content) -> int:
    """Left edge of a window of `width` centred on span, kept inside the picture."""
    left, right = content[0], content[0] + content[2]
    x = (span[0] + span[1]) / 2 - width / 2
    return even(min(max(x, left), right - width))


def frame_shots(
    clip: Clip,
    cuts: list[float],
    samples: list[tuple[float, list[Subject]]],
    faces: list[Sample],
    content: Content,
    src_width: int,
    frame: tuple[int, int],
    src_height: int | None = None,
    moving: Moving | None = None,
) -> list[Shot]:
    width = crop_width(content, frame)
    if width >= content[2]:
        return tall_shots(
            clip, cuts, samples, faces, content, frame, src_height or content[1] + content[3],
            moving,
        )  # fmt: skip
    seen = views(samples, faces, content, src_width)
    shots: list[Shot] = []
    for start, end in shot_bounds(clip, cuts):
        inside = [v for v in seen if start <= v.time < end]
        if not inside:
            inside = [View(start, None)]
        # Split only where the shot changes between fitting and not fitting.
        groups: list[list[View]] = [[inside[0]]]
        for view in inside[1:]:
            if _fits(view, width) == _fits(groups[-1][-1], width):
                groups[-1].append(view)
            else:
                groups.append([view])
        # Between a sample that fits and one that doesn't, the wider framing takes the
        # whole gap: showing a little more for a moment is harmless, slicing a banner as
        # it slides in is not.
        middles = [
            a[-1].time if not _fits(b[0], width) else b[0].time
            for a, b in itertools.pairwise(groups)
        ]
        edges = [start, *middles, end]
        parts = [
            _frame_group(group, a, b, width, content, src_width, faces)
            for group, (a, b) in zip(groups, itertools.pairwise(edges), strict=True)
        ]
        shots += absorb_short(parts)
    return harmonize(shots, width, content)


def absorb_short(parts: list[Shot]) -> list[Shot]:
    """Fold framing changes shorter than MIN_SHOT inside one camera shot into a neighbour,
    keeping the wider framing: a half-second reframe reads as a glitch."""
    parts = list(parts)
    while len(parts) > 1:
        k = min(range(len(parts)), key=lambda i: parts[i].end - parts[i].start)
        short = parts[k]
        if short.end - short.start >= MIN_SHOT:
            break
        neighbours = [i for i in (k - 1, k + 1) if 0 <= i < len(parts)]
        n = max(neighbours, key=lambda i: parts[i].width)
        keep = parts[n] if parts[n].width >= short.width else short
        merged = keep.model_copy(
            update={
                "start": min(short.start, parts[n].start),
                "end": max(short.end, parts[n].end),
            }
        )
        parts[min(k, n)] = merged
        del parts[max(k, n)]
    return parts


def harmonize(shots: list[Shot], width: int, content: Content) -> list[Shot]:
    """Give every widened shot the same width, and every shot that width when most of the
    reel needs it, re-centred on where each window was so nothing it held is lost."""
    wide = [s for s in shots if s.width > width]
    if not wide:
        return shots
    target = max(s.width for s in wide)
    total = sum(s.end - s.start for s in shots)
    uniform = sum(s.end - s.start for s in wide) / total >= UNIFORM
    out = []
    for shot in shots:
        if shot.width == target or not (uniform or shot.width > width):
            out.append(shot)
            continue
        centre = shot.x + shot.width / 2
        reason = (
            "same framing as the rest of the reel, which is mostly too wide for 9:16"
            if shot.width <= width
            else shot.reason
        )
        out.append(
            shot.model_copy(
                update={
                    "x": _place((centre, centre), target, content),
                    "width": target,
                    "follow": False,
                    "reason": reason,
                }
            )
        )
    return out


def on_moving(
    faces: list[Sample],
    samples: list[tuple[float, list[Subject]]],
    moving: Moving | None,
    src_height: int,
) -> tuple[list[Sample], list[tuple[float, list[Subject]]]]:
    """The faces and subjects in the moving part of a picture that is partly a still image
    (see compose.moving_part): a face in a photo above a clip isn't the one to frame. Text
    stays, wherever it is."""
    if moving is None:
        return faces, samples
    y0, y1 = moving.top, moving.bottom
    faces = [(t, [f for f in seen if y0 <= f.cy * src_height < y1]) for t, seen in faces]
    samples = [
        (t, [s for s in items if s.kind == "text" or y0 <= (s.y0 + s.y1) / 2 < y1])
        for t, items in samples
    ]
    return faces, samples


def holding(lo: float, hi: float, height: float, moving: Moving | None) -> Span:
    """The rows within [lo, hi] a window `height` rows tall may take so that it holds the
    moving part of the picture, its spare rows on the plainer side, or stays inside it
    when that is taller."""
    if moving is None:
        return lo, hi
    y0, y1 = moving.top, moving.bottom
    if y1 - y0 >= height:
        wanted = [(y0, y1)]
    else:
        side = (y0, y0 + height) if moving.spare == "below" else (y1 - height, y1)
        wanted = [side, (y1 - height, y0 + height)]
    for a, b in wanted:
        a, b = max(a, lo), min(b, hi)
        if b - a >= height:
            return a, b
    return lo, hi


def tall_shots(
    clip: Clip,
    cuts: list[float],
    samples: list[tuple[float, list[Subject]]],
    faces: list[Sample],
    content: Content,
    frame: tuple[int, int],
    src_height: int,
    moving: Moving | None = None,
) -> list[Shot]:
    """Shots for a picture as narrow as the frame or narrower. One taller than the frame is
    cropped top and bottom, at its full width, around the heads and must-keep items of each
    shot; when those don't fit one window the whole picture is shown, padded at the sides.
    The moving part of a picture that is partly a still image is always in the window."""
    left, top, picture_width, picture_height = content
    height = crop_height(content, frame)
    if height >= picture_height - 1:
        return [Shot(start=clip.start, end=clip.end, x=left, width=picture_width)]
    faces, samples = on_moving(faces, samples, moving, src_height)
    lo, hi = holding(top, top + picture_height, height, moving)
    shots = []
    for start, end in shot_bounds(clip, cuts):
        rows = [
            ((f.cy - 0.85 * f.height) * src_height, (f.cy + 0.6 * f.height) * src_height)
            for t, found in faces
            if start <= t < end
            for f in found
        ]
        rows += [(s.y0, s.y1) for t, found in samples if start <= t < end for s in found if s.keep]
        rows = [(max(a, top), min(b, top + picture_height)) for a, b in rows if b > top]
        whole = union(rows)
        if whole is not None and whole[1] - whole[0] > height:
            reason = "too tall for the frame; showing the whole picture"
            shots.append(Shot(start=start, end=end, x=left, width=picture_width, reason=reason))
            continue
        if whole is None:
            y = lo + (hi - lo - height) / 2
            reason = "nothing that must stay; centred on the picture"
        else:
            # Some room above the heads, as a camera operator would leave.
            y = max(whole[0] - HEADROOM * height, whole[1] - height)
            reason = "everything that matters fits the frame's height"
        y = even(min(max(y, lo), hi - height))
        shots.append(Shot(start=start, end=end, x=left, width=picture_width, y=y, height=height,
                          reason=reason))  # fmt: skip
    whole_share = sum(s.end - s.start for s in shots if s.height is None) / clip.duration
    if 0 < whole_share < 1 and whole_share >= UNIFORM:
        shots = [
            s.model_copy(
                update={
                    "y": 0,
                    "height": None,
                    "reason": "the whole picture, as for most of the reel, which is too tall",
                }
            )
            for s in shots
        ]
    return shots


EDGE_TEXT = 0.35  # of the picture's height: text this near its top or bottom is cut off
JUMP = 0.33  # of a band window's height: how far the main face jumps at a cut the detector missed
MIN_RUN = 1.0  # seconds: no window is held for less, so the picture doesn't twitch
# A band window stays where it sits for most of the reel unless the main face would lose more
# than this share of its height (a little forehead or chin is fine)...
FACE_TOLERANCE = 0.15
# ...for at least this long; a briefer moment isn't worth moving the picture for.
BRIEF = 2.0
# Of the picture's height, kept clear of text that is cut off: the vision model's boxes are
# often a few dozen rows short of the letters, which then peek in at the window's edge.
TEXT_MARGIN = 0.025


def band_shots(
    clip: Clip,
    cuts: list[float],
    samples: list[tuple[float, list[Subject]]],
    faces: list[Sample],
    content: Content,
    rows: int,
    src_height: int,
    texts: list[tuple[int, int]] = (),
    moving: Moving | None = None,
) -> list[Shot]:
    """Shots for a picture shown across the frame's whole width in a band of `rows` source
    rows' height, between a preset's text. Never cropped at the sides: a picture taller
    than the band loses its top and bottom, around the heads and must-keep items of each
    shot. Text the picture brings with it near its top or bottom edge (lyrics, a channel
    name) is cut off too, unless a head is in the way; `texts` are such text rows the
    caller found some other way, such as watermarks. The `moving` part of a picture that is
    partly a still image is always in the window, and only its faces count."""
    left, top, picture_width, picture_height = content
    faces, samples = on_moving(faces, samples, moving, src_height)
    found = [(s.y0, s.y1) for _, items in samples for s in items if s.kind == "text"] + list(texts)
    heads_all = [
        ((f.cy - 0.85 * f.height) * src_height, (f.cy + 0.6 * f.height) * src_height)
        for _, seen in faces
        for f in seen
    ] + [(s.y0, s.y1) for _, items in samples for s in items if s.kind == "head"]
    lo, hi = top, top + picture_height
    for y0, y1 in found:
        if any(a < y1 and b > y0 for a, b in heads_all):
            continue
        if y1 <= top + EDGE_TEXT * picture_height:
            lo = max(lo, y1 + TEXT_MARGIN * picture_height)
        elif y0 >= top + (1 - EDGE_TEXT) * picture_height:
            hi = min(hi, y0 - TEXT_MARGIN * picture_height)
    if hi - lo < picture_height / 2:  # text all over: keep the picture, the text stays
        lo, hi = top, top + picture_height
    height = even(min(rows, hi - lo))
    if height >= picture_height - 1:
        return [
            Shot(start=clip.start, end=clip.end, x=left, width=picture_width, y=top,
                 height=picture_height, reason="the whole picture fits")
        ]  # fmt: skip
    lo, hi = holding(lo, hi, height, moving)

    def head_rows(t0: float, t1: float, core: bool = False) -> list[tuple[float, float]]:
        """Rows the heads cover in [t0, t1): hair to chin, or only the face itself."""
        above, below = (0.55, 0.55) if core else (0.85, 0.6)
        rows_ = [
            ((f.cy - above * f.height) * src_height, (f.cy + below * f.height) * src_height)
            for t, seen in faces
            if t0 <= t < t1
            for f in seen
        ] + [
            (s.y0, s.y1) for t, items in samples if t0 <= t < t1 for s in items if s.kind == "head"
        ]
        return [(max(a, lo), min(b, hi)) for a, b in rows_ if b > lo and a < hi]

    def frame(start: float, end: float) -> Shot:
        heads = head_rows(start, end)
        keep = heads + [
            (max(s.y0, lo), min(s.y1, hi))
            for t, items in samples
            if start <= t < end
            for s in items
            if s.keep and s.kind not in ("head", "text") and s.y1 > lo and s.y0 < hi
        ]
        # Everything that must stay if it fits, else the heads alone, else the faces.
        whole = union(keep)
        if whole is None or whole[1] - whole[0] > height:
            whole = union(heads)
        if whole is not None and whole[1] - whole[0] > height:
            # A close-up too big for the band with its hair: the main singer's face in the
            # middle, where it mostly is, so a passing face or a stray detection doesn't
            # pull the window off it.
            centres = main_faces(start, end)
            middle = float(np.median(centres)) if centres else (whole[0] + whole[1]) / 2
            y = middle - height / 2
            reason = "a close-up: the face in the middle"
        elif whole is None:
            y = lo + (hi - lo - height) / 2
            reason = "nothing that must stay; the middle of the picture"
        else:
            # The main face in the upper third, as a camera operator frames a person, as
            # long as everything that must stay is still in.
            centres = main_faces(start, end)
            y = float(np.median(centres)) - height / 3 if centres else whole[0] - HEADROOM * height
            y = min(whole[0], max(y, whole[1] - height))
            reason = "the picture's top and bottom cut around what matters"
        y = even(min(max(y, lo), hi - height))
        return Shot(start=start, end=end, x=left, width=picture_width, y=y, height=height,
                    reason=reason)  # fmt: skip

    def main_faces(t0: float, t1: float) -> list[float]:
        """The middle row of the biggest face, the main singer's, in each sample."""
        return [
            max(seen, key=lambda f: f.area).cy * src_height
            for t, seen in faces
            if t0 <= t < t1 and seen
        ]

    def face_runs(start: float, end: float) -> list[tuple[float, float]]:
        """The shot split where the main face jumps and stays: a cut the detector missed.
        A face that only drifts, or one stray sighting, keeps the shot whole."""
        track = sorted(
            (t, max(seen, key=lambda f: f.area).cy * src_height)
            for t, seen in faces
            if start <= t < end and seen
        )
        runs, run_start, run = [], start, []
        for i, (t, centre) in enumerate(track):
            moved = run and abs(centre - float(np.median(run))) > JUMP * height
            stays = i + 1 < len(track) and abs(track[i + 1][1] - centre) <= JUMP * height
            split = (track[i - 1][0] + t) / 2 if i else t
            if moved and stays and split - run_start >= MIN_RUN and end - split >= MIN_RUN:
                runs.append((run_start, split))
                run_start, run = split, []
            run.append(centre)
        runs.append((run_start, end))
        return runs

    def face_track(t0: float, t1: float) -> list[tuple[float, float]]:
        """Forehead and chin rows of the main face in each sample of [t0, t1)."""
        return [
            ((f.cy - 0.55 * f.height) * src_height, (f.cy + 0.55 * f.height) * src_height)
            for t, seen in faces
            if t0 <= t < t1 and seen
            for f in [max(seen, key=lambda f: f.area)]
        ]

    def cut_for(y: float, shot: Shot) -> float:
        """Seconds of the shot in which a window at row y cuts the main face past tolerance."""
        track = face_track(shot.start, shot.end)
        if not track:
            return 0.0
        each = (shot.end - shot.start) / len(track)
        lost = [
            (max(0.0, y - top) + max(0.0, bottom - y - height)) / (bottom - top)
            for top, bottom in track
        ]
        return each * sum(share > FACE_TOLERANCE for share in lost)

    def keeping(y: float, shot: Shot) -> float:
        """The row nearest y at which the shot's main face is whole (or, if it moves too much
        for that, within tolerance), or else the shot's own best row. A window that has to
        move anyway goes far enough to show the whole face."""
        track = face_track(shot.start, shot.end)
        if not track:
            return shot.y
        for tolerance in (0.0, FACE_TOLERANCE):
            low = max(b - tolerance * (b - a) for a, b in track) - height
            high = min(a + tolerance * (b - a) for a, b in track)
            if low <= high:
                # On an even row at or past `low`, so rounding never cuts what was kept.
                return min(max(even(y), math.ceil(low / 2) * 2), even(high))
        return shot.y

    # Each shot's own best window, and whether it begins inside a shot of the source (a cut
    # the detector missed) rather than at one of its cuts.
    pieces: list[tuple[Shot, bool]] = []
    for start, end in shot_bounds(clip, cuts):
        heads = union(head_rows(start, end))
        runs = face_runs(start, end) if heads is not None and heads[1] - heads[0] > height else []
        for i, (a, b) in enumerate(runs or [(start, end)]):
            pieces.append((frame(a, b), i > 0))
    # One window for the whole reel, where the shots mostly want it: a picture that moves at
    # every cut makes each cut a jolt.
    framed = [shot for shot, _ in pieces if not shot.reason.startswith("nothing")]
    anchor = weighted_median([s.y for s in framed], [s.end - s.start for s in framed])
    shots: list[Shot] = []
    for shot, inside in pieces:
        y, reason = shot.y, shot.reason
        if anchor is not None:
            y, reason = anchor, "the reel's one window"
            if cut_for(anchor, shot) >= BRIEF:
                y = keeping(anchor, shot)
                reason = "moved only as far as the face needs"
        y = even(min(max(y, lo), hi - height))
        if shots and shots[-1].y == y:
            shots[-1] = shots[-1].model_copy(update={"end": shot.end})
            continue
        # Moving at a cut of the source hides the move; inside a shot it glides.
        shots.append(shot.model_copy(update={"y": y, "reason": reason, "glide": inside}))
    if shots:
        shots[0] = shots[0].model_copy(update={"glide": False})
    return shots


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    order = sorted(zip(values, weights, strict=True))
    half, total = sum(weights) / 2, 0.0
    for value, weight in order:
        total += weight
        if total >= half:
            return value
    return order[-1][0]


def _frame_group(
    group: list[View],
    start: float,
    end: float,
    width: int,
    content: Content,
    src_width: int,
    faces: list[Sample],
) -> Shot:
    spans = [v.span for v in group if v.span is not None]
    whole = union(spans)
    if whole is None:
        centre = union(head_spans(faces, (start + end) / 2, src_width))
        span = centre or (content[0], content[0] + content[2])
        return Shot(start=start, end=end, x=_place(span, width, content), width=width,
                    reason="nothing that must stay; centred on the picture")  # fmt: skip
    if even(whole[1] - whole[0]) <= width:
        return Shot(start=start, end=end, x=_place(whole, width, content), width=width,
                    reason="everything that matters fits a 9:16 crop")  # fmt: skip
    if all(even(s[1] - s[0]) <= width for s in spans):
        return Shot(start=start, end=end, x=_place(whole, width, content), width=width,
                    follow=True, reason="subject moves; the crop follows it")  # fmt: skip
    wide = whole[1] - whole[0] + 2 * MARGIN * width
    if wide >= FULL * content[2]:
        return Shot(start=start, end=end, x=content[0], width=even(content[2]),
                    reason="too wide for 9:16; showing the whole picture")  # fmt: skip
    wide = even(wide)
    return Shot(start=start, end=end, x=_place(whole, wide, content), width=wide,
                reason="too wide for 9:16; widened so nothing is cut")  # fmt: skip


def follow_path(
    shot: Shot,
    seen: list[View],
    faces: list[Sample],
    content: Content,
    src_width: int,
    smoothing: float,
) -> list[tuple[float, int]]:
    """Left edge of a following window over the shot, in seconds from the shot's start.

    The window centres on the head, but never so far that a must-keep item from the nearest
    vision sample (a mic, a caption) would fall outside it."""
    left, right = content[0], content[0] + content[2] - shot.width
    inside = [(t, f) for t, f in faces if shot.start <= t < shot.end and f]
    if not inside:
        return [(0.0, shot.x)]
    near = [v for v in seen if v.span is not None]
    xs, times = [], []
    for t, found in inside:
        face = max(found, key=lambda f: f.area)
        x = face.cx * src_width - shot.width / 2
        if near:
            view = min(near, key=lambda v: abs(v.time - t))
            x = min(max(x, view.span[1] - shot.width), view.span[0])
        xs.append(min(max(x, left), right))
        times.append(t - shot.start)
    return [(t, even(x)) for t, x in zip(times, smooth(xs, smoothing), strict=True)]


def summary(shots: list[Shot], width: int) -> str:
    """One line for the decision log."""
    widened = [s for s in shots if s.width > width]
    if widened and len(widened) == len(shots) and len({s.width for s in shots}) == 1:
        return (
            f"framed all {len(shots)} shot(s) the same, {shots[0].width}px of the picture "
            "wide, because most of the reel is too wide for 9:16"
        )
    followed = sum(s.follow for s in shots)
    line = f"framed {len(shots)} shot(s): {len(shots) - len(widened) - followed} cropped"
    if followed:
        line += f", {followed} following the subject"
    if widened:
        spans = ", ".join(f"{s.start:.1f}-{s.end:.1f}s" for s in widened)
        line += f", {len(widened)} widened so nothing is cut ({spans})"
    return line


def screen_rows(
    faces: list[Sample],
    clip: Clip,
    shots: list[Shot],
    picture: Content,
    src_size: tuple[int, int],
    frame: tuple[int, int],
    window: int,
    band: tuple[int, int] | None = None,
) -> list[tuple[float, list[Span]]]:
    """The rows of the output frame each visible face covers, at reel times.

    The band runs from above the forehead, where hair is, to below the chin. A face is
    on screen if its centre is inside the shot's window."""
    src_w, src_h = src_size
    boxes = [
        (
            t,
            [
                (
                    f.cx * src_w,
                    (f.cy - 0.85 * f.height) * src_h,
                    f.cx * src_w,
                    (f.cy + 0.6 * f.height) * src_h,
                )
                for f in found
            ],
        )
        for t, found in faces
    ]
    return box_rows(boxes, clip, shots, picture, src_size, frame, window, band)


def box_rows(
    boxes: list[tuple[float, list[tuple[float, float, float, float]]]],
    clip: Clip,
    shots: list[Shot],
    picture: Content,
    src_size: tuple[int, int],
    frame: tuple[int, int],
    window: int,
    band: tuple[int, int] | None = None,
) -> list[tuple[float, list[Span]]]:
    """The rows of the output frame each box (x0, y0, x1, y1 in source pixels) covers, at
    reel times, for the boxes the framing of the moment shows any of.

    A box is mapped through the shot's window when there are shots, otherwise one `window`
    pixels wide over the whole clip."""
    width, height = frame
    rows = []
    for t, found in boxes:
        if not clip.start <= t < clip.end:
            continue
        shot = next((s for s in shots if s.start <= t < s.end), None)
        span = shot.width if shot else window
        left = shot.x if shot and not shot.follow else None
        top, picture_height = picture[1], picture[3]
        if shot and shot.height:
            top, picture_height = shot.y, shot.height
        band_top, band_bottom = band or (0, height)
        scale = min(width / span, (band_bottom - band_top) / picture_height)
        offset = band_top + (band_bottom - band_top - picture_height * scale) / 2
        bands = []
        for x0, y0, x1, y1 in found:
            if left is not None and (x1 < left or x0 > left + span):
                continue  # cropped out of this shot
            bands.append(((y0 - top) * scale + offset, (y1 - top) * scale + offset))
        rows.append((t - clip.start, bands))
    return rows
