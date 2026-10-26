from pathlib import Path

GREY = 6.0  # mean distance of a pixel from its own grey under which a picture is black and white
LAYOUT_FRAMES = 12  # frames the picture's colour is read from


def detect_cuts(video: Path, threshold: float = 27.0) -> list[float]:
    """Times in seconds where PySceneDetect finds a hard cut.

    Its score averages the change in hue, saturation and brightness. A black-and-white
    video has no hue or saturation to change, so a cut scores a third of what it should
    and none are found; brightness alone is judged for those."""
    from scenedetect import ContentDetector, detect

    detector = ContentDetector(threshold=threshold, luma_only=is_grey(video))
    scenes = detect(str(video), detector, show_progress=False)
    return [round(start.get_seconds(), 3) for start, _ in scenes[1:]]


def is_grey(video: Path) -> bool:
    """Whether the picture is black and white. A repost's canvas is left out, or a colour
    picture on a large black one would count as grey."""
    from agentic_editor import ffmpeg
    from agentic_editor.analysis import layout
    from agentic_editor.settings import get_settings

    info = ffmpeg.probe(video)
    small = (108, max(2, round(108 * info.height / info.width / 2) * 2))
    frames = layout.sample(video, info.duration, LAYOUT_FRAMES, small, get_settings().ffmpeg_bin)
    if len(frames) == 0:
        return False
    x0, y0, x1, y1 = layout.picture_box(frames, layout.background(frames))
    rows = slice(round(y0 * small[1]), max(round(y1 * small[1]), 1))
    cols = slice(round(x0 * small[0]), max(round(x1 * small[0]), 1))
    return layout.saturation(frames[:, rows, cols]) < GREY
