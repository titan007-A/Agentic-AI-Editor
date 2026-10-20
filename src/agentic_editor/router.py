"""ModelRouter: the one place that knows which model serves which job.

Swap models through .env (WHISPER_MODEL, LLM_MODEL, VISION_MODEL, SEPARATION_MODEL);
pipeline code only talks to this interface.
"""

import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TypeVar

from agentic_editor.analysis import separation, transcribe
from agentic_editor.backends import Backends
from agentic_editor.llm import OllamaClient
from agentic_editor.settings import Settings
from agentic_editor.transcript import Transcript

T = TypeVar("T")


def in_child_process[T](fn: Callable[..., T], *args) -> T:
    """Run fn in a fresh process so any GPU memory it grabs is freed when it exits."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
        return pool.submit(fn, *args).result()


class ModelRouter:
    def __init__(self, settings: Settings, backends: Backends):
        self.settings = settings
        self.backends = backends

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
        s = self.settings
        if self.backends.transcriber == "mlx":
            return in_child_process(transcribe.with_mlx, audio, s.mlx_whisper_model, language)
        device = "cuda" if self.backends.transcriber == "faster-whisper" else "cpu"
        return in_child_process(
            transcribe.with_faster_whisper,
            audio, s.whisper_model, device, s.models_dir / "whisper", language,
        )  # fmt: skip

    def separate_vocals(self, audio: Path, out_dir: Path) -> separation.Stems:
        return in_child_process(
            separation.run_separator,
            audio, out_dir, self.settings.separation_model, self.settings.models_dir / "separation",
        )  # fmt: skip

    def planner(self) -> OllamaClient:
        return OllamaClient(self.settings.ollama_url, self.settings.llm_model)

    def vision(self) -> OllamaClient:
        return OllamaClient(self.settings.ollama_url, self.settings.vision_model)
