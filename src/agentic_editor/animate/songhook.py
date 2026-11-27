"""The song a Song story reel is made to: found by its name, downloaded, its lyrics, the
part people know (the hook) and when every word of it is sung.

1. The lyrics with a time on every line come from LRCLIB (open, free, no key), searched by
   the name typed; they also say how long the song's own recording is.
2. YouTube is searched for the name (yt-dlp) and the song's own upload chosen: the name's
   words in its title, the artist's channel, as long as the lyrics' recording, and never a
   slowed, lo-fi, remix, cover or live version. Its sound is downloaded once and kept.
3. The vocals are separated (the editor's own model) and the lyric lines moved onto this
   upload's audio: each line starts where singing starts after a pause.
4. The hook: runs of whole lines 15-32 s long, scored by YouTube's "most replayed" graph
   (where people go back to; the reels' favourite part of a song shows there), whole
   sections, lines that don't repeat inside it, a length near 20 s, and any of its words
   the person typed. Without the graph, the louder sections and the chorus count instead.
5. Every word's time: whisper hears the vocals of the part, and the lyric words are laid
   over the heard words by how long each takes to sing. A song LRCLIB doesn't know is
   heard whole, and what whisper heard becomes its lyrics.
"""

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
from agentic_editor.animate.plan import HookChoice, Song, SongLine, SongWord
from agentic_editor.settings import Settings
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "agentic-editor (song story reels)"
RESULTS = 8  # YouTube results looked at
# The part a reel plays: whole lines, about this long.
SHORTEST = 15.0
LONGEST = 32.0
AIM = 20.0
LEAD_IN = 0.3  # the part starts this much before its first line is sung
TAIL = 1.2  # and runs on this long after its last word, if nothing is sung there
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
    "early": 0.6, "length": 1.0, "chorus": 0.5, "title": 1.0, "part": 3.0,
}  # fmt: skip


def windows(
    lines: list[tuple[float, str]],
    duration: float,
    heat: np.ndarray | None,
    energy: np.ndarray | None = None,
    part: str = "",
    title: str = "",
) -> list[Window]:
    """Every run of whole lines the reel could play, best first (see the module's notes)."""
    if not lines:
        return []
    texts = [plain(text) for _, text in lines]
    # "Ye jaan kar bhi" and "Yeh jaan kar bhi" are the same line written twice.
    chorus = [
        sum(1 for b in texts if SequenceMatcher(None, a, b).ratio() >= 0.85) >= 2 for a in texts
    ]
    title_words = set(_tokens(title))
    ends = [lines[i + 1][0] if i + 1 < len(lines) else min(duration, lines[i][0] + 6.0)
            for i in range(len(lines))]  # fmt: skip
    # A line sung long ago and a line now aren't one section, nor a chorus and a verse.
    starts_section = [
        i == 0 or lines[i][0] - lines[i - 1][0] > 7.0 or chorus[i] != chorus[i - 1]
        for i in range(len(lines))
    ]
    wanted = _tokens(part)
    found: list[Window] = []
    for i in range(len(lines)):
        for j in range(i, len(lines)):
            start = lines[i][0]
            end = min(ends[j], lines[j][0] + 6.0)
            length = end - start
            if length > LONGEST:
                break
            if length < SHORTEST:
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
                # The first time a part comes is the one people know it by.
                "early": 1.0 - start / max(duration, 1.0),
                "length": -abs(length - AIM) / 10.0,
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
            found.append(Window(i, j, start, end, round(score, 3), why))
    found.sort(key=lambda w: -w.score)
    return found


def _match(wanted: list[str], line: str) -> float:
    """How much of the words typed are in a line (0-1), loosely: "chehra" finds "chehre"."""
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


def lay_words(
    said: list[str], start: float, end: float, heard: list[Word]
) -> list[tuple[str, float, float]]:
    """`said` (a line's words) timed inside [start, end): over the words whisper heard
    there, by how long each takes to sing; spread over the line when nothing was heard."""
    if not said:
        return []
    weights = [syllables(w) for w in said]
    total = float(sum(weights))
    inside = [w for w in heard if start - 0.3 <= (w.start + w.end) / 2 < end - 0.05]
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
            SongLine(
                text=" ".join(w.text for w in chunk),
                said=" ".join(s_chunk),
                start=chunk[0].start,
                end=chunk[-1].end,
                words=list(chunk),
            )
        )
    parts[0] = parts[0].model_copy(update={"end": parts[1].start})
    parts[1] = parts[1].model_copy(update={"end": line.end})
    return parts


def timed_lines(
    lines: list[tuple[float, str]], first: int, last: int, part_end: float, heard: list[Word]
) -> list[SongLine]:
    out: list[SongLine] = []
    for k in range(first, last + 1):
        start, text = lines[k]
        end = lines[k + 1][0] if k + 1 < len(lines) and k < last else part_end
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
    vocals: Path, start: float, end: float, router, language: str | None
) -> tuple[list[Word], str]:
    """The words whisper hears in the vocals between start and end (song times), kept. One
    reel of the song hears at a time, each part from its own clip."""
    from agentic_editor import slots

    key = f"heard-{start:.2f}-{end:.2f}-{language or 'auto'}"
    kept = vocals.parent / f"{key}.json"
    with slots.video(vocals.parent).turn("hear the song's words"):
        if kept.exists():
            data = json.loads(kept.read_text(encoding="utf-8"))
            return [Word(**w) for w in data["words"]], data.get("language") or ""
        clip = _cut(vocals, start, end, vocals.parent / f"{key}.wav")
        heard = router.transcribe(clip, language)
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


def find(idea: str, settings: Settings, router, say=lambda message: None, choice: int | None = None,
         lookup=lrclib, searcher=search, fetch=download) -> Song:  # fmt: skip
    """The Song for what was typed (see `asked`): found, downloaded, its part chosen (the
    best, or `choice` among the parts offered) and every word of it timed."""
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
    title = lyrics.title if lyrics else candidate.title
    best = distinct(windows(lines, duration, heat, energy, want.part, title))
    if not best:
        raise SongError("the song has no part of 15-32 s made of whole lines")
    (vocals.parent / LINES).write_text(
        json.dumps({"lines": lines, "duration": duration}, ensure_ascii=False), encoding="utf-8"
    )
    chosen = best[min(choice or 0, len(best) - 1)]
    say("timing every word")
    song = timed(lines, chosen, duration, vocals, router)
    return song.model_copy(
        update={
            "query": idea.strip(),
            "title": title,
            "artist": lyrics.artist if lyrics else meta.get("channel", "").removesuffix(" - Topic"),
            "video": candidate.id,
            "url": candidate.url,
            "audio": "song.wav",
            "duration": round(duration, 3),
            "lyrics": source,
            "choices": [
                HookChoice(start=w.start, end=w.end, first=lines[w.first][1], score=w.score)
                for w in best
            ],
        }
    )


def timed(
    lines: list[tuple[float, str]], chosen: Window, duration: float, vocals: Path, router
) -> Song:
    """The part `chosen` with every word timed: its start a breath before its first line,
    its end a little after its last word (before the next line)."""
    start = max(0.0, chosen.start - LEAD_IN)
    after = lines[chosen.last + 1][0] if chosen.last + 1 < len(lines) else duration
    heard, language = hear(vocals, start, min(duration, after + 0.3), router, None)
    language = SCRIPT_OF.get(language, language)
    part = timed_lines(lines, chosen.first, chosen.last, after, heard)
    last_word = max((w.end for line in part for w in line.words), default=chosen.end)
    if after - 0.05 > last_word:  # the music after the last word, until the next line
        end = min(duration, after - 0.05, last_word + TAIL)
    else:
        end = min(duration, last_word + 0.4)
    part[-1] = part[-1].model_copy(update={"end": end})
    return Song(start=round(start, 3), end=round(end, 3), language=language, lines=part)


LINES = "lines.json"  # a found song's lyric lines on its upload's times, beside its sound


def rechoose(song: Song, index: int, settings: Settings, router) -> Song:
    """`song` playing another of the parts it offered (song.choices[index]), timed again."""
    if not 0 <= index < len(song.choices):
        raise SongError(f"there is no part {index + 1} to choose")
    folder = audio_path(settings, song).parent
    try:
        kept = json.loads((folder / LINES).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SongError("the song's files are gone: make the reel again") from exc
    lines = [(float(t), str(text)) for t, text in kept["lines"]]
    pick_ = song.choices[index]
    first = min(range(len(lines)), key=lambda k: abs(lines[k][0] - pick_.start))
    last = max(k for k in range(first, len(lines)) if lines[k][0] < pick_.end - 0.05)
    window = Window(first, last, lines[first][0], pick_.end)
    part = timed(lines, window, float(kept["duration"]), folder / "vocals.wav", router)
    return song.model_copy(
        update={"start": part.start, "end": part.end, "lines": part.lines,
                "language": part.language or song.language}
    )  # fmt: skip


def describe(song: Song) -> dict:
    """The song as the studio shows it."""
    return {
        **{k: v for k, v in song.model_dump().items() if k not in ("lines",)},
        "length": round(song.end - song.start, 2),
    }
