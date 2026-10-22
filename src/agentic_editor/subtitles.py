"""Captions of every kind as one timeline of cues, which the renderer burns in and which
is also written out as an .srt file.

Speech and lyrics come straight from the transcript. Translated captions keep the
timing of the spoken lines and swap their text; captions written from the visuals are
timed to the shots they describe. Placement moves a caption off any face it would cover.
"""

import itertools
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agentic_editor.edl import Cue
from agentic_editor.llm import LLMError
from agentic_editor.transcript import Word

LINE_BREAK_PAUSE = 0.6  # a pause this long starts a new caption line
LYRIC_BREAK = 0.8  # sung lines are longer and breathe less often
LYRIC_WORDS = 8
FACE_PAD = 24  # px kept clear around a face
NEAR_EDGE = 220  # px from the frame edge for the fallback positions


def speech_cues(words: list[Word], per_line: int) -> list[Cue]:
    """A few words at a time, broken at pauses, with word timings for the highlight."""
    lines: list[list[Word]] = []
    for word in words:
        current = lines[-1] if lines else None
        if current and len(current) < per_line and word.start - current[-1].end < LINE_BREAK_PAUSE:
            current.append(word)
        else:
            lines.append([word])
    return [_cue(line) for line in lines]


def lyric_cues(
    words: list[Word],
    max_words: int = LYRIC_WORDS,
    breaths: list[tuple[float, float]] | None = None,
) -> list[Cue]:
    """Whole sung lines. A line ends where the singer breathes (the voiced stretches of the
    isolated vocal, when given, else a pause in the words). A stretch longer than max_words
    is split where the transcript starts a new line, at a capital or after punctuation,
    and never leaves a single word on its own."""
    if breaths:
        groups = [[w for w in words if a <= (w.start + w.end) / 2 < b] for a, b in breaths]
    else:
        groups = []
        for word in words:
            if groups and word.start - groups[-1][-1].end < LYRIC_BREAK:
                groups[-1].append(word)
            else:
                groups.append([word])
    groups = _move_stranded([g for g in groups if g])
    lines = [line for group in groups if group for line in _split_line(group, max_words)]
    return [_cue(line) for line in _join_scraps(lines, max_words)]


def _move_stranded(groups: list[list[Word]], near: float = 2.5) -> list[list[Word]]:
    """A capitalised word left at the end of a breath usually starts the next line."""
    groups = [list(g) for g in groups]
    for a, b in itertools.pairwise(groups):
        if len(a) < 3 or not b:
            continue
        last = a[-1]
        word = last.text.strip()
        if word[:1].isupper() and word != "I" and b[0].start - last.end < near:
            b.insert(0, a.pop())
    return groups


def _join_scraps(lines: list[list[Word]], max_words: int, near: float = 2.5) -> list[list[Word]]:
    """Join a one-word line to a close neighbour: a lone "me" after "save" is one phrase."""
    out: list[list[Word]] = []
    for line in lines:
        if out and (len(line) == 1 or len(out[-1]) == 1):
            gap = line[0].start - out[-1][-1].end
            if gap < near and len(out[-1]) + len(line) <= max_words + 2:
                out[-1] = out[-1] + line
                continue
        out.append(line)
    return out


def _split_line(group: list[Word], max_words: int) -> list[list[Word]]:
    if len(group) <= max_words:
        return [group]

    def score(i: int) -> float:
        # Prefer a break before a capital or after punctuation, near the middle.
        natural = group[i].text.strip()[:1].isupper() or group[i - 1].text.strip()[-1:] in ",.;!?"
        return (0 if natural else 1, abs(i - len(group) / 2))

    candidates = range(2, len(group) - 1)  # both sides keep at least two words
    cut = min(candidates, key=score) if candidates else max_words
    return _split_line(group[:cut], max_words) + _split_line(group[cut:], max_words)


def _cue(line: list[Word]) -> Cue:
    return Cue(
        start=line[0].start,
        end=line[-1].end,
        text=" ".join(w.text.strip() for w in line),
        words=line,
    )


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{c.text}\n"
        for i, c in enumerate((c for c in cues if c.end > c.start), start=1)
    ]
    return "\n".join(blocks)


@dataclass(frozen=True)
class Placement:
    """Where a caption sits: an ASS alignment (2 bottom, 5 middle, 8 top) and the
    distance from that edge."""

    alignment: int
    margin: int

    def band(self, height: int, frame_height: int) -> tuple[float, float]:
        """Rows of the frame the caption covers."""
        if self.alignment == 2:
            return frame_height - self.margin - height, frame_height - self.margin
        if self.alignment == 8:
            return self.margin, self.margin + height
        return frame_height / 2 - height / 2, frame_height / 2 + height / 2


_ALIGN = {"bottom": 2, "middle": 5, "top": 8}


def overlap(band: tuple[float, float], faces: list[tuple[float, float]]) -> float:
    """Rows a caption band shares with faces, each face padded a little."""
    top, bottom = band
    return sum(max(0.0, min(bottom, b + FACE_PAD) - max(top, a - FACE_PAD)) for a, b in faces)


def place(
    cues: list[Cue],
    preferred: str,
    margin: int,
    line_height: int,
    frame_height: int,
    faces: list[tuple[float, list[tuple[float, float]]]],
) -> list[Placement]:
    """A position per cue that keeps it off every face on screen while it shows.

    `faces` gives, at reel times, the rows each face covers in the output frame. The
    preferred position wins whenever it is clear; otherwise the clear position nearest to
    it, and a caption stays where the previous one was if that is still clear, so they
    don't hop around between lines."""
    first = Placement(_ALIGN[preferred], margin if preferred != "middle" else 0)
    options = [
        first,
        Placement(2, margin),
        Placement(2, NEAR_EDGE),
        Placement(8, NEAR_EDGE),
        Placement(8, margin),
        Placement(5, 0),
    ]
    chosen: list[Placement] = []
    for cue in cues:
        covered = [
            band for t, bands in faces if cue.start - 0.2 <= t <= cue.end + 0.2 for band in bands
        ]

        def clash(option: Placement, covered: list[tuple[float, float]] = covered) -> float:
            return overlap(option.band(line_height, frame_height), covered)

        previous = chosen[-1] if chosen else None
        if previous is not None and clash(previous) == 0 and clash(first) > 0:
            chosen.append(previous)
            continue
        chosen.append(min(options, key=lambda o: (clash(o), options.index(o))))
    return chosen


TRANSLATE_WORDS = 10  # lines of about a sentence give the model enough to translate well
VISUAL_EVERY = 3.0  # seconds each caption written from the picture stays up
CAPTION_WORDS = 8


class _Numbered(BaseModel):
    n: int
    text: str


class _Lines(BaseModel):
    lines: list[_Numbered]


class _Line(BaseModel):
    line: str


def _ask_translation(llm, cues: dict[int, Cue], language: str, notes: str | None) -> dict[int, str]:
    numbered = "\n".join(f"{n}. {c.text}" for n, c in cues.items())
    extra = f"\nThe editor's notes: {notes}" if notes else ""
    reply = llm.chat_json(
        [
            {
                "role": "user",
                "content": (
                    f"Translate each numbered caption line into {language}. Keep the meaning "
                    "and the spoken, natural tone, and keep each line about as long as the "
                    "original so it fits on screen. Give one entry per line with its number n "
                    f"and the translated text. Never merge or skip lines.{extra}\n\n{numbered}"
                ),
            }
        ],
        _Lines.model_json_schema(),
        temperature=0.2,
    )
    return {e.n: e.text.strip() for e in _Lines.model_validate(reply).lines if e.text.strip()}


def translate(llm, cues: list[Cue], language: str, notes: str | None) -> list[Cue]:
    """The same captions in another language, keeping their timing. Lines the model skips
    are asked for again on their own; raises LLMError if any are still missing."""
    wanted = dict(enumerate(cues))
    done = _ask_translation(llm, wanted, language, notes)
    missing = {n: c for n, c in wanted.items() if n not in done}
    if missing:
        done |= _ask_translation(llm, missing, language, notes)
    still = [n for n in wanted if n not in done]
    if still:
        raise LLMError(f"no translation for caption line(s) {still}")
    return [c.model_copy(update={"text": done[n], "words": []}) for n, c in wanted.items()]


def visual_cues(
    vision, frames: list[Path], duration: float, context: str, notes: str | None
) -> list[Cue]:
    """Captions written from the picture, one per frame, each up for its stretch of the reel.

    One frame per question: small models asked about several images at once tend to
    answer with one long description of them all."""
    if not frames:
        return []
    step = duration / len(frames)
    background = "".join(
        [
            f" What is said in the video: {context}" if context else "",
            f" The editor's notes: {notes}" if notes else "",
        ]
    )
    written: list[str] = []
    for frame in frames:
        caption = ""
        for attempt in range(2):
            avoid = (
                f" It must say something different from the caption before it, which was: "
                f"{written[-1]}"
                if written
                else ""
            )
            reply = vision.chat_json(
                [
                    {
                        "role": "user",
                        "content": (
                            f"This frame is part {len(written) + 1} of {len(frames)} of a short "
                            f"video. Write its on-screen caption: one sentence of at most "
                            f"{CAPTION_WORDS} words saying what is happening or what it means, "
                            "like a good video caption. Never describe clothes or appearance."
                            + avoid
                            + background
                        ),
                    }
                ],
                _Line.model_json_schema(),
                images=[frame],
                temperature=0.5 + 0.3 * attempt,
            )
            words = _Line.model_validate(reply).line.strip().strip('"').split()
            caption = " ".join(words[: CAPTION_WORDS + 2])
            if caption and "/" not in caption and not _repeats(caption, written):
                break
            caption = ""
        written.append(caption)
    # A frame with nothing new to say leaves the caption before it up for longer.
    cues: list[Cue] = []
    for i, text in enumerate(written):
        start, end = round(i * step, 3), round(min(duration, (i + 1) * step), 3)
        if text:
            cues.append(Cue(start=start, end=end, text=text))
        elif cues:
            cues[-1] = cues[-1].model_copy(update={"end": end})
    return cues


def _repeats(caption: str, written: list[str]) -> bool:
    key = caption.lower().strip(" .!")
    return any(key == w.lower().strip(" .!") or key in w.lower() for w in written if w)


def one_line(
    vision,
    frames: list[Path],
    max_words: int,
    hint: str,
    context: str,
    notes: str | None,
    language: str | None = None,
) -> str:
    """One line about the whole video, written by the vision model."""
    guide = " ".join(x for x in (hint, notes or "") if x)
    reply = vision.chat_json(
        [
            {
                "role": "user",
                "content": (
                    f"These frames come from one short video. Write a single line of at most "
                    f"{max_words} words to show on screen for the whole video, that makes people "
                    "want to watch: specific to what it is about, natural, no hashtags, no "
                    "quotation marks, never a generic description of a person."
                    + (f" What is said in it: {context}" if context else "")
                    + (f" Guidance: {guide}" if guide else "")
                    + (f" Write it in {language}." if language else "")
                ),
            }
        ],
        _Line.model_json_schema(),
        images=frames,
        temperature=0.5,
    )
    words = _Line.model_validate(reply).line.strip().strip('"').split()
    return " ".join(words[:max_words])


def fit_lines(cues: list[Cue], measure, max_width: float, max_lines: int = 2) -> list[Cue]:
    """Break whole-line captions (no word timings) into at most `max_lines` lines that fit
    `max_width`, splitting longer ones into several captions timed by their length.
    `measure` gives a string's rendered width in pixels."""
    out: list[Cue] = []
    for cue in cues:
        if cue.words:
            out.append(cue)
            continue
        lines: list[str] = []
        for word in cue.text.split():
            if lines and measure(f"{lines[-1]} {word}") <= max_width:
                lines[-1] = f"{lines[-1]} {word}"
            else:
                lines.append(word)
        chunks = [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]
        total = sum(len(" ".join(c)) for c in chunks)
        start = cue.start
        for chunk in chunks:
            share = len(" ".join(chunk)) / total if total else 1.0
            end = start + (cue.end - cue.start) * share
            out.append(Cue(start=round(start, 3), end=round(end, 3), text="\n".join(chunk)))
            start = end
    return out
