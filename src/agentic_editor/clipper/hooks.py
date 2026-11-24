"""A clip's first seconds decide whether anyone watches it (owner, 2026-11-24: "ek dam badia
la jawab hooks hi dundhe woh"): a clip that opens on build-up or a pause is scrolled past.

A hunt finds more likely moments than it wants clips (the chat, the sound, viewers' clips;
moments.py). For each, the stream around it is fetched in a light quality, what is said
there is written down (whisper, all the moments in one go), a few frames are described
(vision model), and the planning model, told what the chat said, reads it: what happens,
where its payoff lands, how strong the payoff is to a stranger, where the clip could open
and where it ends. A second, cold look rates each proposed opening on its first seconds
alone, the way someone scrolling past meets it, so the model doesn't grade its own pick.
A moment is made only when its best opening grabs and its payoff lands; the best are made
first. The chat-reacted moment always stays in the clip, with its reaction after it (a
planner free to pick its own stretch was seen to miss it, see moments.py).

When nothing can open the moment well but its payoff is strong, the clip may open cold on
a flash of the payoff (FLASH seconds), then play from its best opening: cut so, the job
keeps it whole like any other.
"""

import logging
import math
import re
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from agentic_editor import transcript
from agentic_editor.clipper import moments
from agentic_editor.llm import LLMError, parse
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

MORE = 3  # likely moments looked at for each clip wanted
MOST_LOOKED = 24  # at most, in one hunt
LEAD, TAIL = 32, 16  # seconds of stream looked at before and after a moment's peak
EARLIEST, LATEST = 25, 3  # a clip opens this far before the peak at most, and at least
END_LEAST, END_MOST = 4, 15  # and ends this long after it: the reaction is in
OPENING = 3.0  # seconds a clip has to grab someone scrolling past
LEAST_WORDS = 5  # said in an opening's first seconds: hooks top clip pages open on talk
HEARD_AGAIN = (
    0.5  # of an opening's words, heard again when its first seconds are written down alone
)
HOOK_LEAST = 7.5  # its opening, rated cold (0-10, two looks averaged): it grabs
PAYOFF_LEAST = 6  # its payoff (0-10)
LEAD_IN, TAIL_OUT = 0.15, 0.5  # seconds kept before the first word and after the last
FLASH = 2.5  # a cold open's flash of the payoff
LATE_PAYOFF = 10  # seconds after the opening: a payoff later is worth flashing first
APART = 2.0  # seconds between two clips made of one stretch
LOOK_HEIGHT = 360  # the quality a moment is looked at in
FRAMES_AT = (-24, -18, -12, -7, -3, 2)  # seconds from the peak
SEEN_NEAR = 2.5  # a frame this near an opening says what is on screen there
RATE = 16000
STEP = 0.25  # seconds per loudness value
SILENCE = 1.5  # seconds between two moments' sound when written down in one go
WRITTEN_AT_ONCE = 4  # moments written down in one go (about 3 minutes of sound)
LINE_GAP = 0.45  # a pause this long starts a new line of what is said
LINE_WORDS = 14
CHAT_SHOWN = 18  # chat messages the model is shown
FILLER = {"um", "uh", "uhh", "umm", "yeah", "ok", "okay", "hmm", "mm", "alright", "anyway"}


@dataclass
class Line:
    """A line of what is said: words without a long pause, at most LINE_WORDS."""

    key: str
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Look:
    """One likely moment as looked at: the stretch around it (seconds into the stream),
    what is said and shown there, how loud it is."""

    moment: moments.Moment
    start: float
    end: float
    words: list[Word] = field(default_factory=list)
    loud: np.ndarray = field(default_factory=lambda: np.zeros(0))  # dB every STEP s
    frames: list[tuple[float, Path]] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    sudden: str = ""
    chat: list[str] = field(default_factory=list)
    audio: np.ndarray | None = None
    lines: list[Line] = field(default_factory=list)


@dataclass
class Opening:
    """Where a clip opens and what grabs there."""

    start: float
    said: str
    score: float  # 0-10, as rated cold
    grabs: str
    key: str


def wanted(count: int) -> int:
    """How many likely moments to look at for `count` clips."""
    return min(max(count * MORE, count + 2), max(MOST_LOOKED, count))


# -- what is said, shown and heard


def lines_of(words: list[Word]) -> list[Line]:
    """What is said as lines: split at pauses of LINE_GAP, then where sentences start; a
    line still longer than LINE_WORDS where a clause ends or at its longest pause (so a
    line, which a clip may open on, starts where speech naturally does)."""
    groups = [
        line
        for phrase in transcript.phrases(words, LINE_GAP)
        for line in transcript.split_phrase(phrase, LINE_WORDS)
    ]
    return [Line(f"L{i + 1}", g) for i, g in enumerate(groups)]


def loudness(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """dB every STEP seconds."""
    size = int(rate * STEP)
    n = samples.size // size
    if n == 0:
        return np.zeros(0)
    power = np.mean(samples[: n * size].reshape(n, size).astype(np.float64) ** 2, axis=1)
    return 10 * np.log10(power + 1e-10)


def loud_at(look: Look, start: float, seconds: float = OPENING) -> str:
    """How loud `seconds` from `start` are against the stretch's usual: shouting, normal
    or quiet."""
    if look.loud.size == 0:
        return "unknown"
    lo = max(0, int((start - look.start) / STEP))
    part = look.loud[lo : lo + max(1, int(seconds / STEP))]
    if part.size == 0:
        return "unknown"
    usual = float(np.median(look.loud))
    top = float(np.percentile(part, 90))
    if top >= usual + 8:
        return "much louder than the rest"
    if top >= usual + 4:
        return "louder than the rest"
    if top <= usual - 6:
        return "quiet"
    return "as loud as the rest"


def bursts(look: Look) -> list[float]:
    """Seconds (into the stream) where the sound jumps well above the stretch's usual."""
    if look.loud.size < 8:
        return []
    usual = float(np.median(look.loud))
    found: list[float] = []
    for i in range(1, look.loud.size):
        if look.loud[i] >= usual + 8 and look.loud[i - 1] < usual + 8:
            t = look.start + i * STEP
            if not found or t - found[-1] > 2:
                found.append(t)
    return found


def chat_near(lines: list[tuple[float, str]], peak: float) -> list[str]:
    """What the chat said about the moment: its messages just after it (it answers late),
    the hype ones first, each once."""
    near = [
        (moments.weight(text), at, text)
        for at, text in lines
        if peak - 3 <= at - moments.CHAT_LAG <= peak + 12
    ]
    shown: list[str] = []
    for _, _, text in sorted(near, key=lambda x: (-x[0], x[1])):
        plain = " ".join(moments.EMOTE.sub(lambda m: m.group(1), text).split())[:60]
        if plain and plain.lower() not in (s.lower() for s in shown):
            shown.append(plain)
        if len(shown) >= CHAT_SHOWN:
            break
    return shown


def gather(
    looks: list[Look], fetch: Callable[[float, float, Path], float], folder: Path, ffmpeg: str
) -> list[Look]:
    """Each moment's stretch fetched (`fetch` writes it and says where in the stream it
    begins), its sound read and frames taken; the moments that couldn't be are left out."""

    def one(look: Look) -> Look | None:
        dest = folder / f"look-{look.moment.peak:.0f}.ts"
        try:
            offset = fetch(look.start, look.end, dest)
            sound = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-ss", f"{look.start - offset:.3f}",
                 "-i", str(dest), "-t", f"{look.end - look.start:.3f}", "-vn", "-ac", "1",
                 "-ar", str(RATE), "-f", "f32le", "-"],
                capture_output=True, timeout=300,
            )  # fmt: skip
            if sound.returncode != 0:
                raise RuntimeError(sound.stderr.decode(errors="replace")[-300:])
            look.audio = np.frombuffer(sound.stdout, dtype=np.float32)
            look.loud = loudness(look.audio)
            for at in FRAMES_AT:
                t = min(max(look.start, look.moment.peak + at), look.end - 0.5)
                frame = folder / f"look-{look.moment.peak:.0f}-{len(look.frames)}.jpg"
                subprocess.run(
                    [ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", f"{t - offset:.3f}",
                     "-i", str(dest), "-frames:v", "1", "-vf", "scale=448:-2", "-q:v", "4",
                     str(frame)],
                    capture_output=True, timeout=120,
                )  # fmt: skip
                if frame.is_file():
                    look.frames.append((t, frame))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            log.info("moment at %.0f s couldn't be looked at: %s", look.moment.peak, exc)
            return None
        finally:
            dest.unlink(missing_ok=True)
        return look

    with ThreadPoolExecutor(3) as pool:
        return [x for x in pool.map(one, looks) if x is not None]


def write_down(looks: list[Look], transcribe: Callable[[Path], list[Word]], folder: Path):
    """What is said around every moment, written down a few moments at a time (each a turn
    of its own at the GPU, so a person's job waits for one short turn at most): their
    stretches' sound one after another with SILENCE between, the words given back to each."""
    for first in range(0, len(looks), WRITTEN_AT_ONCE):
        group = looks[first : first + WRITTEN_AT_ONCE]
        gap = np.zeros(int(SILENCE * RATE), dtype=np.float32)
        parts, places, at = [], [], 0.0
        for look in group:
            audio = look.audio if look.audio is not None else np.zeros(0, dtype=np.float32)
            places.append((at, at + audio.size / RATE))
            parts += [audio, gap]
            at += audio.size / RATE + SILENCE
        path = folder / "said.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes((np.clip(np.concatenate(parts), -1, 1) * 32767).astype("<i2").tobytes())
        words = transcribe(path)
        for look, (a, b) in zip(group, places, strict=True):
            look.words = [
                Word(text=w.text, start=look.start + w.start - a, end=look.start + w.end - a,
                     probability=w.probability)
                for w in words if a <= (w.start + w.end) / 2 < b
            ]  # fmt: skip
            look.lines = lines_of(look.words)


class _Seen(BaseModel):
    frames: list[str]
    sudden: str = ""


SEE_PROMPT = (
    "These {n} frames, in order, are from a livestream at {times} seconds around a moment its "
    "chat reacted to (0 is the moment). A streamer's camera may sit in a corner over a game. "
    "In frames, say for each frame in at most 15 words what is happening: who is there and "
    "what they are doing, and whether it is a video game or real life. In sudden, say in a "
    "few words anything sudden or striking on screen (a fight, a crash, someone running, "
    "falling, yelling, an explosion), or nothing."
)


def see(vision, look: Look) -> None:
    """The frames described by the vision model; when it gives no answer for all of them
    (a small model runs on and on now and then), every other frame."""
    frames = look.frames
    for attempt in (frames, frames[::2]) if len(frames) > 3 else (frames,):
        times = ", ".join(f"{t - look.moment.peak:+.0f}" for t, _ in attempt)
        try:
            reply = vision.chat_json(
                [{"role": "user", "content": SEE_PROMPT.format(n=len(attempt), times=times)}],
                _Seen.model_json_schema(),
                images=[p for _, p in attempt],
                temperature=0,
            )
            seen = parse(_Seen, reply)
        except LLMError:
            if attempt is not frames or len(frames) <= 3:
                raise
            continue
        look.frames = attempt
        look.seen = [" ".join(x.split())[:140] for x in seen.frames][: len(attempt)]
        look.sudden = " ".join(seen.sudden.split())[:140]
        return


# -- the model's read of a moment


class Read(BaseModel):
    what_happens: str
    payoff: str = ""
    openings: list[str] = Field(default_factory=list)
    end: str = ""
    payoff_score: int = Field(ge=0, le=10)
    stands_alone: int = Field(ge=0, le=10)
    flash: str = ""
    tease: str = ""


class _Rating(BaseModel):
    option: str
    score: int = Field(ge=0, le=10)
    grabs: str = ""


class _Ratings(BaseModel):
    ratings: list[_Rating]


READ_PROMPT = """You edit clips for a page that posts the best moments of Kick live streams as \
Reels. A Reel lives or dies in its first two seconds: people scroll past unless the first words \
or the first thing on screen grab them. Most stream moments are not worth posting; be strict.

Stream: {name}'s Kick stream "{title}".
At 0.0 s something happened: {signals}. Times below are seconds from that moment.

What is said (id, time, words):
{lines}
Sudden jumps in loudness: {bursts}
What the picture shows:
{seen}
The chat right after it: {chat}

Answer:
- what_happens: one sentence: what happens in this moment, who does what and why it is funny, \
wild or shocking. Only what is said or shown here; never guess names.
- payoff: the id of the line where it pays off (the punchline, the reveal, the big reaction).
- openings: up to three ids of lines the clip could open on, the best first. The clip opens \
between -{earliest} s and -{latest} s, right on the line that starts the tension, so that the \
payoff comes 3 to 10 seconds later when it can. Strongest first: an accusation, insult or bold \
claim aimed at someone; a question whose answer is the payoff; a line that announces action \
("watch this", "three, two, one"); someone starting to do something wild; an argument already \
loud. Never open on silence, music, a video game's own dialogue without the streamer, a \
greeting, filler (um, so, yeah, okay chat), reading out a donation, quiet chatter, or a \
fragment that needs what came before. A line id starting with S is a sudden loud sound with \
nothing said.
- end: the id of the last line, after the payoff and the reaction to it (between +{end_least} \
s and +{end_most} s); never cut a sentence off.
- payoff_score, 0-10: how strong the payoff is to a stranger. 0-3 nothing really happens; 4-6 \
mildly funny or only for fans of the stream; 7-8 clearly funny, wild or shocking; 9-10 \
people would send it to their friends.
- stands_alone, 0-10: does a stranger get it without having watched the stream?
- flash: the id of the line with the punchline or the biggest reaction, to show for two \
or three seconds before the clip as a cold open when the payoff comes late; "" when none is \
worth it.
- tease: in at most 12 words, what a viewer waits to see, without giving the payoff away."""

RATE_PROMPT = """You are scrolling Instagram Reels. Each option is how a clip from a livestream \
could begin: what you hear and see in its first {seconds:.0f} seconds. You know nothing else \
about the clip.

{options}

For each option, rate 0-10 how strongly those first seconds alone make you stop scrolling to \
see what happens. Judge mostly the words; the sound and the screen only add to them. Be strict: \
most openings are 2-5.
0-3: nothing grabs: filler, a greeting, chit-chat, instructions, a half thought that needs \
what came before, silence, music, a video game's own dialogue, plain gameplay.
4-6: a little interesting, or only interesting if you know the streamer.
7-8: grabs: an accusation, insult or bold claim aimed at someone, a question whose answer you \
want, a shocking or very funny line, an argument already going, someone yelling or laughing \
hard, something sudden happening.
9-10: impossible to scroll past.
Crude or rude words are fine: judge whether it grabs, not whether it is polite.
In grabs, say in a few words what grabs you, or "nothing"."""


def signals_of(moment: moments.Moment) -> str:
    said = [w for w in moment.why if w.startswith('"')]
    parts = ["the chat exploded" + (f" ({', '.join(said)})" if said else "")]
    if "loud" in moment.why:
        parts.append("it got loud")
    if "viewers clipped it" in moment.why:
        parts.append("viewers clipped it")
    return ", ".join(parts)


def sounds_of(look: Look) -> list[Line]:
    """Sudden loud sounds where nothing is said, as lines the model may open on."""
    found = []
    for t in bursts(look):
        if not any(line.start - 0.5 <= t <= line.end + 0.3 for line in look.lines):
            found.append(Line(f"S{len(found) + 1}", [Word(text="(loud sound)", start=t, end=t)]))
    return found


def read_prompt(look: Look, name: str, title: str) -> str:
    peak = look.moment.peak
    rows = sorted([*look.lines, *sounds_of(look)], key=lambda x: x.start)
    lines = "\n".join(f"{x.key} {x.start - peak:+.1f} {x.text}" for x in rows)
    seen = "\n".join(
        f"{t - peak:+.0f} s: {s}" for (t, _), s in zip(look.frames, look.seen, strict=False)
    )
    if look.sudden and look.sudden.lower().strip(". ") not in ("nothing", "none", ""):
        seen += f"\nSudden on screen: {look.sudden}"
    jumps = ", ".join(f"{t - peak:+.0f} s" for t in bursts(look)) or "none"
    return READ_PROMPT.format(
        name=name, title=title, signals=signals_of(look.moment),
        lines=lines or "(nothing is said)", bursts=jumps, seen=seen or "(not seen)",
        chat=" | ".join(look.chat) or "(not read)", earliest=EARLIEST, latest=LATEST,
        end_least=END_LEAST, end_most=END_MOST,
    )  # fmt: skip


def line_by_key(look: Look, key: str) -> Line | None:
    key = key.strip().upper()
    return next((x for x in [*look.lines, *sounds_of(look)] if x.key == key), None)


def first_words(look: Look, start: float, seconds: float = OPENING) -> str:
    return " ".join(w.text for w in look.words if start - 0.05 <= w.start < start + seconds)


def shown_at(look: Look, t: float) -> str:
    """What a frame near `t` shows (within SEEN_NEAR after or a second before); "" when no
    frame is that near: a frame from elsewhere in the stretch would mislead."""
    near = [
        (abs(look.frames[i][0] - t), s)
        for i, s in enumerate(look.seen)
        if t - 1 <= look.frames[i][0] <= t + SEEN_NEAR
    ]
    return min(near)[1] if near else ""


def opening_start(look: Look, line: Line) -> float:
    """Where a clip opening on `line` starts: just before its first word, after the word
    before it."""
    start = line.start - LEAD_IN
    before = [w.end for w in look.words if w.end <= line.start + 0.01 and w.start < line.start]
    if before:
        start = max(start, before[-1] + 0.02)
    return start


def talks(look: Look, start: float, seconds: float) -> bool:
    """Whether enough is said in the first `seconds` from `start` to open a clip on: an
    opening on a word or two, a game's line or silence was the weakest kind seen."""
    return len(first_words(look, start, seconds).split()) >= LEAST_WORDS * seconds / OPENING


def mid_sentence(look: Look, start: float) -> bool:
    """Whether an opening at `start` joins speech already going: a word ends just before
    it and didn't end a sentence."""
    before = [w for w in look.words if w.end <= start + 0.2 and w.start < start]
    return (
        bool(before) and start - before[-1].end < 0.25 and not transcript.ends_sentence(before[-1])
    )


def may_open(look: Look, line: Line) -> bool:
    peak = look.moment.peak
    start = opening_start(look, line)
    return peak - EARLIEST <= start <= peak - LATEST and start >= look.start


def rate_openings(llm, look: Look, keys: list[str], flash: Line | None) -> list[Opening]:
    """The proposed openings rated cold, on their first seconds alone; with a flash of the
    payoff as one more option when there is one."""
    options: list[tuple[str, float]] = []  # each opening's line and where it starts
    for key in keys:
        line = line_by_key(look, key)
        if line is None or not may_open(look, line) or any(o[0] == line.key for o in options):
            continue
        start = opening_start(look, line)
        if talks(look, start, OPENING):
            options.append((line.key, start))
    if flash is not None and talks(look, flash.start - LEAD_IN, FLASH):
        options.append(("FLASH", flash.start - LEAD_IN))
    if not options:
        return []
    shown = {}
    for key, start in options:
        span = FLASH if key == "FLASH" else OPENING
        said = first_words(look, start, span) or "(nothing is said)"
        if key != "FLASH" and said.split()[0].lower().strip(",.") in FILLER:
            said += " (opens on filler)"
        elif key != "FLASH" and mid_sentence(look, start):
            said += " (joins a sentence already going)"
        screen = shown_at(look, start)
        shown[key] = f"you hear: {said}. Sound: {loud_at(look, start, span)}." + (
            f" On screen: {screen}." if screen else ""
        )
    # Rated twice, the options in turn and the other way round, and the two averaged: one
    # look at a few options leans on their order.
    scores: dict[str, list[int]] = {key: [] for key, _ in options}
    grabs: dict[str, str] = {}
    for order in (options, options[::-1]) if len(options) > 1 else (options,):
        letters = {chr(65 + n): key for n, (key, _) in enumerate(order)}
        listed = "\n".join(f"Option {letter}: {shown[key]}" for letter, key in letters.items())
        reply = llm.chat_json(
            [{"role": "user", "content": RATE_PROMPT.format(seconds=OPENING, options=listed)}],
            _Ratings.model_json_schema(),
            temperature=0,
        )
        this_time: set[str] = set()
        for rated in parse(_Ratings, reply).ratings:
            found = re.search(r"\b([A-Z])\b", rated.option.upper().replace("OPTION", " "))
            key = letters.get(found.group(1)) if found else None
            if key is not None and key not in this_time:
                this_time.add(key)
                scores[key].append(rated.score)
                grabs.setdefault(key, " ".join(rated.grabs.split()))
    rated_openings = []
    for key, start in options:
        if scores[key]:
            span = FLASH if key == "FLASH" else OPENING
            score = round(sum(scores[key]) / len(scores[key]), 1)
            rated_openings.append(
                Opening(start, first_words(look, start, span), score, grabs.get(key, ""), key)
            )
    return rated_openings


def end_of(look: Look, line: Line | None, start: float) -> float | None:
    """Where the clip ends: after `line` (the model's end) when that fits, inside END_LEAST-
    END_MOST after the peak and SHORTEST-LONGEST long; never mid-word."""
    peak = look.moment.peak
    lo = max(peak + END_LEAST, start + moments.SHORTEST)
    hi = min(peak + END_MOST, start + moments.LONGEST, look.end)
    if lo > hi:
        return None
    ends = [x.end + TAIL_OUT for x in look.lines]
    want = line.end + TAIL_OUT if line is not None else peak + 9
    inside = [e for e in ends if lo <= e <= hi]
    if lo <= want <= hi:
        end = want
    elif inside:
        end = min(inside, key=lambda e: abs(e - want))
    else:
        end = min(max(want, lo), hi)
    # Not into a word said right after.
    for w in look.words:
        if w.start < end < w.end:
            end = w.end + 0.05 if w.end + 0.05 <= hi else w.start - 0.05
            break
    return end if lo - 0.1 <= end <= hi + 0.1 else None


@dataclass
class Judged:
    look: Look
    read: Read
    opening: Opening
    end: float
    flash: tuple[float, float] | None

    @property
    def rank(self) -> float:
        return self.opening.score + self.read.payoff_score + 0.3 * self.read.stands_alone


def judge(llm, look: Look, name: str, title: str) -> Judged | None:
    """The model's read of one moment and its openings rated; None when it isn't strong
    enough to be made (or couldn't be read)."""
    reply = llm.chat_json(
        [{"role": "user", "content": read_prompt(look, name, title)}],
        Read.model_json_schema(),
        temperature=0,
    )
    read = parse(Read, reply)
    keys = list(read.openings)
    # Lines the model didn't name but that may open the clip, so a good one isn't missed
    # for want of a pick: the loudest start in reach when the model named none that fits.
    if not any((x := line_by_key(look, k)) is not None and may_open(look, x) for k in keys):
        fits = [x for x in look.lines if may_open(look, x)]
        if fits:
            keys.append(max(fits, key=lambda x: _loud_value(look, x.start)).key)
    flash = line_by_key(look, read.flash) if read.flash else None
    if flash is not None and not (look.start <= flash.start and flash.start + FLASH <= look.end):
        flash = None
    rated = rate_openings(llm, look, keys, flash)
    natural = [o for o in rated if o.key != "FLASH"]
    if not natural:
        log.info("moment at %.0f s: no opening fits", look.moment.peak)
        return None
    best = max(natural, key=lambda o: (o.score, -keys.index(o.key) if o.key in keys else -9))
    cold = next((o for o in rated if o.key == "FLASH"), None)
    end_line = line_by_key(look, read.end) if read.end else None
    end = end_of(look, end_line, best.start)
    if end is None:
        return None
    # A cold open (top clip pages flash the punchline first when it comes late): where no
    # opening grabs, or the payoff is far in and the flash grabs harder.
    payoff_line = line_by_key(look, read.payoff) if read.payoff else None
    payoff_at = payoff_line.start if payoff_line is not None else look.moment.peak
    late = payoff_at - best.start > LATE_PAYOFF
    flash_span = None
    if (
        cold is not None
        and best.start + 1 <= cold.start <= end - FLASH  # it plays again in its place
        and cold.score >= HOOK_LEAST
        and (best.score < HOOK_LEAST or (late and cold.score > best.score))
        and read.payoff_score >= PAYOFF_LEAST + 1
        and end - best.start + FLASH <= moments.LONGEST
    ):
        flash_span = (cold.start, cold.start + FLASH)
        best = Opening(best.start, cold.said, cold.score, cold.grabs, best.key)
    found = Judged(look, read, best, end, flash_span)
    log.info(
        "moment at %.0f s: opening %g/10 (%s) %r, payoff %d, alone %d%s",
        look.moment.peak, best.score, best.grabs, best.said[:60], read.payoff_score,
        read.stands_alone, ", cold open" if flash_span else "",
    )  # fmt: skip
    if best.score < HOOK_LEAST or read.payoff_score < PAYOFF_LEAST:
        return None
    return found


def _loud_value(look: Look, start: float) -> float:
    if look.loud.size == 0:
        return 0.0
    lo = max(0, int((start - look.start) / STEP))
    part = look.loud[lo : lo + int(OPENING / STEP)]
    return float(part.max()) if part.size else 0.0


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def heard_again(judged: list[Judged], transcribe: Callable, folder: Path) -> list[Judged]:
    """The judged moments whose opening is heard again: each opening's first seconds (the
    flash first, for a cold open) written down on their own, and at least HEARD_AGAIN of
    the words it was judged on found there. A long stretch written down in one go now and
    then hears words in noise or quiet talk that aren't there, and an opening on them
    would open on nothing."""
    spans = []
    for j in judged:
        # From a little before: whisper misses a word that starts its sound.
        at = (j.flash[0] if j.flash else j.opening.start) - 0.3
        seconds = (FLASH if j.flash else OPENING) + 1.3
        audio = j.look.audio if j.look.audio is not None else np.zeros(0, dtype=np.float32)
        lo = max(0, int((at - j.look.start) * RATE))
        spans.append(audio[lo : lo + int(seconds * RATE)])
    gap = np.zeros(int(3 * RATE), dtype=np.float32)
    joined, places, t = [], [], 0.0
    for part in spans:
        places.append((t, t + part.size / RATE))
        joined += [part, gap]
        t += part.size / RATE + 3
    path = folder / "openings.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(np.concatenate(joined), -1, 1) * 32767).astype("<i2").tobytes())
    words = transcribe(path)
    kept = []
    for j, (a, b) in zip(judged, places, strict=True):
        heard = set(_tokens(" ".join(w.text for w in words if a <= w.start < b)))
        said = _tokens(j.opening.said)
        share = sum(w in heard for w in said) / len(said) if said else 0.0
        if share >= HEARD_AGAIN:
            kept.append(j)
        else:
            log.info("moment at %.0f s: its opening %r wasn't heard again (%.0f%%)",
                     j.look.moment.peak, j.opening.said[:60], 100 * share)  # fmt: skip
    return kept


def chosen(judged: list[Judged], count: int, avoid: list[tuple[float, float]]) -> list[Judged]:
    """The best `count`, none overlapping another or a clip made before."""
    taken = list(avoid)
    kept = []
    for j in sorted(judged, key=lambda j: (-j.rank, -j.look.moment.score)):
        span = (j.opening.start, j.end)
        if any(span[0] < b + APART and a - APART < span[1] for a, b in taken):
            continue
        kept.append(j)
        taken.append(span)
        if len(kept) == count:
            break
    return kept


def moment_of(j: Judged) -> moments.Moment:
    """The moment as it will be cut: its clip from the opening to the end, what it opens
    on, what happens."""
    m = j.look.moment
    hook = {
        "said": j.opening.said[:200],
        "grabs": j.opening.grabs[:120],
        "score": j.opening.score,
        "payoff": j.read.payoff_score,
        "alone": j.read.stands_alone,
        "what": " ".join(j.read.what_happens.split())[:300],
        "tease": " ".join(j.read.tease.split())[:120],
    }
    if j.flash:
        hook["flash"] = [round(j.flash[0], 2), round(j.flash[1], 2)]
    why = [*m.why, f"hook {j.opening.score:g}/10", f"payoff {j.read.payoff_score}/10"]
    if j.flash:
        why.append("cold open")
    return moments.Moment(
        start=round(j.opening.start, 2), end=round(j.end, 2), peak=m.peak, score=m.score,
        why=why, chat_at=m.chat_at, hook=hook,
    )  # fmt: skip


# -- a hunt's moments


def choose(
    found: list[moments.Moment],
    count: int,
    fetch: Callable[[float, float, Path], float],
    stretch: tuple[float, float],
    chat: list[tuple[float, str]],
    about: tuple[str, str],
    settings,
    told: Callable[[str], None] = lambda message: None,
    avoid: list[tuple[float, float]] = (),
    router=None,
) -> tuple[list[moments.Moment], str]:
    """The moments worth a clip among `found` (likely moments, the strongest first), at
    most `count`, each with its clip placed by its opening; and what was done, for the
    hunt's message. When nothing could be judged (no models, the stream unreadable), the
    strongest `count` as found, as before."""
    from agentic_editor import slots
    from agentic_editor.backends import get_backends
    from agentic_editor.router import ModelRouter

    if not found:
        return [], ""
    started = time.monotonic()
    name, title = about
    looks = [
        Look(m, max(stretch[0], m.peak - LEAD), min(stretch[1], m.peak + TAIL),
             chat=chat_near(chat, m.peak))
        for m in found
    ]  # fmt: skip
    as_found = found[:count]
    try:
        router = router or ModelRouter(settings, get_backends())
    except Exception as exc:  # no models here at all: the moments as found
        log.warning("no models to judge the hooks with: %s", exc)
        return as_found, "no models to judge the hooks with; cut as found"
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        told(f"listening to {len(looks)} likely moments for their hooks")
        looks = gather(looks, fetch, folder, settings.ffmpeg_bin)
        if not looks:
            return as_found, "the moments couldn't be listened to; cut as found"
        told(f"writing down what is said around {len(looks)} likely moments")
        try:
            with slots.behind():
                write_down(looks, lambda p: router.transcribe(p).words, folder)
        except Exception as exc:  # whisper away: no hooks to judge
            log.warning("what is said couldn't be written down: %s", exc)
            return as_found, "what is said couldn't be written down; cut as found"
        _each(router.vision, looks, see, told, "looking at")
        judged: list[Judged] = []

        def read(llm, look: Look) -> None:
            if (found := judge(llm, look, name, title)) is not None:
                judged.append(found)

        failed = _each(router.planner, looks, read, told, "judging the hook of")
        if failed == len(looks):
            return as_found, "the hooks couldn't be judged; cut as found"
        if judged:
            told(f"listening again to the openings of {len(judged)} moments")
            try:
                with slots.behind():
                    judged = heard_again(judged, lambda p: router.transcribe(p).words, folder)
            except Exception as exc:  # whisper away now: the openings as first heard
                log.warning("the openings couldn't be listened to again: %s", exc)
    kept = chosen(judged, count, list(avoid))
    took = time.monotonic() - started
    log.info("hooks: %d of %d moments strong enough, %d kept, in %.0f s",
             len(judged), len(looks), len(kept), took)  # fmt: skip
    note = f"{len(judged)} of {len(looks)} likely moments had a strong hook"
    return [moment_of(j) for j in kept], note


def _each(model: Callable, looks: list[Look], work: Callable, told, doing: str) -> int:
    """`work(client, look)` for each look with the model open, given back to anyone else
    who waits for the GPU between looks (a person's job goes first). How many failed."""
    from agentic_editor import slots

    failed = 0
    pending = list(looks)
    while pending:
        with slots.behind(), model() as client:
            while pending:
                look = pending.pop(0)
                told(f"{doing} moment {len(looks) - len(pending)} of {len(looks)}")
                try:
                    work(client, look)
                except (LLMError, OSError, ValueError) as exc:
                    log.info("moment at %.0f s: %s failed: %s", look.moment.peak, doing, exc)
                    failed += 1
                except Exception as exc:  # e.g. httpx errors: the model away
                    log.warning("moment at %.0f s: %s failed: %r", look.moment.peak, doing, exc)
                    failed += 1
                if pending and slots.gpu.others_waiting():
                    break
    return failed


def notes_line(hook: dict) -> str:
    """What the job's writers are told about the clip from its hook: what happens, what
    it opens on and what a viewer waits for (worded as content, so the instruction reader
    doesn't take it for a setting)."""

    def sentence(text: str) -> str:
        text = " ".join(re.sub(r'["“”]', "", text).split())
        return text if text[-1:] in ".?!" else text + "."

    parts = []
    if hook.get("what"):
        parts.append("What happens: " + sentence(hook["what"]))
    if hook.get("said"):
        lead = "First seconds (a flash of the payoff)" if hook.get("flash") else "First seconds"
        parts.append(f"{lead}: " + sentence(hook["said"]))
    if hook.get("tease"):
        parts.append("The viewer waits to see: " + sentence(hook["tease"]))
    return " ".join(parts)


WHOOSH = 0.9  # seconds
WHOOSH_BEFORE = 0.25  # it starts this long before the cut back
WHOOSH_LEVEL = 0.35  # its peak, of full scale


def whoosh(path: Path, rate: int = 48000) -> Path:
    """A short descending whoosh (noise swept from bright to dark, fading out), written as
    a stereo WAV: the sound clip pages put over a cold open's cut back."""
    n = int(WHOOSH * rate)
    noise = np.random.default_rng(7).standard_normal(n)
    alpha = 1 - np.exp(-2 * np.pi * np.geomspace(7000, 250, n) / rate)
    swept = np.empty(n)
    y = 0.0
    for i in range(n):  # a one-pole low-pass whose cut-off falls
        y += alpha[i] * (noise[i] - y)
        swept[i] = y
    t = np.arange(n) / rate
    swept *= np.minimum(1.0, t / 0.06) * np.exp(-t / 0.3)
    swept *= WHOOSH_LEVEL / max(float(np.abs(swept).max()), 1e-9)
    frames = (np.repeat(swept[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames.tobytes())
    return path


def flash_of(moment: moments.Moment) -> tuple[float, float] | None:
    span = (moment.hook or {}).get("flash")
    if not span or len(span) != 2 or not all(isinstance(x, int | float) for x in span):
        return None
    a, b = float(span[0]), float(span[1])
    return (a, b) if b > a and math.isfinite(a) and math.isfinite(b) else None
