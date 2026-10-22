"""LLM edit planning, kept to small questions a 7-9B model answers reliably.

Pass 1 scores candidate segments one transcript chunk at a time. Pass 2 picks the one
segment to use and writes the hook and title. A segment is always a continuous run of
lines: the reel is one stretch of the source, never pieces joined together. The model refers to
transcript lines by number; turning lines into timestamps happens here, in code.
"""

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from agentic_editor.analyze import Analysis
from agentic_editor.edl import EDL, CaptionOverrides, Clip, Zoom, problems
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.presets import Preset
from agentic_editor.transcript import Word, phrases

log = logging.getLogger(__name__)

CHUNK_SECONDS = 150.0
MAX_RETRIES = 2
SYSTEM = (
    "You are an experienced short-form video editor. You pick the moments worth posting "
    "and you answer only with JSON that matches the requested schema."
)
QUALITIES = (
    "A strong segment opens with a hook in its first 3 seconds, contains complete "
    "thoughts, builds to a surprising or emotional peak, and never depends on context "
    "from before it starts or leaves a thought dangling at the end."
)


GAP_NOTE_SECONDS = 4.0
MAX_LINE_SECONDS = 12.0


@dataclass(frozen=True)
class Line:
    index: int
    start: float
    end: float
    words: list[Word]
    loudness: str

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words) or "(no clear words)"

    def render(self) -> str:
        return f"L{self.index} [{self.start:.1f}-{self.end:.1f}] ({self.loudness}) {self.text}"


class Candidate(BaseModel):
    start_line: int
    end_line: int
    hook: int = Field(ge=0, le=10)
    completeness: int = Field(ge=0, le=10)
    peak: int = Field(ge=0, le=10)
    reason: str


class CandidateList(BaseModel):
    candidates: list[Candidate]


class Emphasis(BaseModel):
    words: list[str]


class FinalPlan(BaseModel):
    candidate: int
    reason: str
    hook: str
    title: str
    caption_position: str | None = None


def voiced_regions(analysis: Analysis) -> list[tuple[float, float]]:
    """Stretches between detected silences, split at their quietest point when long."""
    regions, cursor = [], 0.0
    for silence_start, silence_end in [*analysis.silences, (analysis.duration, analysis.duration)]:
        if silence_start - cursor >= 0.4:
            regions.append((cursor, silence_start))
        cursor = max(cursor, silence_end)

    hop = analysis.energy_hop
    pieces = []
    while regions:
        start, end = regions.pop(0)
        if end - start <= MAX_LINE_SECONDS:
            pieces.append((start, end))
            continue
        lo, hi = int((start + 3) / hop), int((end - 3) / hop)
        quietest = min(range(lo, hi), key=lambda i: analysis.energy[i])
        regions[:0] = [(start, quietest * hop + hop / 2), (quietest * hop + hop / 2, end)]
    return pieces


def build_lines(analysis: Analysis, max_words: int = 24) -> list[Line]:
    """Numbered planning units: spoken phrases, or for a vocal stem, every stretch where
    the singer is audible, since sung passages often have no transcribable words."""
    words = analysis.transcript.words
    if analysis.audio_source == "vocals" and analysis.silences:
        spans = [
            (start, end, [w for w in words if start <= (w.start + w.end) / 2 < end])
            for start, end in voiced_regions(analysis)
        ]
    else:
        spans = []
        for phrase in phrases(words, min_gap=0.5):
            for i in range(0, len(phrase), max_words):
                group = phrase[i : i + max_words]
                spans.append((group[0].start, group[-1].end, group))

    levels = sorted(analysis.energy_between(start, end) for start, end, _ in spans)
    loud = levels[int(len(levels) * 0.75)] if levels else 0.0
    quiet = levels[int(len(levels) * 0.25)] if levels else 0.0
    lines = []
    for index, (start, end, group) in enumerate(spans):
        level = analysis.energy_between(start, end)
        label = "loud" if level >= loud else "quiet" if level <= quiet else "normal"
        lines.append(Line(index, round(start, 2), round(end, 2), group, label))
    return lines


def describe_lines(lines: list[Line]) -> str:
    rows = []
    for prev, line in zip([None, *lines], lines, strict=False):
        if prev is not None and line.start - prev.end >= GAP_NOTE_SECONDS:
            rows.append(f"   ... {line.start - prev.end:.0f}s with no voice ...")
        rows.append(line.render())
    return "\n".join(rows)


def chunk_lines(lines: list[Line], seconds: float = CHUNK_SECONDS) -> list[list[Line]]:
    chunks: list[list[Line]] = []
    for line in lines:
        if chunks and line.end - chunks[-1][0].start <= seconds:
            chunks[-1].append(line)
        else:
            chunks.append([line])
    return chunks


def _editor_notes(notes: str | None) -> str:
    # Last in the prompt, where small models weigh instructions most.
    if not notes:
        return ""
    return f"\n\nThe editor asked for this version: {notes}\nThis overrides the defaults above."


def _brief(preset: Preset, analysis: Analysis) -> str:
    parts = [
        f"Format: {preset.label} for {preset.platform}.",
        f"The finished video is one continuous stretch of the source, "
        f"{preset.min_duration:g}-{preset.max_duration:g} seconds long. It is never assembled "
        "from separate pieces.",
    ]
    if analysis.audio_source == "vocals":
        parts.append("The transcript is sung lyrics from the isolated lead vocal of a song.")
    if preset.style_notes:
        parts.append(f"Style notes: {preset.style_notes}")
    return "\n".join(parts)


def propose(
    llm: OllamaClient, chunk: list[Line], preset: Preset, analysis: Analysis, notes: str | None
) -> list[Candidate]:
    low, high = preset.min_duration, preset.max_duration
    prompt = (
        f"{_brief(preset, analysis)}\n\n"
        "Transcript lines (line number, start-end seconds, loudness, text):\n"
        + describe_lines(chunk)
        + f"\n\n{QUALITIES}\n"
        f"Propose up to 3 candidate segments from these lines. A segment is the run of "
        f"lines from start_line to end_line inclusive and should last {low:g}-{high:g} "
        f"seconds. Score hook, completeness and peak from 0 to 10 and give a one-line reason."
        + _editor_notes(notes)
    )
    reply = llm.chat_json(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        CandidateList.model_json_schema(),
    )
    first, last = chunk[0].index, chunk[-1].index
    valid = []
    for candidate in CandidateList.model_validate(reply).candidates:
        if first <= candidate.start_line <= candidate.end_line <= last:
            valid.append(candidate)
        else:
            log.debug("dropping out-of-range candidate %s", candidate)
    return valid


def _describe_candidates(candidates: list[Candidate], lines: list[Line]) -> str:
    rows = []
    for i, c in enumerate(candidates):
        start, end = lines[c.start_line].start, lines[c.end_line].end
        rows.append(
            f"C{i} [{start:.1f}-{end:.1f}] {end - start:.1f}s hook {c.hook} complete "
            f'{c.completeness} peak {c.peak}: opens "{lines[c.start_line].text}" ... ends '
            f'"{lines[c.end_line].text}" ({c.reason})'
        )
    return "\n".join(rows)


def fit_duration(clips: list[Clip], lines: list[Line], preset: Preset) -> list[Clip]:
    """Grow a short plan a whole line at a time, at its start or end, until it reaches the
    preset's minimum. Lines only cover voiced stretches, so growing never adds a silent
    head or tail. Plans that run long are trimmed by trim.apply, which picks the window.

    Small models are poor at arithmetic, so a plan that is a few seconds off is
    corrected here rather than sent back for another attempt."""
    clips = list(clips)

    def total() -> float:
        return sum(c.duration for c in clips)

    while total() < preset.min_duration:
        after = next((ln for ln in lines if ln.start >= clips[-1].end - 1e-6), None)
        before = next((ln for ln in reversed(lines) if ln.end <= clips[0].start + 1e-6), None)
        # Grow toward whichever neighbour is closer, so a plan doesn't swallow a long
        # stretch of nothing when there is material right on the other side.
        gap_after = after.start - clips[-1].end if after else float("inf")
        gap_before = clips[0].start - before.end if before else float("inf")
        if after is None and before is None:
            break
        if gap_after <= gap_before:
            clips[-1] = clips[-1].model_copy(update={"end": after.end})
        else:
            clips[0] = clips[0].model_copy(update={"start": before.start})
    return clips


def to_edl(plan: FinalPlan, candidates: list[Candidate], lines: list[Line], preset: Preset) -> EDL:
    if not 0 <= plan.candidate < len(candidates):
        raise ValueError(f"C{plan.candidate} is not one of the candidates")
    c = candidates[plan.candidate]
    clip = Clip(
        start=lines[c.start_line].start,
        end=lines[c.end_line].end,
        reason=plan.reason,
        score=round((c.hook + c.completeness + c.peak) / 30, 2),
    )
    chosen = fit_duration([clip], lines, preset)

    position = (
        plan.caption_position if plan.caption_position in ("top", "middle", "bottom") else None
    )
    return EDL(
        clips=chosen,
        hook=plan.hook.strip(),
        title=plan.title.strip(),
        caption_overrides=CaptionOverrides(position=position),
        crop_mode=preset.framing.mode,
    )


def finalize(
    llm: OllamaClient,
    candidates: list[Candidate],
    lines: list[Line],
    preset: Preset,
    analysis: Analysis,
    notes: str | None,
) -> EDL:
    prompt = (
        f"{_brief(preset, analysis)}\n\n"
        f"Candidate segments:\n{_describe_candidates(candidates, lines)}\n\n"
        "Pick the single candidate to post as one continuous segment, favouring the strongest "
        f"hook and a length of {preset.min_duration:g}-{preset.max_duration:g} seconds; a "
        "longer one is trimmed at its start or end afterwards. Also write: hook, a short on-screen "
        "line of at most 8 words that makes people keep watching; title, at most 60 "
        "characters; caption_position, one of top, middle, bottom or null." + _editor_notes(notes)
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    schema = FinalPlan.model_json_schema()
    for attempt in range(MAX_RETRIES + 1):
        reply = llm.chat_json(messages, schema)
        try:
            edl = to_edl(FinalPlan.model_validate(reply), candidates, lines, preset)
            found = problems(edl, preset, analysis.duration, allow_long=True)
        except (ValidationError, ValueError) as exc:
            found = [str(exc)]
        if not found:
            return edl
        log.info("plan attempt %d rejected: %s", attempt + 1, "; ".join(found))
        messages += [
            {"role": "assistant", "content": json.dumps(reply)},
            {
                "role": "user",
                "content": "That plan breaks these rules:\n- "
                + "\n- ".join(found)
                + "\nFix it and answer again with the complete JSON.",
            },
        ]
    raise LLMError(f"no valid plan after {MAX_RETRIES} retries: {'; '.join(found)}")


def plan_with_llm(
    llm: OllamaClient, analysis: Analysis, preset: Preset, notes: str | None = None
) -> EDL:
    return plan_variants(llm, analysis, preset, notes, count=1)[0]


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Share of the shorter span that the other one covers."""
    shared = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return shared / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def plan_variants(
    llm: OllamaClient, analysis: Analysis, preset: Preset, notes: str | None, count: int
) -> list[EDL]:
    """The best plan, then up to count-1 more from other strong segments that don't mostly
    repeat one already chosen, each with its own hook and title."""
    lines = build_lines(analysis)
    if not lines:
        raise LLMError("the transcript is empty, nothing to plan from")
    candidates = []
    for chunk in chunk_lines(lines):
        candidates += propose(llm, chunk, preset, analysis, notes)
    if not candidates:
        raise LLMError("the model proposed no usable segments")
    # The opening matters most for a short video, so the hook counts double.
    candidates.sort(key=lambda c: 2 * c.hook + c.completeness + c.peak, reverse=True)
    edls = [finalize(llm, candidates[:12], lines, preset, analysis, notes)]
    for other in candidates:
        if len(edls) >= count:
            break
        span = (lines[other.start_line].start, lines[other.end_line].end)
        if any(_overlap(span, (e.clips[0].start, e.clips[0].end)) > 0.5 for e in edls):
            continue
        try:
            edl = finalize(llm, [other], lines, preset, analysis, notes)
        except LLMError:
            continue
        edls.append(edl.model_copy(update={"variant": len(edls)}))
    if preset.zoom_punch_ins:
        edls = [e.model_copy(update={"zooms": pick_emphasis(llm, e, analysis)}) for e in edls]
    return edls


def _normalize(word: str) -> str:
    return word.strip(".,!?;:\"'").lower()


def pick_emphasis(llm: OllamaClient, edl: EDL, analysis: Analysis, limit: int = 3) -> list[Zoom]:
    """Ask for the words worth a punch-in, then find them in the chosen clips."""
    words = [w for clip in edl.clips for w in analysis.transcript.between(clip.start, clip.end)]
    if not words:
        return []
    text = " ".join(w.text for w in words)
    reply = llm.chat_json(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Transcript of a finished short video:\n{text}\n\n"
                    f"Pick up to {limit} single words from it that carry the most emphasis and "
                    "deserve a quick zoom. Copy each word exactly as it appears."
                ),
            },
        ],
        Emphasis.model_json_schema(),
    )
    zooms, used = [], set()
    for target in Emphasis.model_validate(reply).words[:limit]:
        match = next(
            (w for w in words if _normalize(w.text) == _normalize(target) and w.start not in used),
            None,
        )
        if match:
            used.add(match.start)
            zooms.append(Zoom(time=match.start, scale=1.15, duration=0.8))
    return sorted(zooms, key=lambda z: z.time)
