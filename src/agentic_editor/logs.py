import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger(__name__)


def setup(level: int = logging.INFO) -> None:
    # Verbose means this app's debug lines: numba, httpcore and PIL at debug level bury them
    # under megabytes of their own.
    logging.basicConfig(level=max(level, logging.INFO), format=FORMAT, datefmt="%H:%M:%S")
    logging.getLogger("agentic_editor").setLevel(level)
    # One line per request to Ollama drowns out the pipeline's own progress.
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))


def _bytes_in(folder: Path) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            path = Path(root) / name
            try:
                if not path.is_symlink():  # the Hugging Face cache links to its blobs
                    total += path.stat().st_size
            except OSError:
                pass  # renamed or removed by the download meanwhile
    return total


@contextmanager
def download_progress(what: str, folder: Path, every: float = 10.0) -> Iterator[None]:
    """Log how much of a model download has arrived in `folder` every `every` seconds:
    the libraries that download models show their progress, if at all, as terminal bars
    that never reach a log."""
    folder.mkdir(parents=True, exist_ok=True)
    before, started, stop = _bytes_in(folder), time.monotonic(), threading.Event()

    def report() -> None:
        while not stop.wait(every):
            log.info("downloading %s: %.2f GB so far", what, (_bytes_in(folder) - before) / 1e9)

    log.info("downloading %s into %s; this happens once", what, folder)
    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        yield
    finally:
        stop.set()
        reporter.join()
    got = (_bytes_in(folder) - before) / 1e9
    log.info("downloaded %s (%.2f GB) in %.0fs", what, got, time.monotonic() - started)
