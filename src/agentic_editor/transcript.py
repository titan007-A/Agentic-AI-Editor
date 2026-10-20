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
