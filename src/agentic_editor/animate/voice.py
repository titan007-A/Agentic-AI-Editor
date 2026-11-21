"""Voice-over with Kokoro-82M (Apache-2.0), on this machine.

Each scene is spoken on its own, so a scene edited by hand is all that is spoken again,
and its length sets the scene's. Kokoro gives every word's start and end, which the
subtitles and the visual beats use. Spoken scenes are kept on disk by voice, speed and
text, so a preview, a change elsewhere and the final render never speak them twice.
"""

import hashlib
import json
import logging
import threading
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from agentic_editor.animate.timeline import Spoken
from agentic_editor.transcript import Word

log = logging.getLogger(__name__)

RATE = 24000
VERSION = 1  # bump to speak every cached scene again (a new model or text handling)


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    group: str  # narrator | calm | energetic | other
    about: str
    speed: float = 1.0


# The owner's three kinds first, then the rest of Kokoro's English voices.
VOICES: tuple[Voice, ...] = (
    Voice("bm_george", "George", "narrator", "deep British documentary voice", 0.95),
    Voice("am_fenrir", "Fenrir", "narrator", "deep American narrator", 0.97),
    Voice("bm_fable", "Fable", "narrator", "British storyteller", 0.97),
    Voice("af_heart", "Heart", "calm", "warm, calm American woman", 0.97),
    Voice("bf_emma", "Emma", "calm", "soft British woman", 0.97),
    Voice("af_nicole", "Nicole", "calm", "quiet, close American woman", 0.95),
    Voice("am_puck", "Puck", "energetic", "lively young American man", 1.08),
    Voice("af_bella", "Bella", "energetic", "bright young American woman", 1.06),
    Voice("am_michael", "Michael", "energetic", "clear, confident American man", 1.04),
    Voice("af_sarah", "Sarah", "other", "American woman", 1.0),
    Voice("am_adam", "Adam", "other", "American man", 1.0),
    Voice("bm_lewis", "Lewis", "other", "British man", 1.0),
    Voice("bf_isabella", "Isabella", "other", "British woman", 1.0),
)
BY_ID = {v.id: v for v in VOICES}
GROUPS = {"narrator": "bm_george", "calm": "af_heart", "energetic": "am_puck"}


def recommend(kind: str, mood: str) -> str:
    """The voice for a reel nobody chose one for: a documentary voice for history, facts
    and mysteries, a calm one for lessons, a storyteller for stories, a lively one for news."""
    if kind == "lesson" or mood == "emotional":
        return "af_heart"
    if kind == "story":
        return "bm_fable"
    if kind == "news" or mood in ("uplifting",):
        return "am_puck" if kind == "news" else "am_michael"
    return "bm_george"


def resolve(choice: str, kind: str, mood: str) -> str:
    """A voice id from what a person chose: a voice, a kind of voice, or auto."""
    if choice in BY_ID:
        return choice
    if choice in GROUPS:
        return GROUPS[choice]
    return recommend(kind, mood)


_lock = threading.Lock()


@lru_cache(maxsize=2)
def _pipeline(lang: str):
    from kokoro import KPipeline  # heavy (torch): only when a voice is needed

    return KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device="cpu")


def _words(tokens, offset: float) -> list[Word]:
    """Kokoro's tokens as words: punctuation and pieces written together ("1992,") joined
    to the word they belong to, times shifted by the chunk's place in the scene."""
    out: list[Word] = []
    glue = False
    for tok in tokens or []:
        text = tok.text or ""
        timed = tok.start_ts is not None and tok.end_ts is not None
        if out and (glue or not any(ch.isalnum() for ch in text)):
            last = out[-1]
            end = offset + tok.end_ts if timed else last.end
            out[-1] = Word(text=last.text + text, start=last.start, end=max(last.end, end))
        elif any(ch.isalnum() for ch in text) and timed:
            out.append(Word(text=text, start=offset + tok.start_ts, end=offset + tok.end_ts))
        glue = not getattr(tok, "whitespace", " ")
    return out


def _key(text: str, voice: str, speed: float) -> str:
    raw = json.dumps([VERSION, voice, round(speed, 3), text], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _write(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def speak(text: str, voice: str, cache: Path, speed: float | None = None) -> Spoken:
    """`text` in `voice`, as a WAV in `cache` with its words' times (from its start)."""
    chosen = BY_ID.get(voice) or BY_ID[GROUPS["narrator"]]
    speed = speed or chosen.speed
    cache.mkdir(parents=True, exist_ok=True)
    key = _key(text, chosen.id, speed)
    wav, meta = cache / f"{key}.wav", cache / f"{key}.json"
    if wav.exists() and meta.exists():
        saved = json.loads(meta.read_text(encoding="utf-8"))
        return Spoken(str(wav), saved["duration"], [Word(**w) for w in saved["words"]])
    with _lock:
        pipe = _pipeline(chosen.id[0])
        parts: list[np.ndarray] = []
        words: list[Word] = []
        offset = 0.0
        for result in pipe(text, voice=chosen.id, speed=speed):
            if result.audio is None:
                continue
            samples = result.audio.detach().cpu().numpy().astype(np.float32)
            words += _words(result.tokens, offset)
            parts.append(samples)
            offset += len(samples) / RATE
    if not parts:
        raise RuntimeError(f"Kokoro said nothing for {text[:60]!r}")
    samples = np.concatenate(parts)
    peak = float(np.max(np.abs(samples))) or 1.0
    samples = samples * min(1.0, 0.89 / peak)
    partial = wav.with_suffix(".part.wav")
    _write(partial, samples)
    duration = round(len(samples) / RATE, 3)
    meta.write_text(
        json.dumps({"duration": duration, "words": [w.model_dump() for w in words]}),
        encoding="utf-8",
    )
    partial.replace(wav)
    log.info("voice %s: %.1f s for %d words", chosen.id, duration, len(words))
    return Spoken(str(wav), duration, words)


def sample_text(voice: Voice) -> str:
    return (
        f"Hi, I'm {voice.name}. In 1922, a boy found a tomb that had been hidden for three "
        "thousand years."
    )
