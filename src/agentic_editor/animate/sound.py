"""A reel's sound: generated music, generated sound effects and the voice, mixed.

Nothing comes from a library, so nothing needs clearing. The music is drawn with numpy
from a mood (key, tempo, chords, instruments), given a room by convolution with a made-up
impulse response, and ducked under the voice. Sound effects sit on timeline.cues. The
mix is brought to the presets' loudness like every other video (render/audio.py).
"""

import math
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import signal

from agentic_editor.animate.timeline import Timeline
from agentic_editor.render import audio

RATE = audio.SAMPLE_RATE
LOUDNESS = -14.0
TRUE_PEAK = -1.5
VOICE_RMS_DB = -19.0
MUSIC_RMS_DB = -31.0  # under the voice, before ducking
DUCK = 0.6  # how far the music dips while the voice speaks

# Chords as semitones from the key's root, one a bar; instruments by layer.
MOODS: dict[str, dict] = {
    "mysterious": {
        "root": 50, "bpm": 66,
        "chords": [(0, 3, 7), (-4, 0, 3), (-9, -5, -2), (-2, 2, 5)],
        "layers": ("pad", "drone", "bells"),
    },
    "uplifting": {
        "root": 60, "bpm": 100,
        "chords": [(0, 4, 7), (-5, -1, 2), (-3, 0, 4), (-7, -3, 0)],
        "layers": ("pad", "plucks", "kick", "shaker", "bass"),
    },
    "emotional": {
        "root": 57, "bpm": 72,
        "chords": [(0, 3, 7), (-4, 0, 3), (-9, -5, -2), (-2, 2, 5)],
        "layers": ("pad", "piano", "bass"),
    },
    "tense": {
        "root": 52, "bpm": 92,
        "chords": [(0, 3, 7), (-4, 0, 3), (-7, -4, 0), (-5, -1, 2)],
        "layers": ("pad", "pulse", "hats", "drone"),
    },
    "curious": {
        "root": 53, "bpm": 96,
        "chords": [(0, 4, 7), (-3, 0, 4), (-7, -3, 0), (-5, -1, 2)],
        "layers": ("pad", "marimba", "kick", "shaker"),
    },
    # The World map reels' bed: a bright pop loop at 123 bpm, a little louder under the voice.
    "upbeat": {
        "root": 52, "bpm": 123,
        "chords": [(0, 4, 7), (-5, -1, 2), (-3, 0, 4), (-7, -3, 0)],
        "layers": ("pad", "plucks", "kick", "shaker", "bass"),
        "gain": -28.0,
    },
}  # fmt: skip


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a = max(1, int(attack * RATE))
    r = max(1, int(release * RATE))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, min(a, n))[: min(a, n)]
    if r < n:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def _saw(f: float, t: np.ndarray, phase: float = 0.0) -> np.ndarray:
    return 2 * ((f * t + phase) % 1.0) - 1


def _lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, cutoff, "low", fs=RATE, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def _highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, cutoff, "high", fs=RATE, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def _add(track: np.ndarray, part: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * RATE)
    if i >= len(track) or i < 0:
        return
    part = part if part.ndim == 2 else np.stack([part, part], axis=1)
    n = min(len(part), len(track) - i)
    track[i : i + n] += part[:n] * gain


def _db(x: float) -> float:
    return 10 ** (x / 20)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


@lru_cache(maxsize=4)
def _impulse(seconds: float, seed: int) -> np.ndarray:
    rnd = np.random.default_rng(seed)
    n = int(seconds * RATE)
    decay = np.exp(-6.0 * np.arange(n) / n)
    ir = rnd.standard_normal((n, 2)) * decay[:, None]
    ir = _lowpass(ir, 5000)
    ir[: int(0.012 * RATE)] *= np.linspace(0, 1, int(0.012 * RATE))[:, None]
    return ir / np.sqrt(np.sum(ir**2, axis=0))


def reverb(x: np.ndarray, wet: float, seconds: float = 2.4) -> np.ndarray:
    ir = _impulse(seconds, 3)
    out = np.stack([signal.fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in range(2)], axis=1)
    return x * (1 - wet) + out * wet


# ---- instruments (each returns a stereo array the length asked) -------------------------


def _pad(notes: list[float], seconds: float, rnd) -> np.ndarray:
    t = _t(seconds)
    out = np.zeros((len(t), 2))
    for m in notes:
        for c, cents in enumerate((-7, 7)):
            f = hz(m) * 2 ** (cents / 1200)
            out[:, c] += _saw(f, t, rnd.random()) + 0.5 * _saw(f * 2.003, t, rnd.random())
    out = _lowpass(out, 900, 2)
    return out * _env(len(t), 0.9, 1.0)[:, None] / max(1, len(notes))


def _bell(f: float, seconds: float = 2.5) -> np.ndarray:
    t = _t(seconds)
    x = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 0.9)
        + 0.45 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t / 0.35)
        + 0.25 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t / 0.18)
    )
    return x * _env(len(t), 0.004, 0.2)


def _piano(f: float, seconds: float = 2.8) -> np.ndarray:
    t = _t(seconds)
    x = sum(
        (1 / n**1.6)
        * np.sin(2 * np.pi * f * n * (1 + 0.0004 * n * n) * t)
        * np.exp(-t * (0.9 + 0.7 * n))
        for n in range(1, 7)
    )
    return x * _env(len(t), 0.003, 0.25)


def _pluck(f: float, seconds: float = 0.9) -> np.ndarray:
    t = _t(seconds)
    x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) + 0.12 * _saw(f, t)
    return _lowpass(x * np.exp(-t / 0.22), 3500) * _env(len(t), 0.003, 0.1)


def _marimba(f: float, seconds: float = 0.8) -> np.ndarray:
    t = _t(seconds)
    x = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.25) + 0.35 * np.sin(
        2 * np.pi * f * 4 * t
    ) * np.exp(-t / 0.05)
    return x * _env(len(t), 0.002, 0.1)


def _kick() -> np.ndarray:
    t = _t(0.45)
    f = 45 + 75 * np.exp(-t / 0.045)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t / 0.16)


def _noise_hit(rnd, seconds: float, cutoff: float, decay: float) -> np.ndarray:
    t = _t(seconds)
    return _highpass(rnd.standard_normal(len(t)), cutoff) * np.exp(-t / decay)


def _bass(f: float, seconds: float) -> np.ndarray:
    t = _t(seconds)
    x = np.sin(2 * np.pi * f * t) + 0.35 * _saw(f, t)
    return _lowpass(x, 320) * _env(len(t), 0.01, 0.12) * np.exp(-t / max(0.3, seconds))


def music(mood: str, seconds: float, seed: int = 1) -> np.ndarray:
    """Background music for `seconds`, stereo at RATE, quiet enough to sit under a voice."""
    spec = MOODS.get(mood, MOODS["curious"])
    rnd = np.random.default_rng(seed)
    beat = 60 / spec["bpm"]
    bar = beat * 4
    n = int((seconds + 3) * RATE)
    track = np.zeros((n, 2))
    root = spec["root"]
    layers = spec["layers"]
    bars = math.ceil((seconds + 1) / bar)
    for b in range(bars):
        at = b * bar
        chord = [root + s for s in spec["chords"][b % len(spec["chords"])]]
        if "pad" in layers:
            _add(track, _pad(chord, bar + 1.2, rnd), at, 0.35)
        if "drone" in layers:
            t = _t(bar + 0.5)
            drone = np.sin(2 * np.pi * hz(root - 12) * t) + 0.4 * np.sin(
                2 * np.pi * hz(root - 5) * t
            )
            _add(track, drone * _env(len(t), 0.5, 0.5), at, 0.22)
        if "bass" in layers:
            _add(track, _bass(hz(chord[0] - 12), bar * 0.9), at, 0.4)
        arp = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 12]
        if "bells" in layers:
            for k in (0, 2.5):
                note = arp[int(rnd.integers(0, 4))] + 12
                _add(track, _bell(hz(note)), at + k * beat + rnd.random() * 0.05, 0.16)
        if "piano" in layers:
            for k, note in enumerate([chord[0] - 12, *chord]):
                _add(track, _piano(hz(note)), at + k * 0.04, 0.22)
            _add(track, _piano(hz(arp[2] + 12)), at + 2 * beat, 0.14)
        if "plucks" in layers:
            for k in range(8):
                _add(track, _pluck(hz(arp[k % 4])), at + k * beat / 2, 0.16)
        if "marimba" in layers:
            for k in (0, 1, 1.5, 2.5, 3):
                _add(track, _marimba(hz(arp[int(k * 2) % 4] + 12)), at + k * beat, 0.18)
        if "pulse" in layers:
            for k in range(8):
                _add(track, _bass(hz(chord[0] - 12), beat / 2.2), at + k * beat / 2, 0.3)
        if "kick" in layers:
            for k in (0, 2):
                _add(track, _kick(), at + k * beat, 0.5)
        if "shaker" in layers:
            for k in range(8):
                _add(
                    track,
                    _noise_hit(rnd, 0.08, 7000, 0.02),
                    at + k * beat / 2,
                    0.05 + 0.03 * (k % 2),
                )
        if "hats" in layers:
            for k in range(16):
                _add(track, _noise_hit(rnd, 0.05, 8000, 0.012), at + k * beat / 4, 0.06)
    track = reverb(track, 0.32)[: int(seconds * RATE)]
    track *= _env(len(track), 1.2, 1.8)[:, None]
    return track * (_db(spec.get("gain", MUSIC_RMS_DB)) / max(_rms(track), 1e-9))


# ---- sound effects ---------------------------------------------------------------------


def sfx(name: str, seed: int = 5) -> np.ndarray:
    """One sound effect, peaking at full scale (cues give each its level)."""
    rnd = np.random.default_rng(seed)
    if name == "whoosh":
        t = _t(0.6)
        noise = rnd.standard_normal(len(t))
        low, high = _lowpass(noise, 600), _highpass(_lowpass(noise, 6000), 1800)
        sweep = np.sin(np.pi * np.clip(t / 0.6, 0, 1)) ** 2
        mono = (low * (1 - sweep) + high * sweep) * sweep
        pan = np.clip(t / 0.6, 0, 1)
        x = np.stack([mono * (1 - pan * 0.7), mono * (0.3 + pan * 0.7)], axis=1)
    elif name == "pop":
        t = _t(0.12)
        f = 320 + 700 * np.exp(-t / 0.02)
        x = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t / 0.035)
    elif name == "tick":
        t = _t(0.03)
        x = np.sin(2 * np.pi * 2100 * t) * np.exp(-t / 0.006) + 0.3 * _noise_hit(
            rnd, 0.03, 4000, 0.003
        )
    elif name == "ding":
        x = _bell(hz(88), 1.2) + 0.5 * _bell(hz(95), 1.2)
    elif name == "drop":
        t = _t(0.3)
        f = 80 + 260 * np.exp(-t / 0.03)
        x = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t / 0.09)
    elif name == "boom":
        t = _t(1.6)
        f = 32 + 40 * np.exp(-t / 0.08)
        sub = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t / 0.5)
        hit = _lowpass(rnd.standard_normal(len(t)), 260) * np.exp(-t / 0.08) * 0.8
        x = reverb(np.stack([sub + hit] * 2, axis=1), 0.25, 1.8)
    elif name == "riser":
        t = _t(1.3)
        ramp = (t / 1.3) ** 2.2
        f = 180 + 1100 * (t / 1.3) ** 2
        tone = np.sin(2 * np.pi * np.cumsum(f) / RATE) * 0.4
        x = (_highpass(rnd.standard_normal(len(t)), 1500) * 0.6 + tone) * ramp
    elif name == "shimmer":
        t = _t(1.2)
        x = np.zeros(len(t))
        for _ in range(26):
            at = rnd.random() * 0.8
            part = _bell(2000 + rnd.random() * 4500, 0.4) * 0.3
            i = int(at * RATE)
            x[i : i + len(part)] += part[: len(x) - i]
    elif name == "step":
        t = _t(0.12)
        thud = np.sin(2 * np.pi * 85 * t) * np.exp(-t / 0.03)
        scuff = _lowpass(rnd.standard_normal(len(t)), 900) * np.exp(-t / 0.02) * 0.6
        x = thud + scuff
    elif name == "chime":
        t = _t(1.8)
        x = np.zeros(len(t))
        for k, m in enumerate((88, 92, 95)):
            part = _bell(hz(m), 1.5)
            i = int(k * 0.09 * RATE)
            x[i : i + len(part)] += part[: len(x) - i]
    else:
        raise ValueError(f"no sound effect {name!r}")
    x = x if x.ndim == 2 else np.stack([x, x], axis=1)
    return x / max(float(np.max(np.abs(x))), 1e-9)


# ---- the mix ---------------------------------------------------------------------------


def read_wav(path: Path) -> np.ndarray:
    """A 16-bit or float WAV as stereo float at RATE."""
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
    else:
        raise ValueError(f"{path}: {width * 8}-bit WAV")
    data = data.reshape(-1, channels)
    if channels == 1:
        data = np.repeat(data, 2, axis=1)
    if rate != RATE:
        g = math.gcd(rate, RATE)
        data = signal.resample_poly(data, RATE // g, rate // g, axis=0)
    return data


def write_wav(path: Path, data: np.ndarray) -> Path:
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return path


def _follow(x: np.ndarray, attack: float = 0.03, release: float = 0.45) -> np.ndarray:
    """A smooth 0..1 envelope of `x` (mono), quick to rise and slow to fall."""
    level = np.abs(x)
    hop = 240
    blocks = level[: len(level) // hop * hop].reshape(-1, hop).max(axis=1)
    out = np.zeros_like(blocks)
    a, r = math.exp(-hop / (attack * RATE)), math.exp(-hop / (release * RATE))
    v = 0.0
    for i, b in enumerate(blocks):
        v = a * v + (1 - a) * b if b > v else r * v + (1 - r) * b
        out[i] = v
    env = np.repeat(out, hop)
    env = np.pad(env, (0, len(x) - len(env)), mode="edge") if len(env) < len(x) else env[: len(x)]
    peak = float(np.percentile(env, 95)) or 1.0
    return np.clip(env / peak, 0, 1)


def compress(
    x: np.ndarray,
    threshold_db: float = -24.0,
    ratio: float = 3.5,
    attack: float = 0.004,
    release: float = 0.12,
) -> np.ndarray:
    """Speech compression, as narration is mastered: what rises above the threshold rises
    `ratio` times less. Speech peaks sit far above its loudness; without this the mix
    can't reach the presets' loudness inside the true-peak ceiling."""  # fmt: skip
    hop = 120
    level = np.max(np.abs(x), axis=1)
    pad = (-len(level)) % hop
    blocks = np.pad(level, (0, pad)).reshape(-1, hop).max(axis=1)
    a, r = math.exp(-hop / (attack * RATE)), math.exp(-hop / (release * RATE))
    env = np.empty_like(blocks)
    v = 0.0
    for i, b in enumerate(blocks):
        v = a * v + (1 - a) * b if b > v else r * v + (1 - r) * b
        env[i] = v
    env_db = 20 * np.log10(np.maximum(env, 1e-6))
    over = np.maximum(env_db - threshold_db, 0.0)
    gain = 10 ** (-(over - over / ratio) / 20)
    per_sample = np.repeat(gain, hop)[: len(x)]
    return x * per_sample[:, None]


def mix(
    timeline: Timeline,
    voices: list[Path],
    mood: str,
    dest: Path,
    seed: int = 1,
    music_on: bool = True,
) -> Path:
    """The reel's finished sound at `dest` (WAV, RATE, stereo, at the presets' loudness)."""
    n = int(timeline.duration * RATE)
    voice = np.zeros((n, 2))
    for scene, path in zip(timeline.scenes, voices, strict=True):
        _add(voice, read_wav(path), scene.voice_start)
    voice = compress(voice)
    rms = _rms(voice[np.abs(voice[:, 0]) > 1e-4]) or 1.0
    voice *= _db(VOICE_RMS_DB) / rms
    out = voice.copy()
    if music_on:
        bed = music(mood, timeline.duration, seed)
        bed = np.pad(bed, ((0, max(0, n - len(bed))), (0, 0)))[:n]
        duck = 1 - DUCK * _follow(voice[:, 0])
        out += bed * duck[:, None]
    for cue in timeline.cues:
        _add(out, sfx(cue.sound, seed + int(cue.t * 10)), cue.t, _db(cue.gain_db))
    raw = dest.with_suffix(".raw.wav")
    write_wav(raw, out / max(1.0, float(np.max(np.abs(out)))))
    audio.normalize_loudness(raw, dest, LOUDNESS, TRUE_PEAK)
    raw.unlink(missing_ok=True)
    return dest
