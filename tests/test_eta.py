from datetime import UTC, datetime, timedelta

from agentic_editor import eta
from agentic_editor.db import Job, make_sessionmaker

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _job(i, presets, status, started=None, ended=None, progress=0.0, created=None):
    created = created or NOW - timedelta(hours=1, minutes=-i)
    return Job(id=f"j{i:04d}", source="x", title="t", presets=presets, status=status,
               stage=status, progress=progress, started_at=started,
               updated_at=ended or NOW, created_at=created)  # fmt: skip


def _done(i, presets, took):
    start = NOW - timedelta(hours=2, minutes=i)
    return _job(i, presets, "done", start, start + timedelta(seconds=took))


def test_a_running_job_is_judged_by_its_presets_past_and_its_own_pace(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as s:
        s.add_all([_done(i, ["vocal"], 100 + i) for i in range(1, 6)])  # median 103 s
        s.add_all([_done(10 + i, ["shorts"], 300) for i in range(3)])
        s.add(_job(100, ["vocal"], "running", NOW - timedelta(seconds=20), progress=0.05))
        s.add(_job(101, ["vocal"], "running", NOW - timedelta(seconds=80), progress=0.8))
        s.commit()
        got = eta.estimates(s, workers=2, now=NOW)
    assert got["j0100"] == 83  # early: by history, 103 - 20
    # 80% done in 80 s: pace says 20 s, history 23 s, weighted 0.8 / 0.2.
    assert got["j0101"] == round(0.8 * 20 + 0.2 * 23)


def test_a_job_past_its_usual_time_still_says_something_and_never_zero(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as s:
        s.add(_done(1, ["vocal"], 60))
        s.add(_job(100, ["vocal"], "running", NOW - timedelta(seconds=200), progress=0.1))
        s.add(_job(101, ["vocal"], "running", NOW - timedelta(seconds=200), progress=0.99))
        s.commit()
        got = eta.estimates(s, workers=2, now=NOW)
    assert got["j0100"] == eta.OVERTIME and got["j0101"] == eta.FLOOR


def test_queued_jobs_wait_for_a_free_slot_in_turn(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as s:
        s.add(_done(1, ["vocal"], 100))
        # Two slots: one job with ~50 s left, one free.
        s.add(_job(100, ["vocal"], "running", NOW - timedelta(seconds=50), progress=0.1))
        s.add(_job(101, ["vocal"], "queued", created=NOW - timedelta(seconds=30)))
        s.add(_job(102, ["vocal"], "queued", created=NOW - timedelta(seconds=20)))
        s.add(_job(103, ["new-preset"], "queued", created=NOW - timedelta(seconds=10)))
        s.commit()
        got = eta.estimates(s, workers=2, now=NOW)
    assert got["j0100"] == 50
    assert got["j0101"] == 100  # the free slot: its own 100 s
    assert got["j0102"] == 150  # after the running one's 50 s
    assert got["j0103"] == 200  # unknown preset: the median of all, after j0101


def test_nothing_to_go_on_says_nothing(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as s:
        s.add(_job(100, ["vocal"], "running", NOW - timedelta(seconds=5), progress=0.05))
        s.add(_job(101, ["vocal"], "queued"))
        s.commit()
        assert eta.estimates(s, workers=2, now=NOW) == {}
