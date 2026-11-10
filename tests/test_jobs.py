import shutil
from types import SimpleNamespace

import pytest

from agentic_editor import jobs, pipeline
from agentic_editor.backends import Backends
from agentic_editor.db import Event, Job, Output, StyleDraft, make_sessionmaker
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
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue, scheduled=lambda: []))
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
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: [], scheduled=lambda: []))
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
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue, scheduled=lambda: []))
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
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue, scheduled=lambda: []))
    asked = []
    monkeypatch.setattr(jobs, "read_account", asked.append)

    jobs.recover(Session)

    with Session() as db:
        reading = db.get(ResearchAccount, "reading")
        assert (reading.learn_status, reading.learn_error) == ("failed", jobs.INTERRUPTED)
    assert asked == ["lost"]


def test_a_restart_thinks_of_lost_questions_again_and_answers_lost_chat(tmp_path, monkeypatch):
    from agentic_editor.db import Research, ResearchMessage

    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add_all([
            Research(id="thinking", description="x", status="asking"),
            Research(id="waiting", description="x", status="asking",
                     questions=[{"question": "Live?", "choices": ["Yes", "No"]}]),
            Research(id="done", description="x", status="done"),
        ])  # fmt: skip
        db.flush()
        db.add_all([
            ResearchMessage(id=1, research_id="done", role="user", text="hi", answered=True),
            ResearchMessage(id=2, research_id="done", role="researcher", text="hello"),
            ResearchMessage(id=3, research_id="done", role="user", text="more?"),
        ])  # fmt: skip
        db.commit()
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: [], scheduled=lambda: []))
    thought, answered = [], []
    monkeypatch.setattr(jobs, "ask_questions", thought.append)
    monkeypatch.setattr(jobs, "answer_chat", answered.append)

    jobs.recover(Session)

    # Waiting with its questions asked is the person's turn, not the queue's.
    assert thought == ["thinking"] and answered == [3]


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


def _queue(Session, *jobs_, feed_jobs=()):
    from datetime import UTC, datetime

    from agentic_editor.db import Feed, FeedItem

    with Session() as db:
        for i, (job_id, owner, minute) in enumerate(jobs_):
            at = datetime(2026, 11, 3, 10, minute, tzinfo=UTC)
            db.add(Job(id=job_id, source=f"https://x.test/{i}", presets=["p"], owner_id=owner,
                       created_at=at))  # fmt: skip
        if feed_jobs:
            feed = Feed(name="f", kind="link", location="x", presets=["p"])
            db.add(feed)
            db.flush()
            for job_id in feed_jobs:
                db.add(FeedItem(feed_id=feed.id, key=job_id, source="x", job_id=job_id))
        db.commit()


def _order(Session, minute=59):
    from datetime import UTC, datetime

    taken = []
    # A second apart, as workers take jobs up one after another.
    while (
        turn := jobs.claim_turn(Session, datetime(2026, 11, 3, 10, minute, len(taken), tzinfo=UTC))
    ) is not None:
        kind, ident = turn
        taken.append(ident if kind == "job" else f"caption {ident}")
        with Session() as db:
            if kind == "job":
                db.get(Job, ident).status = "done"
            else:
                db.get(Output, ident).caption_status = "done"
            db.commit()
    return taken


def test_one_persons_long_queue_doesnt_hold_up_anyone_else(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    # Riya queues five, then Aman one: Aman's goes second, not sixth, and they take turns.
    _queue(Session, *[(f"riya{i}", "riya", i) for i in range(5)], ("aman0", "aman", 6),
           ("aman1", "aman", 7))  # fmt: skip
    assert _order(Session) == ["riya0", "aman0", "riya1", "aman1", "riya2", "riya3", "riya4"]


def test_peoples_own_jobs_go_before_autopilot_until_it_has_waited_too_long(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    _queue(Session, ("auto0", "me", 0), ("auto1", "me", 1), ("mine", "me", 20),
           feed_jobs=["auto0", "auto1"])  # fmt: skip
    assert _order(Session, minute=25)[0] == "mine"
    Session = make_sessionmaker(tmp_path / "later.db")
    _queue(Session, ("auto0", "me", 0), ("mine", "me", 20), feed_jobs=["auto0"])
    assert _order(Session, minute=45)[0] == "auto0"  # waited 45 minutes: its turn


def test_an_account_already_running_a_job_waits_for_one_that_isnt(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    _queue(Session, ("riya0", "riya", 0), ("riya1", "riya", 1), ("aman0", "aman", 2))
    with Session() as db:
        db.get(Job, "riya0").status = "running"
        db.commit()
    from datetime import UTC, datetime

    assert jobs.claim_turn(Session, datetime(2026, 11, 3, 11, 0, tzinfo=UTC)) == ("job", "aman0")


def test_a_turn_runs_the_job_whose_turn_it_is_and_a_spare_turn_does_nothing(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    _queue(Session, ("riya0", "riya", 0), ("riya1", "riya", 1), ("aman0", "aman", 2))
    with Session() as db:
        db.get(Job, "riya0").status = "running"
        db.commit()
    ran = []
    monkeypatch.setattr(jobs, "sessions", lambda: Session)
    monkeypatch.setattr(jobs, "get_settings", lambda: None)
    monkeypatch.setattr(jobs, "run_job", lambda job_id, *a: ran.append(job_id))
    jobs.process.call_local("riya1")  # made for riya1, but aman hasn't had a turn
    assert ran == ["aman0"]
    with Session() as db:
        for job_id in ("riya1", "aman0"):
            db.get(Job, job_id).status = "done"
        db.commit()
    jobs.process.call_local("aman0")
    assert ran == ["aman0"]


def test_a_turn_given_to_another_job_runs_only_that_jobs_own_video_presets_and_notes(
    tmp_path, monkeypatch
):
    """Riya's job's turn goes to Aman's (she already has one running): the run gets only
    Aman's source, notes and presets, his outputs land on his job, and hers is untouched."""
    import yaml

    from agentic_editor.db import User

    presets_root = tmp_path / "presets"
    for owner, label in (("riya", "Riya's preset"), ("aman", "Aman's preset")):
        folder = presets_root / "people" / owner
        folder.mkdir(parents=True)
        (folder / "p.yaml").write_text(
            yaml.safe_dump({"name": "p", "label": label, "min_duration": 1, "max_duration": 60})
        )
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        for owner in ("riya", "aman"):
            db.add(User(id=owner, username=owner, password_hash="x"))
        db.add_all([
            Job(id="riya-busy", source="https://x.test/riya-0", presets=["p"], owner_id="riya",
                status="running"),
            Job(id="riya-next", source="https://x.test/riya-vocals", presets=["p"],
                owner_id="riya", notes="riya's notes"),
            Job(id="aman-next", source="https://x.test/aman-vocals", presets=["p"],
                owner_id="aman", notes="aman's notes"),
        ])  # fmt: skip
        db.commit()
    settings = Settings(presets_dir=presets_root, media_dir=tmp_path / "media")
    monkeypatch.setattr(jobs, "sessions", lambda: Session)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    seen = {}

    def run(source, presets, settings, backends, notes, progress, run_id):
        seen.update(source=source, labels=[p.label for p in presets], notes=notes, run_id=run_id)
        return pipeline.Result("Aman's song", [])

    real = jobs.run_job
    monkeypatch.setattr(jobs, "run_job", lambda job_id, S, s: real(job_id, S, s, run))
    jobs.process.call_local("riya-next")  # riya-next's turn

    assert seen == {
        "source": "https://x.test/aman-vocals",
        "labels": ["Aman's preset"],
        "notes": "aman's notes",
        "run_id": "aman-next",
    }
    with Session() as db:
        assert db.get(Job, "aman-next").status == "done"
        assert db.get(Job, "aman-next").title == "Aman's song"
        riya = db.get(Job, "riya-next")
        assert (riya.status, riya.title, riya.started_at) == ("queued", "", None)
        assert db.query(Event).filter_by(job_id="riya-next").count() == 0


def _researches(Session, *rows):
    from datetime import UTC, datetime

    from agentic_editor.db import Research

    with Session() as db:
        for research_id, owner, minute, status in rows:
            db.add(Research(id=research_id, owner_id=owner, description="songs", status=status,
                            created_at=datetime(2026, 11, 3, 10, minute, tzinfo=UTC)))  # fmt: skip
        db.commit()


def test_researches_run_one_at_a_time_and_accounts_take_turns(tmp_path):
    from datetime import UTC, datetime

    from agentic_editor.db import Research

    Session = make_sessionmaker(tmp_path / "app.db")
    _researches(Session, ("riya0", "riya", 0, "queued"), ("riya1", "riya", 1, "queued"),
                ("riya2", "riya", 2, "queued"), ("aman0", "aman", 3, "queued"))  # fmt: skip
    order = []
    for second in range(4):
        at = datetime(2026, 11, 3, 10, 30, second, tzinfo=UTC)
        taken = jobs.claim_research(Session, at)
        assert jobs.claim_research(Session, at) == jobs.BUSY  # one at a time
        order.append(taken)
        with Session() as db:
            db.get(Research, taken).status = "done"
            db.commit()
    assert order == ["riya0", "aman0", "riya1", "riya2"]
    assert jobs.claim_research(Session) is None


def test_a_research_turn_waits_in_the_schedule_while_another_runs(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    _researches(Session, ("riya0", "riya", 0, "running"), ("aman0", "aman", 1, "queued"))
    monkeypatch.setattr(jobs, "sessions", lambda: Session)
    monkeypatch.setattr(jobs, "get_settings", lambda: None)
    ran, later = [], []
    monkeypatch.setattr(jobs, "run_research", lambda research_id, *a: ran.append(research_id))
    monkeypatch.setattr(
        jobs.find_reels, "schedule", lambda args, delay: later.append((args, delay))
    )
    jobs.find_reels.call_local("aman0")
    assert ran == [] and later == [(("aman0",), jobs.RESEARCH_RETRY)]


def test_a_restart_counts_waiting_research_turns_as_turns(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    _researches(Session, ("riya0", "riya", 0, "queued"), ("aman0", "aman", 1, "queued"))
    waiting = [SimpleNamespace(name=jobs.RESEARCH, args=("riya0",))]
    monkeypatch.setattr(
        jobs, "huey", SimpleNamespace(pending=lambda: [], scheduled=lambda: waiting)
    )
    started = []
    monkeypatch.setattr(jobs, "find_reels", started.append)
    jobs.recover(Session)
    assert started == ["aman0"]  # riya0's turn is in the schedule; only one was missing


def _caption(Session, output_id, job_id, owner, minute, status="queued"):
    from datetime import UTC, datetime

    with Session() as db:
        job = db.get(Job, job_id) or Job(id=job_id, source="x", presets=["p"], owner_id=owner,
                                         status="done")  # fmt: skip
        job.outputs.append(
            Output(id=output_id, preset="p", path="/x.mp4", duration=1, planner="m", edl={},
                   caption_status=status,
                   caption_queued_at=datetime(2026, 11, 3, 10, minute, tzinfo=UTC))
        )  # fmt: skip
        db.add(job)
        db.commit()


def test_captions_take_turns_with_jobs_so_one_persons_captions_dont_hold_a_job_up(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    # Rishab's three captions were asked for first; Vanshaj's job comes after them.
    for n in range(3):
        _caption(Session, n + 1, f"r{n}", "rishab", n)
    _queue(Session, ("vanshaj0", "vanshaj", 10))
    assert _order(Session) == ["caption 1", "vanshaj0", "caption 2", "caption 3"]


def test_a_running_caption_counts_as_its_owner_being_served(tmp_path):
    from datetime import UTC, datetime

    Session = make_sessionmaker(tmp_path / "app.db")
    _caption(Session, 1, "r0", "rishab", 0, status="writing")
    _queue(Session, ("rishab1", "rishab", 1), ("vanshaj0", "vanshaj", 5))
    turn = jobs.claim_turn(Session, datetime(2026, 11, 3, 11, 0, tzinfo=UTC))
    assert turn == ("job", "vanshaj0")


def test_a_caption_turn_can_run_a_job_and_a_job_turn_a_caption(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    _caption(Session, 1, "r0", "rishab", 0, status="writing")
    _caption(Session, 2, "r1", "rishab", 1)
    _queue(Session, ("vanshaj0", "vanshaj", 5))
    monkeypatch.setattr(jobs, "sessions", lambda: Session)
    monkeypatch.setattr(jobs, "get_settings", lambda: None)
    ran = []
    monkeypatch.setattr(jobs, "run_job", lambda ident, *a: ran.append(("job", ident)))
    monkeypatch.setattr(jobs, "run_caption", lambda ident, *a: ran.append(("caption", ident)))
    jobs.write_caption.call_local(2)  # made for Rishab's caption; Vanshaj hasn't had a turn
    jobs.process.call_local("vanshaj0")
    assert ran == [("job", "vanshaj0"), ("caption", 2)]


def test_a_restart_counts_job_and_caption_turns_together(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    _caption(Session, 1, "r0", "rishab", 0)
    _queue(Session, ("vanshaj0", "vanshaj", 5))
    # Vanshaj's job turn is still queued; one turn, two queued things: one turn was lost.
    queue = [SimpleNamespace(name=jobs.PROCESS, args=("vanshaj0",))]
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue, scheduled=lambda: []))
    started, captions = [], []
    monkeypatch.setattr(jobs, "process", started.append)
    monkeypatch.setattr(jobs, "write_caption", captions.append)
    monkeypatch.setattr(jobs, "learn_style", lambda *a: None)
    jobs.recover(Session)
    assert started == [] and captions == [1]


def test_an_upscale_fills_its_render_share_as_frames_are_drawn(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add(Job(id="j1", source="s", presets=["upscale-hd"]))
        db.commit()
    track = jobs.Tracker(Session, "j1", 1)
    track("render", "upscaling for upscale-hd")
    start = track.value
    track("upscale", "upscaling 320x240 to 1440x1080")
    assert track.value == start  # no count yet: where it was
    track("upscale", "upscaled 120 of 240 frames")
    assert track.value == pytest.approx(jobs.RENDER_START + 0.45 * 0.95 * 0.5)
    track("upscale", "upscaled 240 of 240 frames")
    assert track.value == pytest.approx(jobs.RENDER_START + 0.45 * 0.95)
    track("done", "upscale-hd -> out.mp4")
    assert track.value == 0.99


def test_videos_go_before_a_caption_until_it_has_waited_a_minute(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    # Rishab's caption was asked for at 10:20, his next videos a few seconds later.
    _caption(Session, 1, "r0", "rishab", 20)
    _queue(Session, ("rishab1", "rishab", 20), ("rishab2", "rishab", 20))
    # Within the minute the videos go first, though the caption was asked for first.
    assert _order(Session, minute=20) == ["rishab1", "rishab2", "caption 1"]


def test_a_caption_that_waited_a_minute_takes_its_turn_like_a_video(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    _caption(Session, 1, "r0", "rishab", 20)
    _queue(Session, ("rishab1", "rishab", 21))
    assert _order(Session, minute=22) == ["caption 1", "rishab1"]
