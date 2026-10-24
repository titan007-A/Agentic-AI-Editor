import threading
import time

import pytest

from agentic_editor import slots
from agentic_editor.slots import Slot


def test_one_thread_at_a_time_and_the_holder_may_take_it_again():
    slot = Slot("GPU")
    inside, most = [0], [0]

    def work():
        # A model session inside a stage that holds it already takes it again.
        with slot.turn(), slot.turn():
            inside[0] += 1
            most[0] = max(most[0], inside[0])
            time.sleep(0.02)
            inside[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 1 and slot.held() == 0
    with pytest.raises(RuntimeError):
        slot.release()


def test_a_lock_file_keeps_separate_holders_apart(tmp_path):
    # Two slots on one file stand for two processes: flock locks are per open file.
    shared = lambda: tmp_path / "gpu.lock"  # noqa: E731
    first, second = Slot("GPU", shared), Slot("GPU", shared)
    order = []
    first.acquire()

    def wait():
        with second.turn():
            order.append("second")

    waiter = threading.Thread(target=wait)
    waiter.start()
    time.sleep(0.1)
    order.append("first done")
    first.release()
    waiter.join(timeout=2)
    assert order == ["first done", "second"]


class Model:
    def __init__(self):
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


def test_an_encode_gives_the_gpu_to_another_job(monkeypatch):
    monkeypatch.setattr(slots, "gpu", Slot("GPU"))
    monkeypatch.setattr(slots, "encoder", Slot("encoder"))
    model, ran = Model(), threading.Event()

    def other():
        with slots.gpu.turn("transcribe"):
            ran.set()

    other_job = threading.Thread(target=other)
    with slots.gpu.turn(), slots.gpu.turn():
        with slots.encoding(model):
            assert model.unloaded == 1 and slots.gpu.held() == 0 and slots.encoder.held()
            other_job.start()
            assert ran.wait(2)  # the other job got the GPU while this one encoded
        assert slots.gpu.held() == 2 and not slots.encoder.held()
    other_job.join()


def test_encodes_take_turns_without_holding_the_gpu(monkeypatch):
    monkeypatch.setattr(slots, "gpu", Slot("GPU"))
    monkeypatch.setattr(slots, "encoder", Slot("encoder"))
    log = []

    def job(name):
        with slots.encoding():
            log.append(f"{name} starts")
            time.sleep(0.05)
            log.append(f"{name} ends")

    jobs = [threading.Thread(target=job, args=(n,)) for n in ("a", "b")]
    for j in jobs:
        j.start()
    for j in jobs:
        j.join()
    assert log in (
        ["a starts", "a ends", "b starts", "b ends"],
        ["b starts", "b ends", "a starts", "a ends"],
    )


def test_jobs_on_one_video_take_turns_and_other_videos_do_not_wait(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert slots.video(a) is slots.video(a) and slots.video(a) is not slots.video(b)
    went = threading.Event()

    def other_video():
        with slots.video(b).turn():
            went.set()

    with slots.video(a).turn():
        assert (a / "busy.lock").exists()
        threading.Thread(target=other_video).start()
        assert went.wait(2)  # another video goes ahead


def test_a_job_that_has_to_wait_says_so_and_says_when_it_goes_on():
    slot, notes, holding, done = Slot("GPU"), [], threading.Event(), threading.Event()

    def other_job():
        with slot.turn():
            holding.set()
            done.wait(2)

    threading.Thread(target=other_job).start()
    holding.wait(2)
    threading.Timer(0.1, done.set).start()
    with slot.turn("transcribe", lambda stage, note: notes.append((stage, note))):
        pass
    with slot.turn("plan", lambda stage, note: notes.append((stage, note))):  # free: no notes
        pass
    assert notes == [
        ("wait", "waiting for the GPU to transcribe"),
        ("resume", "got the GPU after 0s"),
    ]
