"""Captions of every kind as one timeline of cues, which the renderer burns in and which
is also written out as an .srt file.

Speech and lyrics come straight from the transcript. Translated captions keep the
timing of the spoken lines and swap their text; captions written from the visuals are
timed to the shots they describe. Placement moves a caption off any face it would cover.
"""

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agentic_editor.edl import Cue
from agentic_editor.llm import LLMError, parse
from agentic_editor.transcript import (
    CLAUSE_END,
    SENTENCE_END,
    Word,
    phrases,
    split_phrase,
)

LINE_BREAK_PAUSE = 0.6  # a pause this long starts a new caption line
LYRIC_BREAK = 0.8  # sung lines are longer and breathe less often
LYRIC_WORDS = 8
FACE_PAD = 24  # px kept clear around a face
NEAR_EDGE = 220  # px from the frame edge for the fallback positions
SETTLE = 0.6  # a pause this long lets a moved caption go back to its usual place


# Longer linking words that, like short ones, belong with the word after them.
LINKING = {
    "they",
    "there",
    "don't",
    "can't",
    "won't",
    "didn't",
    "doesn't",
    "would",
    "could",
    "should",
    "have",
    "been",
    "were",
    "that",
    "this",
    "these",
    "those",
    "with",
    "from",
    "into",
    "onto",
    "than",
    "their",
    "about",
    "over",
    "under",
    "after",
    "before",
    "which",
    "while",
    "when",
    "where",
    "whose",
    "because",
    "para",
    "pero",
    "como",
    "sobre",
    "entre",
    "desde",
    "hasta",
    "esta",
    "este",
    "estos",
    "estas",
    "esos",
    "esas",
    "porque",
    "cuando",
    "della",
    "delle",
    "degli",
    "dello",
    "nella",
    "nelle",
    "negli",
    "sulla",
    "sulle",
    "come",
    "questo",
    "questa",
    "quando",
    "perché",
    "dans",
    "avec",
    "pour",
    "sans",
    "cette",
    "leurs",
    "leur",
    "quand",
    "parce",
    "pelo",
    "pela",
    "pelos",
    "pelas",
    "numa",
    "essa",
    "esse",
    "isso",
}


def break_cost(before: str, after: str | None) -> float:
    """How badly a caption line reads if it ends after `before`, ahead of `after`: free
    after punctuation, cheap after a full word, costly after a short linking word ("of",
    "the", "de", "los") or when it parts a number from the word before it."""
    word = before.strip()
    if word[-1:] in SENTENCE_END + CLAUSE_END:
        return 0.0
    bare = word.strip("\"'()").lower()
    content = (len(bare) > 3 and bare not in LINKING) or any(ch.isdigit() for ch in word)
    cost = 0.5 if content else 2.0
    if after and after.strip()[:1].isdigit():
        cost += 2.0
    return cost


AVERAGE_WORD = 6  # characters, with its space: how long `per_line` words usually run


def split_well(
    texts: list[str],
    target: int,
    longest: int,
    cost_after,
    fits=lambda i, j: True,
    inside=lambda i, j: 0.0,
) -> list[int]:
    """Where to break words into lines of about `target` words' length and at most
    `longest` words, with breaks where the text reads well; `fits(i, j)` says whether
    words i to j fit on one line and `inside(i, j)` what reads badly within it. Length is
    measured in characters, so one long word can be a line of its own. Returns the line
    lengths in words."""
    count = len(texts)
    reach = [0, *itertools.accumulate(len(t) + 1 for t in texts)]
    best = [0.0] + [float("inf")] * count
    back = [0] * (count + 1)
    for j in range(1, count + 1):
        for i in range(max(0, j - longest), j):
            size = j - i
            if size > 1 and not fits(i, j):
                continue
            off = (reach[j] - reach[i] - target * AVERAGE_WORD) / AVERAGE_WORD
            value = best[i] + 0.5 * off**2 + inside(i, j)
            value += cost_after(j) if j < count else 0.0
            if value < best[j]:
                best[j], back[j] = value, i
    sizes, j = [], count
    while j > 0:
        sizes.append(j - back[j])
        j = back[j]
    return sizes[::-1]


def speech_cues(
    words: list[Word], per_line: int, fits: Callable[[str], bool] | None = None
) -> list[Cue]:
    """A few words at a time, broken at pauses, with word timings for the highlight. A
    line may take a word or two more than `per_line` when that keeps it from ending on a
    linking word or leaving one word alone, as long as it `fits` the frame."""
    longest = per_line + 2 if per_line >= 3 else per_line
    lines: list[list[Word]] = []
    # Each sentence starts its own line.
    sentences = [
        part
        for phrase in phrases(words, LINE_BREAK_PAUSE)
        for part in split_phrase(phrase, len(phrase), min_words=1)
    ]
    for phrase in sentences:
        texts = [w.text for w in phrase]

        def cost_after(j: int, texts: list[str] = texts) -> float:
            return break_cost(texts[j - 1], texts[j])

        def fit(i: int, j: int, texts: list[str] = texts) -> bool:
            return fits is None or fits(" ".join(texts[i:j]))

        def inside(i: int, j: int, texts: list[str] = texts) -> float:
            # A clause that ends mid-line reads as if the line ran on past it.
            return 1.5 * sum(t.strip()[-1:] in CLAUSE_END for t in texts[i : j - 1])

        start = 0
        for size in split_well(texts, per_line, longest, cost_after, fit, inside):
            lines.append(phrase[start : start + size])
            start += size
    return [_cue(line) for line in lines]


def lyric_cues(
    words: list[Word],
    max_words: int = LYRIC_WORDS,
    breaths: list[tuple[float, float]] | None = None,
    fits: Callable[[str], bool] | None = None,
) -> list[Cue]:
    """Whole sung lines. A line ends where the singer breathes (the voiced stretches of the
    isolated vocal, when given, else a pause in the words). A stretch longer than max_words,
    or too wide to `fit` the frame, is split where the transcript starts a new line, at a
    capital or after punctuation, and never leaves a single word on its own if it can help
    it."""
    fits = fits or (lambda line: True)
    if breaths:
        # Each word goes with the breath it is nearest: one whose timing runs into the
        # silence after a line, or that is too short to count as voiced, is still sung.
        groups = [[] for _ in breaths]
        for word in words:
            mid = (word.start + word.end) / 2
            nearest = min(
                range(len(breaths)),
                key=lambda i: max(breaths[i][0] - mid, 0.0, mid - breaths[i][1]),
            )
            groups[nearest].append(word)
    else:
        groups = []
        for word in words:
            if groups and word.start - groups[-1][-1].end < LYRIC_BREAK:
                groups[-1].append(word)
            else:
                groups.append([word])
    groups = _move_stranded([g for g in groups if g])
    lines = [line for group in groups if group for line in _split_line(group, max_words, fits)]
    return [_cue(line) for line in _join_scraps(lines, max_words, fits)]


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


def _join_scraps(
    lines: list[list[Word]], max_words: int, fits: Callable[[str], bool], near: float = 2.5
) -> list[list[Word]]:
    """Join a one-word line to a close neighbour: a lone "me" after "save" is one phrase."""
    out: list[list[Word]] = []
    for line in lines:
        if out and (len(line) == 1 or len(out[-1]) == 1):
            gap = line[0].start - out[-1][-1].end
            joined = out[-1] + line
            if gap < near and len(joined) <= max_words + 2 and fits(_text(joined)):
                out[-1] = joined
                continue
        out.append(line)
    return out


def _text(line: list[Word]) -> str:
    return " ".join(w.text.strip() for w in line)


def _split_line(group: list[Word], max_words: int, fits: Callable[[str], bool]) -> list[list[Word]]:
    if len(group) == 1 or (len(group) <= max_words and fits(_text(group))):
        return [group]

    def score(i: int) -> float:
        # Prefer a break before a capital or after punctuation, near the middle.
        natural = group[i].text.strip()[:1].isupper() or group[i - 1].text.strip()[-1:] in ",.;!?"
        return (0 if natural else 1, abs(i - len(group) / 2))

    # Both sides keep at least two words, unless the line is too short for that.
    candidates = range(2, len(group) - 1) if len(group) >= 4 else range(1, len(group))
    cut = min(candidates, key=score)
    return _split_line(group[:cut], max_words, fits) + _split_line(group[cut:], max_words, fits)


def _cue(line: list[Word]) -> Cue:
    return Cue(start=line[0].start, end=line[-1].end, text=_text(line), words=line)


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

    `faces` gives, at reel times, the rows each face (or other thing to keep clear)
    covers in the output frame. The preferred position wins whenever it is clear;
    otherwise the clear position nearest to it. A caption that had to move stays where it
    went, while that is clear, until the speaker pauses, so captions don't jump between
    the top and bottom of the frame line by line."""
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
    for k, cue in enumerate(cues):
        covered = [
            band for t, bands in faces if cue.start - 0.2 <= t <= cue.end + 0.2 for band in bands
        ]

        def clash(option: Placement, covered: list[tuple[float, float]] = covered) -> float:
            return overlap(option.band(line_height, frame_height), covered)

        previous = chosen[-1] if chosen else None
        speaking = k > 0 and cue.start - cues[k - 1].end < SETTLE
        if previous is not None and clash(previous) == 0 and (clash(first) > 0 or speaking):
            chosen.append(previous)
            continue
        chosen.append(min(options, key=lambda o: (clash(o), options.index(o))))
    return chosen


TRANSLATE_WORDS = 10  # lines of about a sentence give the model enough to translate well
SENTENCE_WORDS = 40  # run-on speech is translated in parts no longer than this
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
    return {e.n: e.text.strip() for e in parse(_Lines, reply).lines if e.text.strip()}


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


def sentences(words: list[Word]) -> list[list[Word]]:
    """Speech grouped into whole sentences; a run-on one is split at its pauses."""
    return [g for p in phrases(words, LINE_BREAK_PAUSE) for g in split_phrase(p, SENTENCE_WORDS)]


def even_parts(tokens: list[str], parts: int) -> list[list[str]]:
    """Words in `parts` groups of about equal length, each break moved, by a couple of
    words at most, to where the text reads best (see break_cost)."""
    parts = max(1, min(parts, len(tokens)))
    reach = list(itertools.accumulate(len(t) + 1 for t in tokens))
    breaks: list[int] = []
    for k in range(1, parts):
        target = reach[-1] * k / parts
        allowed = range((breaks[-1] if breaks else 0) + 1, len(tokens) - (parts - k) + 1)
        ideal = min(allowed, key=lambda i: abs(reach[i - 1] - target))
        near = [i for i in allowed if abs(i - ideal) <= 2]
        breaks.append(
            min(
                near,
                key=lambda i: (break_cost(tokens[i - 1], tokens[i]), abs(reach[i - 1] - target)),
            )
        )
    edges = [0, *breaks, len(tokens)]
    return [tokens[a:b] for a, b in itertools.pairwise(edges)]


def spread(text: str, words: list[Word], per_cue: int) -> list[Cue]:
    """A translated sentence over the time its original words took, in captions of about
    `per_cue` words, each up while the matching share of the original is spoken."""
    tokens = text.split()
    if not tokens or not words:
        return []
    chunks = even_parts(tokens, math.ceil(len(tokens) / per_cue))
    lengths = [len(w.text.strip()) + 1 for w in words]
    said, written = sum(lengths), sum(len(t) + 1 for t in tokens)

    def time_at(share: float) -> float:
        left = share * said
        for word, n in zip(words, lengths, strict=True):
            if left <= n:
                return word.start + (word.end - word.start) * left / n
            left -= n
        return words[-1].end

    cues, done, start = [], 0, words[0].start
    for i, chunk in enumerate(chunks):
        done += sum(len(t) + 1 for t in chunk)
        end = words[-1].end if i == len(chunks) - 1 else time_at(done / written)
        cues.append(Cue(start=round(start, 3), end=round(end, 3), text=" ".join(chunk)))
        start = end
    return cues


def translate_speech(
    llm, words: list[Word], language: str, notes: str | None, per_cue: int = TRANSLATE_WORDS
) -> list[Cue]:
    """Spoken captions in another language. Whole sentences go to the model, since a small
    model given fragments rebuilds sentences out of its neighbours' words and drifts, and
    each translation is laid back over its sentence's timing."""
    groups = sentences(words)
    done = translate(llm, [_cue(g) for g in groups], language, notes)
    return [c for g, t in zip(groups, done, strict=True) for c in spread(t.text, g, per_cue)]


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
            words = parse(_Line, reply).line.strip().strip('"').split()
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
    words = parse(_Line, reply).line.strip().strip('"').split()
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

        def wrap(words: list[str]) -> list[str]:
            lines: list[str] = []
            for word in words:
                if lines and measure(f"{lines[-1]} {word}") <= max_width:
                    lines[-1] = f"{lines[-1]} {word}"
                else:
                    lines.append(word)
            if len(lines) != 2:
                return lines
            # Two lines: break where it reads best, keeping them close in width.
            fitting = [
                i
                for i in range(1, len(words))
                if measure(" ".join(words[:i])) <= max_width
                and measure(" ".join(words[i:])) <= max_width
            ]
            if not fitting:
                return lines  # a word wider than the frame; the renderer shrinks that caption
            at = min(
                fitting,
                key=lambda i: (
                    break_cost(words[i - 1], words[i])
                    + abs(measure(" ".join(words[:i])) - measure(" ".join(words[i:]))) / max_width
                ),
            )
            return [" ".join(words[:at]), " ".join(words[at:])]

        words = cue.text.split()
        # As few captions as fit, with the words shared evenly between them, so none is
        # left holding a word or two.
        count = math.ceil(len(wrap(words)) / max_lines)
        while True:
            chunks = [wrap(part) for part in even_parts(words, count)]
            if all(len(c) <= max_lines for c in chunks) or count >= len(words):
                break
            count += 1
        total = sum(len(" ".join(c)) for c in chunks)
        start = cue.start
        for chunk in chunks:
            share = len(" ".join(chunk)) / total if total else 1.0
            end = start + (cue.end - cue.start) * share
            out.append(Cue(start=round(start, 3), end=round(end, 3), text="\n".join(chunk)))
            start = end
    return out
