import os

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


def test_the_server_keeps_a_mac_awake_while_it_runs(monkeypatch):
    import types

    import uvicorn

    from agentic_editor import api, backends, cli

    started, served = [], []
    settings = types.SimpleNamespace(password_hash="h", secret_key="k", host="0.0.0.0", port=8000)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(backends, "get_backends", lambda: None)
    monkeypatch.setattr(api, "create_app", lambda s: "app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(app))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda cmd: started.append(cmd))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.main(["serve"]) == 0
    assert started == [["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())]] and served == ["app"]

    started.clear()
    monkeypatch.setattr(cli.sys, "platform", "linux")
    cli.main(["serve"])
    assert started == []
