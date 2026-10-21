"""Builds the finished soundtrack: cut, fade, music under speech, then loudness."""

import json
import re
from pathlib import Path

from agentic_editor import ffmpeg
from agentic_editor.edl import EDL
from agentic_editor.presets import Preset

SAMPLE_RATE = 48000
_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def cut_graph(
    edl: EDL, preset: Preset, first_input: int, music_input: int | None
) -> tuple[str, str]:
    """Filtergraph that cuts the clips out of the timeline audio and mixes music.

    Each clip is its own input (seeked with -ss/-t), numbered from first_input.
    Returns (graph, output label)."""
    parts = []
    labels = []
    for i, clip in enumerate(edl.clips):
        fade_in = min(preset.audio.fade_in, clip.duration / 4)
        fade_out = min(preset.audio.fade_out, clip.duration / 4)
        chain = [f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"]
        if fade_in:
            chain.append(f"afade=t=in:d={fade_in:.3f}")
        if fade_out:
            chain.append(f"afade=t=out:st={clip.duration - fade_out:.3f}:d={fade_out:.3f}")
        parts.append(f"[{first_input + i}:a]{','.join(chain)}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[cut]")
    out = "cut"

    if preset.audio.highpass_hz:
        parts.append(f"[{out}]highpass=f={preset.audio.highpass_hz}:poles=2[hp]")
        out = "hp"

    if music_input is not None:
        music = (
            f"[{music_input}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
            f"volume={preset.music.volume_db}dB[music]"
        )
        parts.append(music)
        if preset.music.ducking:
            parts.append(f"[{out}]asplit=2[voice][key]")
            parts.append(
                "[music][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked]"
            )
            parts.append("[voice][ducked]amix=inputs=2:duration=first:normalize=0[mixed]")
        else:
            parts.append(f"[{out}][music]amix=inputs=2:duration=first:normalize=0[mixed]")
        out = "mixed"
    return ";".join(parts), out


def render_soundtrack(
    edl: EDL, preset: Preset, timeline_audio: Path, music: Path | None, work_dir: Path
) -> Path:
    inputs = []
    for clip in edl.clips:
        inputs += [
            "-ss",
            f"{clip.start:.3f}",
            "-t",
            f"{clip.duration:.3f}",
            "-i",
            str(timeline_audio),
        ]
    music_input = None
    if music is not None:
        music_input = len(edl.clips)
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    graph, out = cut_graph(edl, preset, 0, music_input)

    mixed = work_dir / "soundtrack-mix.wav"
    ffmpeg.run(["-y", *inputs, "-filter_complex", graph, "-map", f"[{out}]",
                "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), str(mixed)])  # fmt: skip

    final = work_dir / "soundtrack.wav"
    normalize_loudness(mixed, final, preset.audio.loudness_lufs, preset.audio.true_peak_db)
    return final


def normalize_loudness(src: Path, dest: Path, target: float, true_peak: float) -> None:
    """Two-pass loudnorm. With measured values and linear=true the second pass applies
    one constant gain, so the performance's own dynamics are left alone."""
    base = f"loudnorm=I={target}:TP={true_peak}:LRA=11"
    result = ffmpeg.run(["-i", str(src), "-af", f"{base}:print_format=json", "-f", "null", "-"])
    measured = parse_loudnorm(result.stderr)
    second = (
        f"{base}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
    ffmpeg.run(["-y", "-i", str(src), "-af", second, "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s24le", str(dest)])  # fmt: skip


def parse_loudnorm(log: str) -> dict:
    matches = _LOUDNORM_JSON.findall(log)
    if not matches:
        raise ffmpeg.FFmpegError("loudnorm did not print its measurements")
    return json.loads(matches[-1])


def integrated_loudness(path: Path) -> float:
    result = ffmpeg.run(["-i", str(path), "-vn", "-af", "loudnorm=print_format=json",
                         "-f", "null", "-"])  # fmt: skip
    return float(parse_loudnorm(result.stderr)["input_i"])
