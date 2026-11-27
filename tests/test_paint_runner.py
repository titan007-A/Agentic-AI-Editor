"""The image painter's runner keeping its memory down over a batch: MLX's cache emptied after
each picture and held to a limit, every prompt encoded before the first picture and the text
encoder let go. No model and no MLX here: stand-ins count what is asked."""

import io
import json
import sys
import types

import pytest

from agentic_editor.animate import images, paint_runner


@pytest.fixture
def mlx(monkeypatch):
    """A stand-in for mlx.core that notes each call."""
    calls = []
    core = types.SimpleNamespace(
        eval=lambda arrays: calls.append(("eval", len(arrays))),
        clear_cache=lambda: calls.append(("clear_cache",)),
        set_cache_limit=lambda n: calls.append(("set_cache_limit", n)),
    )
    package = types.ModuleType("mlx")
    package.core = core
    monkeypatch.setitem(sys.modules, "mlx", package)
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    return calls


class Klein:
    """FLUX.2 [klein] as the runner calls it: each picture encodes its prompt."""

    def __init__(self):
        self.text_encoder = object()
        self.encoded: list[str] = []

    def _encode_prompt_pair(self, *, prompt, negative_prompt, guidance):
        if self.text_encoder is None:
            raise RuntimeError("the text encoder is gone")
        self.encoded.append(prompt)
        return (f"embeds of {prompt}", "ids", None, None)

    def generate_image(self, seed, prompt, **kw):
        embeds = self._encode_prompt_pair(prompt=prompt, negative_prompt=" ", guidance=1.0)
        return types.SimpleNamespace(
            embeds=embeds, save=lambda path, overwrite=True: open(path, "wb").close()
        )


def test_every_prompt_is_encoded_first_and_the_text_encoder_let_go(mlx):
    model = Klein()
    assert paint_runner.encode_first(model, ["a kitchen", "a street", "a kitchen"])
    assert model.encoded == ["a kitchen", "a street"] and model.text_encoder is None
    assert ("eval", 2) in mlx and mlx[-1] == ("clear_cache",)
    # The pictures are painted from the kept encodings, exactly as they would have been.
    assert model.generate_image(1, "a street").embeds == ("embeds of a street", "ids", None, None)
    assert model.encoded == ["a kitchen", "a street"]
    with pytest.raises(RuntimeError, match="encoded first"):
        model.generate_image(1, "a prompt nobody asked for")
    # A model encoded some other way is left as it is.
    other = types.SimpleNamespace(text_encoder=object())
    assert not paint_runner.encode_first(other, ["x"]) and other.text_encoder is not None


def test_the_cache_is_held_to_a_limit_and_emptied_after_each_picture(mlx, tmp_path, monkeypatch):
    model = Klein()
    jobs = [{"prompt": p, "seed": n, "dest": str(tmp_path / f"{n}.png")}
            for n, p in enumerate(["a kitchen", "a street", "a kitchen"])]  # fmt: skip
    request = {"kind": "likeness", "model": "m", "steps": 4, "width": 64, "height": 64,
               "jobs": jobs}  # fmt: skip
    monkeypatch.setattr(paint_runner, "load", lambda request: model)
    monkeypatch.setattr(paint_runner, "footprint", lambda: (6.2, 7.0))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    paint_runner.main()
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert said[0]["encoded_first"] is True and model.text_encoder is None
    assert [s["done"] for s in said[1:]] == [1, 2, 3]
    assert said[1]["memory_gb"] == 6.2 and said[1]["peak_gb"] == 7.0
    assert mlx[0] == ("set_cache_limit", paint_runner.CACHE_LIMIT)
    # once after the encoder went, then once after each picture
    assert mlx.count(("clear_cache",)) == 1 + len(jobs)
    assert all((tmp_path / f"{n}.png").exists() for n in range(3))


def test_without_mlx_the_runner_paints_as_before(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx", None)  # not installed: import fails
    model = Klein()
    job = {"prompt": "a room", "seed": 1, "dest": str(tmp_path / "a.png")}
    request = {"kind": "likeness", "model": "m", "steps": 4, "width": 64, "height": 64,
               "jobs": [job]}  # fmt: skip
    monkeypatch.setattr(paint_runner, "load", lambda request: model)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    paint_runner.main()
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert "encoded_first" not in said[0] and said[1]["done"] == 1
    assert model.text_encoder is not None and (tmp_path / "a.png").exists()


def test_memory_is_read_only_on_a_mac():
    if sys.platform != "darwin":
        assert paint_runner.footprint() is None


def test_each_pictures_memory_is_logged(caplog):
    from tests.test_animate import FakeProc

    class Telling(FakeProc):
        def close(self):
            super().close()
            self.lines = [line.replace('"seconds": 1.0', '"seconds": 1.0, "memory_gb": 6.2, '
                                       '"peak_gb": 7.0') for line in self.lines]  # fmt: skip

    proc = Telling(None, None, None, None, None)
    request = {"jobs": [{"prompt": "a", "seed": 1, "dest": "/dev/null"}]}
    with caplog.at_level("INFO", logger="agentic_editor.animate.images"):
        images._follow(proc, request, 1, lambda *a: None)
    assert "painted 1 of 1 pictures, 1.0 s, 6.2 GB (most 7.0 GB)" in caplog.text
