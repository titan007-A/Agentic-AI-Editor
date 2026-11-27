"""What is known about a song's rights before its reel is posted: the year it came out and
the record label behind it, as the label's group (UMG, Sony, Warner or another). The job
page and the researcher warn about the groups in NOTIFIED and about new songs: labels
block reels of their songs, and the owner's alerts (2026-11-16) came from the three majors,
the one worldwide block from a song of the last few years.

Nothing paid is asked. The label comes, in order, from what the video itself says (Shazam's
match of its sound; a YouTube description's "Provided to YouTube by ..." and "℗ 2022 ..."
lines; a label's own channel), then MusicBrainz (free, open): the labels of the song's
earliest official releases. Imprints map to their group (Columbia is Sony, Interscope UMG,
Atlantic Warner); a label that isn't one of theirs, T-Series or Zee Music say, is "other".

MusicBrainz's answers are kept in the cache folder: a song's label and year don't change."""

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from agentic_editor import shazam

log = logging.getLogger(__name__)

GROUPS = ("UMG", "Sony", "Warner", "other")
# The groups a job, a research and the channel autopilot warn about. Warner too: it sent
# about 18 of the 47 alerts (Sony 22, UMG 7).
NOTIFIED = ("UMG", "Sony", "Warner")
GROUP_NAMES = {"UMG": "Universal Music", "Sony": "Sony Music", "Warner": "Warner Music"}
# A song this many years old or younger (this year's counting as 0) is new: the worldwide
# block was on a song of the year before, the old songs were only muted in Russia.
NEW_YEARS = 3

# Imprints and the companies that own them, as label names say them (normal(), so no
# accents, "&" as "and", punctuation as spaces). Only label names are matched, never free
# text, so a word like "epic" or "island" is safe here.
_UMG = (
    r"universal(?! pictures)|umg|ume|umle|interscope|republic|island|def jam|capitol|motown|geffen"
    r"|polydor|emi|virgin|verve|decca|mercury|a and m|aftermath|cash money|young money|darkroom"
    r"|astralwerks|caroline|ingrooves|barclay|blue note|deutsche grammophon|machete|fonovisa"
    r"|disa|polygram|vertigo|lost highway|casablanca records|def soul|universal music india"
)
_SONY = (
    r"sony|columbia|rca|epic|arista|legacy recordings|jive|zomba|syco|ministry of sound"
    r"|masterworks|ultra records|ultra music|the orchard|orchard enterprises|awal|ariola"
    r"|kemosabe|laface|provident|black butter|century media|bmg"
)
_WARNER = (
    r"warner|wea|atlantic|elektra|parlophone|reprise|asylum|300 entertainment|rhino|nonesuch"
    r"|sire|fueled by ramen|roadrunner|big beat|east ?west|spinnin|ada|erato|maverick records"
    r"|dro|word entertainment"
)
# Labels that sound like or sit beside the majors but aren't theirs: India's big labels
# above all (T-Series is Super Cassettes Industries), and the independent distributors.
# BMG Rights Management is independent; the old BMG labels (BMG Ariola, BMG Ricordi) went
# to Sony with Sony BMG, so a bare "BMG" is Sony's.
_OTHER = (
    r"t ?series|super cassettes|zee music|saregama|tips|speed records|white hill|desi melodies"
    r"|venus|aditya music|lahari|think music|shemaroo|yrf|yash raj|eros|times music"
    r"|big machine|bmg rights|believe|distrokid|tunecore|cd baby|empire|unitedmasters|onerpm|amuse"
    r"|rimas|dale play|concord|domino|xl recordings|beggars|4ad|matador|glassnote|kobalt"
    r"|secretly canadian|rough trade|warp|ninja tune|epitaph|armada|anjunabeats|monstercat"
)
# Where two match, the one earlier in the name wins, then the longer ("bmg rights").
IMPRINTS = [
    (re.compile(rf"\b(?:{p})\b"), g)
    for p, g in ((_UMG, "UMG"), (_SONY, "Sony"), (_WARNER, "Warner"), (_OTHER, "other"))
]
# What a label's own channel calls itself ("Sony Music India", "T-Series", "Atlantic
# Records"), unlike a singer's ("Epic Rap Battles" isn't Epic Records).
LABELISH = re.compile(
    r"\b(?:music|records|recordings|entertainment|company|official|label|group|t ?series)\b"
)
# Who has the recording after a licence: "℗ 2023 XO, under exclusive license to Republic".
LICENSED = re.compile(r"\blicen[cs]ed? to\s+(.+)$", re.IGNORECASE)
PROVIDED = re.compile(r"^\s*provided to youtube by\s+(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
PHONOGRAM = re.compile(r"^\s*(?:℗|\(p\))\s*((?:19|20)\d{2})?[\s,]*(.+?)\s*$", re.I | re.M)
RELEASED = re.compile(r"^\s*released on:\s*((?:19|20)\d{2})-", re.IGNORECASE | re.MULTILINE)
NO_LABEL = "[no label]"  # MusicBrainz's own name for a self-release

MUSICBRAINZ = "https://musicbrainz.org/ws/2"
RELEASES = 5  # earliest official releases whose labels are read
STALE = 7 * 86400  # s: "MusicBrainz doesn't know it" is asked again after this
_pace = threading.Lock()


@dataclass
class Facts:
    song: str
    artist: str
    year: str | None = None
    year_from: str = ""  # who said the year: "notes", "the post", "MusicBrainz"...
    label: str = ""
    group: str = ""  # one of GROUPS, "" when no label was found
    label_from: str = ""  # "Shazam", "YouTube", "the channel", "MusicBrainz"

    def as_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        """In a few words for the job's log: "Flowers by Miley Cyrus, 2023, Columbia (Sony)"."""
        bits = [f"{self.song} by {self.artist or '?'}", self.year or "year unknown"]
        if self.label:
            bits.append(self.label + (f" ({self.group})" if self.group != "other" else ""))
        return ", ".join(bits)


def normal(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("&", " and "))
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", bare).split())


def group_of(label: str) -> str:
    """The group a label belongs to: "UMG", "Sony", "Warner", "other" for any other label,
    "" for no label. A licence names who has the recording ("XO, under licence to Republic")."""
    if not label.strip() or label.strip().casefold() == NO_LABEL:
        return ""
    licensed = LICENSED.search(label)
    if licensed and (holder := known_group(licensed.group(1))) in NOTIFIED:
        return holder
    return known_group(label) or "other"


def known_group(label: str) -> str:
    """The group of a label this module knows by name, "" for one it doesn't."""
    name, found = normal(label), []
    for pattern, group in IMPRINTS:
        if match := pattern.search(name):
            found.append((match.start(), -len(match.group(0)), group))
    return min(found)[2] if found else ""


def from_description(text: str) -> tuple[list[str], str | None]:
    """The labels a YouTube description names, the recording's holder first ("Provided to
    YouTube by ...", "℗ 2022 ..."), and the year its art track says it was released."""
    labels = [m.group(1) for m in PROVIDED.finditer(text or "")]
    phonograms = list(PHONOGRAM.finditer(text or ""))
    labels = [m.group(2) for m in phonograms] + labels
    year = RELEASED.search(text or "")
    if year is None:
        year = next((m for m in phonograms if m.group(1)), None)
    return [x for x in labels if x.strip()], year.group(1) if year else None


def from_uploader(uploader: str) -> str:
    """The uploader when it is a label's own channel ("Sony Music India", "T-Series"), else ""."""
    name = normal(uploader or "")
    return uploader.strip() if name and known_group(name) and LABELISH.search(name) else ""


def _ask(path: str, params: dict, transport=None) -> dict | None:
    """MusicBrainz's answer, None when it can't be asked. One request a second for the
    whole app (shazam.py asks it too); a refusal for asking too fast is asked again once."""
    agent = {"User-Agent": shazam.AGENT}
    with httpx.Client(transport=transport, timeout=10, headers=agent) as http:
        for attempt in range(2):
            with _pace:
                time.sleep(max(0.0, shazam._asked[0] + 1.1 - time.monotonic()))
                shazam._asked[0] = time.monotonic()
                try:
                    reply = http.get(f"{MUSICBRAINZ}/{path}", params={**params, "fmt": "json"})
                except httpx.HTTPError as exc:
                    log.warning("MusicBrainz couldn't be asked (%s): %s", path, exc)
                    return None
            if reply.status_code == 503 and attempt == 0:
                time.sleep(2.0)
                continue
            try:
                reply.raise_for_status()
                return reply.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("MusicBrainz answered %s badly: %s", path, exc)
                return None
    return None


def musicbrainz(song: str, artist: str, transport=None) -> dict | None:
    """{"year", "labels"}: the year the song first came out (as shazam.first_year) and the
    labels of its earliest official releases, earliest first; None when it can't be asked."""
    query = f"recording:{shazam._quoted(song)}" + (
        f" AND artist:{shazam._quoted(artist)}" if artist.strip() else ""
    )
    found = _ask("recording", {"query": query, "limit": 50}, transport)
    if found is None:
        return None
    wanted = shazam.same(shazam._without_notes(song))
    recordings = [
        r
        for r in found.get("recordings", [])
        if shazam.same(shazam._without_notes(r.get("title", ""))) == wanted
    ]
    years = [
        int(date[:4])
        for r in recordings
        if (date := r.get("first-release-date") or "")[:4].isdigit()
    ]
    releases = {}
    for recording in recordings:
        for release in recording.get("releases", []):
            group = release.get("release-group") or {}
            if (release.get("status") or "Official") != "Official":
                continue  # a promo or a bootleg
            if "Compilation" in (group.get("secondary-types") or []):
                continue  # a hits album, often another label's
            releases.setdefault(release["id"], release.get("date") or "9999")
    earliest = sorted(releases, key=lambda i: releases[i])[:RELEASES]
    labels: list[str] = []
    if earliest:
        query = " OR ".join(f"reid:{i}" for i in earliest)
        answer = _ask("release", {"query": query, "limit": RELEASES}, transport) or {}
        dated = sorted(answer.get("releases", []), key=lambda r: releases.get(r.get("id"), "9999"))
        for release in dated:
            for info in release.get("label-info") or []:
                name = ((info or {}).get("label") or {}).get("name", "")
                if name and name.casefold() != NO_LABEL and name not in labels:
                    labels.append(name)
    return {"year": str(min(years)) if years else None, "labels": labels}


def remembered(
    song: str, artist: str, cache_dir: Path | None, transport=None, ask: bool = True
) -> dict | None:
    """musicbrainz(), kept in the cache folder (an answer that found nothing for a week);
    without `ask`, only what is kept."""
    key = hashlib.sha1(f"{shazam.same(song)}|{shazam.same(artist)}".encode()).hexdigest()[:20]
    path = cache_dir / "songfacts" / f"{key}.json" if cache_dir else None
    if path is not None and path.exists():
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
            if kept.get("year") or kept.get("labels") or time.time() - kept["asked"] < STALE:
                return kept
        except (ValueError, KeyError, OSError):
            pass
    if not ask:
        return None
    found = musicbrainz(song, artist, transport)
    if found is not None and path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**found, "asked": time.time()}), encoding="utf-8")
    return found


def pick_label(labels: list[str]) -> tuple[str, str]:
    """(label, group) of a release's labels: a major's when any is one (the most named of
    them, the earliest on a tie), else the first."""
    groups = [(label, group_of(label)) for label in labels]
    majors = [(label, group) for label, group in groups if group in NOTIFIED]
    if majors:
        counts = {g: sum(1 for _, x in majors if x == g) for _, g in majors}
        best = max(counts.values())
        return next((label, g) for label, g in majors if counts[g] == best)
    return next(((label, group) for label, group in groups if group), ("", ""))


def look_up(
    song: str,
    artist: str,
    *,
    description: str = "",
    uploader: str = "",
    heard: dict | None = None,
    year: str | None = None,
    year_from: str = "",
    cache_dir: Path | None = None,
    transport=None,
    ask: bool = True,
) -> Facts:
    """What is known of the song: `heard` is Shazam's match (shazam.details), `year` one a
    person or the post gave. MusicBrainz is asked only for what the video doesn't say (and
    not at all without `ask`: then only what it said before, if it is kept)."""
    facts = Facts(song.strip(), artist.strip(), year, year_from if year else "")
    heard = heard or {}
    said, said_year = from_description(description)
    for labels, by in (([heard.get("label", "")], "Shazam"), (said, "YouTube"),
                       ([from_uploader(uploader)], "the channel")):  # fmt: skip
        label, group = pick_label([x for x in labels if x])
        if label:
            facts.label, facts.group, facts.label_from = label.strip(), group, by
            break
    # Without the artist a title is anyone's song: no label is better than another's.
    if (not facts.label or not facts.year) and facts.song and facts.artist:
        found = remembered(facts.song, facts.artist, cache_dir, transport, ask) or {}
        if not facts.year and found.get("year"):
            facts.year, facts.year_from = found["year"], "MusicBrainz"
        label, group = pick_label(found.get("labels", []))
        if not facts.label and label:
            facts.label, facts.group, facts.label_from = label, group, "MusicBrainz"
    released = str(heard.get("released") or "")[:4]
    if not facts.year and released.isdigit():
        facts.year, facts.year_from = released, "Shazam"
    if not facts.year and said_year:
        facts.year, facts.year_from = said_year, "YouTube"
    return facts


def is_new(year: str | None, this_year: int) -> bool:
    return bool(year and year.isdigit() and int(year) >= this_year - NEW_YEARS)
