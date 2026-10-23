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
from agentic_editor.silence import Silences, gap_limit, long_gaps, long_silences, merged

PREROLL = 0.25  # keep the breath before the first word
DECAY = 0.6  # let the last note or word ring out
MIN_SILENT_EDGE = 0.6  # shorter quiet at an edge reads as a natural lead-in
OPENING = 3.0  # seconds that decide whether people keep watching


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


def avoid_dead_air(
    start: float, end: float, preset: Preset, analysis: Analysis
) -> tuple[float, float, str | None]:
    """A reel is never cut inside, so when it runs through a long silence, keep the longest
    voiced side that is long enough on its own (the side with the opening, when the
    opening was chosen on purpose)."""
    quiet = long_silences(start, end, analysis)
    if not quiet:
        return start, end, None
    edges = [start, *[t for a, b in quiet for t in (a, b)], end]
    sides = [
        (
            edges[i] if i == 0 else edges[i] - PREROLL,
            edges[i + 1] if i + 2 == len(edges) else edges[i + 1] + DECAY,
        )
        for i in range(0, len(edges), 2)
    ]
    long_enough = [(a, b) for a, b in sides if b - a >= preset.min_duration]
    if not preset.hook.optimize:
        long_enough = long_enough[:1] if long_enough and long_enough[0][0] == start else []
    if not long_enough:
        return start, end, None
    a, b = max(long_enough, key=lambda side: side[1] - side[0])
    gap = next(q for q in quiet if q[1] <= a + PREROLL or q[0] >= b - DECAY)
    return a, b, f"kept {a:.1f}-{b:.1f}s, clear of {gap[1] - gap[0]:.0f}s without voice"


def score(start: float, end: float, anchor: float, limit: float, analysis: Analysis) -> float:
    """Loud and voiced throughout, a strong opening, near where the planner started."""
    return (
        analysis.energy_between(start, end)
        + 0.5 * analysis.energy_between(start, min(end, start + OPENING))
        - 0.5 * long_gaps(start, end, analysis.silences, limit)
        - 0.05 * abs(start - anchor)
    )


def keep_opening(
    start: float, end: float, lines: list[Line], analysis: Analysis, preset: Preset
) -> tuple[float, float, str]:
    """Too long, but the opening was chosen on purpose: keep it, from the start of its
    sentence, and end after the last whole sentence that fits, before any long silence."""
    inside = [ln for ln in lines if ln.start >= start - 1e-6 and ln.end <= end + 1e-6]
    if not inside:
        return start, start + preset.max_duration, f"kept the planned opening at {start:.1f}s"
    first = inside[0]
    begins = [ln for ln in lines if ln.opens and ln.start <= first.start]
    s = max(start, first.start - PREROLL)
    # Back to where the sentence begins, unless that would make it mostly lead-in.
    if begins and first.start - begins[-1].start <= preset.max_duration / 3:
        s = max(0.0, begins[-1].start - PREROLL)
    limit = gap_limit(analysis)
    quiet = [a for a, b in merged(analysis.silences) if b - a > limit and first.start < a < end]
    if quiet and quiet[0] - s >= preset.min_duration:
        end = quiet[0] + DECAY
        inside = [ln for ln in inside if ln.end <= quiet[0] + 1e-6]
    fits = [
        min(end, ln.end + DECAY)
        for ln in inside
        if preset.min_duration <= min(end, ln.end + DECAY) - s <= preset.max_duration
    ]
    whole = [
        min(end, ln.end + DECAY)
        for ln in inside
        if ln.closes and preset.min_duration <= min(end, ln.end + DECAY) - s <= preset.max_duration
    ]
    e = max(whole or fits or [s + preset.max_duration])
    return s, e, f"kept the planned opening at {s:.1f}s and ended at {e:.1f}s to fit"


def choose_window(
    start: float, end: float, lines: list[Line], analysis: Analysis, preset: Preset
) -> tuple[float, float, str]:
    """The best continuous window inside [start, end] that fits the preset, starting where
    a line starts and ending where one ends, so nothing is cut mid-word or mid-note.

    The plan was only too long, so for each possible start the window keeps as much as
    the preset allows; what is chosen is where that window sits."""
    inside = [ln for ln in lines if ln.start >= start - 1e-6 and ln.end <= end + 1e-6]
    limit = gap_limit(analysis)

    def windows_between(whole: bool) -> list[tuple[float, float]]:
        found = []
        for i, first in enumerate(inside):
            if whole and not first.opens:
                continue
            s = max(start, first.start - PREROLL)
            ends = [
                min(end, last.end + DECAY)
                for last in inside[i:]
                if (last.closes or not whole)
                and preset.min_duration <= min(end, last.end + DECAY) - s <= preset.max_duration
            ]
            if ends:
                found.append((s, max(ends)))
        return found

    # Whole sentences when the material allows; a long run-on sentence may leave no choice.
    windows = windows_between(whole=True) or windows_between(whole=False)
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

    new_start = before(rhythm.downbeats, 2 * beat)
    if new_start is None:
        new_start = before(rhythm.beats, beat)
    # An earlier start must not carry a reel that fits past the maximum.
    if new_start is None or end - new_start > max(max_duration, end - start):
        new_start = start
    new_end = after(rhythm.downbeats, 2 * beat, new_start)
    if new_end is None:
        new_end = after(rhythm.beats, beat, new_start)
    if new_end is None:
        new_end = end
    moved = [name for name, a, b in (("start", new_start, start), ("end", new_end, end)) if a != b]
    if not moved:
        return start, end, None
    return new_start, new_end, f"{' and '.join(moved)} moved onto the beat ({rhythm.tempo:.0f} BPM)"


def whole_sentences(
    start: float, end: float, lines: list[Line], preset: Preset
) -> tuple[float, float, list[str]]:
    """Move a start that falls inside a sentence back to where the sentence begins, and an
    end inside one on to where it ends, if the reel stays short enough; otherwise forward
    or back to the nearest sentence boundary inside the reel, if it stays long enough."""
    spoken = [ln for ln in lines if ln.words and start - 0.5 <= ln.start and ln.end <= end + 0.5]
    notes = []
    if spoken and not spoken[0].opens:
        first = spoken[0]
        earlier = [ln for ln in lines if ln.opens and ln.words and ln.start < first.start]
        later = [ln for ln in spoken[1:] if ln.opens]
        if earlier and end - (earlier[-1].start - PREROLL) <= preset.max_duration + 0.25:
            start = max(0.0, earlier[-1].start - PREROLL)
            notes.append(f"started at {start:.1f}s, where the sentence begins")
        elif later and end - (later[0].start - PREROLL) >= preset.min_duration:
            start = later[0].start - PREROLL
            notes.append(f"started at {start:.1f}s, on the next whole sentence")
    if spoken and not spoken[-1].closes:
        last = spoken[-1]
        after = [ln for ln in lines if ln.closes and ln.words and ln.end > last.end]
        before = [ln for ln in spoken[:-1] if ln.closes and ln.start >= start - 0.5]
        if after and after[0].end + DECAY - start <= preset.max_duration + 0.25:
            end = after[0].end + DECAY
            notes.append(f"ended at {end:.1f}s, where the sentence ends")
        elif before and before[-1].end + DECAY - start >= preset.min_duration:
            end = before[-1].end + DECAY
            notes.append(f"ended at {end:.1f}s, after the last whole sentence")
    return start, end, notes


def apply(edl: EDL, analysis: Analysis, preset: Preset) -> EDL:
    clip = edl.clips[0]
    start, end, notes = trim_silence(clip.start, clip.end, analysis.silences)
    lines = build_lines(analysis)
    if end - start > preset.max_duration + 0.25:
        if preset.hook.optimize:
            start, end, note = choose_window(start, end, lines, analysis, preset)
        else:
            # The opening is the plan's, or the editor's, to choose.
            start, end, note = keep_opening(start, end, lines, analysis, preset)
        notes.append(note)
    start, end, note = avoid_dead_air(start, end, preset, analysis)
    if note:
        notes.append(note)
    start, end, moved = whole_sentences(start, end, lines, preset)
    notes += moved
    doubts = []
    clip = clip.model_copy(update={"start": round(start, 3), "end": round(end, 3)})
    if clip.duration < preset.min_duration:
        # Without hook.optimize the opening is the plan's or the editor's, so only the end grows.
        grown = fit_duration([clip], lines, preset, grow_back=preset.hook.optimize)[0]
        if grown != clip:
            notes.append(f"extended to {grown.start:.1f}-{grown.end:.1f}s with neighbouring lines")
            clip = grown
        if clip.duration < preset.min_duration - 0.5:
            what = "voiced material" if preset.hook.optimize else "voice after the chosen opening"
            doubts.append(
                f"only {clip.duration:.1f}s of {what}; left shorter than the "
                f"{preset.min_duration:g}s minimum rather than padded with silence"
            )
            notes.append(doubts[-1])
    if preset.beat_sync.snap_trims:
        start, end, note = snap_to_beat(clip.start, clip.end, analysis.beats, preset.max_duration)
        if note:
            clip = clip.model_copy(update={"start": round(start, 3), "end": round(end, 3)})
            notes.append(note)
    return edl.model_copy(
        update={
            "clips": [clip],
            "decisions": [*edl.decisions, *notes],
            "doubts": list(dict.fromkeys([*edl.doubts, *doubts])),
        }
    )
