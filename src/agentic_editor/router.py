"""ModelRouter: the one place that knows which model serves which job.

Swap models through .env (WHISPER_MODEL, LLM_MODEL, VISION_MODEL, SEPARATION_MODEL);
pipeline code only talks to this interface.
"""

import logging
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from agentic_editor import logs, slots
from agentic_editor.analysis import separation, transcribe
from agentic_editor.backends import Backends
from agentic_editor.llm import OllamaClient
from agentic_editor.settings import Settings
from agentic_editor.transcript import Transcript


def in_child_process[T](fn: Callable[..., T], *args) -> T:
    """Run fn in a fresh process so any GPU memory it grabs is freed when it exits."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=ctx,
        initializer=logs.setup,
        initargs=(logging.getLogger().level,),
    ) as pool:
        return pool.submit(fn, *args).result()


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        backends: Backends,
        progress: Callable[[str, str], None] | None = None,
    ):
        self.settings = settings
        self.backends = backends
        # Says so when a model has to wait for another job to finish with the GPU.
        self.progress = progress

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
        s = self.settings
        with slots.gpu.turn("transcribe", self.progress):
            if self.backends.transcriber == "mlx":
                return in_child_process(transcribe.with_mlx, audio, s.mlx_whisper_model, language)
            device = "cuda" if self.backends.transcriber == "faster-whisper" else "cpu"
            return in_child_process(
                transcribe.with_faster_whisper,
                audio,
                s.whisper_model,
                device,
                s.models_dir / "whisper",
                language,
            )

    def separate_vocals(self, audio: Path, out_dir: Path) -> separation.Stems:
        with slots.gpu.turn("separate the vocals", self.progress):
            return in_child_process(
                separation.run_separator,
                audio,
                out_dir,
                self.settings.separation_model,
                self.settings.models_dir / "separation",
                self.backends.accelerator != "cpu",
            )

    def planner(self) -> OllamaClient:
        """The planning model, holding the GPU while open."""
        return OllamaClient(
            self.settings.ollama_url,
            self.settings.llm_model,
            slot=slots.gpu,
            progress=self.progress,
        )

    def vision(self) -> OllamaClient:
        """The vision model, holding the GPU while open."""
        return OllamaClient(
            self.settings.ollama_url,
            self.settings.vision_model,
            slot=slots.gpu,
            progress=self.progress,
        )
