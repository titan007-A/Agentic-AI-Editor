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


def clean_edl(edl: EDL, words: list[Word], silences: list[tuple[float, float]]) -> EDL:
    return edl.model_copy(update={"clips": [snap_clip(c, words, silences) for c in edl.clips]})
