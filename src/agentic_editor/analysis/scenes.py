from pathlib import Path


def detect_cuts(video: Path, threshold: float = 27.0) -> list[float]:
    """Times in seconds where PySceneDetect finds a hard cut."""
    from scenedetect import ContentDetector, detect

    scenes = detect(str(video), ContentDetector(threshold=threshold), show_progress=False)
    return [round(start.get_seconds(), 3) for start, _ in scenes[1:]]
