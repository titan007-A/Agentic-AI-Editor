"""LLM edit planning, kept to small questions a 7-9B model answers reliably.

Pass 1 scores candidate segments one transcript chunk at a time. Pass 2 picks the one
segment to use and writes the hook and title. A segment is always a continuous run of
lines: the reel is one stretch of the source, never pieces joined together. The model refers to
transcript lines by number; turning lines into timestamps happens here, in code.
"""

import json
import logging
import re
from bisect import bisect_left
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from agentic_editor.analyze import Analysis
from agentic_editor.edl import EDL, CaptionOverrides, Clip, Zoom, problems
from agentic_editor.llm import LLMError, OllamaClient, parse
from agentic_editor.presets import Preset
from agentic_editor.silence import long_silences
from agentic_editor.transcript import Word, ends_sentence, gap, phrases, split_phrase

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
    # False when a long sentence had to be split: the line picks up mid-sentence, or
    # runs on into the next line.
    opens: bool = True
    closes: bool = True

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words) or "(no clear words)"

    def render(self) -> str:
        text = ("..." if not self.opens else "") + self.text + ("..." if not self.closes else "")
        return f"L{self.index} [{self.start:.1f}-{self.end:.1f}] ({self.loudness}) {text}"


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


class _Start(BaseModel):
    line: int
    words: str = ""


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
        # The audio can end before the video does; past its end there is no level to read.
        lo, hi = int((start + 3) / hop), min(int((end - 3) / hop), len(analysis.energy))
        if lo < hi:
            quietest = min(range(lo, hi), key=lambda i: analysis.energy[i])
            cut = quietest * hop + hop / 2
        else:
            cut = (start + end) / 2
        regions[:0] = [(start, cut), (cut, end)]
    return pieces


def build_lines(analysis: Analysis, max_words: int = 24) -> list[Line]:
    """Numbered planning units: spoken phrases, or for a vocal stem, every stretch where
    the singer is audible, since sung passages often have no transcribable words."""
    words = analysis.transcript.words
    # A stem with no silence at all is one long voiced stretch (a crowd singing along, a
    # held "ay-oh"), not a cue to plan from its few transcribable words.
    if analysis.audio_source == "vocals":
        spans = [
            (start, end, [w for w in words if start <= (w.start + w.end) / 2 < end], True, True)
            for start, end in voiced_regions(analysis)
        ]
    else:
        spans = []
        starts = [a for a, _ in analysis.pauses]

        def pause(before: Word, after: Word) -> float:
            # The break sits between the two word starts, inside the stretched first word.
            lo, hi = before.start + 0.1, after.start + 0.1
            found = analysis.pauses[max(0, bisect_left(starts, lo) - 1) : bisect_left(starts, hi)]
            heard = sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in found)
            return max(heard, gap(before, after))

        for phrase in phrases(words, min_gap=0.5):
            groups = split_phrase(phrase, max_words, pause)
            for i, group in enumerate(groups):
                opens = i == 0 or ends_sentence(groups[i - 1][-1])
                closes = i == len(groups) - 1 or ends_sentence(group[-1])
                spans.append((group[0].start, group[-1].end, group, opens, closes))

    levels = sorted(analysis.energy_between(start, end) for start, end, *_ in spans)
    loud = levels[int(len(levels) * 0.75)] if levels else 0.0
    quiet = levels[int(len(levels) * 0.25)] if levels else 0.0
    lines = []
    for index, (start, end, group, opens, closes) in enumerate(spans):
        level = analysis.energy_between(start, end)
        label = "loud" if level >= loud else "quiet" if level <= quiet else "normal"
        lines.append(Line(index, round(start, 2), round(end, 2), group, label, opens, closes))
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
        "Transcript lines (line number, start-end seconds, loudness, text). A line that "
        'starts with "..." picks up in the middle of a sentence and one that ends with "..." '
        "runs on into the next, so a segment starts on a line without the leading dots and "
        "ends on one without the trailing dots:\n" + describe_lines(chunk) + f"\n\n{QUALITIES}\n"
        f"Propose up to 3 candidate segments from these lines. A segment is the run of "
        f"lines from start_line to end_line inclusive and should last {low:g}-{high:g} "
        f"seconds. Score hook, completeness and peak from 0 to 10 and give a one-line reason."
        + _editor_notes(notes)
    )
    first, last = chunk[0].index, chunk[-1].index
    # The model now and then answers a short transcript with no candidates at all; asked
    # again and told to pick the best there is, it nearly always does.
    for insist in ("", "\n\nPropose at least one segment, the best these lines allow."):
        reply = llm.chat_json(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt + insist}],
            CandidateList.model_json_schema(),
        )
        valid = []
        for candidate in parse(CandidateList, reply).candidates:
            if first <= candidate.start_line <= candidate.end_line <= last:
                valid.append(candidate)
            else:
                log.debug("dropping out-of-range candidate %s", candidate)
        if valid:
            return valid
        log.debug("no usable candidates for lines %d-%d", first, last)
    return []


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


def fit_duration(
    clips: list[Clip], lines: list[Line], preset: Preset, grow_back: bool = True
) -> list[Clip]:
    """Grow a short plan a whole line at a time, at its start or end (only its end without
    grow_back, when the opening was chosen on purpose), until it reaches the preset's
    minimum, never past its maximum. Lines only cover voiced stretches, so growing never
    adds a silent head or tail. Plans that run long are trimmed by trim.apply, which picks
    the window.

    Small models are poor at arithmetic, so a plan that is a few seconds off is
    corrected here rather than sent back for another attempt."""
    clips = list(clips)

    def total() -> float:
        return sum(c.duration for c in clips)

    while total() < preset.min_duration:
        after = next((ln for ln in lines if ln.start >= clips[-1].end - 1e-6), None)
        before = next((ln for ln in reversed(lines) if ln.end <= clips[0].start + 1e-6), None)
        if not grow_back:
            before = None
        # A line that would carry the plan past the maximum is left out, even if that
        # leaves the plan short: a reel ends after a whole line, never inside one.
        if after is not None and after.end - clips[0].start > preset.max_duration:
            after = None
        if before is not None and clips[-1].end - before.start > preset.max_duration:
            before = None
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


def to_edl(
    plan: FinalPlan,
    candidates: list[Candidate],
    lines: list[Line],
    preset: Preset,
    keep_start: bool = False,
) -> EDL:
    if not 0 <= plan.candidate < len(candidates):
        raise ValueError(f"C{plan.candidate} is not one of the candidates")
    c = candidates[plan.candidate]
    clip = Clip(
        start=lines[c.start_line].start,
        end=lines[c.end_line].end,
        reason=plan.reason,
        score=round((c.hook + c.completeness + c.peak) / 30, 2),
    )
    chosen = fit_duration([clip], lines, preset, grow_back=not keep_start)

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
    keep_start: bool = False,
) -> EDL:
    """The plan from the candidate the model picks. With keep_start the plan opens where
    its candidate does, the moment the editor asked to start on."""
    # A short source, or the stretch after a requested opening, may hold less than the
    # preset's minimum; a plan using all of it is as long as it can be.
    opening = lines[candidates[0].start_line].start if keep_start else lines[0].start
    available = lines[-1].end - opening
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
    # Only the numbers of real candidates can be decoded; the model has answered C-1.
    schema["properties"]["candidate"]["enum"] = list(range(len(candidates)))
    picked: FinalPlan | None = None
    for attempt in range(MAX_RETRIES + 1):
        reply = llm.chat_json(messages, schema)
        try:
            picked = FinalPlan.model_validate(reply)
            edl = to_edl(picked, candidates, lines, preset, keep_start)
            found = problems(edl, preset, analysis.duration, allow_long=True, available=available)
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
    if picked is not None and not 0 <= picked.candidate < len(candidates):
        # Still no real pick: the candidates come ranked by the model's own scores, so its
        # first is a better plan than the energy heuristic's.
        fallback = picked.model_copy(update={"candidate": 0})
        edl = to_edl(fallback, candidates, lines, preset, keep_start)
        if not problems(edl, preset, analysis.duration, allow_long=True, available=available):
            log.info("no candidate picked; using the best-scored one")
            return edl
    raise LLMError(f"no valid plan after {MAX_RETRIES} retries: {'; '.join(found)}")


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def find_start(llm: OllamaClient, lines: list[Line], asked: str) -> tuple[Line, str] | None:
    """The line where the moment the editor asked to start on begins, and the words that
    show it. One focused question per stretch of transcript, earliest first; an answer
    only counts if its words really are in that line (or run on into the next)."""
    for chunk in chunk_lines(lines):
        reply = llm.chat_json(
            [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Transcript lines of a video (line number, start-end seconds, "
                        f'loudness, text):\n{describe_lines(chunk)}\n\nThe editor said: "{asked}". '
                        "Which line is the first line of that moment? Answer line, its number, "
                        "or -1 if the moment isn't in these lines, and words, copied exactly "
                        "from that line, that show it is the moment."
                    ),
                },
            ],
            _Start.model_json_schema(),
            temperature=0.0,
        )
        found = parse(_Start, reply)
        here = {ln.index: ln for ln in chunk}
        line = here.get(found.line)
        if line is None or not _plain(found.words):
            continue
        following = lines[line.index + 1].text if line.index + 1 < len(lines) else ""
        if _plain(found.words) in _plain(f"{line.text} {following}"):
            return line, found.words.strip()
    return None


def from_line(first: Line, lines: list[Line], preset: Preset, reason: str) -> Candidate:
    """A segment from the given line on, a little longer than the preset allows so the
    trim can end it on a whole sentence, and stopping before a long stretch without voice."""
    last = first
    for line in lines[first.index + 1 :]:
        if (
            line.start - last.end >= GAP_NOTE_SECONDS
            or line.end - first.start > preset.max_duration + 10
        ):
            break
        last = line
    return Candidate(
        start_line=first.index, end_line=last.index, hook=5, completeness=5, peak=5, reason=reason
    )


def plan_with_llm(
    llm: OllamaClient, analysis: Analysis, preset: Preset, notes: str | None = None
) -> EDL:
    return plan_variants(llm, analysis, preset, notes, count=1)[0]


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Share of the shorter span that the other one covers."""
    shared = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return shared / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def plan_variants(
    llm: OllamaClient,
    analysis: Analysis,
    preset: Preset,
    notes: str | None,
    count: int,
    start_at: str | None = None,
) -> list[EDL]:
    """The best plan, then up to count-1 more from other strong segments that don't mostly
    repeat one already chosen, each with its own hook and title. When the editor said where
    to start, that moment is found first and the one plan starts there."""
    lines = build_lines(analysis)
    if not lines:
        raise LLMError("the transcript is empty, nothing to plan from")
    if start_at:
        found = find_start(llm, lines, start_at)
        if found:
            line, words = found
            reason = f'starts where you asked ("{start_at}"): "{words}"'
            candidate = from_line(line, lines, preset, reason)
            edl = finalize(llm, [candidate], lines, preset, analysis, notes, keep_start=True)
            note = f'found where you asked to start: {line.start:.1f}s, "{words}"'
            return [edl.model_copy(update={"decisions": [note]})]
    edls = _plan_best(llm, lines, analysis, preset, notes, count)
    if start_at:
        note = "couldn't find where you asked to start; planned the strongest segment instead"
        edls = [
            e.model_copy(update={"decisions": [note, *e.decisions], "doubts": [note, *e.doubts]})
            for e in edls
        ]
    return edls


def dead_air(candidate: Candidate, lines: list[Line], analysis: Analysis, preset: Preset) -> float:
    """Seconds without voice inside the part of a candidate that a reel would keep."""
    start = lines[candidate.start_line].start
    end = min(lines[candidate.end_line].end, start + preset.max_duration)
    return sum(b - a for a, b in long_silences(start, end, analysis))


def _plan_best(
    llm: OllamaClient,
    lines: list[Line],
    analysis: Analysis,
    preset: Preset,
    notes: str | None,
    count: int,
) -> list[EDL]:
    candidates = []
    for chunk in chunk_lines(lines):
        candidates += propose(llm, chunk, preset, analysis, notes)
    if not candidates:
        raise LLMError("the model proposed no usable segments")
    # The opening matters most for a short video, so the hook counts double.
    candidates.sort(key=lambda c: 2 * c.hook + c.completeness + c.peak, reverse=True)
    # Dead air can't be cut out of a reel, so segments without it go first, and they are
    # the only ones offered when there are any.
    quiet = {id(c): dead_air(c, lines, analysis, preset) for c in candidates}
    candidates.sort(key=lambda c: quiet[id(c)] > 0)
    clean = [c for c in candidates if not quiet[id(c)]]
    edls = [finalize(llm, (clean or candidates)[:12], lines, preset, analysis, notes)]
    for other in candidates:
        if len(edls) >= count:
            break
        span = (lines[other.start_line].start, lines[other.end_line].end)
        if any(overlap(span, (e.clips[0].start, e.clips[0].end)) > 0.5 for e in edls):
            continue
        try:
            edl = finalize(llm, [other], lines, preset, analysis, notes)
        except LLMError:
            continue
        edls.append(edl.model_copy(update={"variant": len(edls)}))
    return edls


def _normalize(word: str) -> str:
    return word.strip(".,!?;:\"'").lower()


def pick_emphasis(llm: OllamaClient, edl: EDL, analysis: Analysis, limit: int = 3) -> list[Zoom]:
    """Ask for the words worth a punch-in, then find them in the chosen clips. Asked of the
    trimmed plan, so the words are ones the reel keeps."""
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
    for target in parse(Emphasis, reply).words[:limit]:
        match = next(
            (w for w in words if _normalize(w.text) == _normalize(target) and w.start not in used),
            None,
        )
        if match:
            used.add(match.start)
            zooms.append(Zoom(time=match.start, scale=1.15, duration=0.8))
    return sorted(zooms, key=lambda z: z.time)
