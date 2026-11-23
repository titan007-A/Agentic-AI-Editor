"""Where in a stretch of stream something happened: the moments worth a clip.

Three signals, each read second by second against what is usual nearby (a busy chat or
a loud streamer isn't a moment; a jump is):
- chat: messages a second, hype words and emotes counted extra ("KEKW", "W", "clip it");
  chat reacts a few seconds after what it reacts to, so it is moved back by CHAT_LAG;
- sound: loudness above the stream's usual (shouting, laughing, a blast in the game);
- viewers' clips: a clip made live ends where it was made; the seconds before it count.
A moment's clip is cut here, around it: the build-up, then the reaction, each end moved
to the quietest second near it (a pause between sentences), 15-30 s long. The job only
dresses it (its preset's text, captions, title): a planner choosing its own 15 s of a
longer window was seen to miss the moment the chat reacted to.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

CHAT_LAG = 6  # seconds between something happening and the chat answering it
BEFORE, AFTER = 18, 9  # a clip around a moment: its build-up, then the reaction
EDGE = 3  # an end may move this far, to a quieter second
SHORTEST, LONGEST = 15, 30  # every clip's length (owner: "clips around 15-30 sec ki hi")
APART = 10  # seconds kept between two moments' clips
STRONG = 0.8  # a live hunt keeps only moments at least this strong
WEAK = 0.4  # below this a moment is shown as weak
NEARBY = 300  # seconds of stream that say what's usual (chat, sound)
EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")
# Said (or shown as emotes) when something just happened.
HYPE_WORDS = {
    "kekw", "lul", "lulw", "omegalul", "icant", "lmao", "lmfao", "lol", "lool", "loool",
    "haha", "hahaha", "pog", "pogu", "poggers", "pogchamp", "w", "ww", "www", "holy",
    "wtf", "omg", "nah", "noway", "huh", "wot", "kkhuh", "hahaa", "ez", "monkas", "aware",
    "insane", "crazy", "bruh", "omegaroll", "deadass", "sheesh", "ayoo", "ayo", "gg",
}  # fmt: skip
HYPE_MARKS = ("😂", "🤣", "💀", "😭", "🔥", "😱", "‼", "???", "!!!")
CLIP_WORDS = re.compile(r"\bclip(?:ped|s|\s+(?:it|that|this))?\b", re.I)
WORD = re.compile(r"[a-z]+")


@dataclass
class Moment:
    start: float  # the window cut from the stream (seconds into it)
    end: float
    peak: float  # where it happened
    score: float
    why: list[str] = field(default_factory=list)
    chat_at: float | None = None  # when the chat answered it most


def words_of(text: str) -> list[str]:
    """A message's words and emotes, lower case, with drawn-out letters cut short
    ("LMAOOOO" is "lmao")."""
    names = [n.lower() for n in EMOTE.findall(text)]
    plain = EMOTE.sub(" ", text).lower()
    return names + [re.sub(r"(.)\1{2,}", r"\1", w) for w in WORD.findall(plain)]


def weight(text: str) -> float:
    """What one message adds: 1, more when it is hype, most when it asks for a clip."""
    found = 1.0
    words = words_of(text)
    if any(w in HYPE_WORDS for w in words) or any(m in text for m in HYPE_MARKS):
        found += 1.0
    if CLIP_WORDS.search(text):
        found += 2.0
    return found


def smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or values.size == 0:
        return values.astype(float)
    kernel = np.ones(width) / width
    return np.convolve(values.astype(float), kernel, mode="same")


def usual(values: np.ndarray, width: int = NEARBY) -> np.ndarray:
    """Each second's usual value: the median of the `width` seconds around it (worked out
    every 10 s and drawn between, which is plenty for a baseline)."""
    n = values.size
    if n == 0:
        return values.astype(float)
    if n <= width:
        return np.full(n, float(np.median(values)))
    half = width // 2
    marks = np.arange(0, n, 10)
    medians = [float(np.median(values[max(0, m - half) : m + half])) for m in marks]
    return np.interp(np.arange(n), marks, medians)


def chat_signal(
    length: int, offset: float, read: list[tuple[float, float]], lines: list[tuple[float, str]]
) -> tuple[np.ndarray, np.ndarray]:
    """Each second's chat (messages, hype counting extra) over `length` seconds from
    `offset`, filled in between the stretches read (`read`: (start, end) pairs), and which
    seconds were read."""
    weights = np.zeros(length)
    known = np.zeros(length, dtype=bool)
    for start, end in read:
        lo, hi = max(0, int(start - offset)), min(length, math.ceil(end - offset))
        known[lo:hi] = True
    for at, text in lines:
        i = int(at - offset)
        if 0 <= i < length:
            weights[i] += weight(text)
    if known.any() and not known.all():
        seen = np.flatnonzero(known)
        weights = np.interp(np.arange(length), seen, weights[seen])
    return weights, known


def chat_burst(weights: np.ndarray) -> np.ndarray:
    """How far each second's chat is above its usual: 1.0 is twice as busy."""
    busy = smooth(weights, 6)
    base = usual(busy)
    floor = max(0.3, 0.5 * float(np.median(busy))) if busy.size else 0.3
    return np.clip((busy - base) / (base + floor), 0, 6)


def loud_burst(loudness: np.ndarray) -> np.ndarray:
    """How far each second is louder than usual (in dB): 1.0 is 9 dB above."""
    db = smooth(loudness, 3)
    return np.clip((db - usual(db, 120) - 3) / 6, 0, 2)


def clip_cover(length: int, offset: float, clips: list[tuple[float, float, int]]) -> np.ndarray:
    """Viewers' clips (end, duration, views): the seconds just before each one's end."""
    cover = np.zeros(length)
    for end, duration, views in clips:
        lo = max(0, int(end - min(duration, 25) - offset))
        hi = min(length, int(end + 3 - offset))
        if lo < hi:
            cover[lo:hi] += 0.6 + 0.25 * math.log1p(max(views, 0))
    return cover


def find(
    start: float,
    end: float,
    count: int,
    lines: list[tuple[float, str]] = (),
    read: list[tuple[float, float]] = (),
    loudness: np.ndarray | None = None,
    clips: list[tuple[float, float, int]] = (),
    least: float = 0.0,
    avoid: list[tuple[float, float]] = (),
) -> list[Moment]:
    """Up to `count` moments in the stretch `start`-`end` (seconds into the stream), the
    strongest first, each with its clip, apart; `least` leaves out weaker ones. `loudness`
    is one value (dB) a second from `start`. `avoid`: clips already made of this stream
    (seconds into it), kept apart from like the moments found here."""
    length = max(1, math.ceil(end - start))
    weights, known = chat_signal(length + CHAT_LAG, start, list(read), list(lines))
    has_chat = bool(known.any() and weights.sum() > 0)
    chat = chat_burst(weights)[CHAT_LAG:] if has_chat else np.zeros(length)
    loud = np.zeros(length)
    db = None
    if loudness is not None and loudness.size and not np.isnan(loudness).all():
        # Seconds not heard (a long stretch is heard only in places) count as usual.
        db = np.full(length, np.nan)
        db[: min(length, loudness.size)] = loudness[:length]
        db = np.where(np.isnan(db), np.nanmedian(db), db)
        loud = loud_burst(db)
    cover = clip_cover(length, start, list(clips))
    score = smooth(chat + 0.6 * loud + cover, 5)
    taken = np.zeros(length, dtype=bool)
    for a, b in avoid:
        lo, hi = math.floor(a - start) - APART, math.ceil(b - start) + APART
        taken[max(0, lo) : max(0, min(length, hi))] = True
    found: list[Moment] = []
    shortest = min(SHORTEST, length)
    while len(found) < count:
        open_score = np.where(taken, -1.0, score)
        i = int(np.argmax(open_score))
        best = float(open_score[i])
        if best < 0 or best < least or (found and best <= 0):
            break
        # The free stretch around it; a stretch too short for a clip is given up.
        before = np.flatnonzero(taken[:i])
        after = np.flatnonzero(taken[i:])
        left = int(before[-1]) + 1 if before.size else 0
        right = i + int(after[0]) if after.size else length
        if right - left < shortest:
            taken[left:right] = True
            continue
        lo, hi = clip_around(i, left, right, db, shortest)
        why = reasons(i, chat, loud, cover, weights, lines, start)
        if best < WEAK:
            why.append("weak moment")
        chat_at = None
        if has_chat:
            around = chat[lo:hi]
            chat_at = start + lo + int(np.argmax(around)) + CHAT_LAG if around.max() > 0 else None
        found.append(Moment(start + lo, start + hi, start + i, round(best, 2), why, chat_at))
        taken[max(0, lo - APART) : hi + APART] = True
    return found


def quietest(db: np.ndarray, near: int, left: int, right: int) -> int:
    """The quietest second within EDGE of `near` (kept inside left-right)."""
    lo, hi = max(left, near - EDGE), min(right - 1, near + EDGE)
    if lo > hi:
        return min(max(near, left), right)
    return lo + int(np.argmin(db[lo : hi + 1]))


def clip_around(i: int, left: int, right: int, db: np.ndarray | None, shortest: int):
    """The clip of a moment at second `i`: BEFORE it to AFTER it, its ends at quiet
    seconds when the sound is known, inside the free stretch left-right, `shortest` to
    LONGEST seconds long."""
    lo, hi = i - BEFORE, i + AFTER
    if db is not None:
        lo = quietest(db, lo, left, right)
        hi = quietest(db, hi, left, right) + 1  # to the end of that quiet second
    lo, hi = max(lo, left), min(hi, right)
    if hi - lo < shortest:  # against an edge: it grows the other way
        hi = min(right, lo + shortest)
        lo = max(left, hi - shortest)
    return lo, min(hi, lo + LONGEST)


def reasons(i, chat, loud, cover, weights, lines, start) -> list[str]:
    why = []
    lo, hi = max(0, i - 10), i + 15
    if chat[lo:hi].max(initial=0) >= 0.5:
        why.append(f"chat ×{1 + chat[lo:hi].max():.1f}")
    said = Counter()
    for at, text in lines:
        if start + lo + CHAT_LAG <= at < start + hi + CHAT_LAG:
            said.update(w for w in set(words_of(text)) if w in HYPE_WORDS or w.startswith("clip"))
    why += [f'"{w}" ×{n}' for w, n in said.most_common(3) if n >= 2]
    if loud[lo:hi].max(initial=0) >= 0.5:
        why.append("loud")
    if cover[lo:hi].max(initial=0) > 0:
        why.append("viewers clipped it")
    return why
