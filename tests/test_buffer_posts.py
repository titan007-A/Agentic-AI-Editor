import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from agentic_editor import buffer, buffer_posts, hosting
from agentic_editor.db import BufferKey, BufferPost, HostedFile, Setting, User, make_sessionmaker
from agentic_editor.settings import Settings

NOW = datetime(2026, 11, 18, 1, 0, tzinfo=UTC)
MEDIA_ERROR = "It looks like there was an issue with the media attached to this post."
RAW = "Failed to create media container for instagram: ERROR: ERROR"


class FakeBuffer:
    """Buffer as the module sees it: posts by id, what was created and deleted."""

    def __init__(self):
        self.posts: dict[str, buffer.Posted] = {}
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.asked: list[str] = []
        self.waiting: dict[str, list[buffer.Posted]] = {}

    def post(self, key, post_id, client=None, count=None):
        count.n += 1
        self.asked.append(post_id)
        return self.posts.get(post_id)

    def create(self, key, data, client=None, count=None):
        count.n += 1
        self.created.append((key, data))
        made = buffer.Posted(
            f"new-{len(self.created)}", "scheduled", buffer.when(data.get("dueAt")) or NOW
        )
        self.posts[made.id] = made
        return made

    def delete_post(self, key, post_id, client=None, count=None):
        count.n += 1
        self.deleted.append(post_id)
        self.posts.pop(post_id, None)

    def pending(self, key, client=None, count=None):
        count.n += 1
        if key not in self.waiting:
            raise buffer.BufferError("Buffer refused the API key: it may be wrong or expired")
        return self.waiting[key]


@pytest.fixture
def world(tmp_path, monkeypatch):
    settings = Settings(media_dir=tmp_path / "media", database=tmp_path / "app.db")
    settings.public_url = "https://editor.example.com"
    sessions = make_sessionmaker(settings.database)
    fake = FakeBuffer()
    for name in ("post", "create", "delete_post", "pending"):
        monkeypatch.setattr(buffer, name, getattr(fake, name))
    monkeypatch.setattr(buffer_posts, "_last_backfill", None)
    with sessions() as session:
        owner = User(username="rishab", password_hash="x")
        session.add(owner)
        session.flush()
        channel = {"id": "c1", "name": "findultimatesongs", "service": "instagram",
                   "avatar": None, "paused": False, "kind": "business"}  # fmt: skip
        key = BufferKey(owner_id=owner.id, label="Find ultimate songs", key="key-1",
                        channels=[channel])  # fmt: skip
        session.add(key)
        session.commit()
        owner_id, key_id = owner.id, key.id
    return settings, sessions, fake, owner_id, key_id


def video(settings, name="Zo8iE8TAnJzCKKMXWXRRwwgeuGN7Bbjc.mp4", size=1000):
    folder = settings.media_dir / "pub"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"v" * size)
    return name


def add_post(sessions, owner_id, key_id, post_id="p1", status="scheduled", due=NOW, **more):
    with sessions() as session:
        row = BufferPost(
            post_id=post_id,
            owner_id=owner_id,
            key_id=key_id,
            channel_id="c1",
            channel="findultimatesongs",
            service="instagram",
            link=f"https://editor.example.com/api/pub/{more.get('pub', 'x.mp4')}",
            due_at=due,
            status=status,
            text="In 2016, Maren Morris...",
            created_at=due - timedelta(hours=12),
            **more,
        )
        session.add(row)
        session.commit()
        return row.id


def get(sessions, row_id):
    with sessions() as session:
        return session.get(BufferPost, row_id)


def test_a_post_is_first_asked_about_a_few_minutes_after_its_time_then_less_often():
    row = BufferPost(status="scheduled", due_at=NOW, created_at=NOW - timedelta(hours=1))
    assert buffer_posts.look(row, NOW + timedelta(minutes=2)) == "wait"
    assert buffer_posts.look(row, NOW + timedelta(minutes=3)) == "look"
    row.checked_at = NOW + timedelta(minutes=3)
    assert buffer_posts.look(row, NOW + timedelta(minutes=6)) == "wait"
    assert buffer_posts.look(row, NOW + timedelta(minutes=8)) == "look"
    row.checked_at = NOW + timedelta(hours=1)
    assert buffer_posts.look(row, NOW + timedelta(hours=1, minutes=20)) == "wait"
    assert buffer_posts.look(row, NOW + timedelta(hours=1, minutes=30)) == "look"
    assert buffer_posts.look(row, NOW + timedelta(hours=49)) == "give_up"
    # A post moved later in Buffer waits for its new time.
    row.checked_at, row.due_at = NOW + timedelta(minutes=3), NOW + timedelta(hours=5)
    assert buffer_posts.look(row, NOW + timedelta(hours=1)) == "wait"
    # Sent and gone posts are never asked about again.
    for status in ("sent", "gone", "retried"):
        row.status = status
        assert buffer_posts.look(row, NOW + timedelta(hours=6)) == "wait"


def test_a_post_that_went_out_is_marked_sent(world):
    settings, sessions, fake, owner_id, key_id = world
    row_id = add_post(sessions, owner_id, key_id)
    fake.posts["p1"] = buffer.Posted("p1", "sent", NOW, NOW + timedelta(minutes=2))
    assert buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=1))["asked"] == 0
    done = buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=4))
    assert done["asked"] == 1 and done["failed"] == 0
    row = get(sessions, row_id)
    assert row.status == "sent" and row.checks == 1
    assert buffer_posts.aware(row.sent_at) == NOW + timedelta(minutes=2)
    with sessions() as session:
        assert session.get(BufferKey, key_id).used >= 1  # counted on the key


def test_a_video_instagram_couldnt_take_is_sent_again_by_itself_once(world):
    settings, sessions, fake, owner_id, key_id = world
    pub = video(settings)
    row_id = add_post(sessions, owner_id, key_id, pub=pub, first_comment="first!")
    fake.posts["p1"] = buffer.Posted("p1", "error", NOW, error=MEDIA_ERROR, raw_error=RAW)
    done = buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=4))
    assert done == done | {"asked": 1, "failed": 1, "retried": 1}
    old = get(sessions, row_id)
    assert old.status == "retried" and old.raw_error == RAW
    assert fake.deleted == ["p1"]  # the failed post leaves Buffer
    (key, data), = fake.created  # fmt: skip
    assert key == "key-1" and data["mode"] == "customScheduled" and data["channelId"] == "c1"
    assert data["assets"] == [{"video": {"url": f"https://editor.example.com/api/pub/{pub}"}}]
    assert data["metadata"]["instagram"]["firstComment"] == "first!"
    due = buffer.when(data["dueAt"])
    assert timedelta(minutes=4) <= due - (NOW + timedelta(minutes=4)) <= timedelta(minutes=16)
    assert due.minute % 15 not in (14, 0, 1)  # off the minutes everyone posts at
    new = get(sessions, old.retried_by)
    assert new.auto and new.retry_of == row_id and new.status == "scheduled"
    assert new.pub == pub and new.text == old.text

    # The second try fails too: it stays failed for the person to decide.
    fake.posts[new.post_id] = buffer.Posted(new.post_id, "error", due, error=MEDIA_ERROR)
    done = buffer_posts.watch(sessions, settings, due + timedelta(minutes=4))
    assert done["failed"] == 1 and done["retried"] == 0
    assert get(sessions, new.id).status == "error" and len(fake.created) == 1


def test_failures_not_about_the_video_or_noticed_late_are_left_to_the_person(world):
    settings, sessions, fake, owner_id, key_id = world
    pub = video(settings)
    add_post(sessions, owner_id, key_id, "p1", pub=pub)
    fake.posts["p1"] = buffer.Posted("p1", "error", NOW, error="The account needs reconnecting")
    add_post(sessions, owner_id, key_id, "p2", pub=pub, due=NOW - timedelta(hours=7))
    fake.posts["p2"] = buffer.Posted("p2", "error", NOW - timedelta(hours=7), error=MEDIA_ERROR)
    done = buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=4))
    assert done["failed"] == 2 and done["retried"] == 0 and fake.created == []


def test_an_account_can_turn_sending_again_by_itself_off(world):
    settings, sessions, fake, owner_id, key_id = world
    pub = video(settings)
    add_post(sessions, owner_id, key_id, pub=pub)
    with sessions() as session:
        assert buffer_posts.auto_retry_on(session, owner_id)
        buffer_posts.set_auto_retry(session, owner_id, False)
        session.commit()
    fake.posts["p1"] = buffer.Posted("p1", "error", NOW, error=MEDIA_ERROR)
    assert buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=4))["retried"] == 0


def test_the_person_sends_a_failed_post_again_now_or_at_a_time(world):
    settings, sessions, fake, owner_id, key_id = world
    pub = video(settings)
    row_id = add_post(sessions, owner_id, key_id, status="error", pub=pub, error=MEDIA_ERROR)
    with pytest.raises(LookupError):
        buffer_posts.retry_by_id(sessions, settings, row_id, "someone-else")
    new = buffer_posts.retry_by_id(sessions, settings, row_id, owner_id)
    assert fake.created[-1][1]["mode"] == "shareNow" and fake.deleted == ["p1"]
    assert new["status"] == "scheduled" and new["retry_of"] == row_id and not new["auto"]
    with pytest.raises(buffer.BufferError, match="only a failed post"):
        buffer_posts.retry_by_id(sessions, settings, row_id, owner_id)  # already sent again

    later = add_post(sessions, owner_id, key_id, "p2", status="error", pub=pub)
    at = NOW + timedelta(days=1)
    buffer_posts.retry_by_id(sessions, settings, later, owner_id, at)
    assert fake.created[-1][1]["mode"] == "customScheduled"
    assert buffer.when(fake.created[-1][1]["dueAt"]) == at

    gone = add_post(sessions, owner_id, key_id, "p3", status="error", pub="missing.mp4")
    with pytest.raises(buffer.BufferError, match="isn't on the server"):
        buffer_posts.retry_by_id(sessions, settings, gone, owner_id)


def test_a_failed_post_can_be_removed_from_buffer(world):
    _settings, sessions, fake, owner_id, key_id = world
    row_id = add_post(sessions, owner_id, key_id, status="error")
    fake.posts["p1"] = buffer.Posted("p1", "error")
    waiting = add_post(sessions, owner_id, key_id, "p2")
    with pytest.raises(buffer.BufferError):
        buffer_posts.dismiss(sessions, waiting, owner_id)
    with pytest.raises(LookupError):
        buffer_posts.dismiss(sessions, row_id, "someone-else")
    buffer_posts.dismiss(sessions, row_id, owner_id)
    assert get(sessions, row_id).status == "gone" and fake.deleted == ["p1"]


def test_a_post_deleted_in_buffer_is_gone(world):
    settings, sessions, _fake, owner_id, key_id = world
    row_id = add_post(sessions, owner_id, key_id)
    buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=4))
    assert get(sessions, row_id).status == "gone"


def test_posts_waiting_in_buffer_from_before_are_read_once(world):
    settings, sessions, fake, owner_id, _key_id = world
    pub = video(settings)
    link = f"https://pjmediahubeditor.tech/api/pub/{pub}"
    fake.waiting["key-1"] = [
        buffer.Posted(
            "old1",
            "error",
            NOW,
            error=MEDIA_ERROR,
            raw_error=RAW,
            text="Maren Morris\nmore",
            channel_id="c1",
            assets=(link,),
        ),
        buffer.Posted("old2", "scheduled", NOW, channel_id="c1", assets=(link,)),
        buffer.Posted(
            "theirs", "scheduled", NOW, channel_id="c1", assets=("https://buffer-media.s3/x.mp4",)
        ),
    ]
    assert buffer_posts.backfill(sessions, settings, NOW)
    with sessions() as session:
        rows = {r.post_id: r for r in session.scalars(select(BufferPost))}
        assert set(rows) == {"old1", "old2"}  # a post made in Buffer itself isn't ours
        assert rows["old1"].status == "error" and rows["old1"].pub == pub
        assert rows["old1"].title == "Maren Morris" and rows["old1"].channel == "findultimatesongs"
        assert rows["old1"].owner_id == owner_id and rows["old1"].link == link
        assert session.get(Setting, buffer_posts.BACKFILLED) is not None
    # Done once: never asked again.
    fake.waiting["key-1"] = []
    assert buffer_posts.backfill(sessions, settings, NOW + timedelta(days=1))
    # Read failed posts aren't sent again by themselves: they failed before anyone watched.
    done = buffer_posts.watch(sessions, settings, NOW + timedelta(minutes=10))
    assert done["retried"] == 0 and fake.created == []


def test_reading_waits_for_every_key_before_old_files_may_go(world):
    settings, sessions, fake, _owner_id, _key_id = world
    assert not buffer_posts.backfill(sessions, settings, NOW)  # key-1 refused
    with sessions() as session:
        assert session.get(Setting, buffer_posts.BACKFILLED) is None
    # Asked again only hours later.
    fake.waiting["key-1"] = []
    assert not buffer_posts.backfill(sessions, settings, NOW + timedelta(hours=1))
    assert buffer_posts.backfill(sessions, settings, NOW + timedelta(hours=7))


def old(path, days):
    then = time.time() - days * 86400
    os.utime(path, (then, then))


def test_videos_here_go_once_no_post_waits_for_them(world, monkeypatch):
    settings, sessions, _fake, owner_id, key_id = world
    now = datetime.now(UTC)
    folder = settings.media_dir / "pub"
    sent = video(settings, "a" * 32 + ".mp4")
    add_post(sessions, owner_id, key_id, "p1", "sent", now - timedelta(days=4), pub=sent)
    recent = video(settings, "b" * 32 + ".mp4")
    add_post(sessions, owner_id, key_id, "p2", "sent", now - timedelta(days=1), pub=recent)
    waiting = video(settings, "c" * 32 + ".mp4")
    add_post(sessions, owner_id, key_id, "p3", "scheduled", now + timedelta(days=9), pub=waiting)
    failed = video(settings, "d" * 32 + ".mp4")
    add_post(sessions, owner_id, key_id, "p4", "error", now - timedelta(days=2), pub=failed)
    loose = video(settings, "e" * 32 + ".mp4")  # from before posts were followed
    # Old files without a post: their age is when the file last changed (ctime), which a
    # test can't set back, so the limit is moved instead.
    monkeypatch.setattr(buffer_posts, "KEEP_OLD_PUB", timedelta(seconds=-1))
    buffer_posts.sweep(sessions, settings, now)
    left = {p.name for p in folder.iterdir()}
    assert sent not in left and {recent, waiting, failed, loose} <= left  # not read yet
    with sessions() as session:
        session.add(Setting(key=buffer_posts.BACKFILLED, value="x"))
        session.commit()
    assert buffer_posts.sweep(sessions, settings, now)["pub_deleted"] == 1
    assert loose not in {p.name for p in folder.iterdir()}
    # Two weeks after its post failed, a video isn't kept for sending it again.
    buffer_posts.sweep(sessions, settings, now + timedelta(days=9))
    assert {p.name for p in folder.iterdir()} == {waiting}


class FakeR2:
    def __init__(self):
        self.put, self.deleted = [], []
        self.fail = None
        self.slow = 0.0

    def upload(self, config, path, key):
        time.sleep(self.slow)
        if self.fail:
            raise hosting.HostingError(self.fail)
        self.put.append((key, path.read_bytes()))

    def delete(self, config, key):
        self.deleted.append(key)


@pytest.fixture
def r2(world, monkeypatch):
    _settings, sessions, *_ = world
    fake = FakeR2()
    monkeypatch.setattr(hosting, "upload", fake.upload)
    monkeypatch.setattr(hosting, "delete", fake.delete)
    config = hosting.R2("0123456789abcdef", "AKID1234", "secret-xyz", "reels",
                        "https://media.example.com")  # fmt: skip
    with sessions() as session:
        hosting.save(session, config)
        session.commit()
    return fake


def test_a_video_goes_up_to_r2_once_even_when_asked_twice_at_once(world, r2):
    settings, sessions, *_ = world
    pub = video(settings)
    r2.slow = 0.2
    got = []
    threads = [
        threading.Thread(
            target=lambda: got.append(buffer_posts.ensure_hosted(sessions, settings, pub))
        )
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got == [(f"reels/{pub}", "")] * 3 and len(r2.put) == 1
    with sessions() as session:
        assert buffer_posts.link_of(session, f"reels/{pub}", pub, "") == (
            f"https://media.example.com/reels/{pub}"
        )
        assert buffer_posts.hosted_bytes(session) == 1000


def test_without_r2_or_when_it_fails_the_editors_own_link_is_used(world, r2, monkeypatch):
    settings, sessions, *_ = world
    pub = video(settings)
    r2.fail = "the token may not write to this bucket"
    key, note = buffer_posts.ensure_hosted(sessions, settings, pub)
    assert key is None and "token may not write" in note
    r2.fail = None
    monkeypatch.setattr(hosting, "CAP", 500)  # full
    key, note = buffer_posts.ensure_hosted(sessions, settings, pub)
    assert key is None and "nearly full" in note
    with sessions() as session:
        config = hosting.load(session)
        config.enabled = False
        hosting.save(session, config)
        session.commit()
        assert buffer_posts.ensure_hosted(sessions, settings, pub) == (None, "")
        with pytest.raises(buffer.BufferError, match="https"):
            buffer_posts.link_of(session, None, pub, "http://192.168.1.2:8000")


def test_r2_copies_go_an_hour_after_their_post_went_out(world, r2):
    settings, sessions, _fake, owner_id, key_id = world
    now = datetime.now(UTC)
    with sessions() as session:
        for name in ("sent", "waiting", "lonely", "ancient"):
            made = now - timedelta(days=50 if name == "ancient" else 0, hours=2)
            session.add(HostedFile(key=f"reels/{name}", pub=name, size=10, created_at=made))
        session.commit()
    sent_at = now - timedelta(minutes=90)
    add_post(sessions, owner_id, key_id, "p1", "sent", sent_at, hosted="reels/sent",
             sent_at=sent_at)  # fmt: skip
    add_post(sessions, owner_id, key_id, "p2", "scheduled", now + timedelta(hours=3),
             hosted="reels/waiting")  # fmt: skip
    add_post(sessions, owner_id, key_id, "p3", "scheduled", now + timedelta(hours=3),
             hosted="reels/ancient")  # fmt: skip
    buffer_posts.sweep(sessions, settings, now)
    # Sent, never used (a dialog closed without posting), and too old to keep anyway.
    assert sorted(r2.deleted) == ["reels/ancient", "reels/lonely", "reels/sent"]
    with sessions() as session:
        left = session.scalars(select(HostedFile).where(HostedFile.deleted_at.is_(None)))
        assert [f.key for f in left] == ["reels/waiting"]


def test_downloads_of_a_video_are_noted_on_its_posts(world):
    _settings, sessions, _fake, owner_id, key_id = world
    row_id = add_post(sessions, owner_id, key_id, pub="v.mp4")
    for n in range(12):
        entry = {"by": buffer_posts.who("2a03:2880:10ff:59::1"), "whole": n < 11, "bytes": n}
        buffer_posts.fetched(sessions, "v.mp4", entry)
    row = get(sessions, row_id)
    assert len(row.fetches) == 10 and row.fetches[-1]["bytes"] == 11
    assert buffer_posts.instagram_cut(row) and buffer_posts.view(row)["instagram_cut"]
    assert buffer_posts.who("54.87.198.8") == "54.87.198.8"


def test_retry_times_stay_off_the_round_minutes():
    for minute in range(60):
        at = buffer_posts.retry_time(NOW.replace(minute=minute))
        assert at.minute % 15 not in (14, 0, 1) and at > NOW.replace(minute=minute)


def test_a_post_buffer_names_twice_is_kept_once(world):
    _settings, sessions, _fake, owner_id, key_id = world
    add_post(sessions, owner_id, key_id, "p1", status="error")
    with sessions() as session:
        key = session.get(BufferKey, key_id)
        channel = buffer_posts.channel_of(key, "c1")
        args = dict(owner_id=owner_id, key=key, channel=channel, link="https://m/x.mp4", pub="x",
                    hosted="", text="t", first_comment="", draft=False, title="t")  # fmt: skip
        again = buffer_posts.record(session, made=buffer.Posted("p1", "scheduled", NOW),
                                    output_id=7, **args)  # fmt: skip
        session.commit()
        rows = session.scalars(select(BufferPost)).all()
        assert len(rows) == 1 and again.status == "scheduled" and again.output_id == 7
