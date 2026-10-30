import shutil
from types import SimpleNamespace

import pytest

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

    def run(source, presets, settings, backends, notes, progress, run_id):
        broken = pipeline.Output("square-1x1", tmp_path / "a.mp4", None, "fake-model")
        return pipeline.Result("A song", [broken])

    jobs.run_job("j1", Session, settings, run)

    with Session() as db:
        job = db.get(Job, "j1")
        assert job.status == "failed" and job.error.startswith("AttributeError")


def test_a_job_shows_the_sources_title_while_it_runs_and_keeps_its_own_outputs(
    tmp_path, monkeypatch
):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add(Job(id="j1", source="https://x.test/v", title="https://x.test/v", presets=[]))
        db.commit()
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    settings = Settings(presets_dir=ROOT / "presets", media_dir=tmp_path / "media")
    seen = {}

    def run(source, presets, settings, backends, notes, progress, run_id):
        progress("download", "fetching it")
        progress("title", "A song")
        with Session() as db:
            seen["title"] = db.get(Job, "j1").title
            seen["events"] = [e.stage for e in db.query(Event).filter_by(job_id="j1")]
        seen["run_id"] = run_id
        return pipeline.Result("A song", [])

    jobs.run_job("j1", Session, settings, run)
    assert seen == {"title": "A song", "events": ["download"], "run_id": "j1"}


def test_progress_has_a_share_for_every_video_a_job_makes(tmp_path, monkeypatch):
    presets = tmp_path / "presets"
    shutil.copytree(ROOT / "presets", presets)
    square = presets / "square-1x1.yaml"
    square.write_text(square.read_text() + "hook:\n  variants: 2\n")
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add(Job(id="j1", source="https://x.test/v", presets=["square-1x1", "shorts-9x16"]))
        db.commit()
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    settings = Settings(presets_dir=presets, media_dir=tmp_path / "media")

    def run(source, presets, settings, backends, notes, progress, run_id):
        for name in ("square-1x1", "square-1x1-b", "shorts-9x16"):
            progress("render", f"rendering {name}")
            progress("done", f"{name} -> {name}.mp4")
        return pipeline.Result("A song", [])

    jobs.run_job("j1", Session, settings, run)
    with Session() as db:
        done = [e.progress for e in db.query(Event).filter_by(job_id="j1", stage="done")]
    assert done == pytest.approx([0.55 + 0.15, 0.55 + 0.30, 0.99])


def test_a_restart_settles_captions_that_were_being_written(tmp_path, monkeypatch):
    from agentic_editor.db import Output

    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        job = Job(id="done", source="https://x.test/4", presets=["p"], status="done")
        for status in ("writing", "queued", "done"):
            job.outputs.append(
                Output(preset="p", path="/x.mp4", duration=1, planner="m", edl={},
                       caption_status=status)
            )  # fmt: skip
        db.add(job)
        db.commit()
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: []))
    asked = []
    monkeypatch.setattr(jobs, "process", lambda job_id: None)
    monkeypatch.setattr(jobs, "learn_style", lambda draft_id: None)
    monkeypatch.setattr(jobs, "write_caption", asked.append)

    jobs.recover(Session)

    with Session() as db:
        statuses = [o.caption_status for o in db.get(Job, "done").outputs]
    assert statuses == ["failed", "queued", "done"] and asked == [2]


def test_a_restart_settles_researches_like_jobs(tmp_path, monkeypatch):
    from agentic_editor.db import Research

    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add_all([
            Research(id="searching", description="x", status="running"),
            Research(id="waiting", description="x"),
            Research(id="lost", description="x"),
        ])  # fmt: skip
        db.commit()
    queue = [SimpleNamespace(name=jobs.RESEARCH, args=("waiting",))]
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue))
    asked = []
    monkeypatch.setattr(jobs, "find_reels", asked.append)

    jobs.recover(Session)

    with Session() as db:
        assert db.get(Research, "searching").status == "failed"
        assert db.get(Research, "searching").error == jobs.INTERRUPTED
    assert asked == ["lost"]


def test_a_restart_settles_the_learning_of_instagram_accounts(tmp_path, monkeypatch):
    from agentic_editor.db import ResearchAccount

    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add_all([
            ResearchAccount(id="reading", handle="a", learn_status="running"),
            ResearchAccount(id="waiting", handle="b", learn_status="queued"),
            ResearchAccount(id="lost", handle="c", learn_status="queued"),
            ResearchAccount(id="known", handle="d", learn_status="done"),
        ])  # fmt: skip
        db.commit()
    queue = [SimpleNamespace(name=jobs.ACCOUNT, args=("waiting",))]
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue))
    asked = []
    monkeypatch.setattr(jobs, "read_account", asked.append)

    jobs.recover(Session)

    with Session() as db:
        reading = db.get(ResearchAccount, "reading")
        assert (reading.learn_status, reading.learn_error) == ("failed", jobs.INTERRUPTED)
    assert asked == ["lost"]


def test_an_account_is_learned_again_after_a_week():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    assert jobs.stale(None, now)
    # As the database gives it back: UTC, without its zone.
    assert not jobs.stale(datetime(2026, 10, 25, 12), now)
    assert jobs.stale(datetime(2026, 10, 30, 12) - timedelta(days=8), now)


def test_reels_are_known_by_code_among_a_job_s_sources():
    sources = [
        "https://www.instagram.com/reel/DcafVd6BocI/?igsh=x",
        "https://www.instagram.com/p/CAjcakIDpkV/",
        "https://www.youtube.com/watch?v=abc",
        "upload:0123456789abcdef0123/video.mp4",
    ]
    assert jobs.reel_codes(sources) == {"DcafVd6BocI", "CAjcakIDpkV"}
