"""Instruments against vocals (presets.Split): one stretch of a song in two panels, the
first part heard whole in the top one, the rest as vocals only in the bottom one.

Where the sound changes is worked out from the reel, the singer's silences and the words
(switch_time, voice_pauses), once, so the picture and a soundtrack laid again later
change at the same moment. The picture is the reel's own, shown whole in the top panel's
rows: the graph cuts those rows out twice, holds each copy's frame while the other plays,
and lays both on the background (graph_parts). The labels are one frame-sized PNG drawn by Pillow
(labels_layer), pasted by the preview too.
"""

import itertools
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from agentic_editor import ffmpeg
from agentic_editor.edl import EDL
from agentic_editor.presets import Preset, Split
from agentic_editor.render import audio, text
from agentic_editor.transcript import Word

SNAP = 2.5  # seconds the change may move to land in a pause between sung words
EDGE = 1.0  # seconds each part keeps at least
SEAM = 0.04  # seconds each side fades over where the sound changes, so it doesn't click
# The singer is silent where the isolated voice is this far under its loudest (the 95th
# percentile of WINDOW-long stretches) for PAUSE seconds or more. Whisper's word timings
# run each sung word up to the next, and a held note has no gap at all: on four real songs
# the words' widest gap sat 0-13 dB under the voice's loudest, mid-note, while a true
# silence 40-70 dB under was within the snap.
SILENT = 25.0
WINDOW = 0.02
PAUSE = 0.12


def voice_pauses(vocals: Path, start: float, end: float) -> list[tuple[float, float]]:
    """Where the singer is silent in the stretch `start`-`end` of `vocals` (the isolated
    voice), in seconds from `start`."""
    rate = 16000
    samples = ffmpeg.read_audio(vocals, rate)[int(start * rate) : int(end * rate)]
    size = int(rate * WINDOW)
    count = len(samples) // size
    if count == 0:
        return []
    frames = samples[: count * size].reshape(count, size).astype(np.float64)
    level = 20 * np.log10(np.maximum(np.sqrt(np.mean(np.square(frames), axis=1)), 1e-9))
    quiet = level < np.percentile(level, 95) - SILENT
    found, begun = [], None
    for i, still in enumerate([*quiet, False]):
        if still and begun is None:
            begun = i
        elif not still and begun is not None:
            if (i - begun) * WINDOW >= PAUSE:
                found.append((round(begun * WINDOW, 3), round(i * WINDOW, 3)))
            begun = None
    return found


def switch_time(
    duration: float, split: Split, words: list[Word], pauses: list[tuple[float, float]] = ()
) -> float:
    """Seconds into the reel where the vocals take over: `split.switch` of the reel, moved
    to the middle of the widest of the singer's silences (`pauses`, see voice_pauses)
    within SNAP seconds of it, else of the widest gap between words there (the nearest
    when two are as wide), else left where it is."""
    if duration <= 2 * EDGE:
        return duration / 2
    wanted = split.switch * duration
    timed = sorted(words, key=lambda w: w.start)
    gaps = [(a.end, b.start) for a, b in itertools.pairwise(timed)]
    for spans in (pauses, gaps):
        best: tuple[float, float, float] | None = None  # (-width, distance, middle)
        for start, end in spans:
            width, middle = end - start, (start + end) / 2
            if width <= 0 or abs(middle - wanted) > SNAP:
                continue
            key = (-round(width, 2), abs(middle - wanted), middle)
            best = min(best, key) if best else key
        if best:
            return min(max(best[2], EDGE), duration - EDGE)
    return min(max(wanted, EDGE), duration - EDGE)


def _label(split: Split, words: str, fonts_dir: Path) -> Image.Image:
    font = text.font_at(text.font_file(fonts_dir, split.font), split.size)
    stroke = round(split.outline)
    left, top, right, bottom = font.getbbox(words, stroke_width=stroke)
    image = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (-left, -top), words, font=font, fill=split.color,
        stroke_width=stroke, stroke_fill=split.outline_color,
    )  # fmt: skip
    return image


def labels_image(split: Split, frame: tuple[int, int], fonts_dir: Path) -> Image.Image:
    """Both panels' labels on a clear frame-sized picture, each at label_x, label_y from
    its panel's top left corner."""
    full = Image.new("RGBA", frame, (0, 0, 0, 0))
    for words, top in ((split.first_label, split.top), (split.second_label, split.rows[1])):
        if words.strip():
            where = (split.label_x, top + split.label_y)
            full.alpha_composite(_label(split, words, fonts_dir), where)
    return full


def labels_layer(split: Split, frame: tuple[int, int], fonts_dir: Path, dest: Path) -> Path:
    labels_image(split, frame, fonts_dir).save(dest)
    return dest


def graph_parts(
    base: str, under: str, split: Split, frame: tuple[int, int], at: float, duration: float
) -> list[str]:
    """Filters that cut the top panel's rows out of `base` twice and lay them on `under`
    (the background, frame-sized): the top copy plays until `at` and then holds its last
    frame, the bottom one holds its first frame until `at` and then plays. The result is
    labelled "split"."""
    width = frame[0]
    top, bottom = split.rows
    height = bottom - top
    crop = f"crop={width}:{height}:0:{top}"
    rest = max(duration - at, 0.0)
    return [
        f"[{base}]split=2[splita][splitb]",
        f"[splita]{crop},trim=end={at:.3f},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={rest:.3f}[panela]",
        f"[splitb]{crop},trim=start={at:.3f},setpts=PTS-STARTPTS,"
        f"tpad=start_mode=clone:start_duration={at:.3f}[panelb]",
        f"[{under}][panela]overlay=0:{top}:shortest=1[splitone]",
        f"[splitone][panelb]overlay=0:{bottom}:shortest=1[split]",
    ]


def soundtrack(
    edl: EDL, preset: Preset, mix: Path, vocals: Path, at: float, work_dir: Path
) -> Path:
    """The whole sound until `at`, the vocals after, each brought to the preset's loudness
    on its own (the vocals alone measure quieter than the song) and joined with a short
    fade either side of the change."""
    clip = edl.clips[0]  # one continuous stretch (see trim.py)
    sound = preset.audio
    parts = []
    for name, source, start, length, fade_in, fade_out in (
        ("whole", mix, clip.start, at, sound.fade_in, SEAM),
        ("vocals", vocals, clip.start + at, clip.duration - at, SEAM, sound.fade_out),
    ):
        fade_in, fade_out = min(fade_in, length / 4), min(fade_out, length / 4)
        chain = [f"aformat=sample_rates={audio.SAMPLE_RATE}:channel_layouts=stereo"]
        if sound.highpass_hz:
            chain.append(f"highpass=f={sound.highpass_hz}:poles=2")
        if fade_in:
            chain.append(f"afade=t=in:d={fade_in:.3f}")
        if fade_out:
            chain.append(f"afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f}")
        raw = work_dir / f"split-{name}-raw.wav"
        ffmpeg.run(["-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source),
                    "-af", ",".join(chain), "-c:a", "pcm_s24le", "-ar", str(audio.SAMPLE_RATE),
                    str(raw)])  # fmt: skip
        even = work_dir / f"split-{name}.wav"
        audio.normalize_loudness(raw, even, sound.loudness_lufs, sound.true_peak_db)
        parts.append(even)
    final = work_dir / "soundtrack.wav"
    ffmpeg.run(["-y", "-i", str(parts[0]), "-i", str(parts[1]), "-filter_complex",
                "[0:a][1:a]concat=n=2:v=0:a=1[out]", "-map", "[out]",
                "-c:a", "pcm_s24le", "-ar", str(audio.SAMPLE_RATE), str(final)])  # fmt: skip
    return final
