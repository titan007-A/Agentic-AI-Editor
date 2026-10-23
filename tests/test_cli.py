from agentic_editor import cli, pipeline
from agentic_editor.backends import Backends
from agentic_editor.edl import EDL, Clip


def test_a_video_that_fails_doesnt_stop_the_rest_of_the_batch(monkeypatch, tmp_path, capsys):
    edited = []

    def run(source, presets, settings, backends, notes):
        edited.append(source)
        if source == "broken.mp4":
            raise FileNotFoundError("broken.mp4")
        edl = EDL(clips=[Clip(start=1, end=21)])
        return pipeline.Result(
            "A talk", [pipeline.Output("square-1x1", tmp_path / "a.mp4", edl, "m")]
        )

    monkeypatch.setattr(cli, "detect", lambda settings: Backends("cpu", "cpu", "x264", "h264"))
    monkeypatch.setattr(cli.pipeline, "run", run)
    assert cli.main(["run", "broken.mp4", "talk.mp4", "-p", "square-1x1"]) == 1
    assert edited == ["broken.mp4", "talk.mp4"]
    printed = capsys.readouterr().out
    assert "broken.mp4: failed: FileNotFoundError" in printed and "ready to post" in printed
