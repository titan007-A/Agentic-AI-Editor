import pytest

from agentic_editor import download, pipeline
from agentic_editor.backends import Backends
from agentic_editor.settings import Settings


class Stop(Exception):
    pass


@pytest.fixture
def started(tmp_path, monkeypatch):
    """Runs pipeline.run up to the point where it has made its output folder."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"not really a video")
    source = download.Source(video, "A talk")
    monkeypatch.setattr(pipeline, "resolve_source", lambda ref, settings: source)

    def stop(*args):
        raise Stop

    monkeypatch.setattr(pipeline, "ModelRouter", stop)
    settings = Settings(media_dir=tmp_path / "media", cache_dir=tmp_path / "cache")
    backends = Backends("cpu", "cpu", "x264", "h264")
    events = []

    def progress(stage, message):
        events.append((stage, message))

    def run(run_id=None):
        with pytest.raises(Stop):
            pipeline.run("talk.mp4", [], settings, backends, None, progress, run_id)

    run.outputs, run.events = settings.media_dir / "outputs", events
    return run


def test_each_run_keeps_its_outputs_in_a_folder_of_its_own(started):
    for run_id in ("job1", None, None):
        started(run_id)
    (video,) = started.outputs.iterdir()
    runs = {p.name for p in video.iterdir()}
    assert len(runs) == 3 and "job1" in runs
