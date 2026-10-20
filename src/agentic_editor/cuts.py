"""Deterministic cut cleanup: the planner picks rough times, this puts them on word
boundaries so no cut lands mid-word."""

from agentic_editor.edl import EDL, Clip
from agentic_editor.transcript import Word

LEAD = 0.12
TAIL = 0.30
LOOKAHEAD = 1.5


def snap_start(t: float, words: list[Word]) -> float:
    for i, word in enumerate(words):
        if word.end <= t:
            continue
        if word.start - t > LOOKAHEAD:
            return t
        earliest = (words[i - 1].end + word.start) / 2 if i else 0.0
        return max(earliest, word.start - LEAD)
    return t


def snap_end(t: float, words: list[Word]) -> float:
    for i in range(len(words) - 1, -1, -1):
        word = words[i]
        if word.start >= t:
            continue
        if t - word.end > LOOKAHEAD:
            return t
        if i + 1 < len(words):
            return min((word.end + words[i + 1].start) / 2, word.end + TAIL)
        return word.end + TAIL
    return t


def extend_to_silence(t: float, silences: list[tuple[float, float]], limit: float = 2.0) -> float:
    """Push an end time out to where the audio actually goes quiet, so held notes and
    trailing breaths aren't clipped."""
    for start, end in silences:
        if start <= t < end:
            return t
        if t < start <= t + limit:
            return start + 0.05
    return t


def snap_clip(clip: Clip, words: list[Word], silences: list[tuple[float, float]]) -> Clip:
    start = snap_start(clip.start, words)
    end = extend_to_silence(snap_end(clip.end, words), silences)
    if end <= start:
        return clip
    return clip.model_copy(update={"start": round(start, 3), "end": round(end, 3)})


def split_on_pauses(clip: Clip, words: list[Word], max_pause: float) -> list[Clip]:
    """Cut pauses longer than max_pause out of a clip."""
    inside = [w for w in words if clip.start <= w.start and w.end <= clip.end]
    pieces = []
    start = clip.start
    for prev, nxt in zip(inside, inside[1:], strict=False):
        if nxt.start - prev.end > max_pause:
            pieces.append(clip.model_copy(update={"start": start, "end": prev.end + TAIL}))
            start = nxt.start - LEAD
    pieces.append(clip.model_copy(update={"start": start, "end": clip.end}))
    return [p for p in pieces if p.duration > 0.2]


def clean_edl(
    edl: EDL, words: list[Word], silences: list[tuple[float, float]], max_pause: float | None
) -> EDL:
    clips = [snap_clip(c, words, silences) for c in edl.clips]
    if max_pause:
        clips = [piece for c in clips for piece in split_on_pauses(c, words, max_pause)]
    return edl.model_copy(update={"clips": clips})
