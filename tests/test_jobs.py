from types import SimpleNamespace

from agentic_editor import jobs, pipeline
from agentic_editor.backends import Backends
from agentic_editor.db import Event, Job, StyleDraft, make_sessionmaker
from agentic_editor.settings import ROOT, Settings


def test_a_restart_fails_what_was_running_and_queues_again_what_was_lost(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add_all([
            Job(id="running", source="https://x.test/1", presets=["p"], status="running"),
            Job(id="waiting", source="https://x.test/2", presets=["p"]),
            Job(id="lost", source="https://x.test/3", presets=["p"]),
            Job(id="done", source="https://x.test/4", presets=["p"], status="done"),
            StyleDraft(id="reading", source="https://x.test/5", status="running"),
            StyleDraft(id="unqueued", source="https://x.test/6"),
        ])  # fmt: skip
        db.commit()
    queue = [SimpleNamespace(name=jobs.PROCESS, args=("waiting",))]
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue))
    started, learning = [], []
    monkeypatch.setattr(jobs, "process", started.append)
    monkeypatch.setattr(jobs, "learn_style", learning.append)

    jobs.recover(Session)

    with Session() as db:
        assert db.get(Job, "running").status == "failed"
        assert db.get(Job, "running").error == jobs.INTERRUPTED
        assert db.query(Event).filter_by(job_id="running", stage="failed").count() == 1
        assert db.get(StyleDraft, "reading").status == "failed"
        assert db.get(Job, "done").status == "done"
    assert started == ["lost"] and learning == ["unqueued"]


def test_a_job_that_fails_while_saving_its_outputs_is_marked_failed(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add(Job(id="j1", source="https://x.test/v", presets=["square-1x1"]))
        db.commit()
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    settings = Settings(presets_dir=ROOT / "presets", media_dir=tmp_path / "media")

    def run(source, presets, settings, backends, notes, progress):
        broken = pipeline.Output("square-1x1", tmp_path / "a.mp4", None, "fake-model")
        return pipeline.Result("A song", [broken])

    jobs.run_job("j1", Session, settings, run)

    with Session() as db:
        job = db.get(Job, "j1")
        assert job.status == "failed" and job.error.startswith("AttributeError")
