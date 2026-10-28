"""A song and its artist from a video's name, "Song - Artist", for presets whose text
names them with {song} and {artist}. The rules are the owner's earlier reel tool's."""

import re

from agentic_editor.presets import Preset

# Most specific first: a spaced hyphen beats a bare one, so Jay-Z or a hyphenated title
# isn't split in the middle.
SEPARATORS = ("—", "–", " - ", " -", "- ", "-")
PLACEHOLDERS = ("{song}", "{artist}")

# A bracketed group with a digit in it, "(2)", "(Live 1982)", "[2011 Remaster]": a second
# copy or a release note, not part of the name. "(feat. Adam Levine)" stays.
NUMBERED_BRACKETS = re.compile(r"\s*[(\[{][^)\]}]*\d[^)\]}]*[)\]}]")
# A track number: "01. ", "3) " or a zero-padded "07 ". A bare "50 Cent" is a name.
TRACK_NUMBER = re.compile(r"^(?:\d{1,3}\s*[.)]\s+|0\d{1,2}[\s.)_-]+)")
EDGE_PUNCTUATION = " \t-_,;:|/\\"


class UnnamedSong(ValueError):
    """The video's name doesn't say which song and artist it is."""


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


def fill(preset: Preset, title: str) -> Preset:
    """The preset with {song} and {artist} in its text replaced from the video's name."""
    if not any(p in block.text for block in preset.text_blocks for p in PLACEHOLDERS):
        return preset
    song, artist = song_and_artist(title)
    blocks = [
        block.model_copy(
            update={"text": block.text.replace("{song}", song).replace("{artist}", artist)}
        )
        for block in preset.text_blocks
    ]
    return preset.model_copy(update={"text_blocks": blocks})
