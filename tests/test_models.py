import logging
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from agentic_editor import cli, logs
from agentic_editor.analysis import faces, separation, transcribe
from agentic_editor.backends import Backends
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings


def fake_module(monkeypatch, name: str, **attributes) -> None:
    """The ML packages are optional, so tests stand in for them."""
    parent = name.rpartition(".")[0]
    if parent:
        monkeypatch.setitem(sys.modules, parent, ModuleType(parent))
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_whisper_is_downloaded_once_and_says_so(tmp_path, monkeypatch, caplog):
    asked = []

    def download_model(model, local_files_only=False, cache_dir=None):
        asked.append(local_files_only)
        folder = Path(cache_dir) / "faster-whisper-large-v3-turbo"
        if local_files_only and not folder.exists():
            raise FileNotFoundError(model)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.bin").write_bytes(b"weights")
        return str(folder)

    fake_module(monkeypatch, "faster_whisper.utils", download_model=download_model)
    with caplog.at_level(logging.INFO):
        first = transcribe.fetch_faster_whisper("large-v3-turbo", tmp_path)
        again = transcribe.fetch_faster_whisper("large-v3-turbo", tmp_path)
    assert first == again and asked == [True, False, True]
    assert caplog.text.count("downloading whisper large-v3-turbo into") == 1
    assert "downloaded whisper large-v3-turbo" in caplog.text


def test_a_long_download_logs_how_much_has_arrived(tmp_path, caplog):
    with caplog.at_level(logging.INFO), logs.download_progress("a model", tmp_path, every=0.05):
        (tmp_path / "model.bin").write_bytes(b"x" * 20_000_000)
        time.sleep(0.3)
    assert "downloading a model: 0.02 GB so far" in caplog.text
    assert "downloaded a model (0.02 GB)" in caplog.text


def test_the_separation_model_is_only_downloaded_never_loaded(tmp_path, monkeypatch):
    made = []

    class Separator:
        def __init__(self, **options):
            made.append(options)
            self.folder = Path(options["model_file_dir"])

        def download_model_files(self, model):
            (self.folder / model).write_bytes(b"weights")

    fake_module(monkeypatch, "audio_separator.separator", Separator=Separator)
    separation.fetch_model("vocals.ckpt", tmp_path)
    separation.fetch_model("vocals.ckpt", tmp_path)
    assert made == [{"model_file_dir": str(tmp_path), "info_only": True}]
    assert (tmp_path / "vocals.ckpt").exists()


def test_a_face_model_download_that_breaks_off_is_not_kept(tmp_path, monkeypatch):
    def broken(url, path):
        Path(path).write_bytes(b"half a mod")
        raise OSError("connection reset")

    monkeypatch.setattr(faces.urllib.request, "urlretrieve", broken)
    with pytest.raises(OSError):
        faces.model_file(tmp_path)
    assert not (tmp_path / "mediapipe" / "blaze_face_short_range.tflite").exists()


@pytest.mark.parametrize("transcriber", ["faster-whisper", "mlx"])
def test_fetch_models_gets_every_model_a_run_loads(tmp_path, monkeypatch, transcriber):
    got = []
    monkeypatch.setattr(transcribe, "fetch_faster_whisper", lambda m, d: got.append(("whisper", d)))
    monkeypatch.setattr(transcribe, "fetch_mlx", lambda m: got.append(("mlx", m)))
    monkeypatch.setattr(separation, "fetch_model", lambda m, d: got.append(("separation", d)))
    monkeypatch.setattr(faces, "model_file", lambda d: got.append(("faces", d)))
    settings = Settings(models_dir=tmp_path)
    ModelRouter(settings, Backends("cuda", transcriber, "x264", "h264")).fetch_models()
    whisper = (
        ("mlx", settings.mlx_whisper_model)
        if transcriber == "mlx"
        else ("whisper", tmp_path / "whisper")
    )
    assert got == [whisper, ("separation", tmp_path / "separation"), ("faces", tmp_path)]


def test_the_fetch_models_command(monkeypatch, capsys):
    fetched = []
    monkeypatch.setattr(cli, "detect", lambda settings: Backends("cpu", "cpu", "x264", "h264"))
    monkeypatch.setattr(ModelRouter, "fetch_models", lambda router: fetched.append(router))
    assert cli.main(["fetch-models"]) == 0 and len(fetched) == 1
    assert "all models are on this machine" in capsys.readouterr().out
