import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=FORMAT, datefmt="%H:%M:%S")
    # One line per request to Ollama drowns out the pipeline's own progress.
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))
