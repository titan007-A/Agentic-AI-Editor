"""A song and its artist from a video's name, "Song - Artist", for presets whose text
names them with {song} and {artist}. The rules are the owner's earlier reel tool's. A link's
name ("Video by illusive.paradox") can't be changed, so lines "song: ..." and "artist: ..."
in the job's notes give them instead, and win over the name.

Other words in braces are the preset's own, filled per reel: {year} from a "(1980)" in the
video's name, and any of them, {day} say, from a line "day: 103" in the job's notes, else
a random number when the preset's random_fields gives the field a range.

Lines "top text: ..." and "bottom text: ..." replace the preset's own words above and below
the video for one job (the researcher's PDF mode gives each reel its own); "\\n" in them
starts a new line."""

import random
import re
from collections.abc import Callable

from agentic_editor.presets import Preset, TextBlock

Song = tuple[str, str]

# Most specific first: a spaced hyphen beats a bare one, so Jay-Z or a hyphenated title
# isn't split in the middle.
SEPARATORS = ("—", "–", " - ", " -", "- ", "-")
PLACEHOLDERS = ("{song}", "{artist}")
FIELD = re.compile(r"\{(\w+)\}")
# Filled elsewhere: the song and artist from the name, the headline by headline.py.
OWN = {"song", "artist", "headline"}
YEAR = re.compile(r"[(\[][^)\]]*?\b((?:19|20)\d{2})\b[^)\]]*[)\]]")
ANY_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

# A bracketed group with a digit in it, "(2)", "(Live 1982)", "[2011 Remaster]": a second
# copy or a release note, not part of the name. "(feat. Adam Levine)" stays.
NUMBERED_BRACKETS = re.compile(r"\s*[(\[{][^)\]}]*\d[^)\]}]*[)\]}]")
# A track number: "01. ", "3) " or a zero-padded "07 ". A bare "50 Cent" is a name.
TRACK_NUMBER = re.compile(r"^(?:\d{1,3}\s*[.)]\s+|0\d{1,2}[\s.)_-]+)")
EDGE_PUNCTUATION = " \t-_,;:|/\\"
NOTES_HINT = " or add lines 'song: ...' and 'artist: ...' to the notes"


class UnnamedSong(ValueError):
    """The video's name doesn't say which song and artist it is."""


class MissingField(ValueError):
    """Nothing gives a value for one of the preset's own words in braces."""


def strip_numbers(name: str) -> str:
    """The name without the numbers a file name carries for its own sake; never empty."""
    cleaned = NUMBERED_BRACKETS.sub(" ", name)
    cleaned = TRACK_NUMBER.sub("", cleaned)
    cleaned = " ".join(cleaned.split()).strip(EDGE_PUNCTUATION)
    return cleaned or name


def song_and_artist(
    title: str, given: dict[str, str] | None = None, hear: Callable[[], Song | None] | None = None
) -> tuple[str, str]:
    """From the notes (`given`), else the name, else `hear` (the sound, see shazam.py)."""
    given = given or {}
    if "song" in given and "artist" in given:
        return given["song"], given["artist"]
    try:
        song, artist = _from_name(title)
    except UnnamedSong:
        if hear is None:
            raise
        if not (heard := hear()):
            raise UnnamedSong(
                f"'{title}' doesn't say the song and artist, and neither its sound nor its "
                "post's caption told them; add lines 'song: ...' and 'artist: ...' to the notes"
            ) from None
        song, artist = heard
    return given.get("song", song), given.get("artist", artist)


def _from_name(title: str) -> tuple[str, str]:
    for separator in SEPARATORS:
        if separator in title:
            song, _, artist = title.partition(separator)
            song, artist = song.strip(), artist.strip()
            if song and artist:
                return strip_numbers(song), strip_numbers(artist)
            raise UnnamedSong(
                f"'{title}' has a '{separator.strip() or separator}' but nothing on one side "
                f"of it; name the file 'Song - Artist'{NOTES_HINT}"
            )
    raise UnnamedSong(
        f"'{title}' doesn't say the song and artist; name it 'Song - Artist'{NOTES_HINT}"
    )


def names_song(preset: Preset) -> bool:
    return any(p in block.text for block in preset.text_blocks for p in PLACEHOLDERS)


def fields(preset: Preset) -> set[str]:
    """The preset's own words in braces: {year}, {day}, not {song} or {headline}."""
    return {f for block in preset.text_blocks for f in FIELD.findall(block.text)} - OWN


def wanted(preset: Preset) -> set[str]:
    """Every word in braces the notes can give for this preset, the song and artist too."""
    return fields(preset) | ({"song", "artist"} if names_song(preset) else set())


def _key(name: str) -> str:
    return re.escape(name).replace("_", "[ _]")


def from_notes(notes: str | None, wanted: set[str]) -> tuple[dict[str, str], str | None]:
    """Values the notes give for `wanted` fields, one "name: value" line each, and the notes
    without those lines, so the instruction reader doesn't take them for an instruction.
    The last line for a field wins: a regenerated job's notes are the earlier ones plus
    the new."""
    if not notes or not wanted:
        return {}, notes
    given = {}
    for name in sorted(wanted):
        line = re.compile(rf"^\s*{_key(name)}\s*:\s*(.+?)\s*$", re.I | re.M)
        if found := list(line.finditer(notes)):
            given[name] = found[-1].group(1)
            notes = line.sub("", notes)
    return given, notes.strip() or None


TEXT_LINE = re.compile(r"^\s*(top|bottom) text\s*:\s*(.+?)\s*$", re.I | re.M)


def texts_from_notes(notes: str | None) -> tuple[dict[str, str], str | None]:
    """The words a job's notes give for above and below the video ({"top": ..., "bottom":
    ...}), the last line of each winning, and the notes without those lines."""
    if not notes:
        return {}, notes
    texts = {m.group(1).lower(): m.group(2).replace("\\n", "\n") for m in TEXT_LINE.finditer(notes)}
    rest = "\n".join(line for line in notes.splitlines() if not TEXT_LINE.match(line))
    return texts, rest.strip() or None


def with_texts(preset: Preset, texts: dict[str, str]) -> Preset:
    """The preset with `texts` above ("top") and below ("bottom") the video instead of its
    own: the first block anchored there takes the words, keeping its look, and any other
    block there goes, so the reel shows just those words. A preset without a block there
    gets one in the default look. Where a block there has {headline} in it, the words are
    that headline instead (headline_from_texts), and the blocks stay as they are."""
    texts = {a: w for a, w in texts.items() if a not in headline_anchors(preset)}
    if not texts:
        return preset
    blocks, placed = [], set()
    for block in preset.text_blocks:
        if block.anchor not in texts:
            blocks.append(block)
        elif block.anchor not in placed:
            placed.add(block.anchor)
            blocks.append(block.model_copy(update={"text": texts[block.anchor]}))
    for anchor in ("top", "bottom"):
        if anchor in texts and anchor not in placed:
            blocks.append(
                TextBlock(
                    text=texts[anchor],
                    size=64,
                    anchor=anchor,
                    offset=240 if anchor == "top" else 420,
                )
            )
    return preset.model_copy(update={"text_blocks": blocks})


def headline_anchors(preset: Preset) -> set[str]:
    """Where the preset has a block with {headline} in it: "top", "bottom"."""
    return {b.anchor for b in preset.text_blocks if "{headline}" in b.text}


def headline_from_texts(presets: list[Preset], texts: dict[str, str]) -> str | None:
    """The words given for above or below the video, when a preset's block there writes
    {headline}: they are its headline, wrapped like one, and the block's other words
    ("SONG: {song}") stay."""
    for anchor in ("top", "bottom"):
        if anchor in texts and any(anchor in headline_anchors(p) for p in presets):
            return " ".join(texts[anchor].split())
    return None


def year_of(title: str) -> str | None:
    match = YEAR.search(title)
    return match.group(1) if match else None


def year_in(text: str, song: str) -> str | None:
    """The year a post's caption gives its song, "Maldita primavera (1981) - Yuri": on a
    line that names the song, so the post's own date or another song's year isn't it."""
    if not song.strip():
        return None
    for line in text.splitlines():
        if same_name(song) in same_name(line) and (match := ANY_YEAR.search(line)):
            return match.group(1)
    return None


def same_name(text: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def fill(
    preset: Preset,
    title: str,
    given: dict[str, str] | None = None,
    hear: Callable[[], Song | None] | None = None,
    find_year: Callable[[str, str], str | None] | None = None,
) -> Preset:
    """The preset with {song} and {artist} in its text replaced from `given` (the notes) or
    the video's name, and its own fields from `given` or, for {year}, the name, and else
    `find_year` (song, artist) -> the year the song came out."""
    own = fields(preset)
    if not names_song(preset) and not own:
        return preset
    values = dict(given or {})
    if "year" in own and "year" not in values and (year := year_of(title)):
        values["year"] = year
    if names_song(preset):
        values["song"], values["artist"] = song_and_artist(title, values, hear)
    for name, (low, high) in preset.random_fields.items():
        if name in own and not values.get(name, "").strip():
            values[name] = str(random.randint(low, high))
    looked = "year" in own and "year" not in values and find_year and values.get("song")
    if looked and (year := find_year(values["song"], values.get("artist", ""))):
        values["year"] = year
    if missing := sorted(own - values.keys()):
        hint = (
            " or put the year in the video's name, 'Song (1980) - Artist'"
            if (missing == ["year"])
            else ""
        )
        raise MissingField(
            "nothing gives "
            + ", ".join(f"{{{m}}}" for m in missing)
            + ": add a line for each to the notes, like "
            + ", ".join(f"'{m.replace('_', ' ')}: ...'" for m in missing)
            + hint
        )

    def filled(text: str) -> str:
        return FIELD.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    blocks = [block.model_copy(update={"text": filled(block.text)}) for block in preset.text_blocks]
    return preset.model_copy(update={"text_blocks": blocks})
