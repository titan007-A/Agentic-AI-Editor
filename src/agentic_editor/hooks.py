"""Where a reel starts: the first seconds decide whether people keep watching.

A reel is one continuous stretch and may only be trimmed at its ends, so making the
opening stronger means starting a little later, on a better line. For speech the planning
model reads the candidate opening lines and picks the most gripping; for music the
loudest, busiest entrance wins, as the voice coming in strong is what grabs attention.
"""

from pydantic import BaseModel

from agentic_editor.analyze import Analysis
from agentic_editor.edl import EDL
from agentic_editor.llm import LLMError, parse
from agentic_editor.planner import Line, build_lines
from agentic_editor.presets import Preset
from agentic_editor.trim import PREROLL

OPENING = 3.0  # seconds that decide whether someone scrolls on
LOOKAHEAD = 15.0  # how far into the segment the start may move
MAX_OPTIONS = 5
MIN_GAIN_DB = 2.0  # a music opening has to be clearly stronger to be worth the trim


class _Choice(BaseModel):
    option: int
    reason: str


def options(edl: EDL, lines: list[Line], preset: Preset) -> list[Line]:
    """Lines the reel could open on: early in the segment, keeping it long enough, and
    starting a sentence rather than picking one up halfway. The first is the planned start."""
    clip = edl.clips[0]
    found = [
        ln
        for ln in lines
        if clip.start - 0.5 <= ln.start <= clip.start + LOOKAHEAD
        and clip.end - (ln.start - PREROLL) >= preset.min_duration
    ]
    return [ln for i, ln in enumerate(found) if i == 0 or ln.opens][:MAX_OPTIONS]


def opening_strength(start: float, analysis: Analysis) -> float:
    """Loudness of the first seconds, with a penalty for opening on silence."""
    quiet = any(a <= start + 0.3 and b >= start + 1.0 for a, b in analysis.silences)
    return analysis.energy_between(start, start + OPENING) - (6.0 if quiet else 0.0)


def pick_by_energy(choices: list[Line], analysis: Analysis) -> tuple[int, str]:
    scores = [opening_strength(ln.start, analysis) for ln in choices]
    best = max(range(len(choices)), key=lambda i: scores[i])
    if scores[best] - scores[0] < MIN_GAIN_DB:
        return 0, ""
    return best, f"{scores[best] - scores[0]:.0f} dB stronger first {OPENING:.0f} seconds"


def pick_by_reading(llm, choices: list[Line], notes: str | None) -> tuple[int, str]:
    rows = "\n".join(
        f"O{i}: " + " ".join(w.text for w in ln.words[:24]) for i, ln in enumerate(choices)
    )
    extra = f"\nThe editor's notes: {notes}" if notes else ""
    reply = llm.chat_json(
        [
            {
                "role": "user",
                "content": (
                    "A short vertical video can open on any of these lines. The first three "
                    "seconds decide whether people keep watching. Which line makes the "
                    "strongest opening: a surprising claim, a question, tension or a vivid "
                    "image, rather than filler or a slow lead-in? Answer with its number and a "
                    f"short reason.{extra}\n\n{rows}"
                ),
            }
        ],
        _Choice.model_json_schema(),
        temperature=0.0,
    )
    choice = parse(_Choice, reply)
    if not 0 <= choice.option < len(choices):
        return 0, ""
    return choice.option, choice.reason.strip()


def sharpen(llm, edl: EDL, analysis: Analysis, preset: Preset, notes: str | None) -> EDL:
    """Move the start on to the strongest opening line, if one beats the current start."""
    if not preset.hook.optimize:
        return edl
    choices = options(edl, build_lines(analysis), preset)
    if len(choices) < 2:
        return edl
    spoken = analysis.audio_source != "vocals" and any(ln.words for ln in choices)
    try:
        best, reason = (
            pick_by_reading(llm, choices, notes) if spoken else pick_by_energy(choices, analysis)
        )
    except LLMError:
        return edl
    if best == 0:
        note = f"kept the planned opening; it was the strongest of {len(choices)}"
        return edl.model_copy(update={"decisions": [*edl.decisions, note]})
    clip = edl.clips[0]
    start = round(max(clip.start, choices[best].start - PREROLL), 3)
    note = f"opened {start - clip.start:.1f}s later, on a stronger line"
    if reason:
        note += f": {reason}"
    return edl.model_copy(
        update={
            "clips": [clip.model_copy(update={"start": start})],
            "decisions": [*edl.decisions, note],
        }
    )
