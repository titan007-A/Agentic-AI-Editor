"""A song and its artist from a video's name, "Song - Artist", for presets whose text
names them with {song} and {artist}. The rules are the owner's earlier reel tool's.

Other words in braces are the preset's own, filled per reel: {year} from a "(1980)" in the
video's name, and any of them, {day} say, from a line "day: 103" in the job's notes."""

import re

from agentic_editor.presets import Preset

# Most specific first: a spaced hyphen beats a bare one, so Jay-Z or a hyphenated title
# isn't split in the middle.
SEPARATORS = ("—", "–", " - ", " -", "- ", "-")
PLACEHOLDERS = ("{song}", "{artist}")
FIELD = re.compile(r"\{(\w+)\}")
# Filled elsewhere: the song and artist from the name, the headline by headline.py.
OWN = {"song", "artist", "headline"}
YEAR = re.compile(r"[(\[][^)\]]*?\b((?:19|20)\d{2})\b[^)\]]*[)\]]")

# A bracketed group with a digit in it, "(2)", "(Live 1982)", "[2011 Remaster]": a second
# copy or a release note, not part of the name. "(feat. Adam Levine)" stays.
NUMBERED_BRACKETS = re.compile(r"\s*[(\[{][^)\]}]*\d[^)\]}]*[)\]}]")
# A track number: "01. ", "3) " or a zero-padded "07 ". A bare "50 Cent" is a name.
TRACK_NUMBER = re.compile(r"^(?:\d{1,3}\s*[.)]\s+|0\d{1,2}[\s.)_-]+)")
EDGE_PUNCTUATION = " \t-_,;:|/\\"


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


def song_and_artist(title: str) -> tuple[str, str]:
    for separator in SEPARATORS:
        if separator in title:
            song, _, artist = title.partition(separator)
            song, artist = song.strip(), artist.strip()
            if song and artist:
                return strip_numbers(song), strip_numbers(artist)
            raise UnnamedSong(
                f"'{title}' has a '{separator.strip() or separator}' but nothing on one side "
                "of it; name the file 'Song - Artist'"
            )
    raise UnnamedSong(f"'{title}' doesn't say the song and artist; name it 'Song - Artist'")


def names_song(preset: Preset) -> bool:
    return any(p in block.text for block in preset.text_blocks for p in PLACEHOLDERS)


def reel_title(title: str) -> str:
    """The reel's title for a preset that names the song: 'Song - Artist', from the
    video's name rather than the planner's guess, which can name the wrong artist."""
    song, artist = song_and_artist(title)
    return f"{song} - {artist}"


def fields(preset: Preset) -> set[str]:
    """The preset's own words in braces: {year}, {day}, not {song} or {headline}."""
    return {f for block in preset.text_blocks for f in FIELD.findall(block.text)} - OWN


def _key(name: str) -> str:
    return re.escape(name).replace("_", "[ _]")


def from_notes(notes: str | None, wanted: set[str]) -> tuple[dict[str, str], str | None]:
    """Values the notes give for `wanted` fields, one "name: value" line each, and the notes
    without those lines, so the instruction reader doesn't take them for an instruction."""
    if not notes or not wanted:
        return {}, notes
    given = {}
    for name in sorted(wanted):
        line = re.compile(rf"^\s*{_key(name)}\s*:\s*(.+?)\s*$", re.I | re.M)
        if match := line.search(notes):
            given[name] = match.group(1)
            notes = notes[: match.start()] + notes[match.end() :]
    return given, notes.strip() or None


def year_of(title: str) -> str | None:
    match = YEAR.search(title)
    return match.group(1) if match else None


def fill(preset: Preset, title: str, given: dict[str, str] | None = None) -> Preset:
    """The preset with {song} and {artist} in its text replaced from the video's name, and
    its own fields from `given` (the notes) or, for {year}, the name."""
    own = fields(preset)
    if not names_song(preset) and not own:
        return preset
    values = dict(given or {})
    if "year" in own and "year" not in values and (year := year_of(title)):
        values["year"] = year
    if names_song(preset):
        values["song"], values["artist"] = song_and_artist(title)
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
