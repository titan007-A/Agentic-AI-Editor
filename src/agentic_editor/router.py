"""ModelRouter: the one place that knows which model serves which job.

Swap models through .env (WHISPER_MODEL, LLM_MODEL, VISION_MODEL, SEPARATION_MODEL);
pipeline code only talks to this interface.
"""

import ctypes
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from agentic_editor import logs, slots
from agentic_editor.analysis import faces, separation, transcribe
from agentic_editor.backends import Backends
from agentic_editor.llm import OllamaClient
from agentic_editor.settings import Settings
from agentic_editor.transcript import Transcript

PR_SET_PDEATHSIG = 1
# Four frames are about 3,100 tokens; Ollama's default of 4,096 left the answer ~1,000 and
# cut it off. A reply past VISION_REPLY tokens is a model repeating itself.
VISION_CONTEXT = 8192
VISION_REPLY = 2048


def exit_with(parent: int, every: float = 1.0) -> None:
    """End this process soon after `parent` is gone, for systems without a parent-death
    signal (macOS): an orphan is handed to another parent, so its parent id changes."""

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(every)
        os._exit(1)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()


def _start_child(level: int) -> None:
    logs.setup(level)
    # Killed with its parent: a worker left behind keeps its model in GPU memory without
    # holding the GPU's turn, and the next job runs out of memory.
    if sys.platform == "linux":
        parent = os.getppid()
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        if os.getppid() != parent:
            os._exit(1)  # the parent was already gone before the line above took effect
    else:
        exit_with(os.getppid())


def in_child_process[T](fn: Callable[..., T], *args) -> T:
    """Run fn in a fresh process so any GPU memory it grabs is freed when it exits."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=ctx,
        initializer=_start_child,
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

    def fetch_models(self) -> None:
        """Download every model a run loads here, so the first run doesn't stop for them.
        The Ollama models come from `ollama pull`."""
        s = self.settings
        if self.backends.transcriber == "mlx":
            transcribe.fetch_mlx(s.mlx_whisper_model)
        else:
            transcribe.fetch_faster_whisper(s.whisper_model, s.models_dir / "whisper")
        separation.fetch_model(s.separation_model, s.models_dir / "separation")
        faces.model_file(s.models_dir)

    def planner(self) -> OllamaClient:
        """The planning model, holding the GPU while open."""
        return OllamaClient(
            self.settings.ollama_url,
            self.settings.llm_model,
            slot=slots.gpu,
            progress=self.progress,
            gpu_layers=self.settings.llm_gpu_layers,
        )

    def vision(self) -> OllamaClient:
        """The vision model, holding the GPU while open. Its calls carry several frames, more
        than Ollama's default context holds with the answer: the answer was cut off."""
        return OllamaClient(
            self.settings.ollama_url,
            self.settings.vision_model,
            slot=slots.gpu,
            progress=self.progress,
            gpu_layers=self.settings.llm_gpu_layers,
            context=VISION_CONTEXT,
            max_reply=VISION_REPLY,
        )
