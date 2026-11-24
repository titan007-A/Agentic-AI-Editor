"""The channel autopilot's checks (channel/pilot.py): on a found video before it is edited,
and on the reel the edit made. Each gives a Check: what it looked at, whether it passed and
what it measured. A check that can't be made fails: the video is skipped, or the reel left
for a person, never guessed about (the owner: not one wrong video, not one bad edit).

Before the edit:
- posted before: the song isn't one the account posted or has waiting (research.Posted);
- original: the song's own video or a performance, not someone's edit of it (its vocals
  alone, a "vocal vs instrument" reel, a cover...), by its title;
- length: long enough for the preset's reel and not a concert or a jukebox;
- one song: Shazam hears the same song at two points of the video at least, no other;
- named: that song and its singer are the ones the video's title or caption names;
- vocals: they separate cleanly (the edit's own check, analysis/separation.py), there are
  instruments to take away, and the singer is heard in enough of it.

After the edit, on the reel itself:
- confidence: the edit's own doubts and issues (none) and its confidence;
- caption: written, with nothing left for a person to check;
- length: within the preset's;
- labels: both panels' labels, where the preset puts them;
- panels: the top panel plays and then holds, the bottom holds and then plays, changing at
  one moment; no black or frozen stretch in the one playing, no blank held picture;
- switch: the sound changes where the picture does, between two sung words: no word spans
  it and the voice is low there;
- voice: the singer is heard in most of the vocals part;
- leak: the vocals part has no instruments left, by the separation model's own measure;
- loudness: the reel at the preset's loudness, both parts alike.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agentic_editor import ffmpeg, research, shazam
from agentic_editor.analysis import separation
from agentic_editor.analysis.audio import levels, loudest_window_db, to_db
from agentic_editor.presets import Preset
from agentic_editor.render import split as split_render
from agentic_editor.render.audio import measure_loudness
from agentic_editor.settings import get_settings
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

# ---- before the edit -------------------------------------------------------------------

LEAD = 10.0  # seconds a source has beyond the preset's shortest reel, at least
LONGEST = float(research.MAX_SONG)  # a whole song's video; longer is a concert or a jukebox
HEAR_AT = (0.2, 0.5, 0.8)  # where Shazam listens, as shares of the video
HEARD_AGREE = 2  # points that must name the song, all of those that name one alike
SUNG_LEVEL = 20.0  # dB under the voice's loudest that still counts as singing
SUNG_SHARE = 0.3  # of the whole video the singer is heard in, at least
SUNG_WINDOW = 0.5  # seconds

# ---- after the edit --------------------------------------------------------------------

MIN_CONFIDENCE = 0.99
SCALE = 6  # the reel is read at a sixth of its size for motion
FPS = 10  # frames a second read for motion
HELD = 0.35  # mean difference (of 255) from the frame before: a panel holding its frame
PLAYING = 0.35  # and above it, one that plays; a held copy measures about 0.0-0.1
HELD_SHARE = 0.97  # of the frames where a panel holds, at least, it measures as held
FROZEN = 1.0  # seconds a playing panel may show one picture (a still shot)
BLACK_LUMA = 16  # mean luma (of 255) of a black picture
BLACK = 0.3  # seconds of black a playing panel may show
BLANK_LUMA, BLANK_SPREAD = 20, 8  # a held picture this dark, or this even, is blank
LABEL_DIFF = 40.0  # mean difference (of 255) of a label's pixels from how it is drawn
SWITCH_AGREE = 0.25  # seconds between where the picture and the sound change, at most
WORD_EDGE = 0.05  # seconds a word may overlap the change at its ends (timings are rough)
VOICE_GAP = 12.0  # dB under its loudest the voice must be where the sound changes
VOICE_AROUND = 0.06  # seconds either side of the change listened to
VOICE_SHARE = 0.6  # of the vocals part the singer is heard in, at least
# The vocals part separated again: its instruments' stem this far under it. Calibrated as
# the edit's own "instrument-free" test (analysis/separation.py): clean vocals leave the
# stem 27-45 dB down, a full mix 1-4 dB.
LEAK_RMS = separation.INSTRUMENT_FREE_RMS_MARGIN_DB
LEAK_LOUDEST = separation.INSTRUMENT_FREE_LOUDEST_MARGIN_DB
LOUDNESS_OFF = 1.0  # LU the reel may be from the preset's loudness
PEAK_OVER = 0.5  # dB its peak may be over the preset's
PARTS_APART = 3.0  # LU the two parts may differ


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def __post_init__(self) -> None:
        self.ok = bool(self.ok)  # numpy's comparisons give numpy booleans

    def row(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


# A video that is already someone's edit of the song, by its title: the vocals alone, a
# "vocal vs instrument" reel, an instrumental, an altered copy, a cover.
EDITED = re.compile(
    r"\b(?:isolated|a ?cappella|acapella|vocals? only|only vocals?|just (?:the )?vocals?|"
    r"no music|without (?:music|instruments?|the music)|music removed|instrumental|karaoke|"
    r"vocals? vs\.?|vs\.? instruments?|instruments? vs\.?|8d|slowed|sped up|nightcore|"
    r"remix|cover|lyrics?)\b",
    re.IGNORECASE,
)


def original(title: str, caption: str = "", by: str = "its title") -> Check:
    """Whether the video is the song's own (its video or a performance), not someone's edit
    of it, by its title and its caption's first line (`by` says which were read)."""
    text = f"{title}\n{caption.strip().split(chr(10), 1)[0] if caption.strip() else ''}"
    if found := EDITED.search(text):
        return Check("original", False, f"it is an edit of the song (“{found.group(0)}”, {by})")
    return Check("original", True, f"the song's own video or a performance, by {by}")


def length(duration: float | None, preset: Preset) -> Check:
    least = preset.min_duration + LEAD
    if duration is None:
        return Check("length", False, "the video's length isn't known")
    ok = least <= duration <= LONGEST
    return Check("length", ok, f"{duration:.0f} s (needs {least:.0f}-{LONGEST:.0f} s)")


def not_posted(song: str, artist: str, posted: research.Posted, by: str) -> Check:
    """`by`: who named the song ("the research", "Shazam")."""
    if not song.strip():
        return Check("posted before", True, f"{by} didn't name the song; checked by its sound")
    name = f"“{song}” by {artist or '?'}"
    if posted.has(song, artist):
        return Check("posted before", False, f"{name} was posted or is waiting already")
    return Check("posted before", True, f"{name} as {by} names it: not posted before")


def _excerpt(video: Path, start: float, dest: Path) -> None:
    ffmpeg.run(["-y", "-ss", f"{start:.2f}", "-i", str(video), "-map", "0:a:0",
                "-t", f"{shazam.EXCERPT:g}", "-ac", "1", "-ar", "44100", "-vn",
                str(dest)])  # fmt: skip


def hear(
    video: Path, work_dir: Path, duration: float, ask=None, look=None
) -> list[tuple[str, str] | None]:
    """What Shazam hears at each of HEAR_AT, without its notes about the take (None where
    it knows nothing). Kept in the work dir once every point was asked; raises when Shazam
    can't be asked at all."""
    cache = work_dir / "channel-heard.json"
    if cache.exists():
        return [tuple(h) if h else None for h in json.loads(cache.read_text())]
    ask = ask or shazam._recognise
    look = look or shazam.titles
    starts = sorted(
        {round(max(0.0, min(duration - shazam.EXCERPT, duration * p - shazam.EXCERPT / 2)), 1)
         for p in HEAR_AT}
    )  # fmt: skip
    heard: list[tuple[str, str] | None] = []
    for start in starts:
        clip = work_dir / "channel-shazam.wav"
        try:
            _excerpt(video, start, clip)
            track = ask(clip)
        finally:
            clip.unlink(missing_ok=True)
        named = shazam.named(track)
        heard.append(shazam.names_only(*named, look) if named else None)
        log.info("Shazam at %.0f s of %s: %s", start, video.name, heard[-1])
    cache.write_text(json.dumps(heard))
    return heard


def one_song(heard: list[tuple[str, str] | None]) -> tuple[Check, tuple[str, str] | None]:
    """The song, when Shazam named the same one at HEARD_AGREE points or more and no other."""
    found = [h for h in heard if h]
    titles = {research.title_key(song) for song, _ in found}
    if len(titles) > 1:
        names = "; ".join(dict.fromkeys(f"{s} by {a}" for s, a in found))
        return Check("one song", False, f"Shazam heard different songs: {names}"), None
    if len(found) < HEARD_AGREE:
        return Check(
            "one song", False, f"Shazam knew it at {len(found)} of {len(heard)} points"
        ), None
    song, artist = found[0]
    said = f"Shazam heard “{song}” by {artist} at {len(found)} of {len(heard)} points"
    return Check("one song", True, said), found[0]


def named(heard: tuple[str, str], title: str, description: str, uploader: str) -> Check:
    """Whether the video's own words name the song Shazam heard and its singer."""
    song, artist = heard
    text = f" {research.plain(' '.join((title, description, uploader)))} "
    title_ok = f" {research.title_key(song)} " in text
    artists = research.artist_keys(artist)
    artist_ok = any(f" {a} " in text for a in artists)
    if title_ok and artist_ok:
        return Check("named", True, f"the title or caption names “{song}” and {artist}")
    missing = " and ".join(
        what for what, ok in ((f"“{song}”", title_ok), (artist, artist_ok)) if not ok
    )
    said = f"Shazam heard “{song}” by {artist}; the post doesn't name {missing}"
    return Check("named", False, said)


def window_levels(samples: np.ndarray, rate: int, window: float) -> np.ndarray:
    """RMS level in dB of each `window` seconds."""
    size = max(1, int(rate * window))
    count = len(samples) // size
    if count == 0:
        return np.array([levels(samples)[0]])
    frames = samples[: count * size].reshape(count, size).astype(np.float64)
    return np.array([to_db(v) for v in np.sqrt(np.mean(np.square(frames), axis=1))])


def sung_share(samples: np.ndarray, rate: int, loud: float | None = None) -> float:
    """The share of SUNG_WINDOW windows where the voice is within SUNG_LEVEL dB of its
    loudest (the 95th percentile, or `loud`)."""
    found = window_levels(samples, rate, SUNG_WINDOW)
    top = float(np.percentile(found, 95)) if loud is None else loud
    if top < -60:
        return 0.0
    return float(np.mean(found >= top - SUNG_LEVEL))


def vocals(isolated: Path | None, instrument_free: bool, failed: str = "") -> Check:
    """`isolated`: the vocals the edit's own separation kept (None when it refused them, for
    `failed`); `instrument_free`: the source had no instruments to take away."""
    if isolated is None:
        return Check("vocals", False, f"the vocals don't separate cleanly: {failed}")
    if instrument_free:
        return Check("vocals", False, "nothing to take away: it is voice alone")
    rate = 16000
    share = sung_share(ffmpeg.read_audio(isolated, rate), rate)
    ok = share >= SUNG_SHARE
    return Check("vocals", ok, f"separated cleanly; the singer is heard in {share:.0%} of it"
                 + ("" if ok else f" (needs {SUNG_SHARE:.0%})"))  # fmt: skip


# ---- after the edit --------------------------------------------------------------------


def confidence(score: float, doubts: list[str], issues: list[str]) -> Check:
    said = [*issues, *doubts]
    ok = not said and score >= MIN_CONFIDENCE
    detail = f"confidence {score:.2f}" + (f"; {'; '.join(said)}" if said else ", no doubts")
    return Check("confidence", ok, detail)


def caption(text: str, status: str | None, notes: list[str]) -> Check:
    if status != "done" or not text.strip():
        why = "; ".join(notes) or status or "not written"
        return Check("caption", False, f"no caption: {why}")
    if notes:
        return Check("caption", False, "to check before posting: " + "; ".join(notes))
    return Check("caption", True, f"written, {len(text.split())} words, checked by its sources")


def reel_length(duration: float, preset: Preset) -> Check:
    ok = preset.min_duration - 0.5 <= duration <= preset.max_duration + 0.5
    return Check("reel length", ok, f"{duration:.1f} s (preset {preset.min_duration:g}-"
                 f"{preset.max_duration:g} s)")  # fmt: skip


def read_frames(path: Path, size: tuple[int, int], fps: int = FPS) -> np.ndarray:
    """The reel's frames in grey at a SCALE-th of `size`, `fps` a second."""
    width, height = size[0] // SCALE, size[1] // SCALE
    cmd = [get_settings().ffmpeg_bin, "-nostdin", "-v", "error", "-i", str(path),
           "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise ffmpeg.FFmpegError(f"couldn't read {path.name}: {result.stderr.decode()[-300:]}")
    frames = np.frombuffer(result.stdout, dtype=np.uint8)
    return frames[: len(frames) // (width * height) * width * height].reshape(-1, height, width)


def grab(path: Path, at: float, size: tuple[int, int]) -> np.ndarray:
    """One frame of the reel in colour, full size."""
    cmd = [get_settings().ffmpeg_bin, "-nostdin", "-v", "error", "-ss", f"{at:.3f}",
           "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-"]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True)
    need = size[0] * size[1] * 3
    if result.returncode != 0 or len(result.stdout) < need:
        raise ffmpeg.FFmpegError(f"couldn't grab a frame of {path.name} at {at:.1f} s")
    return np.frombuffer(result.stdout[:need], dtype=np.uint8).reshape(size[1], size[0], 3)


@dataclass
class Panels:
    """Each panel's motion (mean difference from the frame before), mean luma and spread,
    frame by frame, FPS a second."""

    top: np.ndarray
    bottom: np.ndarray
    top_luma: np.ndarray
    bottom_luma: np.ndarray
    top_spread: np.ndarray
    bottom_spread: np.ndarray

    @property
    def count(self) -> int:
        return len(self.top)


def panels_of(frames: np.ndarray, preset: Preset) -> Panels:
    rows = [(a // SCALE, b // SCALE) for a, b in
            ((preset.split.top, preset.split.rows[1]),
             (preset.split.rows[1], preset.split.rows[1] + preset.split.height))]  # fmt: skip
    # A pixel row or two in from each edge: the panels' borders blur into each other.
    parts = [frames[:, a + 1 : b - 1].astype(np.float32) for a, b in rows]
    moved = [np.concatenate([[0.0], np.abs(np.diff(p, axis=0)).mean(axis=(1, 2))]) for p in parts]
    return Panels(
        top=moved[0], bottom=moved[1],
        top_luma=parts[0].mean(axis=(1, 2)), bottom_luma=parts[1].mean(axis=(1, 2)),
        top_spread=parts[0].std(axis=(1, 2)), bottom_spread=parts[1].std(axis=(1, 2)),
    )  # fmt: skip


def change_seen(p: Panels) -> int:
    """The frame where the bottom panel starts playing and the top one holds: the one that
    best splits the frames into "top moves more" before and "bottom moves more" after."""
    lean = np.sign(np.round(p.top - p.bottom, 2))  # +1: the top moves more
    lean[0] = 0  # the first frame has none before it
    before = np.concatenate([[0.0], np.cumsum(lean)])  # sum of lean before frame k
    after = before[-1] - before  # sum from frame k on
    return int(np.argmax(before - after))


def _longest(flags: np.ndarray) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def panels(p: Panels, at: int, fps: int = FPS) -> Check:
    """Whether the panels play and hold as the preset makes them, changing at frame `at`,
    with no black or frozen stretch in the playing one and no blank held picture."""
    wrong = []
    if not 1 <= at < p.count - 1:
        return Check("panels", False, "couldn't see where the picture changes panels")
    first, second = slice(1, at), slice(at + 1, p.count)
    for name, held, playing, played_luma, part in (
        ("bottom", p.bottom[first], p.top[first], p.top_luma[first], "first"),
        ("top", p.top[second], p.bottom[second], p.bottom_luma[second], "second"),
    ):
        if len(held) and np.mean(held <= HELD) < HELD_SHARE:
            wrong.append(f"the {name} panel doesn't hold its picture in the {part} part")
        if len(playing) and np.mean(playing > PLAYING) < 0.5:
            wrong.append(f"the picture hardly moves in the {part} part")
        frozen = _longest(playing <= HELD) / fps
        if frozen > FROZEN:
            wrong.append(f"the picture stands still for {frozen:.1f} s in the {part} part")
        black = _longest(played_luma < BLACK_LUMA) / fps
        if black > BLACK:
            wrong.append(f"{black:.1f} s of black in the {part} part")
    # The held pictures: the bottom's first (shown all through the first part), the top's
    # last (all through the second).
    for name, luma, spread in (
        ("bottom", p.bottom_luma[at], p.bottom_spread[at]),
        ("top", p.top_luma[at - 1], p.top_spread[at - 1]),
    ):
        if luma < BLANK_LUMA or spread < BLANK_SPREAD:
            wrong.append(f"the {name} panel holds a blank picture (luma {luma:.0f})")
    if wrong:
        return Check("panels", False, "; ".join(wrong))
    return Check("panels", True, f"top plays then holds, bottom holds then plays, change at "
                 f"{at / fps:.1f} s; no black or frozen stretch")  # fmt: skip


def labels(frame: np.ndarray, preset: Preset, fonts_dir: Path) -> Check:
    """Whether each panel's label is on `frame` where and as the preset draws it."""
    drawn = np.asarray(split_render.labels_image(preset.split, preset.size, fonts_dir))
    solid = drawn[..., 3] >= 250
    # Inner pixels only: colour is blurred at the edges of shapes by the video's encoding.
    inner = solid.copy()
    inner[1:-1, 1:-1] &= solid[:-2, 1:-1] & solid[2:, 1:-1] & solid[1:-1, :-2] & solid[1:-1, 2:]
    found = []
    middle = preset.split.rows[1]
    for name, words, rows in (
        ("top", preset.split.first_label, slice(0, middle)),
        ("bottom", preset.split.second_label, slice(middle, None)),
    ):
        if not words.strip():
            continue
        mask = np.zeros_like(inner)
        mask[rows] = inner[rows]
        if not mask.any():
            found.append((name, words, None))
            continue
        diff = np.abs(frame[mask].astype(np.float32) - drawn[..., :3][mask]).mean()
        found.append((name, words, float(diff)))
    wrong = [f"“{w}” ({'not drawn' if d is None else f'{d:.0f} off'})"
             for _, w, d in found if d is None or d > LABEL_DIFF]  # fmt: skip
    if wrong:
        return Check("labels", False, "missing or wrong: " + ", ".join(wrong))
    said = ", ".join(f"“{w}” ({d:.0f} off)" for _, w, d in found)
    return Check("labels", True, said or "the preset has none")


def switch(
    at_seen: float, at_words: float, words: list[Word], voice: np.ndarray, rate: int
) -> Check:
    """Whether the sound changes where the picture does, between two words: none spans it,
    and the voice (`voice`: the isolated vocals of the reel's stretch, `rate` a second)
    is VOICE_GAP dB under its loudest there."""
    if abs(at_seen - at_words) > SWITCH_AGREE:
        return Check("switch", False, f"the picture changes at {at_seen:.2f} s, the words "
                     f"say {at_words:.2f} s")  # fmt: skip
    for w in words:
        if w.start + WORD_EDGE < at_words < w.end - WORD_EDGE:
            said = (f"mid-word: “{w.text.strip()}” is sung {w.start:.2f}-{w.end:.2f} s, "
                    f"the change is at {at_words:.2f} s")  # fmt: skip
            return Check("switch", False, said)
    loud = float(np.percentile(window_levels(voice, rate, 0.05), 95))
    a = max(0, int((at_words - VOICE_AROUND) * rate))
    b = max(a + 1, int((at_words + VOICE_AROUND) * rate))
    there = levels(voice[a:b])[0]
    gap = loud - there
    before = [w for w in words if w.end <= at_words + WORD_EDGE]
    after = [w for w in words if w.start >= at_words - WORD_EDGE]
    between = (f"between “{before[-1].text.strip()}” and “{after[0].text.strip()}”"
               if before and after else "in a pause")  # fmt: skip
    if gap < VOICE_GAP:
        return Check("switch", False, f"at {at_words:.2f} s the voice is only {gap:.0f} dB "
                     f"under its loudest: mid-note (needs {VOICE_GAP:.0f})")  # fmt: skip
    return Check("switch", True, f"at {at_words:.2f} s, {between}; the voice is {gap:.0f} dB "
                 "under its loudest there")  # fmt: skip


def voice(part: np.ndarray, whole: np.ndarray, rate: int) -> Check:
    """Whether the singer is heard in most of the vocals part (`part`, of the isolated
    vocals of the reel's whole stretch `whole`)."""
    loud = float(np.percentile(window_levels(whole, rate, SUNG_WINDOW), 95))
    share = sung_share(part, rate, loud)
    ok = share >= VOICE_SHARE
    return Check("voice", ok, f"the singer is heard in {share:.0%} of the vocals part"
                 + ("" if ok else f" (needs {VOICE_SHARE:.0%})"))  # fmt: skip


def cut(path: Path, start: float, length: float, dest: Path) -> Path:
    ffmpeg.run(["-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(dest)])  # fmt: skip
    return dest


def leak(part: Path, separate, work_dir: Path) -> Check:
    """Instruments left in the vocals part (`part`, a wav of it): separated once more, its
    instruments' stem must be as far under it as clean vocals leave it. `separate(wav,
    out_dir)` is the edit's separation (router.separate_vocals)."""
    out = work_dir / "leak-stems"
    out.mkdir(parents=True, exist_ok=True)
    stems = separate(part, out)
    try:
        rate = 22050
        sound = ffmpeg.read_audio(part, rate)
        rest = ffmpeg.read_audio(stems.instrumental, rate)
        rms = levels(sound)[0] - levels(rest)[0]
        loudest = loudest_window_db(sound, rate) - loudest_window_db(rest, rate)
    finally:
        stems.vocals.unlink(missing_ok=True)
        stems.instrumental.unlink(missing_ok=True)
    ok = rms >= LEAK_RMS and loudest >= LEAK_LOUDEST
    return Check("leak", ok, f"instruments {rms:.0f} dB under the vocals, {loudest:.0f} dB at "
                 f"their loudest (needs {LEAK_RMS:.0f} and {LEAK_LOUDEST:.0f})")  # fmt: skip


def loudness(path: Path, first: Path, second: Path, preset: Preset) -> Check:
    """The reel's loudness and peak against the preset's, and its two parts' (`first`,
    `second`: wavs of them) against each other."""
    whole = measure_loudness(path)
    one, two = measure_loudness(first).integrated, measure_loudness(second).integrated
    target, peak = preset.audio.loudness_lufs, preset.audio.true_peak_db
    wrong = []
    if abs(whole.integrated - target) > LOUDNESS_OFF:
        wrong.append(f"{whole.integrated:.1f} LUFS, not {target:g}")
    if whole.true_peak > peak + PEAK_OVER:
        wrong.append(f"peaks at {whole.true_peak:.1f} dB, over {peak:g}")
    if abs(one - two) > PARTS_APART:
        wrong.append(f"the parts differ: {one:.1f} and {two:.1f} LUFS")
    detail = (f"{whole.integrated:.1f} LUFS, peak {whole.true_peak:.1f} dB; parts {one:.1f} "
              f"and {two:.1f} LUFS")  # fmt: skip
    return Check("loudness", not wrong, "; ".join(wrong) if wrong else detail)
