import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    # Verbose means this app's debug lines: numba, httpcore and PIL at debug level bury them
    # under megabytes of their own.
    logging.basicConfig(level=max(level, logging.INFO), format=FORMAT, datefmt="%H:%M:%S")
    logging.getLogger("agentic_editor").setLevel(level)
    # One line per request to Ollama drowns out the pipeline's own progress.
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))
