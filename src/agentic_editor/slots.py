"""The GPU and the encoder, each used by one job at a time.

Every model here fits on the card only on its own, and two libx264 encodes at once just
share the CPU. So jobs run side by side and take turns: while one encodes, the next
transcribes, separates and plans on the GPU. A job hands the GPU back for its encode,
unloading its model first, and never holds one slot while waiting for the other, so two
jobs can't wait on each other. The GPU and encoder are also file locks in the system's
temp folder, so every run on the machine takes turns, whatever its settings.
"""

import fcntl
import logging
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]


def _log(stage: str, message: str) -> None:
    log.info("%s: %s", stage, message)


class Slot:
    """A lock one thread holds at a time, which the holding thread may take again, and
    which other processes see through `lock_file`."""

    def __init__(self, name: str, lock_file: Callable[[], Path] | None = None):
        self.name = name
        self._lock = threading.Lock()
        self._owner: int | None = None
        self._depth = 0
        self._lock_file = lock_file
        self._file: TextIO | None = None

    def held(self) -> int:
        """How many times the calling thread holds the slot."""
        return self._depth if self._owner == threading.get_ident() else 0

    def acquire(self, why: str = "", times: int = 1, progress: Progress | None = None) -> None:
        """Take the slot. If another job has it, say so through `progress` ("wait"), and
        again once it's ours ("resume"), so a job's stage doesn't read "wait" all through
        the work it waited for. Without `progress` the notes go to the log."""
        if self.held():
            self._depth += times
            return
        progress = progress or _log
        started = time.monotonic()
        waited = False

        def wait() -> None:
            nonlocal waited
            if not waited:
                progress("wait", f"waiting for the {self.name}" + (f" to {why}" if why else ""))
                waited = True

        if not self._lock.acquire(blocking=False):
            wait()
            self._lock.acquire()
        if self._lock_file is not None:
            try:
                path = self._lock_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._file = path.open("a")
                try:
                    fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    wait()
                    fcntl.flock(self._file, fcntl.LOCK_EX)
            except BaseException:
                if self._file is not None:
                    self._file.close()
                    self._file = None
                self._lock.release()
                raise
        self._owner, self._depth = threading.get_ident(), times
        if waited:
            try:
                progress("resume", f"got the {self.name} after {time.monotonic() - started:.0f}s")
            except BaseException:
                # Nothing would ever release it, and every other job would wait for good.
                self._depth = 1
                self.release()
                raise

    def release(self) -> None:
        if not self.held():
            raise RuntimeError(f"the {self.name} isn't held by this thread")
        self._depth -= 1
        if not self._depth:
            if self._file is not None:
                fcntl.flock(self._file, fcntl.LOCK_UN)
                self._file.close()
                self._file = None
            self._owner = None
            self._lock.release()

    @contextmanager
    def turn(self, why: str = "", progress: Progress | None = None) -> Iterator[None]:
        self.acquire(why, progress=progress)
        try:
            yield
        finally:
            self.release()


def _lock_file(name: str) -> Callable[[], Path]:
    return lambda: Path(tempfile.gettempdir()) / "agentic-editor" / f"{name}.lock"


gpu = Slot("GPU", _lock_file("gpu"))
encoder = Slot("encoder", _lock_file("encoder"))


_videos: dict[Path, Slot] = {}
_videos_lock = threading.Lock()


def video(work_dir: Path) -> Slot:
    """The slot for one video, by its folder: the cache folder while it is edited, the
    download folder while it downloads."""
    with _videos_lock:
        if work_dir not in _videos:
            _videos[work_dir] = Slot(f"video {work_dir.name}", lambda: work_dir / "busy.lock")
        return _videos[work_dir]


@contextmanager
def encoding(*models, progress: Progress | None = None) -> Iterator[None]:
    """Encode with the GPU given back meanwhile, for another job's models. The caller's
    models are unloaded first and load again on their next use."""
    depth = gpu.held()
    if depth:
        for model in models:
            if model is not None:
                model.unload()
        for _ in range(depth):
            gpu.release()
    try:
        with encoder.turn("encode", progress):
            yield
    finally:
        if depth:
            gpu.acquire("check the video", times=depth, progress=progress)
