"""Shot-by-shot framing for a vertical crop, so nothing that matters gets cut.

What must stay in frame comes from two places: heads from the face detector (fast, every
fifth of a second) and the vision model's must-keep boxes (heads it can see in profile,
the microphone or instrument being used, readable text) on a few frames per shot. Every
shot then gets the plainest framing that keeps all of it:

- fixed: everything fits one full-height 9:16 window for the whole shot;
- follow: at each moment it fits, but the subject moves, so the window tracks the head;
- fit: it is wider than 9:16, so the window widens to hold it and the picture is padded
  above and below, up to showing the whole frame, rather than cutting anything off.

A long shot is only split where it changes between fitting and not fitting, which is
usually a name banner or title animating in or out. Widened shots then share one width,
and when most of the reel needs widening every shot gets it, so the framing doesn't jump
between sizes at each cut.
"""

import itertools
from dataclasses import dataclass

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

Content = tuple[int, int, int, int]  # x, y, width, height of the picture, bars excluded
Span = tuple[float, float]


@dataclass(frozen=True)
class View:
    """What must stay in frame at one moment, as a horizontal span in source pixels."""

    time: float
    span: Span | None


def even(value: float) -> int:
    return int(value) // 2 * 2


def crop_width(content: Content, frame: tuple[int, int]) -> int:
    return even(content[3] * frame[0] / frame[1])


def view_times(clip: Clip, cuts: list[float]) -> list[float]:
    """Where to sample for the vision model: early in each shot, then every few seconds."""
    times = []
    for start, end in shot_bounds(clip, cuts):
        t = start + min(0.5, (end - start) / 2)
        while t < end:
            times.append(round(t, 3))
            t += VIEW_EVERY
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
) -> list[Shot]:
    width = crop_width(content, frame)
    if width >= content[2]:
        # A picture already as narrow as the frame: show all of it.
        return [Shot(start=clip.start, end=clip.end, x=content[0], width=content[2])]
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
    return box_rows(boxes, clip, shots, picture, src_size, frame, window)


def box_rows(
    boxes: list[tuple[float, list[tuple[float, float, float, float]]]],
    clip: Clip,
    shots: list[Shot],
    picture: Content,
    src_size: tuple[int, int],
    frame: tuple[int, int],
    window: int,
) -> list[tuple[float, list[Span]]]:
    """The rows of the output frame each box (x0, y0, x1, y1 in source pixels) covers, at
    reel times, for the boxes the framing of the moment shows any of.

    A box is mapped through the shot's window when there are shots, otherwise one `window`
    pixels wide over the whole clip."""
    width, height = frame
    top, picture_height = picture[1], picture[3]
    rows = []
    for t, found in boxes:
        if not clip.start <= t < clip.end:
            continue
        shot = next((s for s in shots if s.start <= t < s.end), None)
        span = shot.width if shot else window
        left = shot.x if shot and not shot.follow else None
        scale = min(width / span, height / picture_height)
        offset = (height - picture_height * scale) / 2
        bands = []
        for x0, y0, x1, y1 in found:
            if left is not None and (x1 < left or x0 > left + span):
                continue  # cropped out of this shot
            bands.append(((y0 - top) * scale + offset, (y1 - top) * scale + offset))
        rows.append((t - clip.start, bands))
    return rows
