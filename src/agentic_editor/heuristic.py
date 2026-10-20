"""A planner that needs no model: picks the strongest phrase-aligned window by audio
energy. Used when the LLM is unavailable and as the planner's fallback."""

from agentic_editor.analyze import Analysis
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset
from agentic_editor.transcript import phrases

HOOK_SECONDS = 3.0


def target_length(preset: Preset, source_duration: float) -> float:
    wanted = (preset.min_duration + preset.max_duration) / 2
    return min(wanted, source_duration)


def plan_by_energy(analysis: Analysis, preset: Preset) -> EDL:
    length = target_length(preset, analysis.duration)
    groups = phrases(analysis.transcript.words)
    starts = [g[0].start for g in groups] or [i * 0.5 for i in range(len(analysis.energy))]
    ends = [g[-1].end for g in groups] or [analysis.duration]

    best: tuple[float, float, float] | None = None
    for start in starts:
        lo, hi = start + preset.min_duration, min(start + preset.max_duration, analysis.duration)
        candidates = [e for e in ends if lo <= e <= hi] or [min(start + length, analysis.duration)]
        end = min(candidates, key=lambda e: abs(e - start - length))
        if end - start < min(preset.min_duration, analysis.duration) - 0.5:
            continue
        score = analysis.energy_between(start, end) + 0.5 * analysis.energy_between(
            start, start + HOOK_SECONDS
        )
        if best is None or score > best[0]:
            best = (score, start, end)

    if best is None:
        best = (0.0, 0.0, min(length, analysis.duration))
    _, start, end = best
    return EDL(
        clips=[Clip(start=start, end=end, reason="loudest phrase-aligned window", score=0.5)],
        crop_mode=preset.framing.mode,
    )
