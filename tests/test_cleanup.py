import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agentic_editor import cleanup, download
from agentic_editor.db import Job, Output, make_sessionmaker

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def aged(path, days):
    """Sets a file's or folder's (and its files') time `days` before NOW."""
    stamp = (NOW - timedelta(days=days)).timestamp()
    for p in [path, *(path.rglob("*") if path.is_dir() else [])]:
        os.utime(p, (stamp, stamp))
    return path


def video(folder, name="reel.mp4", days=0):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"x" * 1000)
    return aged(folder, days)


def job(db, media, job_id, source, days, status="done"):
    when = (NOW - timedelta(days=days)).replace(tzinfo=None)
    found = Job(id=job_id, source=source, title=job_id, presets=["p"], status=status,
                stage=status, progress=1.0, created_at=when, updated_at=when)  # fmt: skip
    db.add(found)
    folder = video(media / "outputs" / "abc0123456789abcdef0" / job_id, days=days)
    db.add(Output(job_id=job_id, preset="p", path=str(folder / "reel.mp4"), duration=20,
                  planner="m", edl={}))  # fmt: skip
    return folder


def test_jobs_past_the_keep_time_go_with_their_videos_and_what_nothing_kept_needs(tmp_path):
    media, cache = tmp_path / "media", tmp_path / "cache"
    settings = SimpleNamespace(media_dir=media, cache_dir=cache)
    Session = make_sessionmaker(tmp_path / "app.db")
    sources = media / "sources"
    with Session() as db:
        old = job(db, media, "old000000001", "https://x.test/old", days=5)
        new = job(db, media, "new000000001", "https://x.test/new", days=1)
        job(db, media, "run000000001", "upload:aaaaaaaaaaaaaaaaaaaa/kept.mp4", days=9,
            status="running")  # fmt: skip
        db.commit()
    for url, key, days in (
        ("https://x.test/old", "Site-old", 5),
        ("https://x.test/new", "Site-new", 5),
    ):
        video(sources / key, days=days)
        link = download._link_file(url, sources)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(key)
        aged(link, days)
    video(media / "uploads" / "aaaaaaaaaaaaaaaaaaaa", "kept.mp4", days=9)  # a running job's
    video(media / "uploads" / "bbbbbbbbbbbbbbbbbbbb", "gone.mp4", days=9)
    video(media / "uploads" / "cccccccccccccccccccc", "fresh.mp4", days=1)
    orphan = video(media / "outputs" / "abc0123456789abcdef0" / "deleted00001", days=6)
    video(cache / "0123456789abcdef0123", "audio.wav", days=6)
    video(cache / "captions", "song.json", days=30)  # not a video's cache

    gone = cleanup.sweep(Session, settings, NOW)

    assert not old.exists() and new.exists() and not orphan.exists()
    with Session() as db:
        assert {j.id for j in db.query(Job)} == {"new000000001", "run000000001"}
    # A link a kept job uses keeps its download, however old; the old job's goes.
    assert (sources / "Site-new").exists() and not (sources / "Site-old").exists()
    assert download._link_file("https://x.test/new", sources).exists()
    assert not download._link_file("https://x.test/old", sources).exists()
    assert sorted(p.name for p in (media / "uploads").iterdir()) == [
        "aaaaaaaaaaaaaaaaaaaa", "cccccccccccccccccccc"
    ]  # fmt: skip
    # A job is running: no cache is touched while it may be reading one.
    assert (cache / "0123456789abcdef0123").exists() and (cache / "captions").exists()
    assert gone["jobs"] == 1 and gone["downloads"] == 1 and gone["uploads"] == 1
    assert gone["orphan outputs"] == 1 and gone["bytes"] == 4 * 1000 + len("Site-old")


def test_caches_go_once_nothing_runs_and_zero_days_keeps_everything(tmp_path):
    media, cache = tmp_path / "media", tmp_path / "cache"
    settings = SimpleNamespace(media_dir=media, cache_dir=cache)
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        folder = job(db, media, "old000000001", "https://x.test/old", days=30)
        db.commit()
        cleanup.set_keep_days(db, 0)
        assert cleanup.keep_days(db) == 0
    video(cache / "0123456789abcdef0123", "audio.wav", days=30)
    assert cleanup.sweep(Session, settings, NOW) == {}
    assert folder.exists()
    with Session() as db:
        cleanup.set_keep_days(db, 4)
    cleanup.sweep(Session, settings, NOW)
    assert not folder.exists() and not (cache / "0123456789abcdef0123").exists()


def test_the_keep_time_is_four_days_until_set(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        assert cleanup.keep_days(db) == 4
