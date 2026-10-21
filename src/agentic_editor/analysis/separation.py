"""Vocal isolation with an MDX-Net model, plus the checks that decide whether to trust it."""

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agentic_editor import ffmpeg
from agentic_editor.analysis.audio import levels

log = logging.getLogger(__name__)

# Calibrated on real songs with the original tool: full mixes leave the instrumental
# stem 1-4 dB RMS below the source, already-clean vocals leave it 27-45 dB below.
INSTRUMENT_FREE_RMS_MARGIN_DB = 20.0
INSTRUMENT_FREE_PEAK_MARGIN_DB = 12.0
MIN_FINGERPRINT_CORRELATION = 0.20
MAX_VOLUME_DROP_DB = 30.0


class SeparationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class Stems:
    vocals: Path
    instrumental: Path


@dataclass(frozen=True)
class VocalTrack:
    path: Path
    instrument_free: bool


def run_separator(
    audio: Path, out_dir: Path, model: str, model_dir: Path, require_gpu: bool
) -> Stems:
    """Run audio-separator on one file. Meant to run in a child process."""
    from audio_separator.separator import Separator

    started = time.monotonic()
    separator = Separator(
        output_dir=str(out_dir),
        model_file_dir=str(model_dir),
        output_format="WAV",
        # Denoise runs each chunk twice, once phase-inverted, and averages: the model's own
        # artifacts don't correlate between the passes and mostly cancel. High overlap
        # hides the seams between chunks. Both cost time, neither changes what is removed.
        mdx_params={
            "hop_length": 1024,
            "segment_size": 256,
            "overlap": 0.75,
            "batch_size": 1,
            "enable_denoise": True,
        },
    )
    separator.load_model(model_filename=model)
    if require_gpu:
        _check_provider(model_dir / model, separator.onnx_execution_provider)
    outputs = [out_dir / Path(name).name for name in separator.separate(str(audio))]
    vocals = next(p for p in outputs if "(Vocals)" in p.name)
    instrumental = next(p for p in outputs if p != vocals)
    log.info("separated %s in %.1fs", audio.name, time.monotonic() - started)
    return Stems(vocals=vocals, instrumental=instrumental)


def _check_provider(model_path: Path, providers: list[str] | None) -> None:
    """audio-separator only logs a warning when ONNX Runtime can't load its GPU provider
    and then runs on the CPU, many times slower. Fail instead."""
    import onnxruntime

    wanted = (providers or ["CPUExecutionProvider"])[0]
    if wanted == "CPUExecutionProvider":
        raise SeparationFailed("audio-separator found no GPU and would run on the CPU")
    session = onnxruntime.InferenceSession(str(model_path), providers=providers)
    if wanted not in session.get_providers():
        raise SeparationFailed(
            f"ONNX Runtime could not start {wanted}; check that its CUDA libraries match"
        )
    log.info("vocal separation running on %s", wanted)


def is_instrument_free(source: np.ndarray, instrumental: np.ndarray) -> bool:
    """True when the model found essentially nothing but voice in the source."""
    src_rms, src_peak = levels(source)
    inst_rms, inst_peak = levels(instrumental)
    rms_margin, peak_margin = src_rms - inst_rms, src_peak - inst_peak
    log.info(
        "instrumental stem sits %.1f dB RMS / %.1f dB peak below the source",
        rms_margin,
        peak_margin,
    )
    return (
        rms_margin >= INSTRUMENT_FREE_RMS_MARGIN_DB
        and peak_margin >= INSTRUMENT_FREE_PEAK_MARGIN_DB
    )


def fingerprint_correlation(source: np.ndarray, vocals: np.ndarray, sample_rate: int) -> float:
    """Pearson correlation of MFCCs 1-12, which ignores overall loudness."""
    import librosa

    n = min(len(source), len(vocals), sample_rate * 15)
    if n == 0:
        return 0.0
    # Coefficient 0 is frame energy; leaving it in lets two unrelated tracks with
    # similar loudness envelopes look alike.
    a = librosa.feature.mfcc(y=source[:n], sr=sample_rate, n_mfcc=13)[1:].ravel()
    b = librosa.feature.mfcc(y=vocals[:n], sr=sample_rate, n_mfcc=13)[1:].ravel()
    return float(np.corrcoef(a, b)[0, 1])


def check_vocals(source: np.ndarray, vocals: np.ndarray, sample_rate: int) -> None:
    src_peak = levels(source)[1]
    drop = src_peak - levels(vocals)[1]
    if src_peak > -50 and drop > MAX_VOLUME_DROP_DB:
        raise SeparationFailed(f"isolated vocals are {drop:.1f} dB quieter than the source")
    correlation = fingerprint_correlation(source, vocals, sample_rate)
    log.info("vocal fingerprint correlation %.3f", correlation)
    if correlation < MIN_FINGERPRINT_CORRELATION:
        raise SeparationFailed(f"vocals don't match the source (correlation {correlation:.2f})")


def accept_stems(source_wav: Path, stems: Stems, dest: Path) -> VocalTrack:
    """Decide which audio to keep and put it at `dest`."""
    rate = 22050
    source = ffmpeg.read_audio(source_wav, rate)
    if is_instrument_free(source, ffmpeg.read_audio(stems.instrumental, rate)):
        log.info("source has no instruments to remove, keeping the original audio")
        shutil.copyfile(source_wav, dest)
        stems.vocals.unlink(missing_ok=True)
        stems.instrumental.unlink(missing_ok=True)
        return VocalTrack(dest, instrument_free=True)

    check_vocals(source, ffmpeg.read_audio(stems.vocals, rate), rate)
    stems.vocals.replace(dest)
    stems.instrumental.unlink(missing_ok=True)
    return VocalTrack(dest, instrument_free=False)
