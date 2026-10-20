"""Runs the real pipeline. Opt in with `pytest -m e2e`; needs the ML extras, ffmpeg and
network access, and uses the GPU when one is available.

E2E_SOURCE overrides the sample video (URL or local path)."""

import os

import pytest

from agentic_editor import ffmpeg, pipeline
from agentic_editor.backends import detect
from agentic_editor.presets import load_presets
from agentic_editor.render.audio import integrated_loudness
from agentic_editor.settings import get_settings

# "Breathe" by Rockbitch, CC BY 3.0, via Wikimedia Commons.
SAMPLE = "https://commons.wikimedia.org/wiki/File:Breathe_by_Rockbitch_(circa_2000).webm"

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("preset_name", ["vocal-music-9x16"])
def test_pipeline_renders_a_video_that_follows_the_preset(preset_name):
    settings = get_settings()
    preset = load_presets(settings.presets_dir)[preset_name]
    result = pipeline.run(
        os.environ.get("E2E_SOURCE", SAMPLE), [preset], settings, detect(settings)
    )
    output = result.outputs[0]

    info = ffmpeg.probe(output.path)
    assert (info.width, info.height) == preset.size
    assert preset.min_duration - 0.5 <= info.duration <= preset.max_duration + 0.5
    assert abs(integrated_loudness(output.path) - preset.audio.loudness_lufs) <= 1.0
    assert output.issues == []
