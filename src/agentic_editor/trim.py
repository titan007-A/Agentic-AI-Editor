"""Where a reel starts and ends.

A reel is always one continuous stretch of the source, so its start and its end are the
only things this module changes. It trims silence left at either end (an instrumental
intro goes quiet once the instruments are removed), and when the material runs longer
than the preset allows it chooses which continuous window to keep.
"""

from agentic_editor.analysis.beats import Beats
from agentic_editor.analyze import Analysis
from agentic_editor.edl import EDL
from agentic_editor.planner import Line, build_lines, fit_duration
from agentic_editor.presets import Preset

PREROLL = 0.25  # keep the breath before the first word
DECAY = 0.6  # let the last note or word ring out
MIN_SILENT_EDGE = 0.6  # shorter quiet at an edge reads as a natural lead-in
BLIP = 0.3  # sound this short between two silences is a click, not a voice
MIN_LONG_GAP = 4.0  # silence inside a window only matters past this, or past
GAP_FACTOR = 2.0  # this many times the source's usual gap between phrases
OPENING = 3.0  # seconds that decide whether people keep watching

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


def voice_bounds(start: float, end: float, silences: Silences) -> tuple[float, float]:
    """First and last moments with sound between start and end."""
    silences = merged(silences)
    first, last = start, end
    for s, e in silences:
        if s <= first < e:
            first = e
    for s, e in sorted(silences, reverse=True):
        if s < last <= e:
            last = s
    return first, last


def trim_silence(start: float, end: float, silences: Silences) -> tuple[float, float, list[str]]:
    first, last = voice_bounds(start, end, silences)
    if first >= last:
        return start, end, []  # nothing audible at all; leave it for review to flag
    notes = []
    if first - start >= MIN_SILENT_EDGE:
        notes.append(f"trimmed {first - start:.1f}s of silence at the start")
        start = first - PREROLL
    if end - last >= MIN_SILENT_EDGE:
        notes.append(f"trimmed {end - last:.1f}s of silence at the end")
        end = min(end, last + DECAY)
    return start, end, notes


def gap_limit(silences: Silences) -> float:
    """How long a silence has to be to stand out. Songs pause a couple of seconds between
    every sung phrase; only breaks well beyond the usual pause are worth avoiding."""
    gaps = sorted(e - s for s, e in merged(silences))
    usual = gaps[len(gaps) // 2] if gaps else 0.0
    return max(MIN_LONG_GAP, GAP_FACTOR * usual)


def long_gaps(start: float, end: float, silences: Silences, limit: float) -> float:
    """Seconds of unusually long silence inside a window."""
    return sum(
        min(end, b) - max(start, a)
        for a, b in merged(silences)
        if b - a >= limit and b > start and a < end
    )


def score(start: float, end: float, anchor: float, limit: float, analysis: Analysis) -> float:
    """Loud and voiced throughout, a strong opening, near where the planner started."""
    return (
        analysis.energy_between(start, end)
        + 0.5 * analysis.energy_between(start, min(end, start + OPENING))
        - 0.5 * long_gaps(start, end, analysis.silences, limit)
        - 0.05 * abs(start - anchor)
    )


def choose_window(
    start: float, end: float, lines: list[Line], analysis: Analysis, preset: Preset
) -> tuple[float, float, str]:
    """The best continuous window inside [start, end] that fits the preset, starting where
    a line starts and ending where one ends, so nothing is cut mid-word or mid-note.

    The plan was only too long, so for each possible start the window keeps as much as
    the preset allows; what is chosen is where that window sits."""
    inside = [ln for ln in lines if ln.start >= start - 1e-6 and ln.end <= end + 1e-6]
    limit = gap_limit(analysis.silences)
    windows = []
    for i, first in enumerate(inside):
        s = max(start, first.start - PREROLL)
        ends = [
            min(end, last.end + DECAY)
            for last in inside[i:]
            if preset.min_duration <= min(end, last.end + DECAY) - s <= preset.max_duration
        ]
        if ends:
            windows.append((s, max(ends)))
    best: tuple[float, float, float] | None = None
    if windows:
        # Windows that start late run into the end of the material and come out short;
        # only those close to the longest possible compete.
        longest = max(e - s for s, e in windows)
        for s, e in windows:
            if e - s >= 0.85 * longest:
                value = score(s, e, start, limit, analysis)
                if best is None or value > best[0]:
                    best = (value, s, e)
    if best is not None:
        _, s, e = best
        return (
            s,
            e,
            f"kept {s:.1f}-{e:.1f}s, the strongest {e - s:.0f}s stretch that fits "
            f"{preset.max_duration:g}s",
        )
    # No line-aligned window fits, usually one very long sung or spoken passage: keep the
    # opening and stop at the limit, after the last line that still fits.
    s = max(start, inside[0].start - PREROLL) if inside else start
    fitting = [
        ln.end for ln in inside if s + preset.min_duration <= ln.end <= s + preset.max_duration
    ]
    e = min(fitting[-1] + DECAY, s + preset.max_duration) if fitting else s + preset.max_duration
    return (
        s,
        e,
        f"kept {s:.1f}-{e:.1f}s; no window ending between lines fits, so it stops at the limit",
    )


def snap_to_beat(
    start: float, end: float, rhythm: Beats, max_duration: float
) -> tuple[float, float, str | None]:
    """Move the start back onto the bar line or beat just before it, and the end on to the
    next one after, so the reel opens and closes with the music. Only ever outward, so no
    word is cut, and only by a beat or two, so no long quiet lead-in is added."""
    beat = rhythm.interval
    if not beat:
        return start, end, None

    def before(times: list[float], reach: float) -> float | None:
        found = [t for t in times if start - reach <= t <= start]
        return max(found) if found else None

    def after(times: list[float], reach: float, opening: float) -> float | None:
        found = [t for t in times if end <= t <= end + reach and t - opening <= max_duration]
        return min(found) if found else None

    new_start = before(rhythm.downbeats, 2 * beat) or before(rhythm.beats, beat) or start
    new_end = after(rhythm.downbeats, 2 * beat, new_start) or after(rhythm.beats, beat, new_start)
    new_end = new_end or end
    if (new_start, new_end) == (start, end):
        return start, end, None
    note = f"start and end moved onto the beat ({rhythm.tempo:.0f} BPM)"
    return new_start, new_end, note


def apply(edl: EDL, analysis: Analysis, preset: Preset) -> EDL:
    clip = edl.clips[0]
    start, end, notes = trim_silence(clip.start, clip.end, analysis.silences)
    lines = build_lines(analysis)
    if end - start > preset.max_duration + 0.25:
        start, end, note = choose_window(start, end, lines, analysis, preset)
        notes.append(note)
    clip = clip.model_copy(update={"start": round(start, 3), "end": round(end, 3)})
    if clip.duration < preset.min_duration:
        grown = fit_duration([clip], lines, preset)[0]
        if grown != clip:
            notes.append(f"extended to {grown.start:.1f}-{grown.end:.1f}s with neighbouring lines")
            clip = grown
        if clip.duration < preset.min_duration - 0.5:
            notes.append(
                f"only {clip.duration:.1f}s of voiced material; left shorter than the "
                f"{preset.min_duration:g}s minimum rather than padded with silence"
            )
    if preset.beat_sync.snap_trims:
        start, end, note = snap_to_beat(clip.start, clip.end, analysis.beats, preset.max_duration)
        if note:
            clip = clip.model_copy(update={"start": round(start, 3), "end": round(end, 3)})
            notes.append(note)
    return edl.model_copy(update={"clips": [clip], "decisions": [*edl.decisions, *notes]})
