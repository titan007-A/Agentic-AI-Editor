"""Numbered song lists in a PDF, read by rule rather than by the model.

A list like

    1960-1969
    1.  Gracias a la vida (1966)  -  Violeta Parra
    Una celebración luminosa de la existencia...
    2.  Alfonsina y el mar (1969)  -  Mercedes Sosa
    ...

is read whole, however long, with each entry's number, its section (the heading above
where the numbering starts again at 1: a decade, a genre, anything), song, singer, year
when it gives one, and the lines written under it. The person then picks the sections and
the numbers to research, a preset for each section, and where the lines under each song
go (above the video, below it, or nowhere); lines the PDF labels "Top text:" or "Bottom
text:" go where their label says. Which side of the dash is the song is checked on a few
samples and can be swapped: "Song - Singer" and "Singer - Song" lists both exist.

A PDF that isn't a numbered list of this kind is left to the model (research.pdf_entries).
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass

PAGE_BREAK = "\f"
# "12. ", " 3) ", "007. ": a number, a dot or bracket, then the entry.
ENTRY = re.compile(r"^\s*(\d{1,4})\s*[.)]\s+(\S.*)$")
# The dash between song and singer, spaced, so "Jay-Z" or "Ob-La-Di" isn't split.
DASH = re.compile(r"\s+[-–—]\s+")
YEAR = re.compile(r"\s*[(\[]\s*((?:1[89]|20)\d\d)\s*[)\]]")
PAGE_NUMBER = re.compile(
    r"^(?:page|página|pagina|pag\.?|seite|p\.)?\s*\d{1,4}(?:\s*/\s*\d+)?$", re.IGNORECASE
)
# "Top text: ..." and the like (research.LABEL has the words in more languages).
LABEL = re.compile(r"^\s*([^:]{2,40}?)\s*:\s*(.*)$")
HEADING_LINES = 3  # lines above a list's first entry that may name its section
HEADING_LENGTH = 70
MIN_ENTRIES = 5  # fewer numbered lines than this aren't a list worth reading by rule


@dataclass
class Listed:
    number: int
    section: str
    song: str
    singer: str
    year: str
    lines: list[str]  # what is written under it, each line as the PDF has it
    labelled: dict[str, str]  # "top"/"bottom" -> words a "Top text:" line gives

    def as_dict(self) -> dict:
        return asdict(self)


def _boilerplate(pages: list[list[str]]) -> set[str]:
    """Running headers and footers: a line at the start or end of most pages."""
    if len(pages) < 3:
        return set()
    edges = Counter(
        line for page in pages for line in {*page[:3], *page[-3:]} if not ENTRY.match(line)
    )
    return {line for line, n in edges.items() if n >= max(3, len(pages) // 2)}


def _pages(text: str) -> list[list[str]]:
    pages = [
        [line.strip() for line in page.splitlines() if line.strip()]
        for page in text.split(PAGE_BREAK)
    ]
    noise = _boilerplate(pages)
    return [
        [line for line in page if line not in noise and not PAGE_NUMBER.match(line)]
        for page in pages
    ]


def _heading(line: str) -> bool:
    return len(line) <= HEADING_LENGTH and not line.rstrip().endswith((".", ",", ";", "»", '"'))


def _split(rest: str) -> tuple[str, str, str]:
    """(first part, second part, year) of an entry's text: the two sides of its dash."""
    year = ""
    if match := YEAR.search(rest):
        year = match.group(1)
        rest = (rest[: match.start()] + rest[match.end() :]).strip()
    parts = DASH.split(rest, maxsplit=1)
    first = parts[0].strip(" \t-–—")
    second = parts[1].strip(" \t-–—") if len(parts) > 1 else ""
    return first, second, year


def read(text: str, labels: dict[str, set[str]] | None = None) -> list[Listed]:
    """The entries of a numbered song list, in order; [] when `text` isn't one. `labels`
    maps "top"/"bottom" to words that label a line as going there."""
    labels = labels or {"top": set(), "bottom": set()}
    lines = [line for page in _pages(text) for line in page]
    found: list[Listed] = []
    sections: list[str] = []
    previous = 0
    pending: list[str] = []  # lines since the last entry
    for line in lines:
        match = ENTRY.match(line)
        if not match:
            pending.append(line)
            continue
        number = int(match.group(1))
        if number <= previous or not found:
            # The numbering starts again: a new section, named by the heading lines just
            # above, which aren't the last entry's lines.
            heading: list[str] = []
            while pending and len(heading) < HEADING_LINES and _heading(pending[-1]):
                heading.insert(0, pending.pop())
            sections.append(heading[0] if heading else f"Part {len(sections) + 1}")
        if found:
            _attach(found[-1], pending, labels)
        pending = []
        first, second, year = _split(match.group(2))
        found.append(Listed(number, sections[-1], first, second, year, [], {}))
        previous = number
    if found:
        _attach(found[-1], pending, labels)
    if len(found) < MIN_ENTRIES:
        return []
    # After the last entry may come the PDF's closing words: it keeps as many lines as
    # entries usually have.
    usual = Counter(len(e.lines) for e in found[:-1]).most_common(1)[0][0]
    found[-1].lines = found[-1].lines[: max(1, usual)]
    return found


def _attach(entry: Listed, lines: list[str], labels: dict[str, set[str]]) -> None:
    """The lines under an entry: wrapped lines joined back into the sentences they were;
    a labelled line ("Top text: ...") kept by its label."""
    current: str | None = None
    for line in lines:
        if match := LABEL.match(line):
            words = set(re.findall(r"\w+", match.group(1).lower()))
            place = next((p for p, said in labels.items() if words & said), None)
            if place is not None:
                current = place
                entry.labelled[place] = match.group(2).strip()
                continue
        if current is not None:
            entry.labelled[current] = f"{entry.labelled[current]} {line}".strip()
        elif entry.lines and not entry.lines[-1].rstrip().endswith((".", "!", "?", "…")):
            entry.lines[-1] = f"{entry.lines[-1]} {line}"  # a sentence wrapped onto two
        else:
            entry.lines.append(line)


def sections(entries: list[Listed]) -> list[dict]:
    """Each section once, in order: its name, how many entries and their numbers."""
    return sections_of([e.as_dict() for e in entries])


def sections_of(listing: list[dict]) -> list[dict]:
    """sections() of a listing stored as dicts."""
    out: dict[str, dict] = {}
    for e in (Listed(**d) for d in listing):
        s = out.setdefault(e.section, {"name": e.section, "count": 0, "first": e.number,
                                       "last": e.number})  # fmt: skip
        s["count"] += 1
        s["first"], s["last"] = min(s["first"], e.number), max(s["last"], e.number)
    return list(out.values())


def pick(
    listing: list[dict],
    sections: list[str] | None = None,
    first: int = 1,
    last: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[int]:
    """The positions in `listing` of the songs in `sections` (all when None), numbered
    `first` to `last` within their section, from `year_from` to `year_to` (a song without
    a year is kept by a year range)."""
    kept = []
    for i, e in enumerate(listing):
        if sections is not None and e["section"] not in sections:
            continue
        if e["number"] < first or (last is not None and e["number"] > last):
            continue
        year = int(e["year"]) if str(e.get("year", "")).isdigit() else None
        if year is not None and (
            (year_from is not None and year < year_from) or (year_to is not None and year > year_to)
        ):
            continue
        kept.append(i)
    return kept


def entries_for(
    listing: list[dict], keep: list[int], swap: bool, line: str, presets: dict[str, str]
) -> list[dict]:
    """The research entries of the songs at `keep`: song and singer the other way round
    if `swap`, the lines under each song as its `line` ("top", "bottom" or "none") text
    unless the PDF labels them, and each section's preset."""
    out = []
    for i in keep:
        e = listing[i]
        song, singer = (e["singer"], e["song"]) if swap else (e["song"], e["singer"])
        texts = {"top": "", "bottom": ""}
        written = "\n".join(e.get("lines") or [])
        if line in texts and written:
            texts[line] = written
        texts.update({k: v for k, v in (e.get("labelled") or {}).items() if k in texts})
        out.append(
            {
                "singer": singer,
                "song": song,
                "top": texts["top"],
                "bottom": texts["bottom"],
                "year": e.get("year", ""),
                "number": e["number"],
                "section": e["section"],
                "preset": presets.get(e["section"], ""),
            }
        )
    return out


def chosen(
    entries: list[Listed],
    wanted: list[str] | None,
    first: int,
    last: int | None,
    swap: bool,
    line: str,
    presets: dict[str, str],
) -> list[dict]:
    """entries_for the songs pick() keeps of `entries`."""
    listing = [e.as_dict() for e in entries]
    return entries_for(listing, pick(listing, wanted, first, last), swap, line, presets)
