import threading
import time
from types import SimpleNamespace

from agentic_editor import benchmark, slots
from agentic_editor.benchmark import Result, realtime, report
from agentic_editor.settings import Settings
from agentic_editor.slots import Slot


def test_realtime_factor():
    assert realtime(240, 24) == "10.0x realtime"
    assert realtime(10, 0) == "instant"


def test_report_aligns_stages():
    text = report(
        [Result("transcription", 23.4, "10.3x realtime"), Result("render", 31.0, "0.6x")],
        "Breathe",
        241.2,
    )
    lines = text.splitlines()
    assert lines[0] == "Breathe (241s)"
    assert lines[1].index("23.4s") == lines[2].index("31.0s")


def test_a_benchmark_waiting_its_turn_leaves_the_running_ones_files_alone(tmp_path, monkeypatch):
    # Slots without lock files, so the test never waits for a real run on this machine.
    monkeypatch.setattr(slots, "gpu", Slot("GPU"))
    monkeypatch.setattr(slots, "encoder", Slot("encoder"))
    monkeypatch.setattr(benchmark.ffmpeg, "probe", lambda video: SimpleNamespace(has_audio=False))
    monkeypatch.setattr(benchmark, "time_render", lambda *args: Result("render", 1.0, ""))
    settings = Settings(cache_dir=tmp_path / "cache")
    running = settings.cache_dir / "benchmark" / "render.mp4"
    running.parent.mkdir(parents=True)
    running.write_bytes(b"the other benchmark's encode")
    holding, done, results = threading.Event(), threading.Event(), []

    def other_benchmark():
        with slots.gpu.turn():
            holding.set()
            done.wait(5)

    threading.Thread(target=other_benchmark).start()
    holding.wait(5)
    waiting = threading.Thread(
        target=lambda: results.append(
            benchmark.run(tmp_path / "v.mp4", None, settings, None, False)
        )
    )
    waiting.start()
    time.sleep(0.2)
    assert running.exists()
    done.set()
    waiting.join(5)
    assert [r.stage for r in results[0]] == ["render"]
