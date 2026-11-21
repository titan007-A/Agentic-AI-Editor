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


@pytest.fixture(autouse=True)
def no_tries_left_over():
    autopilot._tries.clear()  # feed ids start again at 1 in each test's database


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


def test_an_instagram_account_is_read_through_the_search_engines_not_yt_dlp():
    def find(query, count):
        assert query.endswith("adele___official")
        links = ["OLDREEL01", "NEWREEL01", "OTHERS001"]
        return [{"href": f"https://www.instagram.com/reel/{c}/", "title": c} for c in links]

    def info(url):
        code = url.rstrip("/").rsplit("/", 1)[1]
        if code == "OTHERS001":  # a fan page naming the account
            return {"channel": "adele_fans", "description": "Adele cover"}
        posted = {"OLDREEL01": 1_700_000_000, "NEWREEL01": 1_760_000_000}[code]
        return {"channel": "adele___official", "description": f"Adele {code}", "timestamp": posted}

    link = "https://www.instagram.com/adele___official?cplk=NWl5aXJoMmxpMDV1"
    assert autopilot.instagram_handle(link) == "adele___official"
    assert autopilot.instagram_handle("https://www.instagram.com/reel/DcafVd6BocI/") is None
    assert autopilot.instagram_handle("https://www.youtube.com/@adele") is None
    assert autopilot.instagram_entries("adele___official", find, info, pause=0) == [
        Entry(
            "Instagram-NEWREEL01", "Adele NEWREEL01", "https://www.instagram.com/reel/NEWREEL01/"
        ),
        Entry(
            "Instagram-OLDREEL01", "Adele OLDREEL01", "https://www.instagram.com/reel/OLDREEL01/"
        ),
    ]

    def down(query, count):
        raise TimeoutError

    with pytest.raises(RuntimeError, match="search engines didn't answer"):
        autopilot.instagram_entries("adele___official", down, info, pause=0)

    def walled(url):
        raise RuntimeError("login required")

    with pytest.raises(RuntimeError, match="Instagram didn't show any reel"):
        autopilot.instagram_entries("adele___official", find, walled, pause=0)


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


def judge_by_name(calls=None):
    """Says a video is about the topic when its name starts with "gta"; one named "gone"
    couldn't be downloaded."""

    def judge(topic, found):
        if calls is not None:
            calls.append([e.key for e in found])
        verdicts = []
        for e in found:
            if e.key.startswith("gone"):
                verdicts.append(autopilot.Verdict(None, "couldn't download it: HTTP Error 404"))
            else:
                verdicts.append(autopilot.Verdict(e.key.startswith("gta"), "shows a street race"))
        return verdicts

    return judge


def test_a_topic_feed_edits_only_videos_about_it_and_never_looks_twice(Session):
    feed_id = add_feed(Session, include_existing=True, topic="GTA 5", batch=3)
    found = lambda f: entries("gta1", "minecraft", "gone", "gta2")  # noqa: E731
    started, calls = [], []
    result = check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls))
    assert result == (
        "started 2 new video(s) about GTA 5, passed over 1, 1 couldn't be downloaded (tried "
        "again next check)"
    )
    with Session() as db:
        items = {i.key: i for i in db.query(FeedItem)}
        jobs = db.query(Job).all()
        assert sorted(j.source for j in jobs) == ["/videos/gta1.mp4", "/videos/gta2.mp4"]
        assert items["minecraft"].skipped and items["minecraft"].job_id is None
        assert items["minecraft"].reason == "not GTA 5: shows a street race"
        assert "gone" not in items
        assert db.get(Feed, feed_id).last_result == result
    # 3 at a time: the first look took 3, then 1 more for the place left.
    assert calls == [["gta1", "minecraft", "gone"], ["gta2"]]
    # Only the one that couldn't be downloaded is tried again, and passed over at the
    # last try; nothing is started twice.
    for _ in range(autopilot.TOPIC_TRIES - 1):
        check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls))
    assert calls[2:] == [["gone"]] * (autopilot.TOPIC_TRIES - 1) and len(started) == 2
    with Session() as db:
        gone = db.query(FeedItem).filter_by(key="gone").one()
        assert gone.reason == "couldn't download it: HTTP Error 404"
    check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls))
    assert len(calls) == autopilot.TOPIC_TRIES + 1  # nothing left to look at


def test_a_topic_feed_looks_at_a_few_videos_a_check_and_only_while_there_is_room(Session):
    feed_id = add_feed(Session, include_existing=True, topic="GTA 5", batch=2)
    names = [f"other{i}" for i in range(10)] + ["gta1", "gta2", "gta3"]
    found = lambda f: entries(*names)  # noqa: E731
    started, calls = [], []
    result = check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls))
    assert sum(map(len, calls)) == autopilot.TOPIC_LOOKS
    assert result == f"started 0 new video(s) about GTA 5, passed over 8, {13 - 8} waiting"
    check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls))
    assert len(started) == 2
    # Both places are taken: the third isn't even downloaded until one finishes.
    looked = sum(map(len, calls))
    assert check(feed_id, Session, started.append, found, NIGHT, judge_by_name(calls)) == (
        "started 0 new video(s), 1 waiting"
    )
    assert sum(map(len, calls)) == looked


def test_a_topic_feed_whose_model_cant_be_reached_tries_again_next_time(Session):
    feed_id = add_feed(Session, include_existing=True, topic="GTA 5")

    def down(topic, found):
        raise RuntimeError("qwen3.5:4b could not be reached")

    started = []
    result = check(feed_id, Session, started.append, lambda f: entries("gta1"), NIGHT, down)
    assert result.endswith("couldn't look at the rest (qwen3.5:4b could not be reached), the "
                           "next check tries again")  # fmt: skip
    with Session() as db:
        assert db.query(FeedItem).count() == 0
    check(feed_id, Session, started.append, lambda f: entries("gta1"), NIGHT, judge_by_name())
    assert len(started) == 1


def test_a_topic_feeds_first_look_still_only_notes_what_is_there(Session):
    feed_id = add_feed(Session, topic="GTA 5")
    calls = []
    result = check(feed_id, Session, print, lambda f: entries("gta1"), NIGHT, judge_by_name(calls))
    assert result.startswith("1 video(s) already there") and calls == []


def test_the_scan_checks_topic_feeds_on_their_own_thread_and_never_twice_at_once(Session):
    plain = add_feed(Session, include_existing=True)
    topical = add_feed(Session, include_existing=True, topic="GTA 5")
    gate, looking = threading.Event(), threading.Event()

    def judge(topic, found):
        looking.set()
        gate.wait(5)  # waiting for the GPU behind a job
        return judge_by_name()(topic, found)

    started = []
    lister = lambda f: entries(f"gta{f.id}")  # noqa: E731
    threads = autopilot.check_all(Session, started.append, lister=lister, now=NIGHT, judge=judge)
    assert len(threads) == 1 and looking.wait(5)
    with Session() as db:  # the plain feed didn't wait for it
        assert db.get(Feed, plain).last_result == "started 1 new video(s)"
    # The next scan finds that feed's check still going and leaves it be.
    assert autopilot.check_all(Session, started.append, lister=lister, now=NIGHT) == []
    gate.set()
    threads[0].join(5)
    with Session() as db:
        assert db.get(Feed, topical).last_result == "started 1 new video(s) about GTA 5"
    assert len(started) == 2


def test_judging_a_topic_shows_the_model_frames_from_each_downloaded_video(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agentic_editor import download, ffmpeg, pipeline, review

    def resolve(source, settings):
        if "gone" in source:
            raise RuntimeError("HTTP Error 404: Not Found")
        return download.Source(path=tmp_path / "v.mp4", title="t", description="gta rp moments")

    def sample(video, duration, dest, count):
        assert duration == 30.0 and count == autopilot.TOPIC_FRAMES
        return [dest / f"frame-{i}.jpg" for i in range(count)]

    monkeypatch.setattr(pipeline, "resolve_source", resolve)
    monkeypatch.setattr(ffmpeg, "probe", lambda path: SimpleNamespace(duration=30.0))
    monkeypatch.setattr(review, "sample_frames", sample)
    asked = []

    class Vision:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def chat_json(self, messages, schema, images=None, temperature=0.3):
            asked.append((messages[0]["content"], len(images or [])))
            if images:  # what the frames show, asked without the topic
                return {"game": "Grand Theft Auto V", "shows": "a car chase", "text": "GTA VI"}
            first = len(asked) == 2
            return {"same_series": first, "exact": False, "named": first, "named_other": False,
                    "why": " a car chase\n in Vice City " if first else "a quiz"}  # fmt: skip

    router = SimpleNamespace(vision=lambda: Vision())
    found = [Entry("a", "a", "https://x.test/a"), Entry("gone", "g", "https://x.test/gone"),
             Entry("b", "b", "https://x.test/b")]  # fmt: skip
    verdicts = autopilot.judge_topic("GTA 6", found, settings=object(), router=router)
    assert verdicts == [
        autopilot.Verdict(True, "a car chase in Vice City"),
        autopilot.Verdict(None, "couldn't download it: HTTP Error 404: Not Found"),
        autopilot.Verdict(False, "a quiz"),
    ]
    assert [n for _, n in asked] == [autopilot.TOPIC_FRAMES, 0] * 2
    assert "GTA 6" not in asked[0][0]  # the frames are described before the topic is told
    assert "GTA 6" in asked[1][0] and "gta rp moments" in asked[1][0] and "GTA VI" in asked[1][0]


@pytest.mark.parametrize(
    "answer, fitting",
    [
        # GTA 6 footage the model calls GTA V, in a video that says GTA 6
        ({"same_series": True, "exact": False, "named": True, "named_other": False}, True),
        # GTA 5 roleplay, asked about GTA 6
        ({"same_series": True, "exact": False, "named": False, "named_other": True}, False),
        # GTA 6 footage the model calls GTA V, asked about GTA 5: the video says GTA 6
        ({"same_series": True, "exact": True, "named": False, "named_other": True}, False),
        # the topic itself, with nothing said about it
        ({"same_series": True, "exact": True, "named": False, "named_other": False}, True),
        # real life under a caption that names the topic
        ({"same_series": False, "exact": False, "named": True, "named_other": False}, False),
        ({}, False),
    ],
)
def test_the_videos_own_words_decide_which_part_of_a_series_it_is(answer, fitting):
    assert autopilot.fits(answer) is fitting


def test_a_feeds_finished_videos_go_in_its_folder_made_once(Session):
    from agentic_editor.db import Output, PileFolder

    feed_id = add_feed(Session, include_existing=True, folder="GTA-kick", owner_id="emp1")
    check(feed_id, Session, print, lambda f: entries("a", "b"), NIGHT)
    with Session() as db:
        # Someone else's folder of that name, and one in another preset's folder, don't count.
        db.add_all([PileFolder(owner_id="other", preset="p", name="GTA-kick"),
                    PileFolder(owner_id="emp1", preset="q", name="GTA-kick")])  # fmt: skip
        db.commit()
        for job in db.query(Job).all():
            job.outputs.append(Output(preset="p", path="/o.mp4", duration=10, planner="m", edl={}))
            db.flush()
            autopilot.file_outputs(db, job)
        db.commit()
        mine = db.query(PileFolder).filter_by(owner_id="emp1", preset="p").all()
        assert [f.name for f in mine] == ["GTA-kick"]
        assert {o.folder_id for o in db.query(Output)} == {mine[0].id}


def test_a_feed_without_a_folder_leaves_its_videos_in_the_presets_folder(Session):
    from agentic_editor.db import Output

    feed_id = add_feed(Session, include_existing=True)
    check(feed_id, Session, print, lambda f: entries("a"), NIGHT)
    with Session() as db:
        job = db.query(Job).one()
        job.outputs.append(Output(preset="p", path="/o.mp4", duration=10, planner="m", edl={}))
        db.flush()
        autopilot.file_outputs(db, job)
        assert job.outputs[0].folder_id is None


def test_a_topic_without_a_part_takes_any_part_of_the_series():
    # GTA 5 roleplay, the video saying "GTA V": not GTA 6, but GTA.
    answer = {"same_series": True, "exact": False, "named": False, "named_other": True}
    assert not autopilot.fits(answer, autopilot.names_a_part("GTA 6"))
    assert autopilot.fits(answer, autopilot.names_a_part("GTA"))
    assert not autopilot.fits({"same_series": False}, autopilot.names_a_part("GTA"))
    assert [autopilot.names_a_part(t) for t in ("GTA VI", "FIFA 24", "GTA", "Minecraft")] == [
        True, True, False, False,
    ]  # fmt: skip
