"""The song a Song story reel is made to: found by its name, downloaded, its lyrics, the
part people know (the hook) and when every word of it is sung.

1. The lyrics with a time on every line come from LRCLIB (open, free, no key), searched by
   the name typed; they also say how long the song's own recording is.
2. YouTube is searched for the name (yt-dlp) and the song's own upload chosen: the name's
   words in its title, the artist's channel, as long as the lyrics' recording, and never a
   slowed, lo-fi, remix, cover or live version. Its sound is downloaded once and kept.
3. The vocals are separated (the editor's own model) and the lyric lines moved onto this
   upload's audio: each line starts where singing starts after a pause.
4. The hook: runs of whole lines 24-40 s long, scored by YouTube's "most replayed" graph
   (where people go back to; the reels' favourite part of a song shows there), whole
   sections, lines that don't repeat inside it, a length near 30 s, and any of its words
   the person typed. Without the graph, the louder sections and the chorus count instead.
   Two of the parts offered can be joined into one reel (`join`): a stretch of each, about
   30 s in all, the sound cut where a line ends, on a beat.
5. Every word's time: whisper hears the vocals of the part, and the lyric words are laid
   over the heard words by how long each takes to sing. A song LRCLIB doesn't know is
   heard whole, and what whisper heard becomes its lyrics.
"""

import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import httpx
import numpy as np

from agentic_editor import ffmpeg
from agentic_editor.animate.plan import HookChoice, Song, SongLine, SongPiece, SongWord
from agentic_editor.settings import Settings
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "agentic-editor (song story reels)"
RESULTS = 8  # YouTube results looked at
# The part a reel plays: whole lines, about this long.
SHORTEST = 24.0
LONGEST = 40.0
AIM = 30.0
MOST_LINES = 16  # lyric lines in a part (each is a scene or two), when the song allows
# A part with longer music than this between two of its lines (an instrumental build, a
# solo) shows a lyric over no singing: it is offered only when the song has nothing else.
MOST_MUSIC = 6.0
# A reel joined from two parts: a stretch of each about this long.
PIECE_AIM = AIM / 2
PIECE_SHORTEST = 8.0
PIECE_LONGEST = 24.0
LEAD_IN = 0.3  # the part starts this much before its first line is sung
TAIL = 1.2  # and runs on this long after its last word, if nothing is sung there
PROMPT_CHARS = 600  # of the part's lyrics whisper is told to expect (its prompt is short)
CHOICES = 4  # parts offered to the person
# A line sung over longer than this, or with more words, is shown as two.
LONG_LINE = 4.4
LONG_WORDS = 9
RATE = 16000  # vocals are looked at as mono at this rate
FRAME = 0.02  # s, the vocals' loudness is measured in frames this long

# Versions of a song that aren't the song: never chosen when the song's own is there.
OTHER_TAKE = re.compile(
    r"\b(slowed|reverb|lo-?fi|8d|remix|cover|karaoke|instrumental|live|status|shorts?|"
    r"nightcore|sped ?up|bass ?boosted|ringtone|reaction|mashup|unplugged|female version|"
    r"male version|whatsapp|lyrics? video edit|dj)\b",
    re.I,
)
OWN_UPLOAD = re.compile(r"\b(official|audio|full song|lyrical|lyrics|video song)\b", re.I)


class SongError(RuntimeError):
    pass


@dataclass
class Candidate:
    """A YouTube result for the song's name."""

    id: str
    title: str
    channel: str = ""
    duration: float = 0.0
    views: int = 0

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class Lyrics:
    title: str
    artist: str
    duration: float
    lines: list[tuple[float, str]]  # (when it starts in the recording, the line)


@dataclass
class Asked:
    """What the person typed: the song's name, and maybe some words of the part they want."""

    name: str
    part: str = ""


@dataclass
class Window:
    first: int  # index of the part's first line
    last: int  # and its last
    start: float
    end: float
    score: float = 0.0
    why: dict = field(default_factory=dict)
    music: float = 0.0  # the longest music between two of its lines (s), past their singing


# ---- what was asked --------------------------------------------------------------------


def asked(idea: str) -> Asked:
    """The song's name is the first line of what was typed; the lines after it (or what
    follows a "|") are words from the part of the song wanted."""
    text = (idea or "").strip()
    if "\n" in text:
        name, part = text.split("\n", 1)
    elif "|" in text:
        name, part = text.split("|", 1)
    else:
        name, part = text, ""
    return Asked(" ".join(name.split()), " ".join(part.split()))


# ---- words -----------------------------------------------------------------------------


def plain(text: str) -> str:
    """Lower case, letters and digits only, accents off: for comparing words and names."""
    flat = unicodedata.normalize("NFKD", text or "")
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", flat.lower()).strip()


def _tokens(text: str) -> list[str]:
    return [w for w in plain(text).split() if w]


DEVANAGARI_LETTER = re.compile(r"[ऄ-हक़-ॡॲ-ॿ]")
VIRAMA = "्"


def syllables(word: str) -> int:
    """About how many syllables singing `word` takes: vowel groups in Latin letters, letters
    not joined by a virama in Devanagari, half the letters in anything else."""
    if DEVANAGARI_LETTER.search(word):
        count = 0
        for i, ch in enumerate(word):
            if DEVANAGARI_LETTER.match(ch) and word[i + 1 : i + 2] != VIRAMA:
                count += 1
        return max(1, count)
    latin = plain(word)
    if re.search(r"[a-z]", latin):
        return max(1, len(re.findall(r"[aeiouy]+", latin)))
    return max(1, len(latin) // 2)


# ---- lyrics (LRCLIB) -------------------------------------------------------------------

LRC_LINE = re.compile(r"^\s*((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+)(.*)$")
LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Synced lyrics as (start, line), in time order; empty lines and tags left out. A line
    with several times (a chorus written once) comes at each of them."""
    out: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        found = LRC_LINE.match(raw)
        if not found:
            continue
        words = " ".join(found.group(2).split())
        if not words or not re.search(r"\w", words):
            continue
        for m, s, frac in LRC_TIME.findall(found.group(1)):
            seconds = int(m) * 60 + int(s) + (int(frac) / 10 ** len(frac) if frac else 0.0)
            out.append((round(seconds, 3), words))
    return sorted(out)


def lrclib(name: str, client: httpx.Client | None = None) -> list[dict]:
    """LRCLIB's records for a song's name (track and artist in any order)."""
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
    try:
        reply = client.get(f"{LRCLIB}/search", params={"q": name})
        reply.raise_for_status()
        found = reply.json()
        return found if isinstance(found, list) else []
    finally:
        if own:
            client.close()


def best_lyrics(records: list[dict], name: str) -> Lyrics | None:
    """The record whose track and artist best match the name, with synced lyrics."""
    wanted = set(_tokens(name))
    best: tuple[float, dict] | None = None
    for r in records:
        synced = parse_lrc(r.get("syncedLyrics") or "")
        if len(synced) < 4 or r.get("instrumental"):
            continue
        said = set(_tokens(f"{r.get('trackName', '')} {r.get('artistName', '')}"))
        track = set(_tokens(r.get("trackName", "")))
        if not track or not track <= wanted | said:
            continue
        overlap = len(wanted & said) / max(1, len(wanted))
        named_track = len(track & wanted) / max(1, len(track))
        score = overlap + named_track
        if best is None or score > best[0] + 1e-9:
            best = (score, r)
    if best is None or best[0] < 1.0:
        return None
    r = best[1]
    return Lyrics(
        title=str(r.get("trackName", "")),
        artist=str(r.get("artistName", "")),
        duration=float(r.get("duration") or 0.0),
        lines=parse_lrc(r.get("syncedLyrics") or ""),
    )


# ---- the song's name -------------------------------------------------------------------

_DASH = re.compile(r"\s+[-\u2013\u2014|]\s+")
# What an upload's title carries besides the song's name: "(Official Music Video)", "[HD]".
_UPLOAD_EXTRAS = re.compile(
    r"[(\[][^)\]]*[)\]]|\b(official|music|lyric|lyrics|video|audio|visualizer|hd|4k|hq|"
    r"remaster(ed)?|full song)\b",
    re.I,
)
_CHANNEL_EXTRAS = re.compile(r"\s*-\s*topic$|vevo$|\bofficial\b", re.I)


def _tidy_name(text: str) -> str:
    text = " ".join((text or "").split()).strip(" -|\u2013\u2014")
    return text.title() if text and text == text.lower() else text


def names(title: str, artist: str, upload: str = "", channel: str = "") -> tuple[str, str]:
    """The song's title and singer as a reel and its caption show them, from LRCLIB's record
    (which may be "Artist - Title", or both fields the whole name), else the upload's title
    ("Artist - Title (Official Video)") and channel ("Artist - Topic", "ArtistVEVO")."""
    title, artist = " ".join((title or "").split()), " ".join((artist or "").split())
    if title and artist:
        parts = _DASH.split(title)
        if len(parts) == 2 and plain(parts[0]) == plain(artist):
            title = parts[1]  # "Adele - Someone Like You" by Adele
        elif len(parts) == 2 and plain(parts[1]) == plain(artist):
            title = parts[0]
    if title and artist and plain(title) != plain(artist):
        return _tidy_name(title), _tidy_name(artist)
    # LRCLIB's names look wrong (none, or the title and singer both the whole name): the
    # upload's own.
    who = _tidy_name(_CHANNEL_EXTRAS.sub("", channel or ""))
    named = " ".join(_UPLOAD_EXTRAS.sub(" ", upload or "").split())
    parts = [x for x in _DASH.split(named) if x.strip()]
    if len(parts) >= 2:
        first, second = parts[0], " - ".join(parts[1:])
        if who and plain(second) == plain(who):
            first, second = second, first
        return _tidy_name(second), _tidy_name(first)
    if named:
        return _tidy_name(named), who or _tidy_name(artist)
    return _tidy_name(title), _tidy_name(artist or who)


# ---- YouTube ---------------------------------------------------------------------------


def search(query: str, count: int = RESULTS) -> list[Candidate]:
    from yt_dlp import YoutubeDL

    from agentic_editor.download import signed_in

    options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with signed_in(options) as asked_with, YoutubeDL(asked_with) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False) or {}
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append(
            Candidate(
                id=str(e["id"]),
                title=str(e.get("title") or ""),
                channel=str(e.get("channel") or e.get("uploader") or ""),
                duration=float(e.get("duration") or 0.0),
                views=int(e.get("view_count") or 0),
            )
        )
    return out


def pick(found: list[Candidate], name: str, artist: str = "", length: float = 0.0) -> Candidate:
    """The song's own upload among the results: its name's words in the title, the artist's
    channel, no other take of it, as long as the lyrics' recording, then the most watched."""
    wanted = set(_tokens(name))
    artist_words = set(_tokens(artist))

    def score(c: Candidate) -> float:
        said = set(_tokens(f"{c.title} {c.channel}"))
        value = 3.0 * len(wanted & said) / max(1, len(wanted))
        if OTHER_TAKE.search(c.title) and not OTHER_TAKE.search(name):
            value -= 6.0
        if artist_words and artist_words & set(_tokens(c.channel)):
            value += 1.0
        if c.channel.endswith(" - Topic"):  # YouTube's own copy of the record
            value += 0.8
        if OWN_UPLOAD.search(c.title):
            value += 0.3
        if not 60 <= c.duration <= 900:
            value -= 4.0
        if length and c.duration:
            value -= min(3.0, abs(c.duration - length) / 4.0)
        return value + 0.15 * math.log10(max(10, c.views))

    if not found:
        raise SongError(f"YouTube found nothing for {name!r}")
    return max(found, key=score)


def song_folder(settings: Settings, video: str) -> Path:
    return settings.cache_dir / "songs" / re.sub(r"[^\w-]", "_", video)


def audio_path(settings: Settings, song: Song) -> Path:
    return song_folder(settings, song.video) / "song.wav"


def download(candidate: Candidate, settings: Settings) -> tuple[Path, dict]:
    """The upload's sound as a WAV, and what YouTube says of it (title, channel, length,
    its most-replayed graph); both kept, so a song is downloaded once."""
    from agentic_editor import slots
    from agentic_editor.download import _extract

    folder = song_folder(settings, candidate.id)
    folder.mkdir(parents=True, exist_ok=True)
    wav, meta_file = folder / "song.wav", folder / "song.json"
    with slots.video(folder).turn("download the song"):
        if wav.exists() and meta_file.exists():
            return wav, json.loads(meta_file.read_text(encoding="utf-8"))
        options = {
            "format": "bestaudio/best",
            "outtmpl": str(folder / "download.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        info, _ = _extract(candidate.url, options, download=True)
        got = next((p for p in folder.iterdir() if p.stem == "download"), None)
        if got is None:
            raise SongError("the song's sound didn't download")
        ffmpeg.extract_audio(got, wav.with_suffix(".part.wav"))
        wav.with_suffix(".part.wav").replace(wav)
        got.unlink(missing_ok=True)
        meta = {
            "id": candidate.id,
            "title": info.get("title") or candidate.title,
            "channel": info.get("channel") or info.get("uploader") or candidate.channel,
            "duration": float(info.get("duration") or candidate.duration or 0.0),
            "heatmap": [
                [float(h["start_time"]), float(h["end_time"]), float(h["value"])]
                for h in info.get("heatmap") or []
                if {"start_time", "end_time", "value"} <= set(h)
            ],
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return wav, meta


# ---- the vocals ------------------------------------------------------------------------


def loudness(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """The vocals' loudness in dB, a value every FRAME seconds."""
    hop = max(1, int(rate * FRAME))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(0)
    frames = samples[: n * hop].reshape(n, hop).astype(np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def onsets(level: np.ndarray, quiet: float = 0.18) -> list[tuple[float, float]]:
    """Where singing starts after a pause, and how long the pause was (capped at 1 s):
    frames above the vocals' singing level after at least `quiet` seconds below it."""
    if level.size == 0:
        return []
    loud = float(np.percentile(level, 90))
    threshold = max(loud - 24.0, -55.0)
    out: list[tuple[float, float]] = []
    below = 0
    for i, value in enumerate(level):
        if value < threshold:
            below += 1
            continue
        pause = below * FRAME
        if pause >= quiet or (i == below and i > 0):
            out.append((round(i * FRAME, 3), min(1.0, pause)))
        below = 0
    return out


def shift(starts: list[float], found: list[tuple[float, float]], reach: float) -> float:
    """How far the lyric lines' times must move to sit on this upload's singing: the move
    (within `reach` s) that puts the most line starts on a start of singing after a pause."""
    if not starts or not found:
        return 0.0
    at = np.array([t for t, _ in found])
    weight = np.array([0.4 + w for _, w in found])
    lines = np.array(starts)
    best, best_score = 0.0, -1.0
    for move in np.arange(-reach, reach + 1e-9, 0.02):
        near = np.abs(at[None, :] - (lines[:, None] + move))
        fit = np.max(np.exp(-((near / 0.22) ** 2)) * weight[None, :], axis=1)
        score = float(np.sum(fit)) - 0.04 * abs(move)
        if score > best_score + 1e-9:
            best, best_score = float(move), score
    return round(best, 3)


# ---- the hook --------------------------------------------------------------------------


def heat_curve(heatmap: list, duration: float, step: float = 0.5) -> np.ndarray | None:
    """YouTube's most-replayed graph as how far each moment stands above the rest (in
    spreads): half how high it is over the whole song, half how high over the 30 s around
    it (people leave as a video goes on, so later parts read lower than they are). The
    always-high opening and the fall at the end are left out. None without a graph."""
    if not heatmap or duration <= 0:
        return None
    t = np.arange(0, duration, step)
    values = np.zeros_like(t)
    for start, end, value in heatmap:
        values[(t >= start) & (t < end)] = value
    inner = (t > duration * 0.06) & (t < duration * 0.94)
    if inner.sum() < 8:
        return None

    def spread(x: np.ndarray) -> np.ndarray:
        x = x - float(np.mean(x[inner]))
        return x / (float(np.std(x[inner])) or 1.0)

    half = int(15 / step)
    padded = np.pad(values, half, mode="edge")
    around = np.array([np.median(padded[i : i + 2 * half + 1]) for i in range(len(values))])
    rest = 0.5 * spread(values) + 0.5 * spread(values - around)
    rest[~inner] = 0.0
    return rest


def energy_curve(level: np.ndarray, duration: float, step: float = 0.5) -> np.ndarray:
    """The song's loudness every `step` s, as how far above its own average (in spreads)."""
    per = max(1, int(step / FRAME))
    n = int(duration / step)
    values = np.array(
        [float(np.mean(level[i * per : (i + 1) * per])) if i * per < len(level) else -90.0
         for i in range(n)]
    )  # fmt: skip
    sung = values > -80
    if sung.sum() < 4:
        return np.zeros(n)
    spread = float(np.std(values[sung])) or 1.0
    return np.where(sung, (values - float(np.mean(values[sung]))) / spread, 0.0)


def _mean(curve: np.ndarray | None, start: float, end: float, step: float = 0.5) -> float:
    if curve is None or curve.size == 0:
        return 0.0
    a, b = int(start / step), max(int(start / step) + 1, int(end / step))
    part = curve[a:b]
    return float(np.mean(part)) if part.size else 0.0


# How much each of a window's features counts (see `windows`).
WEIGHTS = {
    "heat": 1.5, "opening": 1.0, "energy": 0.8, "section": 1.0, "closes": 0.6, "fresh": 0.8,
    "early": 0.6, "length": 1.0, "chorus": 0.5, "title": 1.0, "part": 3.0, "music": 1.0,
}  # fmt: skip


def windows(
    lines: list[tuple[float, str]],
    duration: float,
    heat: np.ndarray | None,
    energy: np.ndarray | None = None,
    part: str = "",
    title: str = "",
    bounds: tuple[float, float, float] = (SHORTEST, LONGEST, AIM),
    within: tuple[int, int] | None = None,
) -> list[Window]:
    """Every run of whole lines the reel could play, best first (see the module's notes):
    `bounds` its shortest, longest and best length, `within` the first and last line it
    may take (all of them when None)."""
    if not lines:
        return []
    shortest, longest, aim = bounds
    lo, hi = within if within is not None else (0, len(lines) - 1)
    texts = [plain(text) for _, text in lines]
    # "Ye jaan kar bhi" and "Yeh jaan kar bhi" are the same line written twice.
    chorus = [
        sum(1 for b in texts if SequenceMatcher(None, a, b).ratio() >= 0.85) >= 2 for a in texts
    ]
    title_words = set(_tokens(title))
    ends = [lines[i + 1][0] if i + 1 < len(lines) else min(duration, lines[i][0] + 6.0)
            for i in range(len(lines))]  # fmt: skip
    # How long each line can take to sing, and the music after it until the next line (an
    # instrumental is no part of a line: a part ends soon after its last line is sung).
    singing = [min(6.0, longest_singing(text.split()) + TAIL) for _, text in lines]
    music = [max(0.0, ends[k] - lines[k][0] - singing[k]) for k in range(len(lines))]
    # A line sung long ago and a line now aren't one section, nor a chorus and a verse.
    starts_section = [
        i == 0 or lines[i][0] - lines[i - 1][0] > 7.0 or chorus[i] != chorus[i - 1]
        for i in range(len(lines))
    ]
    wanted = _tokens(part)
    found: list[Window] = []
    for i in range(lo, hi + 1):
        for j in range(i, hi + 1):
            start = lines[i][0]
            end = min(ends[j], lines[j][0] + singing[j])
            length = end - start
            if length > longest:
                break
            if length < shortest:
                continue
            inside = texts[i : j + 1]
            fresh = sum(
                1 for k, a in enumerate(inside)
                if all(SequenceMatcher(None, a, b).ratio() < 0.85 for b in inside[:k])
            )  # fmt: skip
            why = {
                "heat": _mean(heat, start, end),
                # People go back to where the part begins: its first seconds count again.
                "opening": _mean(heat, max(0.0, start - 1.5), start + 5.0),
                "energy": _mean(energy, start, end) if heat is None else 0.0,
                "section": 1.0 if starts_section[i] else 0.0,
                "closes": 1.0 if j + 1 >= len(lines) or starts_section[j + 1] else 0.0,
                "fresh": fresh / len(inside),
                # Long music between its lines: a lyric line on screen over an instrumental.
                "music": -sum(music[i:j]) / 5.0,
                # The first time a part comes is the one people know it by.
                "early": 1.0 - start / max(duration, 1.0),
                "length": -abs(length - aim) / 10.0,
                "chorus": (sum(chorus[i : j + 1]) / len(inside)) if heat is None else 0.0,
                "title": 0.3 if title_words and title_words <= set(" ".join(inside).split())
                else 0.0,
            }  # fmt: skip
            if wanted:
                # The part named starts here (or a line in): above everything else.
                found_at = [_match(wanted, texts[k]) * (1.0 if k == i else 0.8)
                            for k in range(i, min(j + 1, i + 2))]  # fmt: skip
                best_match = max(found_at)
                why["part"] = 4.0 * best_match if best_match >= 0.5 else 0.0
            score = sum(WEIGHTS[k] * v for k, v in why.items())
            found.append(
                Window(i, j, start, end, round(score, 3), why, max(music[i:j], default=0.0))
            )
    found.sort(key=lambda w: -w.score)
    # A part of more lines than a reel has scenes for, or with long music between its lines,
    # only when the song has nothing else.
    roomy = [w for w in found if w.last - w.first + 1 <= MOST_LINES]
    calm = [w for w in roomy if w.music <= MOST_MUSIC]
    return calm or roomy or found


def _match(wanted: list[str], line: str) -> float:
    """How much of the words typed are in a line (0-1), loosely: "sapna" finds "sapne"."""
    have = [h for h in line.split() if len(h) > 2]
    wanted = [w for w in wanted if len(w) > 2] or wanted
    if not wanted or not have:
        return 0.0
    hits = sum(1 for w in wanted if any(SequenceMatcher(None, w, h).ratio() >= 0.75 for h in have))
    return hits / len(wanted)


def _overlap(a: Window, b: Window) -> float:
    return min(a.end, b.end) - max(a.start, b.start)


def distinct(found: list[Window], count: int = CHOICES) -> list[Window]:
    """The best windows that don't mostly overlap one another."""
    out: list[Window] = []
    for w in found:
        if all(_overlap(w, o) < 0.4 * min(w.end - w.start, o.end - o.start) for o in out):
            out.append(w)
        if len(out) >= count:
            break
    return out


# ---- every word's time -----------------------------------------------------------------


# What singing a line can take: whisper "hears" words in a long instrumental after a line
# too (a harmonica solo), and a line laid over those stretched its last words across it.
RUN_GAP = 1.3  # s: heard words further apart than this aren't one sung line
SYLLABLE_MOST = 0.6  # s a syllable takes at most, sung slowly
SLACK = 1.0  # s a line may take beyond that (a held last note, a breath)
LATE = 1.5  # s: the words heard for a line start no later than this after it


def longest_singing(said: list[str]) -> float:
    """The most a line of these words takes to sing (seconds)."""
    return SYLLABLE_MOST * sum(syllables(w) for w in said) + SLACK


def heard_run(said: list[str], start: float, end: float, heard: list[Word]) -> list[Word]:
    """The words whisper heard singing the line written at `start` (before `end`): the run
    of them from near its start, until a pause longer than RUN_GAP or longer than the line's
    words could take to sing; empty when nothing heard starts near it."""
    inside = sorted(
        (w for w in heard if start - 0.3 <= (w.start + w.end) / 2 < end - 0.05),
        key=lambda w: w.start,
    )
    if not inside or inside[0].start > start + LATE:
        return []
    most = longest_singing(said)
    run = [inside[0]]
    for w in inside[1:]:
        if w.start - run[-1].end > RUN_GAP or w.end - run[0].start > most:
            break
        run.append(w)
    return run


def lay_words(
    said: list[str], start: float, end: float, heard: list[Word]
) -> list[tuple[str, float, float]]:
    """`said` (a line's words) timed inside [start, end): over the words whisper heard
    singing it (heard_run), by how long each takes to sing; spread over the time singing
    them takes when what was heard doesn't make a line."""
    if not said:
        return []
    weights = [syllables(w) for w in said]
    total = float(sum(weights))
    inside = heard_run(said, start, end, heard)
    if len(inside) >= 2:
        h_weights = [syllables(w.text) for w in inside]
        h_total = float(sum(h_weights))
        marks = [0.0]
        for wt in h_weights:
            marks.append(marks[-1] + wt / h_total)
        times = [w.start for w in inside] + [inside[-1].end]
        first = min(max(inside[0].start, start - 0.2), start + 0.6)
        times[0] = first

        def at(fraction: float) -> float:
            return float(np.interp(fraction, marks, times))
    else:
        sung = min(end - start, 0.32 * total + 0.4)

        def at(fraction: float) -> float:
            return start + fraction * sung

    out = []
    done = 0.0
    for word, wt in zip(said, weights, strict=True):
        a = at(done / total)
        done += wt
        b = at(done / total)
        out.append((word, round(a, 3), round(max(b, a + 0.08), 3)))
    return out


def too_long(line: SongLine) -> bool:
    words = line.words
    length = (words[-1].end - words[0].start) if words else 0.0
    return len(words) >= 6 and (length > LONG_LINE or len(words) > LONG_WORDS)


def split_long(line: SongLine, at: int | None = None) -> list[SongLine]:
    """A line too long to read at once as two: split before word `at` (where its meaning
    breaks, as the writer said), else between the words with the longest gap near its
    middle."""
    words = line.words
    if not too_long(line):
        return [line]
    if at is not None and 2 <= at <= len(words) - 2:
        best = at
    else:
        middle = len(words) / 2
        best = max(
            range(2, len(words) - 1),
            key=lambda k: (words[k].start - words[k - 1].end) - 0.12 * abs(k - middle),
        )
    said = line.said.split()
    cut = best if len(said) == len(words) else round(best * len(said) / len(words))
    parts = []
    for chunk, s_chunk in ((words[:best], said[:cut]), (words[best:], said[cut:])):
        parts.append(
            line.model_copy(
                update={
                    "text": " ".join(w.text for w in chunk),
                    "said": " ".join(s_chunk),
                    "start": chunk[0].start,
                    "end": chunk[-1].end,
                    "words": list(chunk),
                }
            )
        )
    parts[0] = parts[0].model_copy(update={"end": parts[1].start})
    parts[1] = parts[1].model_copy(update={"end": line.end})
    return parts


def _sung(level: np.ndarray) -> np.ndarray:
    """Which vocal frames are sung: above the vocals' singing level (as `onsets`), blips
    and breaths between syllables shorter than 0.06 s ignored."""
    if level.size == 0:
        return np.zeros(0, dtype=bool)
    loud = level >= max(float(np.percentile(level, 90)) - 24.0, -55.0)
    short = max(1, int(0.06 / FRAME))
    out = loud.copy()
    k = 0
    while k < len(out):  # runs shorter than `short` take the value around them
        j = k
        while j < len(out) and loud[j] == loud[k]:
            j += 1
        if j - k < short and k > 0 and j < len(out):
            out[k:j] = loud[k - 1]
        k = j
    return out


def sung_from(sung: np.ndarray, start: float, end: float) -> float | None:
    """The first time singing is heard in the vocals between start and end, if it is."""
    a, b = max(0, int(start / FRAME)), min(len(sung), int(end / FRAME))
    found = np.flatnonzero(sung[a:b]) if b > a else np.zeros(0)
    return round((a + int(found[0])) * FRAME, 3) if found.size else None


def timed_lines(
    lines: list[tuple[float, str]], first: int, last: int, part_end: float, heard: list[Word],
    sung: np.ndarray | None = None,
) -> list[SongLine]:  # fmt: skip
    """The lines first-last with their words timed. A line written well before it is sung
    (the vocals quiet for a while after its time: music before it) starts where the
    singing does."""
    out: list[SongLine] = []
    for k in range(first, last + 1):
        start, text = lines[k]
        end = lines[k + 1][0] if k + 1 < len(lines) and k < last else part_end
        if sung is not None and sung.size:
            begins = sung_from(sung, start - 0.3, end)
            if begins is not None and begins > start + 0.8:
                start = begins - 0.05
        laid = lay_words(text.split(), start, end, heard)
        words = [SongWord(text=w, start=a, end=min(b, end)) for w, a, b in laid]
        out.append(SongLine(text=text, said=text, start=start, end=end, words=words))
    return out


def heard_lines(heard: list[Word], gap: float = 0.6) -> list[tuple[float, str]]:
    """Lines from what whisper heard, for a song without lyrics: a new line after a pause,
    or after LONG_WORDS words."""
    lines: list[list[Word]] = []
    for w in heard:
        if lines and w.start - lines[-1][-1].end < gap and len(lines[-1]) < LONG_WORDS:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [(ws[0].start, " ".join(w.text for w in ws)) for ws in lines if ws]


# ---- all of it -------------------------------------------------------------------------


def _cut(src: Path, start: float, end: float, dest: Path) -> Path:
    ffmpeg.run([
        "-y", "-ss", f"{max(0.0, start):.3f}", "-to", f"{end:.3f}", "-i", str(src),
        "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(dest),
    ])  # fmt: skip
    return dest


def separated(audio: Path, router) -> Path:
    """The song's vocals alone (kept beside the song). Two reels of one song at once: the
    second waits for the first one's vocals instead of separating them again."""
    from agentic_editor import slots

    folder = audio.parent
    vocals = folder / "vocals.wav"
    with slots.video(folder).turn("separate the song's vocals"):
        if not vocals.exists():
            stems_dir = folder / "stems"
            stems_dir.mkdir(exist_ok=True)
            stems = router.separate_vocals(audio, stems_dir)
            Path(stems.vocals).replace(vocals)
            Path(stems.instrumental).unlink(missing_ok=True)
    return vocals


def hear(
    vocals: Path, start: float, end: float, router, language: str | None, lyrics: str = ""
) -> tuple[list[Word], str]:
    """The words whisper hears in the vocals between start and end (song times), kept. One
    reel of the song hears at a time, each part from its own clip. `lyrics` (the part's
    lines) are what whisper is told to expect: over loud guitars it heard 3 of 40 words
    without them."""
    from agentic_editor import slots

    told = hashlib.sha1(lyrics.encode()).hexdigest()[:8] if lyrics else "none"
    key = f"heard-{start:.2f}-{end:.2f}-{language or 'auto'}-{told}"
    kept = vocals.parent / f"{key}.json"
    with slots.video(vocals.parent).turn("hear the song's words"):
        if kept.exists():
            data = json.loads(kept.read_text(encoding="utf-8"))
            return [Word(**w) for w in data["words"]], data.get("language") or ""
        clip = _cut(vocals, start, end, vocals.parent / f"{key}.wav")
        heard = router.transcribe(clip, language, lyrics[:PROMPT_CHARS] or None)
        words = [
            Word(text=w.text, start=round(w.start + start, 3), end=round(w.end + start, 3))
            for w in heard.words
        ]
        kept.write_text(
            json.dumps({"language": heard.language, "words": [w.model_dump() for w in words]},
                       ensure_ascii=False),
            encoding="utf-8",
        )  # fmt: skip
        clip.unlink(missing_ok=True)
    return words, heard.language or ""


# Whisper writes Hindi sung in a film song as Urdu often enough: the reels show Devanagari.
SCRIPT_OF = {"ur": "hi"}
# Words a language's lyrics are full of and the others' hardly ever use. Whisper left to
# guess may take a sung part for another language and hear next to nothing (mlx-whisper on
# the Mac took Koi Apna Hoga for English and heard one word): the lyrics tell it instead.
_WORDS = {
    "en": """
the and you your i'm i you're don't can't it's that what this with for all but just know
love baby never will when like got gonna wanna feel heart tonight there they how she he
""",
    "es": """
que el la los las y en mi tu te me es por con una un se para como más pero yo amor corazón
quiero eres estoy cuando sin todo nada siempre vida quién porque ya si cómo noche contigo
""",
    "hi": """
hai hain mein mujhe tujhe mera meri mere tera teri tere apna apni apne yeh woh kya kyun nahi
nahin kabhi koi dil pyaar pyar ishq jaan zindagi yaar sanam hoga hogi gaya gayi gaye raha
rahi rahe karo karna lete dete hum tum aap bhi toh tha thi thay kaise jaise saath dard wafa
""",
}
LANGUAGE_WORDS = {code: frozenset(words.split()) for code, words in _WORDS.items()}


def lyrics_language(texts: list[str]) -> str | None:
    """The language lyrics are in: by their letters (Devanagari: Hindi; Arabic script: Urdu),
    else by the everyday words of English, Spanish or Hindi in Latin letters that fill them;
    None when they don't tell (whisper guesses)."""
    joined = " ".join(texts)
    if re.search(r"[\u0900-\u097f]", joined):
        return "hi"
    if re.search(r"[\u0600-\u06ff]", joined):
        return "ur"
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", joined.lower())
    if len(words) < 6:
        return None
    share = {
        code: sum(w in known for w in words) / len(words) for code, known in LANGUAGE_WORDS.items()
    }
    best = max(share, key=share.get)
    return best if share[best] >= 0.15 else None


def find(idea: str, settings: Settings, router, say=lambda message: None, choice: int | None = None,
         lookup=lrclib, searcher=search, fetch=download,
         bounds: tuple[float, float, float] = (SHORTEST, LONGEST, AIM)) -> Song:  # fmt: skip
    """The Song for what was typed (see `asked`): found, downloaded, its part chosen (the
    best, or `choice` among the parts offered; `bounds` its shortest, longest and best
    length) and every word of it timed."""
    want = asked(idea)
    if not want.name:
        raise SongError("type the song's name (and the singer, to be sure of it)")
    say("looking up the lyrics")
    try:
        lyrics = best_lyrics(lookup(want.name), want.name)
    except httpx.HTTPError as exc:
        log.info("LRCLIB failed for %r: %s", want.name, exc)
        lyrics = None
    say("finding the song on YouTube")
    query = f"{lyrics.title} {lyrics.artist}" if lyrics else want.name
    candidate = pick(searcher(query), query, lyrics.artist if lyrics else "",
                     lyrics.duration if lyrics else 0.0)  # fmt: skip
    say(f"downloading {candidate.title}")
    audio, meta = fetch(candidate, settings)
    duration = float(meta.get("duration") or candidate.duration)
    say("separating the vocals")
    vocals = separated(audio, router)
    level = loudness(ffmpeg.read_audio(vocals, RATE))
    duration = duration or len(level) * FRAME
    if lyrics:
        reach = min(12.0, abs(duration - lyrics.duration) + 2.0) if lyrics.duration else 6.0
        move = shift([t for t, _ in lyrics.lines], onsets(level), reach)
        lines = [(round(t + move, 3), text) for t, text in lyrics.lines if 0 <= t + move < duration]
        source = "lrclib"
        log.info("song %s: lyrics moved %+.2f s onto the upload", candidate.id, move)
    else:
        say("listening to the whole song (no lyrics found)")
        whole, language = hear(vocals, 0.0, duration, router, None)
        if language in SCRIPT_OF:
            whole, _ = hear(vocals, 0.0, duration, router, SCRIPT_OF[language])
        lines = heard_lines(whole)
        source = "heard"
    if len(lines) < 3:
        raise SongError("couldn't make out the song's lyrics")
    heat = heat_curve(meta.get("heatmap") or [], duration)
    energy = None
    if heat is None:
        energy = energy_curve(loudness(ffmpeg.read_audio(audio, RATE)), duration)
    title, artist = names(
        lyrics.title if lyrics else "", lyrics.artist if lyrics else "",
        str(meta.get("title") or candidate.title), str(meta.get("channel") or candidate.channel),
    )  # fmt: skip
    best = distinct(windows(lines, duration, heat, energy, want.part, title, bounds))
    if not best:
        raise SongError(
            f"the song has no part of {bounds[0]:.0f}-{bounds[1]:.0f} s made of whole lines"
        )
    (vocals.parent / LINES).write_text(
        json.dumps({"lines": lines, "duration": duration}, ensure_ascii=False), encoding="utf-8"
    )
    index = min(choice or 0, len(best) - 1)
    chosen = best[index]
    say("timing every word")
    song = timed(lines, chosen, duration, vocals, router, bounds[1])
    return song.model_copy(
        update={
            "query": idea.strip(),
            "title": title,
            "artist": artist,
            "video": candidate.id,
            "url": candidate.url,
            "audio": "song.wav",
            "duration": round(duration, 3),
            "lyrics": source,
            "choices": [
                HookChoice(start=w.start, end=w.end, first=lines[w.first][1], score=w.score)
                for w in best
            ],
            "playing": [index],
        }
    )


def timed(
    lines: list[tuple[float, str]], chosen: Window, duration: float, vocals: Path, router,
    longest: float = LONGEST,
) -> Song:  # fmt: skip
    """The part `chosen` with every word timed: its start a breath before its first line is
    sung (never over the music before it), its end a little after its last sung word
    (never over a long stretch of music after it, before the next line), and never longer
    than `longest`: timed longer than its lines' times said, it ends a line or more sooner."""
    start = max(0.0, chosen.start - LEAD_IN)
    after = lines[chosen.last + 1][0] if chosen.last + 1 < len(lines) else duration
    hint = lyrics_language([text for _, text in lines])
    expected = " ".join(text for _, text in lines[chosen.first : chosen.last + 1])
    heard, language = hear(vocals, start, min(duration, after + 0.3), router, hint, expected)
    language = hint or language
    language = SCRIPT_OF.get(language, language)
    sung = _sung(loudness(ffmpeg.read_audio(vocals, RATE)))
    part = timed_lines(lines, chosen.first, chosen.last, after, heard, sung)
    start = max(0.0, part[0].start - LEAD_IN)  # later than written when sung later
    end = _end_of(part, after, duration, chosen.end)
    while len(part) > 1 and end - start > longest:
        after, part = part[-1].start, part[:-1]
        end = _end_of(part, after, duration, chosen.end)
    end = round(end, 3)
    part[-1] = part[-1].model_copy(update={"end": end})
    return Song(start=round(start, 3), end=end, language=language, lines=part)


def _end_of(part: list[SongLine], after: float, duration: float, fallback: float) -> float:
    """Where a part of these lines ends: TAIL after its last word, before the line after it
    (sung at `after`)."""
    last_word = max((w.end for line in part for w in line.words), default=fallback)
    if after - 0.05 > last_word:  # the music after the last word, until the next line
        return min(duration, after - 0.05, last_word + TAIL)
    return min(duration, last_word + 0.4)


LINES = "lines.json"  # a found song's lyric lines on its upload's times, beside its sound


def _kept(song: Song, settings: Settings) -> tuple[Path, list[tuple[float, str]], float]:
    """The found song's folder, its lyric lines on its upload's times and its length."""
    folder = audio_path(settings, song).parent
    try:
        kept = json.loads((folder / LINES).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SongError("the song's files are gone: make the reel again") from exc
    return folder, [(float(t), str(text)) for t, text in kept["lines"]], float(kept["duration"])


def _window_of(song: Song, index: int, lines: list[tuple[float, str]]) -> Window:
    """The lines of the part song.choices[index]."""
    if not 0 <= index < len(song.choices):
        raise SongError(f"there is no part {index + 1} to choose")
    pick_ = song.choices[index]
    first = min(range(len(lines)), key=lambda k: abs(lines[k][0] - pick_.start))
    last = max(k for k in range(first, len(lines)) if lines[k][0] < pick_.end - 0.05)
    return Window(first, last, lines[first][0], pick_.end)


def rechoose(song: Song, index: int, settings: Settings, router) -> Song:
    """`song` playing another of the parts it offered (song.choices[index]), timed again."""
    if not 0 <= index < len(song.choices):
        raise SongError(f"there is no part {index + 1} to choose")
    folder, lines, duration = _kept(song, settings)
    window = _window_of(song, index, lines)
    part = timed(lines, window, duration, folder / "vocals.wav", router)
    return song.model_copy(
        update={"start": part.start, "end": part.end, "lines": part.lines,
                "language": part.language or song.language, "playing": [index], "pieces": []}
    )  # fmt: skip


# ---- two parts joined -------------------------------------------------------------------

ONSET_RATE = 22050
ONSET_FFT = 1024
ONSET_HOP = 256
GAP = 0.15  # s of quiet vocals that end a sung line


def onset_strength(samples: np.ndarray) -> np.ndarray:
    """How sharply the sound's spectrum rises at each hop of ONSET_HOP samples (spectral
    flux of log magnitudes): high where a drum hits or a note starts."""
    if len(samples) < ONSET_FFT:
        return np.zeros(0)
    n = 1 + (len(samples) - ONSET_FFT) // ONSET_HOP
    at = np.arange(ONSET_FFT)[None, :] + ONSET_HOP * np.arange(n)[:, None]
    frames = samples[at].astype(np.float64) * np.hanning(ONSET_FFT)[None, :]
    magnitude = np.log1p(100.0 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(0.0, np.diff(magnitude, axis=0)).sum(axis=1)
    return np.concatenate([[0.0], flux])


def onset_peaks(strength: np.ndarray, at: float) -> list[tuple[float, float]]:
    """The onsets in a stretch of sound starting at song time `at`: (when, how strong),
    each a peak of `strength` standing well over the stretch's usual rise (a steady sound
    has none)."""
    if strength.size < 3:
        return []
    usual = float(np.median(strength))
    floor = max(usual + 0.5 * float(np.std(strength)), 3.0 * usual)
    centre = ONSET_FFT / 2 / ONSET_RATE
    return [
        (round(at + k * ONSET_HOP / ONSET_RATE + centre, 3), float(strength[k]))
        for k in range(1, strength.size - 1)
        if strength[k] >= strength[k - 1] and strength[k] > strength[k + 1] and strength[k] > floor
    ]


def cut_point(audio: Path, lo: float, hi: float, prefer: float) -> float:
    """Where to cut the song between `lo` and `hi` (song seconds): on its strongest beat or
    onset there, the nearer `prefer` the better; `prefer` when there is none."""
    prefer = min(max(prefer, lo), hi) if hi >= lo else prefer
    if hi - lo < 0.04:
        return round(prefer, 3)
    start = max(0.0, lo - 0.3)
    samples = ffmpeg.read_audio(audio, ONSET_RATE, start=start, length=hi - start + 0.3)
    peaks = [(t, v) for t, v in onset_peaks(onset_strength(samples), start) if lo <= t <= hi]
    if not peaks:
        return round(prefer, 3)
    top = max(v for _, v in peaks)
    best = max(peaks, key=lambda p: p[1] / top - 0.5 * abs(p[0] - prefer) / (hi - lo))
    return round(best[0], 3)


def gap_after(sung: np.ndarray, at: float, reach: float = 1.5) -> tuple[float, float] | None:
    """The first quiet stretch in the vocals (GAP s or more) from `at` on, within `reach`:
    when it starts and when singing comes again (capped 3 s after)."""
    n, need = len(sung), max(1, int(GAP / FRAME))
    k = max(0, int(at / FRAME))
    while k < min(n, int((at + reach) / FRAME)):
        if not sung[k] and not sung[k : k + need].any() and k + need <= n:
            j = k
            while j < min(n, k + int(3.0 / FRAME)) and not sung[j]:
                j += 1
            return round(k * FRAME, 3), round(j * FRAME, 3)
        k += 1
    return None


def onset_before(sung: np.ndarray, at: float, reach: float = 2.0) -> tuple[float, float] | None:
    """Where the singing at `at` (or up to 0.3 s after) starts, and where the quiet before
    it does (as far back as `reach`); None when nothing is sung there or the singing runs
    on from more than 0.6 s before."""
    n, k = len(sung), max(0, int(at / FRAME))
    if k >= n:
        return None
    j = k
    while j < min(n - 1, k + int(0.3 / FRAME)) and not sung[j]:
        j += 1
    if not sung[j]:
        return None
    i = j
    while i > 0 and sung[i - 1]:
        i -= 1
        if (j - i) * FRAME > 0.6:
            return None
    q = i
    while q > 0 and not sung[q - 1] and (i - q) * FRAME < reach:
        q -= 1
    return round(i * FRAME, 3), round(q * FRAME, 3)


def _curves(folder: Path, duration: float) -> tuple[np.ndarray | None, np.ndarray | None]:
    """The song's most-replayed graph (kept with its download), or its loudness without."""
    try:
        meta = json.loads((folder / "song.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    heat = heat_curve(meta.get("heatmap") or [], duration)
    if heat is not None:
        return heat, None
    return None, energy_curve(loudness(ffmpeg.read_audio(folder / "song.wav", RATE)), duration)


def stretches(
    lines: list[tuple[float, str]], duration: float, heat, energy, a: Window, b: Window,
    title: str = "", aim: float = AIM,
) -> tuple[Window, Window]:  # fmt: skip
    """The stretch of whole lines from each of two parts a joined reel plays: each scored
    as a part is (about PIECE_AIM s), the pair about `aim` s in all (as the lines' times
    say: sung, the stretches come out a little shorter), no line in both."""
    bounds = (PIECE_SHORTEST, PIECE_LONGEST, PIECE_AIM)
    ones = windows(lines, duration, heat, energy, "", title, bounds, (a.first, a.last))[:8]
    twos = windows(lines, duration, heat, energy, "", title, bounds, (b.first, b.last))[:8]
    pairs = [
        (x, y) for x in ones or [a] for y in twos or [b] if x.last < y.first or y.last < x.first
    ]
    if not pairs:
        raise SongError("those two parts are the same lines of the song: choose another")

    def score(pair: tuple[Window, Window]) -> float:
        total = sum(w.end - w.start for w in pair)
        fits = SHORTEST + (aim - AIM) <= total <= LONGEST
        return (
            sum(w.score for w in pair)
            - WEIGHTS["length"] * abs(total - aim) / 10
            - (0.0 if fits else 5.0)
        )

    return max(pairs, key=score)


def join(song: Song, first: int, second: int, settings: Settings, router) -> Song:
    """`song` playing two of the parts it offered, `first` then `second`, as one reel: a
    stretch of whole lines from each (about AIM s in all), every word timed, the first cut
    in the quiet after its last line and the second in the quiet before its first, each on
    a beat or onset of the music (the sound crossfades there, sound.song_part)."""
    if first == second:
        raise SongError("choose two different parts to join")
    folder, lines, duration = _kept(song, settings)
    a, b = _window_of(song, first, lines), _window_of(song, second, lines)
    heat, energy = _curves(folder, duration)
    sung = _sung(loudness(ffmpeg.read_audio(folder / "vocals.wav", RATE)))
    aim, tried = AIM, set()
    for _ in range(3):  # sung stretches short of the reel's length: longer ones, aimed higher
        pair = stretches(lines, duration, heat, energy, a, b, song.title, aim)
        key = tuple((w.first, w.last) for w in pair)
        if key in tried:
            break
        tried.add(key)
        one, two, cut_a, cut_b = _joint(lines, *pair, duration, folder, sung, router)
        total = (cut_a - one.start) + (two.end - cut_b)
        if total >= SHORTEST:
            break
        aim += SHORTEST - total + 2.0
    ones = _within(one.lines, end=cut_a)
    twos = [line.model_copy(update={"piece": 1}) for line in _within(two.lines, start=cut_b)]
    return song.model_copy(
        update={
            "start": one.start,
            "end": two.end,
            "lines": ones + twos,
            "language": one.language or two.language or song.language,
            "playing": [first, second],
            "pieces": [SongPiece(start=one.start, end=cut_a), SongPiece(start=cut_b, end=two.end)],
        }
    )


def _joint(
    lines: list[tuple[float, str]], pick_a: Window, pick_b: Window, duration: float,
    folder: Path, sung: np.ndarray, router,
) -> tuple[Song, Song, float, float]:  # fmt: skip
    """Two stretches timed, and where they are cut to meet: the first after its last word,
    the second before its first, each on a beat or onset of the music; the two never
    longer than LONGEST together."""
    vocals, audio = folder / "vocals.wav", folder / "song.wav"
    one = timed(lines, pick_a, duration, vocals, router, PIECE_LONGEST)
    # The first part ends in the quiet after its last word, before the next line.
    last_word = max((w.end for w in one.lines[-1].words), default=one.lines[-1].start)
    nxt = pick_a.first + len(one.lines)  # the line after its last (timing may end it sooner)
    after = lines[nxt][0] if nxt < len(lines) else duration
    gap = gap_after(sung, last_word - 0.1)
    if gap is not None and gap[0] < after - 0.1:
        lo, hi = gap[0] + 0.05, min(gap[1] - 0.1, gap[0] + TAIL, after - 0.1, duration)
    else:
        lo = hi = min(last_word + 0.1, max(last_word, after - 0.05))
    cut_a = cut_point(audio, lo, max(lo, hi), lo + 0.35)
    room = max(PIECE_SHORTEST, LONGEST - (cut_a - one.start))
    two = timed(lines, pick_b, duration, vocals, router, room)
    # The second starts in the quiet before its first word, on the music's beat.
    first_word = two.lines[0].words[0].start if two.lines[0].words else two.lines[0].start
    found = onset_before(sung, first_word - 0.1)
    if found is None:
        cut_b = two.start
    else:
        onset, quiet = found
        lo, hi = max(0.0, quiet + 0.05, onset - 1.0), onset - 0.04
        cut_b = cut_point(audio, lo, hi, onset - LEAD_IN) if lo <= hi else max(0.0, hi)
    return one, two, cut_a, cut_b


def _within(lines: list[SongLine], start: float = 0.0, end: float = 1e9) -> list[SongLine]:
    """`lines` with every word inside start-end (song seconds), the edge lines too."""
    out = []
    for line in lines:
        words = [
            w.model_copy(update={"start": min(max(w.start, start + 0.02), end - 0.1),
                                 "end": min(max(w.end, start + 0.1), end)})
            for w in line.words
        ]  # fmt: skip
        out.append(
            line.model_copy(
                update={"words": words, "start": max(line.start, start), "end": min(line.end, end)}
            )
        )
    if out:
        out[-1] = out[-1].model_copy(update={"end": end if end < 1e9 else out[-1].end})
    return out


def describe(song: Song) -> dict:
    """The song as the studio shows it."""
    return {
        **{k: v for k, v in song.model_dump().items() if k not in ("lines",)},
        "length": round(song.end - song.start, 2),
    }
