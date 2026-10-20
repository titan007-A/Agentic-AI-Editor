import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=FORMAT, datefmt="%H:%M:%S")
