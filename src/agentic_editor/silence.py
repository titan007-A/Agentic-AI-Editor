"""Silences that matter: a click doesn't break one, and only a long one inside a reel
plays as dead air."""

from agentic_editor.analyze import Analysis

BLIP = 0.3  # sound this short between two silences is a click, not a voice
MIN_LONG_GAP = 4.0  # silence inside a window only matters past this, or past
GAP_FACTOR = 2.0  # this many times the source's usual gap between phrases

Silences = list[tuple[float, float]]


def merged(silences: Silences) -> Silences:
    """Join silences that only a blip separates, so a click doesn't count as the voice."""
    out: Silences = []
    for s, e in sorted(silences):
        if out and s - out[-1][1] < BLIP:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def gap_limit(analysis: Analysis) -> float:
    """How long a silence has to be to stand out. Songs pause a couple of seconds between
    every sung phrase, so on a vocal stem only breaks well beyond the usual pause count. In
    speech any long pause does: a noisy room may show no short silences at all, only the
    long ones, which would otherwise pass as usual."""
    if analysis.audio_source != "vocals":
        return MIN_LONG_GAP
    gaps = sorted(e - s for s, e in merged(analysis.silences))
    usual = gaps[len(gaps) // 2] if gaps else 0.0
    return max(MIN_LONG_GAP, GAP_FACTOR * usual)


def long_gaps(start: float, end: float, silences: Silences, limit: float) -> float:
    """Seconds of unusually long silence inside a window."""
    return sum(
        min(end, b) - max(start, a)
        for a, b in merged(silences)
        if b - a >= limit and b > start and a < end
    )


def long_silences(start: float, end: float, analysis: Analysis) -> Silences:
    """Silences well inside [start, end] long enough to play as dead air."""
    limit = gap_limit(analysis)
    return [
        (a, b)
        for a, b in merged(analysis.silences)
        if b - a >= limit and start + 0.5 < a and b < end - 0.5
    ]
