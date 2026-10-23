import contextlib
import os
import threading
from datetime import datetime

import pytest

from agentic_editor import autopilot
from agentic_editor.autopilot import Entry, active, channel_videos, check, folder_entries
from agentic_editor.db import Feed, FeedItem, Job, make_sessionmaker


@pytest.fixture
def Session(tmp_path):
    return make_sessionmaker(tmp_path / "app.db")


def add_feed(Session, **fields) -> int:
    with Session() as db:
        feed = Feed(name="f", kind="folder", location="/x", presets=["p"], **fields)
        db.add(feed)
        db.commit()
        return feed.id


def entries(*names: str) -> list[Entry]:
    return [Entry(key=n, title=n, source=f"/videos/{n}.mp4") for n in names]


def finish(Session, *statuses: str) -> None:
    with Session() as db:
        for job, status in zip(db.query(Job).order_by(Job.created_at), statuses, strict=False):
            job.status = status
        db.commit()


NIGHT = datetime(2026, 10, 22, 23, 30)


def test_a_folder_lists_finished_video_files_oldest_first(tmp_path):
    for i, name in enumerate(["b.mp4", "a.MOV", "notes.txt", ".hidden.mp4", "copying.mkv"]):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (1000 + i, 1000 + i))
    os.utime(tmp_path / "copying.mkv", (5000, 5000))  # written moments ago
    found = folder_entries(tmp_path, now=5010)
    assert [e.title for e in found] == ["b", "a"]
    assert found[0].source == str((tmp_path / "b.mp4").resolve())


def test_a_bare_channel_link_reads_its_videos_tab():
    assert (
        channel_videos("https://www.youtube.com/@someone")
        == "https://www.youtube.com/@someone/videos"
    )
    shorts = "https://www.youtube.com/@someone/shorts"
    assert channel_videos(shorts) == shorts
    playlist = "https://www.youtube.com/playlist?list=PL1"
    assert channel_videos(playlist) == playlist


def test_a_channel_lists_its_videos_not_its_tabs(monkeypatch):
    class FakeYDL:
        def __init__(self, options):
            assert options["extract_flat"] == "in_playlist"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            assert url.endswith("/videos") and not download
            return {
                "_type": "playlist",
                "extractor_key": "YoutubeTab",
                "entries": [
                    {"_type": "url", "ie_key": "Youtube", "id": "abc", "title": "First", "url": "https://y/abc"},
                    {"_type": "playlist", "id": "tab", "title": "Shorts", "url": None},
                ],
            }  # fmt: skip

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    assert autopilot.link_entries("https://www.youtube.com/@someone") == [
        Entry(key="Youtube-abc", title="First", source="https://y/abc")
    ]


def test_hours_can_run_overnight():
    night = Feed(active_from=23, active_to=7)
    day = Feed(active_from=9, active_to=17)
    assert active(night, 23) and active(night, 3) and not active(night, 12)
    assert active(day, 9) and not active(day, 17) and not active(day, 2)
    assert active(Feed(), 15)


def test_only_videos_added_after_the_feed_began_are_edited(Session):
    feed_id = add_feed(Session)
    started = []
    result = check(feed_id, Session, started.append, lambda f: entries("old1", "old2"), NIGHT)
    assert started == [] and result.startswith("2 video(s) already there")
    check(feed_id, Session, started.append, lambda f: entries("old1", "old2", "new"), NIGHT)
    with Session() as db:
        jobs = db.query(Job).all()
        assert [j.id for j in jobs] == started and jobs[0].source == "/videos/new.mp4"
        assert jobs[0].presets == ["p"]


def test_existing_videos_go_a_few_at_a_time_and_never_twice(Session):
    feed_id = add_feed(Session, include_existing=True, batch=2)
    started = []
    found = lambda f: entries("a", "b", "c")  # noqa: E731
    assert (
        check(feed_id, Session, started.append, found, NIGHT) == "started 2 new video(s), 1 waiting"
    )
    assert (
        check(feed_id, Session, started.append, found, NIGHT) == "started 0 new video(s), 1 waiting"
    )
    finish(Session, "done", "failed")  # a failed video doesn't hold up the rest
    check(feed_id, Session, started.append, found, NIGHT)
    check(feed_id, Session, started.append, found, NIGHT)
    with Session() as db:
        assert sorted(i.key for i in db.query(FeedItem)) == ["a", "b", "c"]
        assert len(started) == 3


def test_nothing_starts_outside_the_feeds_hours(Session):
    feed_id = add_feed(Session, include_existing=True, active_from=23, active_to=7)
    started = []
    noon = datetime(2026, 10, 22, 12, 0)
    assert check(feed_id, Session, started.append, lambda f: entries("a"), noon) == "waiting"
    check(feed_id, Session, started.append, lambda f: entries("a"), NIGHT)
    assert len(started) == 1


def test_a_feed_that_cant_be_read_says_so(Session):
    feed_id = add_feed(Session, include_existing=True)

    def broken(feed):
        raise FileNotFoundError("no such folder: /gone")

    result = check(feed_id, Session, lambda job_id: None, broken, NIGHT)
    assert result == "couldn't read it: no such folder: /gone"
    with Session() as db:
        assert db.get(Feed, feed_id).last_result == result


def test_a_first_look_that_fails_still_leaves_what_was_there_alone(Session):
    feed_id = add_feed(Session)
    started = []

    def offline(feed):
        raise OSError("HTTP Error 429: Too Many Requests")

    check(feed_id, Session, started.append, offline, NIGHT)
    result = check(feed_id, Session, started.append, lambda f: entries("old1", "old2"), NIGHT)
    assert started == [] and result.startswith("2 video(s) already there")


def test_two_checks_of_one_feed_at_once_start_each_video_once(Session):
    feed_id = add_feed(Session, include_existing=True)
    both = threading.Barrier(2, timeout=0.5)

    def slow(feed):
        # Without the per-feed lock both listings run side by side.
        with contextlib.suppress(threading.BrokenBarrierError):
            both.wait()
        return entries("a", "b")

    started, errors = [], []

    def look():
        try:
            check(feed_id, Session, started.append, slow, NIGHT)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=look) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(started) == 2


def test_one_feed_failing_doesnt_stop_the_others_being_checked(Session):
    feeds = [add_feed(Session, include_existing=True) for _ in range(2)]

    def start(job_id):
        raise RuntimeError("the queue is gone")

    autopilot.check_all(Session, start, lister=lambda f: entries(f"v{f.id}"), now=NIGHT)
    with Session() as db:
        assert all(db.get(Feed, i).last_result for i in feeds)
