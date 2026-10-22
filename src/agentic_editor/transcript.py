from collections.abc import Callable

from pydantic import BaseModel


class Word(BaseModel):
    text: str
    start: float
    end: float
    probability: float = 1.0


class Transcript(BaseModel):
    language: str | None = None
    words: list[Word] = []

    def between(self, start: float, end: float) -> list[Word]:
        """Words whose midpoint falls inside [start, end)."""
        return [w for w in self.words if start <= (w.start + w.end) / 2 < end]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.between(start, end))


def phrases(words: list[Word], min_gap: float = 0.6) -> list[list[Word]]:
    """Group words into phrases separated by pauses of at least min_gap seconds."""
    groups: list[list[Word]] = []
    for word in words:
        if groups and word.start - groups[-1][-1].end < min_gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


SENTENCE_END = ".?!"
CLAUSE_END = ",;:"


def ends_sentence(word: Word) -> bool:
    return word.text.rstrip()[-1:] in SENTENCE_END


def gap(before: Word, after: Word) -> float:
    return max(0.0, after.start - before.end)


def _break_value(before: Word, after: Word, pause: Callable[[Word, Word], float]) -> float:
    """How natural a place it is to start a new line: after a sentence or a clause ends,
    or at a pause. A capital alone means nothing mid-sentence; it is usually a name."""
    tail = before.text.rstrip()[-1:]
    value = 3.0 if tail in SENTENCE_END else 1.5 if tail in CLAUSE_END else 0.0
    return value + min(1.0, pause(before, after) / 0.5)


def split_phrase(
    phrase: list[Word],
    max_words: int,
    pause: Callable[[Word, Word], float] = gap,
    min_words: int = 4,
) -> list[list[Word]]:
    """A phrase as lines that start where a sentence starts. A line still longer than
    max_words is split where a clause ends, or failing that at its longest pause."""
    options = range(min_words, len(phrase) - min_words + 1)
    if len(phrase) <= max_words:
        options = [i for i in options if ends_sentence(phrase[i - 1])]
    if not options:
        return [phrase]
    middle = len(phrase) / 2
    at = max(
        options, key=lambda i: (_break_value(phrase[i - 1], phrase[i], pause), -abs(i - middle))
    )
    return split_phrase(phrase[:at], max_words, pause, min_words) + split_phrase(
        phrase[at:], max_words, pause, min_words
    )
