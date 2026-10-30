"""A headline written for each video, for presets whose text has {headline} in it: the
editor's own, given in the notes as "headline: ...", or one the planner writes from the
video's title and description in the way the preset's hint asks."""

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from agentic_editor import caption
from agentic_editor.agent import LANGUAGES
from agentic_editor.download import Source
from agentic_editor.llm import parse
from agentic_editor.presets import Preset
from agentic_editor.render import text

PLACEHOLDER = "{headline}"
# A notes line that gives the headline itself rather than an instruction.
GIVEN = re.compile(r"^\s*(headline|heading|titular|título|titulo)\s*:\s*(.+?)\s*$", re.I | re.M)
DESCRIPTION_CHARS = 1500
MAX_LINES = 5  # past this the block shrinks instead


class Written(BaseModel):
    song: str
    artist: str
    headline: str


PROMPT = """Write the headline of a short vertical video, shown in big letters above the
picture.

The video:
Title: {title}
Channel: {uploader}
Description: {description}

Reply with:
- song: the name of the song the video is about, as the title or description give it;
  empty if they name no song.
- artist: who sings or made it, as they give it; empty if they don't say.
- headline: {hint}
  One sentence of at most {max_chars} characters, without hashtags or emoji, with the
  song's name in quotes, saying nothing the title and description don't support."""


SAID_CHARS = 2500  # of what the clip says, for the model
QUOTE = re.compile(r"[“\"]([^”\"]{3,})[”\"]")
# A quoted line counts as said in the clip when this share of its words were heard.
HEARD = 0.75

IDENTIFY = """A short vertical video is a clip from something people know: a film, a
series, a speech, a song. Say what it is from. Go by what is said in the clip: a page's
title and description can name the wrong film.

Title: {title}
Channel: {uploader}
Description: {description}
Said in it (speech recognition, may mishear a word): {said}

Reply with:
- named: the film (or series, speech, song) the title or description say it is from;
  empty if they name none.
- said_from: the film (or series, speech, song) whose scene the words said in the clip
  are from, as far as you know that scene's dialogue; empty if you don't know it.
- subject: the one it is from, by its title as Wikipedia has it, e.g. "The Godfather":
  said_from when you know it, else named; empty if neither.
- sure: "yes" only if the words said in the clip are that film's own dialogue as you
  know it; "no" if they are from another one; "unsure" if you can't tell.
- line: the best-known line said in the clip, as it is said, mending only a word the
  speech recognition plainly misheard; empty if nothing said in it is a known line."""

RESEARCHED = """Write the headline of a short vertical video, shown in big letters above the
picture. {hint}
One line of at most {max_chars} characters, in {language}, without hashtags or emoji. Say
only what the clip and the sources below say, nothing else you know: no year, name or
number they don't give.

The clip is from: {subject}
The line said in it: {line}
Its title: {title}
Its description: {description}

{sources}

Reply with headline."""


class Subject(BaseModel):
    # All required, as for ratings: the model leaves out what it may.
    named: str
    said_from: str
    subject: str
    sure: Literal["yes", "no", "unsure"]
    line: str


class Researched(BaseModel):
    headline: str


class NoHeadline(ValueError):
    """Neither the notes nor the video's title and description give a headline."""


def wanted(preset: Preset) -> bool:
    return any(PLACEHOLDER in block.text for block in preset.text_blocks)


def from_notes(notes: str | None) -> tuple[str | None, str | None]:
    """The headline the notes give, and the notes without it for the instruction reader."""
    if not notes or not (match := GIVEN.search(notes)):
        return None, notes
    rest = (notes[: match.start()] + notes[match.end() :]).strip()
    return match.group(2).strip(), rest or None


def write(llm, source: Source, preset: Preset) -> str:
    """The planner's headline for the video, in the way the preset's hint asks."""
    prompt = PROMPT.format(
        title=source.title,
        uploader=source.uploader or "(unknown)",
        description=source.description.strip()[:DESCRIPTION_CHARS] or "(none)",
        hint=preset.headline.hint or "Say plainly what the video shows.",
        max_chars=preset.headline.max_chars,
    )
    messages = [{"role": "user", "content": prompt}]
    reply = _ask(llm, messages)
    # A video about an artist's early days can name no song; one that names neither is
    # about nothing the headline could say.
    named = bool(reply.song.strip() or reply.artist.strip())
    if named and len(reply.headline) > preset.headline.max_chars:
        messages += [
            {"role": "assistant", "content": reply.model_dump_json()},
            {"role": "user", "content": f"Shorter: at most {preset.headline.max_chars} letters."},
        ]
        reply = _ask(llm, messages)
    if not named or not reply.headline:
        raise NoHeadline(
            "the video's title and description don't say which song it is; write the "
            "headline in the notes as 'headline: ...'"
        )
    return quote(reply.headline, reply.song.strip())


def write_researched(
    llm, source: Source, preset: Preset, said: str, cache
) -> tuple[str, list[str], str]:
    """The headline from what the clip is and says, looked up on Wikipedia (see
    Headline.research); what to check before posting it (names and numbers the sources
    don't give, a quote the clip doesn't say); and the line said, for the planner to keep."""
    said = " ".join(said.split())[:SAID_CHARS]
    description = source.description.strip()[:DESCRIPTION_CHARS] or "(none)"
    asked = IDENTIFY.format(title=source.title, uploader=source.uploader or "(unknown)",
                            description=description, said=said or "(nothing)")  # fmt: skip
    found = parse(
        Subject,
        llm.chat_json(
            [{"role": "user", "content": asked}], Subject.model_json_schema(), temperature=0.0
        ),
    )
    subject, line = found.subject.strip(), found.line.strip()
    if not subject:
        raise NoHeadline(
            "couldn't tell what the clip is from; write the headline in the notes as "
            "'headline: ...'"
        )
    # A page that labels a clip with the wrong film is caught by what the clip says: a
    # headline naming that film would be wrong, so none is written.
    if found.sure == "no":
        raise NoHeadline(
            f"the page says the clip is from {found.named or subject}, but what is said in "
            "it isn't that film's; write the headline in the notes as 'headline: ...'"
        )
    doubts = []
    if found.sure == "unsure":
        doubts.append(f"headline: check the clip is from {subject}; what it says didn't settle it")
    try:
        sources = caption.gather_topic(subject, preset.headline.language, cache)
        block = caption._sources_block(sources)
    except caption.CaptionError:
        sources, block = caption.Sources(), "(Wikipedia has nothing on it)"
        doubts.append(f"headline: Wikipedia has no article on {subject}, so nothing was checked")
    prompt = RESEARCHED.format(
        hint=preset.headline.hint or "Say what the clip is and the line said in it.",
        max_chars=preset.headline.max_chars,
        language=LANGUAGES.get(preset.headline.language, preset.headline.language),
        subject=subject, line=line or "(none)", title=source.title, description=description,
        sources=block,
    )  # fmt: skip
    messages = [{"role": "user", "content": prompt}]
    written = _researched(llm, messages)
    if len(written) > preset.headline.max_chars:
        messages += [
            {"role": "assistant", "content": Researched(headline=written).model_dump_json()},
            {"role": "user", "content": f"Shorter: at most {preset.headline.max_chars} letters."},
        ]
        written = _researched(llm, messages)
    if not written:
        raise NoHeadline("no headline came back; write it in the notes as 'headline: ...'")
    known = "\n".join([sources.corpus(), said, line, subject, source.title, description])
    doubts += [f"headline: not in the sources: {t}"
               for t in caption.unsupported_terms(written, known)]  # fmt: skip
    doubts += [f"headline quotes “{q}”, which isn't what the clip says"
               for q in QUOTE.findall(written) if not heard(q, said)]  # fmt: skip
    return written, doubts, line


def heard(quote: str, said: str) -> bool:
    """Whether the clip says the quoted line: most of its words were heard."""
    words = caption.NAME.findall(caption.plain(quote))
    spoken = set(caption.NAME.findall(caption.plain(said)))
    return bool(words) and sum(w in spoken for w in words) >= HEARD * len(words)


def _researched(llm, messages: list[dict]) -> str:
    reply = parse(
        Researched, llm.chat_json(messages, Researched.model_json_schema(), temperature=0.0)
    )
    return tidy(reply.headline)


def _ask(llm, messages: list[dict]) -> Written:
    reply = parse(Written, llm.chat_json(messages, Written.model_json_schema(), temperature=0.0))
    return reply.model_copy(update={"headline": tidy(reply.headline)})


def quote(line: str, song: str) -> str:
    """The headline with the song's name in curly quotes, as the model often forgets."""
    if not song:
        return line
    found = re.search(rf"[\"'‘’“”]?({re.escape(song)})[\"'‘’“”]?", line, re.I)
    if not found:
        return line
    return line[: found.start()] + f"“{found.group(1)}”" + line[found.end() :]


def tidy(line: str) -> str:
    """One line, without a full stop or quotes around the whole of it, and with straight
    quotes made curly."""
    line = " ".join(line.split()).rstrip(".")
    for opening, closing in ('""', "“”", "''"):
        inner = line[1:-1]
        if line[:1] == opening and line[-1:] == closing and opening not in inner:
            line = inner.strip()
    if line.count('"') % 2 == 0:
        parts = line.split('"')
        line = "".join(
            part + ("“" if i % 2 == 0 else "”") if i < len(parts) - 1 else part
            for i, part in enumerate(parts)
        )
    return line


def lines(headline: str, face: text.Face, size: int, max_width: int) -> str:
    """The headline in as few lines as fit `max_width` at `size`, of even widths and never
    breaking inside a quoted name, so it is wrapped rather than shrunk."""
    words = headline.split()
    quoted = _inside_quotes(words)
    best: tuple[int, float, list[str]] = (0, 0.0, [headline])
    for count in range(1, min(len(words), MAX_LINES) + 1):
        candidates = []
        for split in _splits(len(words), count):
            rows = [" ".join(words[a:b]) for a, b in split]
            widths = [face.length(row, size) for row in rows]
            widest = max(widths)
            broken = sum(quoted[a] for a, _ in split[1:])
            ragged = sum((widest - w) ** 2 for w in widths)
            candidates.append((widest > max_width, broken, widest, ragged, rows))
        too_wide, broken, widest, _, rows = min(candidates, key=lambda c: c[:4])
        best = (broken, widest, rows)
        if not too_wide:
            break
    return "\n".join(best[2])


def _inside_quotes(words: list[str]) -> list[bool]:
    """For each word, whether a line starting at it would split a quoted name."""
    inside, found = False, []
    for word in words:
        found.append(inside)
        for ch in word:
            if ch == "“" or (ch == '"' and not inside):
                inside = True
            elif ch in '”"':
                inside = False
    return found


def _splits(n: int, count: int) -> list[list[tuple[int, int]]]:
    """Every way to cut n words into `count` non-empty consecutive runs."""
    if count == 1:
        return [[(0, n)]]
    if count > n:
        return []
    found: list[list[tuple[int, int]]] = []
    for first in range(1, n - count + 2):
        for rest in _splits(n - first, count - 1):
            found.append([(0, first), *[(a + first, b + first) for a, b in rest]])
    return found


def fill(preset: Preset, headline: str, face: Callable[..., text.Face]) -> Preset:
    """The preset with {headline} in its text replaced, wrapped to each block's width."""
    if preset.headline.uppercase:
        headline = headline.upper()
    blocks = []
    for block in preset.text_blocks:
        if PLACEHOLDER in block.text:
            filled = block.text.replace(PLACEHOLDER, headline)
            wrapped = "\n".join(
                lines(row, face(block.font, row), block.size, block.max_width)
                for row in filled.split("\n")
            )
            block = block.model_copy(update={"text": wrapped})
        blocks.append(block)
    return preset.model_copy(update={"text_blocks": blocks})
