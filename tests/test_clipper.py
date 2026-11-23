import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import yaml

from agentic_editor import autopilot, cleanup, jobs, pipeline
from agentic_editor.backends import Backends
from agentic_editor.clipper import hunt, kick, moments
from agentic_editor.db import (
    Job,
    KickHunt,
    KickStreamer,
    Output,
    PileFolder,
    User,
    make_sessionmaker,
)
from agentic_editor.settings import Settings

STARTED = datetime(2026, 11, 19, 21, 3, 0, tzinfo=UTC)
UUID = "61802db4-bbbd-4c96-a0a2-30db8bb54598"
# The website's newer id for the same stream: a UUIDv7 made when it started (STARTED).
NEW_ID = "01a1f59e-afa0-7c3e-9d1f-2b8e4f6a0c11"


def playlist_text(seconds: float, start: datetime = STARTED, piece: float = 10.0) -> str:
    rows = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", "#EXT-X-PLAYLIST-TYPE:EVENT"]
    t = 0.0
    i = 0
    while t < seconds:
        length = min(piece, seconds - t)
        at = (start + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        rows += [f"#EXT-X-PROGRAM-DATE-TIME:{at}", f"#EXTINF:{length:.3f},", f"{i}.ts"]
        t += length
        i += 1
    return "\n".join([*rows, "#EXT-X-ENDLIST"]) + "\n"


class FakeKick:
    """Kick as the clipper sees it: a streamer `xqc` with one past stream (and a live one
    when `live_for` is set), a chat that is quiet but for a burst, and viewers' clips."""

    def __init__(self, seconds=3600.0, burst=(1810, 1822), live_for=None, clips=()):
        self.seconds = seconds
        self.burst = burst
        self.live_for = live_for  # seconds the live stream has been on, or None
        self.viewer_clips = list(clips)
        self.chat_reads = 0
        self.closed = False

    def close(self):
        self.closed = True

    def _stream(self, live=False):
        return kick.Stream(
            id=999 if live else 138952164, title="LIVE GTA 6" if live else "GTA 6 playthrough",
            started=STARTED, duration=0 if live else self.seconds, live=live,
            source=f"https://stream.test/{'live' if live else 'vod'}/master.m3u8",
            uuid="0d23ed0a-41f0-408c-83b5-c0f58921ca42" if live else UUID,
        )  # fmt: skip

    def channel(self, slug):
        if slug != "xqc":
            raise kick.KickError(f"no Kick channel {slug!r}")
        live = self._stream(live=True) if self.live_for is not None else None
        return kick.Channel(id=668, slug="xqc", username="xQc", avatar="a.webp", live=live)

    def streams(self, slug):
        found = [self._stream()]
        if self.live_for is not None:
            found.insert(0, self._stream(live=True))
        return found

    def video(self, uuid, slug=""):
        """By the API's id or (with the streamer named) the website's; the stream comes
        back with the website's id, as Kick's answer gives it."""
        if uuid != UUID and not (uuid == NEW_ID and slug == "xqc"):
            raise kick.KickError("Kick doesn't know that stream")
        return "xqc", replace(self._stream(), uuid=NEW_ID)

    def variants(self, source):
        base = source.rsplit("/", 1)[0]
        return {"1080p60": f"{base}/1080p60/playlist.m3u8", "160p30": f"{base}/160p30/p.m3u8"}

    def playlist(self, url):
        seconds = self.live_for if "/live/" in url else self.seconds
        return kick.parse_playlist(playlist_text(seconds), url)

    def chat(self, channel_id, start):
        self.chat_reads += 1
        t = (start - STARTED).total_seconds()
        lines = []
        for s in range(int(t), int(t + kick.CHAT_SPAN)):
            at = STARTED + timedelta(seconds=s)
            lines.append(kick.Line(at, "hello"))
            if self.burst[0] <= s < self.burst[1]:
                lines += [kick.Line(at, "KEKW"), kick.Line(at, "[emote:1:KEKW] clip it")] * 3
        return lines

    def clips(self, slug, stream):
        return self.viewer_clips


# -- links, Kick's answers, playlists


def test_links_name_a_stream_or_a_streamer():
    vod = f"https://kick.com/xqc/videos/{UUID}"
    assert kick.parse_link(vod) == kick.Link("stream", "xqc", UUID)
    assert kick.parse_link(f"kick.com/video/{UUID}?t=5") == kick.Link("stream", "", UUID)
    assert kick.parse_link("https://kick.com/XQC") == kick.Link("channel", "xqc")
    assert kick.parse_link("@xqc") == kick.Link("channel", "xqc")
    for wrong in ("https://kick.com/xqc/clips/clip_01M7", "https://youtube.com/watch?v=1", "a b"):
        with pytest.raises(kick.KickError):
            kick.parse_link(wrong)


def test_kick_answers_are_read_into_a_channel_its_streams_chat_and_clips():
    live = {
        "id": 999, "is_live": True, "session_title": "  LIVE   now ",
        "start_time": "2026-11-22 01:21:19", "source": None, "duration": 0,
    }  # fmt: skip
    answers = {
        "/api/v2/channels/xqc": {
            "id": 668, "slug": "xqc", "user": {"username": "xQc", "profile_pic": "p.webp"},
            "livestream": live, "chatroom": {"id": 668},
        },
        "/api/v2/channels/xqc/videos": [
            {**live, "source": "https://stream.test/m.m3u8", "video": {"uuid": "u-live"}},
            {"id": 5, "is_live": False, "session_title": "old", "start_time": "2026-11-19 21:03:03",
             "duration": 48979000, "source": "https://stream.test/old.m3u8",
             "video": {"uuid": UUID, "views": 84085}, "thumbnail": {"src": "t.webp"}},
        ],
        "/api/v2/channels/668/messages": {"data": {"messages": [
            {"created_at": "2026-11-20T22:00:01Z", "content": "LOL", "sender": {"username": "b"}},
            {"created_at": "2026-11-20T22:00:00Z", "content": "hi", "sender": {"username": "a"}},
        ]}},
        "/api/v2/channels/xqc/clips": {"clips": [
            {"id": "clip_1", "livestream_id": "5", "created_at": "2026-11-19T22:00:00.628411123Z",
             "duration": 30, "views": 120, "title": "big play"},
            {"id": "clip_2", "livestream_id": "5", "created_at": "2026-11-21T22:00:00Z",
             "duration": 30, "views": 9, "title": "made later from the video"},
            {"id": "clip_3", "livestream_id": "77", "created_at": "2026-11-19T22:00:00Z",
             "duration": 30, "views": 9, "title": "another stream"},
        ]},
    }  # fmt: skip
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        assert request.headers["user-agent"].startswith("Mozilla/5.0")
        return httpx.Response(200, json=answers[request.url.path])

    client = kick.Client(httpx.MockTransport(answer), pause=0)
    channel = client.channel("xqc")
    assert (channel.id, channel.username, channel.avatar) == (668, "xQc", "p.webp")
    assert channel.live.title == "LIVE now" and channel.live.source == "https://stream.test/m.m3u8"
    old = client.streams("xqc")[1]
    assert (old.id, old.uuid, old.duration, old.views) == (5, UUID, 48979, 84085)
    assert old.thumbnail == "t.webp"
    assert old.started == datetime(2026, 11, 19, 21, 3, 3, tzinfo=UTC)
    lines = client.chat(668, datetime(2026, 11, 20, 22, 0, tzinfo=UTC))
    assert [line.text for line in lines] == ["hi", "LOL"]
    assert "start_time=2026-11-20T22:00:00.000Z" in asked[-1]
    clips = client.clips("xqc", old)
    assert [c.id for c in clips] == ["clip_1"]  # placed only when made live, of this stream


def test_kick_refusing_is_asked_again_then_told():
    calls = []

    def answer(request):
        calls.append(1)
        return httpx.Response(429 if len(calls) < 2 else 200, json={"id": 1, "slug": "a"})

    assert kick.Client(httpx.MockTransport(answer), pause=0).channel("a").id == 1
    gone = kick.Client(httpx.MockTransport(lambda r: httpx.Response(404)), pause=0)
    with pytest.raises(kick.KickError, match="404"):
        gone.channel("nobody")
    locked = kick.Client(httpx.MockTransport(lambda r: httpx.Response(403)), pause=0)
    with pytest.raises(kick.KickError, match="subscribers-only stream needs a signed-in"):
        locked.playlist("https://stream.kick.com/x/160p30/playlist.m3u8")


def test_the_website_s_newer_stream_id_is_found_by_when_the_stream_started():
    assert kick.minted(NEW_ID) == STARTED
    assert kick.minted(UUID) is None  # a version 4 id carries no time
    listed = [
        {"id": 6, "session_title": "later", "start_time": "2026-11-20 01:00:00",
         "source": "https://stream.test/later.m3u8", "video": {"uuid": "u-later"}},
        {"id": 5, "session_title": "GTA 6", "start_time": "2026-11-19 21:03:00",
         "source": "https://stream.test/old.m3u8", "video": {"uuid": UUID}},
    ]  # fmt: skip
    by_old_id = {
        "uuid": UUID, "source": "https://stream.test/old.m3u8",
        "livestream": {"id": 5, "session_title": "GTA 6", "start_time": "2026-11-19 21:03:00",
                       "vod_id": NEW_ID, "channel": {"slug": "xqc"}},
    }  # fmt: skip
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == f"/api/v1/video/{UUID}":
            return httpx.Response(200, json=by_old_id)
        if request.url.path.startswith("/api/v1/video/"):
            return httpx.Response(404, json={"message": "No query results for model [Video]."})
        return httpx.Response(200, json=listed)

    client = kick.Client(httpx.MockTransport(answer), pause=0)
    slug, stream = client.video(NEW_ID, "xqc")
    # Found in the list, and kept by the website's id: the website no longer opens the old.
    assert (slug, stream.id, stream.uuid, stream.source) == (
        "xqc", 5, NEW_ID, "https://stream.test/old.m3u8"
    )  # fmt: skip
    assert asked == [f"/api/v1/video/{NEW_ID}", "/api/v2/channels/xqc/videos"]
    slug, stream = client.video(UUID)
    assert (slug, stream.id, stream.uuid) == ("xqc", 5, NEW_ID)  # Kick names the website's id
    assert hunt.website_id(client, UUID) == NEW_ID
    assert hunt.website_id(client, "11111111-2222-4333-8444-555555555555") == (
        "11111111-2222-4333-8444-555555555555"
    )  # Kick won't say: the id kept as it was
    assert hunt.website_id(client, "") == ""
    with pytest.raises(kick.KickError, match="no streamer's name"):
        client.video(NEW_ID)
    gone = "01a1f000-0000-7000-8000-000000000000"  # started before every stream listed
    with pytest.raises(kick.KickError, match="no longer lists xqc's stream"):
        client.video(gone, "xqc")
    with pytest.raises(kick.KickError, match="404"):  # an old id Kick doesn't know stays a 404
        client.video("11111111-2222-4333-8444-555555555555", "xqc")


def test_a_playlist_places_the_chat_s_clock_times_in_the_stream():
    text = playlist_text(35)
    pl = kick.parse_playlist(text, "https://stream.test/v/160p30/playlist.m3u8")
    assert [p.start for p in pl.pieces] == [0, 10, 20, 30] and pl.duration == 35
    assert pl.pieces[1].url == "https://stream.test/v/160p30/1.ts"
    assert pl.at(12.5) == STARTED + timedelta(seconds=12.5)
    assert pl.time_of(STARTED + timedelta(seconds=27)) == pytest.approx(27)
    assert [p.start for p in pl.between(9, 21)] == [0, 10, 20]
    master = (
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=9,RESOLUTION=1920x1080,VIDEO="1080p60"\n1080p60/p.m3u8\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=284x160,VIDEO="160p30"\n160p30/p.m3u8\n'
    )
    client = kick.Client(httpx.MockTransport(lambda r: httpx.Response(200, text=master)))
    found = client.variants("https://stream.test/v/master.m3u8")
    assert kick.best(found) == "https://stream.test/v/1080p60/p.m3u8"
    assert kick.smallest(found) == "https://stream.test/v/160p30/p.m3u8"
    assert kick.best({"1440p60": "a", "720p60": "b"}, 1080) == "b"


# -- moments


def chat_lines(seconds, bursts=(), base=1):
    lines = []
    for s in range(seconds):
        lines += [(s + 0.5, "hello")] * base
        for lo, hi, text, per in bursts:
            if lo <= s < hi:
                lines += [(s + 0.5, text)] * per
    return lines


def test_hype_and_clip_requests_weigh_more_than_plain_chat():
    assert moments.weight("hello there") == 1
    assert moments.weight("KEKWWWW") == 2 and moments.weight("[emote:37226:KEKW]") == 2
    assert moments.weight("LMAOOOOO 💀") == 2
    assert moments.weight("clip it") == 3 and moments.weight("CLIP THAT KEKW") == 4


def test_a_burst_in_chat_is_a_moment_just_before_the_chat_answers_it():
    lines = chat_lines(600, [(300, 310, "KEKW", 6)])
    found = moments.find(1000, 1600, 1, [(t + 1000, x) for t, x in lines], [(1000, 1600)])
    m = found[0]
    assert 1290 <= m.peak <= 1306  # what happened is before the chat's answer at 1300
    assert m.start < m.peak < m.end and m.end - m.start == moments.BEFORE + moments.AFTER
    assert m.start <= 1300 <= m.end and 1300 <= m.chat_at <= m.end + moments.CHAT_LAG
    assert m.why[0].startswith("chat ×") and '"kekw" ×' in " ".join(m.why)


def test_moments_keep_apart_and_a_live_stretch_keeps_only_strong_ones():
    lines = chat_lines(900, [(200, 208, "LOL", 5), (215, 220, "LOL", 4), (700, 708, "W", 5)])
    found = moments.find(0, 900, 5, lines, [(0, 900)])
    spans = sorted((m.start, m.end) for m in found)
    assert all(a[1] + moments.APART <= b[0] for a, b in itertools.pairwise(spans))
    assert {round(m.peak, -2) for m in found[:2]} == {200, 700}
    flat = chat_lines(900)
    assert moments.find(0, 900, 3, flat, [(0, 900)], least=moments.STRONG) == []
    assert len(moments.find(0, 900, 3, flat, [(0, 900)])) == 1  # asked for: the best there is


def test_a_short_stretch_gets_windows_inside_it():
    """A 3-minute stretch (the owner's short test): its moments are apart and inside it."""
    lines = chat_lines(180, [(30, 36, "KEKW", 6), (140, 146, "W", 6)])
    found = moments.find(0, 180, 3, lines, [(0, 180)])
    assert len(found) >= 2 and {round(m.peak, -1) for m in found[:2]} == {30, 140}
    for m in found:
        assert 0 <= m.start < m.peak < m.end <= 180
        assert moments.SHORTEST <= m.end - m.start <= moments.LONGEST
    (tiny,) = moments.find(0, 25, 1, chat_lines(25, [(2, 4, "LOL", 5)]), [(0, 25)])
    assert tiny.start >= 0 and tiny.end <= 25 and tiny.end - tiny.start == 15


def test_a_clip_s_ends_move_to_quiet_seconds_near_them():
    loud = np.full(300, -20.0)
    loud[100:106] = -5.0  # the moment
    loud[84] = -60.0  # a pause 2 s before where the clip would start (103 - 18 = 85)
    loud[112] = -60.0  # and one near its end
    (m,) = moments.find(0, 300, 1, loudness=loud)
    assert (m.start, m.end) == (84, 113)  # through the end of the quiet second
    loud[112], loud[114] = -20.0, -60.0
    (m,) = moments.find(0, 300, 1, loudness=loud)
    assert (m.start, m.end) == (84, 114)  # never longer than LONGEST


def test_sound_and_viewers_clips_find_moments_without_chat():
    loud = np.full(600, -40.0)
    loud[400:406] = -22.0
    found = moments.find(0, 600, 1, loudness=loud)
    assert 395 <= found[0].peak <= 410 and "loud" in found[0].why
    found = moments.find(0, 600, 1, clips=[(250.0, 30.0, 500)])
    assert 225 <= found[0].peak <= 253 and "viewers clipped it" in found[0].why


def test_seconds_not_heard_count_as_usual():
    loud = np.full(600, np.nan)
    loud[300:420] = -40.0
    loud[350:356] = -20.0
    (m,) = moments.find(0, 600, 1, loudness=loud)
    assert 345 <= m.peak <= 362 and "loud" in m.why
    assert moments.find(0, 600, 1, loudness=np.full(600, np.nan))[0].why == ["weak moment"]


def test_gaps_in_the_chat_read_are_filled_between_reads():
    weights, known = moments.chat_signal(30, 0, [(0, 5), (20, 25)], [(1, "a"), (21, "a")] * 2)
    assert known[:5].all() and not known[5:20].any()
    assert weights[10] == pytest.approx(0.0, abs=0.5)


# -- a hunt


@pytest.fixture
def world(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    presets = tmp_path / "presets"
    folder = presets / "people" / "emp1"
    folder.mkdir(parents=True)
    preset = {"name": "kick-clips", "label": "Kick clips", "min_duration": 12, "max_duration": 45}
    (folder / "kick-clips.yaml").write_text(yaml.safe_dump(preset))
    settings = Settings(presets_dir=presets, media_dir=tmp_path / "media")
    with Session() as db:
        db.add(User(id="emp1", username="employee", password_hash="x"))
        db.commit()
    cuts = []

    def fake_cut(full, start, end, dest):
        cuts.append((start, end, full.pieces[0].url))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"window")

    monkeypatch.setattr(hunt, "cut", fake_cut)
    monkeypatch.setattr(hunt, "listen", lambda light, start, end: np.full(int(end - start), -40.0))
    return SimpleNamespace(Session=Session, settings=settings, cuts=cuts)


def add_hunt(world, client, start=0.0, end=0.0, count=3, live=False, folder=""):
    with world.Session() as db:
        found = hunt.new_hunt(
            db, "emp1", "xqc", "xQc", 668, client.video(UUID)[1] if not live else
            client.channel("xqc").live, start, end, count, "kick-clips", live=live, folder=folder,
        )  # fmt: skip
        db.commit()
        return found.id


def test_a_hunt_cuts_its_moments_and_queues_15_to_30_second_jobs(world):
    client = FakeKick(seconds=3600, burst=(1810, 1822))
    hunt_id = add_hunt(world, client, start=1700, end=1880, count=3)
    queued = []
    hunt.run(hunt_id, world.Session, world.settings, client=client, queue=queued.append)

    with world.Session() as db:
        found = db.get(KickHunt, hunt_id)
        assert found.status == "done", found.error
        assert found.message.startswith("found ") and found.start == 1700 and found.end == 1880
        first = found.moments[0]
        assert first["start"] <= 1810 <= first["end"] and first["job_id"] == queued[0]
        job = db.get(Job, queued[0])
        assert job.source == f"kick:kick-{hunt_id}-0" and job.presets == ["kick-clips"]
        assert not job.trim and job.owner_id == "emp1"  # kept whole: the clip is cut here
        assert "into this video" in job.notes and job.title.startswith("xQc · GTA 6")
        for m in found.moments:
            assert 15 <= m["end"] - m["start"] <= 30
    # The whole 3 minutes and 15 s past them (the chat answers late), five seconds a read.
    assert client.chat_reads == (180 + hunt.CHAT_AFTER) // 5
    assert world.cuts[0][2].endswith("/1080p60/0.ts")  # cut in full quality
    source = pipeline.resolve_source(f"kick:kick-{hunt_id}-0", world.settings)
    assert source.uploader == "xQc" and source.path.read_bytes() == b"window"
    # The post caption is told whose stream and its title (which names the game).
    assert source.description.startswith("A moment from xQc's Kick live stream titled \"GTA 6")
    assert '"kekw" ×' in source.description
    # The website opens the stream by its newer id, not the one the API lists.
    assert source.url == f"https://kick.com/xqc/videos/{NEW_ID}?t={int(first['start'])}"


def test_a_long_stream_is_sampled_then_read_again_around_its_likely_moments(world, monkeypatch):
    heard = []

    def listen(light, a, b):
        heard.append((a, b))
        return np.full(int(b - a), -40.0)

    monkeypatch.setattr(hunt, "listen", listen)
    client = FakeKick(seconds=6 * 3600, burst=(9010, 9050))  # between two samples' starts
    hunt_id = add_hunt(world, client, count=2)
    hunt.run(hunt_id, world.Session, world.settings, client=client, queue=lambda j: None)
    with world.Session() as db:
        found = db.get(KickHunt, hunt_id)
        assert found.status == "done" and found.end == 6 * 3600
        best = found.moments[0]
        assert best["start"] <= 9010 <= best["end"]
        assert 9000 <= best["peak"] <= 9020  # placed by the full read, not the samples
    # Sampled (one read every 36 s), then in full around the likely moments.
    assert hunt.CHAT_READS < client.chat_reads < hunt.CHAT_READS + 6 * 150 // 5 + 10
    assert heard and sum(b - a for a, b in heard) <= 6 * sum(hunt.CLOSER)
    assert any(a <= 9010 < b for a, b in heard)  # heard only around the likely moments


def test_regions_around_likely_moments_join_where_they_meet():
    near = [moments.Moment(0, 0, peak, 1.0) for peak in (500, 600, 2000)]
    assert hunt.regions_of(near, 0, 2030) == [(410, 660), (1910, 2030)]


def test_a_hunt_that_cant_be_done_says_why(world):
    client = FakeKick(seconds=3600)
    hunt_id = add_hunt(world, client, start=100, end=110)
    hunt.run(hunt_id, world.Session, world.settings, client=client, queue=lambda j: None)
    with world.Session() as db:
        found = db.get(KickHunt, hunt_id)
        assert found.status == "failed" and "too short for a clip" in found.error

    class Gone(FakeKick):
        def variants(self, source):
            raise kick.KickError("Kick answered 503; try again later")

    hunt_id = add_hunt(world, client)
    hunt.run(hunt_id, world.Session, world.settings, client=Gone(), queue=lambda j: None)
    with world.Session() as db:
        assert db.get(KickHunt, hunt_id).error == "Kick answered 503; try again later"


def test_a_moment_s_job_keeps_its_clip_whole(world, monkeypatch):
    client = FakeKick(seconds=3600, burst=(1810, 1822))
    hunt_id = add_hunt(world, client, start=1700, end=1880, count=1)
    queued = []
    hunt.run(hunt_id, world.Session, world.settings, client=client, queue=queued.append)
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    seen = {}

    def run(source, presets, settings, backends, notes, progress, run_id):
        seen.update(source=source, trim=presets[0].trim, notes=notes)
        return pipeline.Result("t", [])

    jobs.run_job(queued[0], world.Session, world.settings, run)
    assert seen["source"] == f"kick:kick-{hunt_id}-0" and seen["trim"] is False
    assert seen["notes"].startswith("A clip of a moment from a Kick live stream")


def test_a_moment_s_clips_go_in_its_hunt_s_folder_and_its_video_is_kept(world):
    client = FakeKick()
    hunt_id = add_hunt(world, client, folder="  GTA   clips ")
    with world.Session() as db:
        job = Job(id="j1", source=f"kick:kick-{hunt_id}-0", presets=["kick-clips"],
                  owner_id="emp1", status="done")  # fmt: skip
        out = Output(preset="kick-clips", path="a.mp4", duration=20, planner="m", edl={})
        job.outputs.append(out)
        db.add(job)
        db.flush()
        autopilot.file_outputs(db, job, now=datetime(2026, 11, 24, 9))
        day = db.get(PileFolder, job.outputs[0].folder_id)  # the day's folder, inside the hunt's
        folder = db.get(PileFolder, day.parent_id)
        assert (day.name, day.owner_id, day.preset) == ("24-NOV", "emp1", "kick-clips")
        assert (folder.name, folder.owner_id, folder.preset) == ("GTA clips", "emp1", "kick-clips")
        db.add(Job(id="j2", source=f"kick:kick-{hunt_id}-1", presets=["kick-clips"]))
        db.commit()
    sources = world.settings.media_dir / "sources"
    for n in (0, 1):
        (sources / f"kick-{hunt_id}-{n}").mkdir(parents=True)
        (sources / f"kick-{hunt_id}-{n}" / "source.mp4").write_bytes(b"x")
    cleanup.sweep(world.Session, world.settings, now=datetime.now(UTC) + timedelta(days=400))
    assert (sources / f"kick-{hunt_id}-1").is_dir()  # its job still waits for it
    assert not (sources / f"kick-{hunt_id}-0").exists()  # went with its old job


def test_followed_streamers_are_clipped_live_a_stretch_at_a_time(world, monkeypatch):
    monkeypatch.setattr(hunt.features, "enabled", lambda account, name, db: True)
    with world.Session() as db:
        db.add(KickStreamer(id="s1", owner_id="emp1", slug="xqc", live=True, preset="kick-clips",
                            per_hour=6, folder="xQc live"))  # fmt: skip
        db.commit()
    client = FakeKick(seconds=40000, live_for=3000)
    started = hunt.check_live(world.Session, client=client, queue=lambda h: None)
    with world.Session() as db:
        first = db.get(KickHunt, started[0])
        assert (first.start, first.end, first.live, first.count) == (2355, 2955, True, 1)
        assert first.folder == "xQc live" and first.streamer_id == "s1"
        streamer = db.get(KickStreamer, "s1")
        assert streamer.is_live and streamer.stream_id == 999 and streamer.done_until == 2955
    client.live_for = 3700
    # One stretch at a time: the first is still waiting.
    assert hunt.check_live(world.Session, client=client, queue=lambda h: None) == []
    with world.Session() as db:
        db.get(KickHunt, started[0]).status = "done"
        db.commit()
    client.live_for = 3300
    assert hunt.check_live(world.Session, client=client, queue=lambda h: None) == []
    with world.Session() as db:
        assert db.get(KickStreamer, "s1").last_result == "live; next stretch in about 5 min"
    client.live_for = 3600
    nxt = hunt.check_live(world.Session, client=client, queue=lambda h: None)
    with world.Session() as db:
        found = db.get(KickHunt, nxt[0])
        assert (found.start, found.end) == (2955, 3555)
        found.status = "done"
        db.commit()

    class Ended(FakeKick):
        def channel(self, slug):
            return kick.Channel(id=668, slug="xqc", username="xQc", live=None)

        def streams(self, slug):
            return [self._stream(live=True)]

    ended = Ended(live_for=3700)
    last = hunt.check_live(world.Session, client=ended, queue=lambda h: None)
    with world.Session() as db:
        found = db.get(KickHunt, last[0])
        assert (found.start, found.end) == (3555, 3700)
        found.status = "done"
        streamer = db.get(KickStreamer, "s1")
        assert not streamer.is_live and streamer.stream_id is None
        db.commit()
    assert hunt.check_live(world.Session, client=ended, queue=lambda h: None) == []
    with world.Session() as db:
        assert db.get(KickStreamer, "s1").last_result == "offline"


def test_a_restart_fails_a_busy_hunt_and_queues_a_lost_one(tmp_path, monkeypatch):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add_all([
            KickHunt(id="busy", slug="x", status="cutting"),
            KickHunt(id="waiting", slug="x"),
            KickHunt(id="lost", slug="x"),
        ])  # fmt: skip
        db.commit()
    queue = [SimpleNamespace(name=jobs.HUNT, args=("waiting",))]
    monkeypatch.setattr(jobs, "huey", SimpleNamespace(pending=lambda: queue, scheduled=lambda: []))
    again = []
    monkeypatch.setattr(jobs, "hunt", again.append)
    jobs.recover(Session)
    with Session() as db:
        assert db.get(KickHunt, "busy").status == "failed"
        assert db.get(KickHunt, "busy").error == jobs.INTERRUPTED
    assert again == ["lost"]


def test_a_hunt_deleted_while_it_waited_is_let_go(world):
    hunt.run("gone", world.Session, world.settings, client=FakeKick(), queue=lambda j: None)


def test_the_moment_link_and_clock():
    assert hunt.clock(3723) == "1:02:03" and hunt.clock(75) == "1:15"
    assert hunt.hunt_of_source("kick:kick-abc123-2") == "abc123"
    assert hunt.hunt_of_source("https://kick.com/x") is None
    assert json.loads(json.dumps(hunt.LENGTH)) == [15, 30]  # owner: 15-30 s clips


def test_the_vision_step_is_told_a_kick_clip_s_stream_title():
    asked = []

    class Vision:
        def chat_json(self, messages, schema, **kwargs):
            asked.append(messages[0]["content"])
            return {"description": "a car on a road"}

    from agentic_editor import caption

    assert caption.watch(Vision(), []) == "a car on a road"
    assert asked[-1] == caption.WATCH_PROMPT
    caption.watch(Vision(), [], 'xQc\'s stream titled "60 FPS GTA 6"')
    assert asked[-1].startswith(caption.WATCH_PROMPT)
    assert 'Known for sure about this video: xQc\'s stream titled "60 FPS GTA 6"' in asked[-1]
    assert "older one of the same series" in asked[-1]


@pytest.mark.parametrize("source, told", [("kick:kick-abc-0", True), ("https://x.test/r", False)])
def test_only_a_kick_clip_s_description_is_taken_as_sure(tmp_path, monkeypatch, source, told):
    from contextlib import contextmanager

    from agentic_editor import caption, download
    from agentic_editor.analysis import subjects
    from agentic_editor.presets import PostCaption

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(pipeline, "resolve_source", lambda ref, s: download.Source(video, "t"))
    monkeypatch.setattr(subjects, "grab", lambda src, at, dest, ffmpeg: dest)
    seen = {}
    monkeypatch.setattr(caption, "watch", lambda v, frames, known="": seen.update(known=known))
    monkeypatch.setattr(caption, "video_subject", lambda *a: ("cars", [], []))
    monkeypatch.setattr(caption, "video_sources", lambda *a: caption.Sources())
    monkeypatch.setattr(caption, "write", lambda *a, **k: "written")

    @contextmanager
    def model():
        yield SimpleNamespace(context=None)

    router = SimpleNamespace(vision=model, planner=model)
    settings = Settings(media_dir=tmp_path / "media", cache_dir=tmp_path / "cache")
    out = tmp_path / "out" / "clip.mp4"
    out.parent.mkdir()
    about = 'A moment from xQc\'s Kick live stream titled "60 FPS GTA 6".'
    edl = {"clips": [{"start": 0, "end": 20}]}
    jobs.caption_from_video(router, settings, source, edl, out, about, "", PostCaption())
    assert seen["known"] == (about if told else "")


def test_a_stream_title_loses_its_emoji_for_the_description():
    raw = "🥥60 FPS GTA 6🥥LIVE🥥FINISHING PLAYTHROUGH🥥"
    assert hunt.plain_title(raw) == "60 FPS GTA 6 · LIVE · FINISHING PLAYTHROUGH"
    assert hunt.plain_title("Late night chill w/ chat! #ad") == "Late night chill w/ chat! #ad"
    assert hunt.plain_title("🫂🫂") == ""
