from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from agentic_editor import autopilot
from agentic_editor.clipper import daily, hunt, kick, moments
from agentic_editor.db import Feed, FeedItem, Job, KickHunt, Output, PileFolder, User
from agentic_editor.presets import load_presets
from tests import test_api, test_clipper_api
from tests.test_clipper import UUID
from tests.test_clipper_api import add_preset, clipper_on

env = test_api.env  # the editor with a signed-in admin (a fixture)
kicked = test_clipper_api.kicked  # Kick faked, hunts run at once (a fixture)
DAY = datetime(2026, 11, 24, 8, 5)


def stream(n: int, title: str, hours: float = 2.0, live: bool = False) -> kick.Stream:
    return kick.Stream(
        id=n, title=title, started=datetime(2026, 11, 23, tzinfo=UTC) - timedelta(hours=n),
        duration=0 if live else hours * 3600, live=live, source=f"https://s.test/{n}.m3u8",
        uuid=f"00000000-0000-4000-8000-{n:012d}",
    )  # fmt: skip


STREAMS = {
    "ddg": [stream(11, "GTA 6!!!"), stream(12, "GTA 6 day 2"), stream(13, "IRL Miami"),
            stream(14, "an older one")],
    "jeanemarie": [stream(21, "IRL GTA"), stream(22, "beach day", hours=0.1)],
    "sheismichaela": [stream(31, "RAY J AND MICHAELA TALK"), stream(32, "pool party")],
    "n3on": [stream(41, "LIVE NOW", live=True), stream(42, "JACK.COM GIVEAWAY")],
}  # fmt: skip


class ManyKick:
    """Kick with a few streamers (STREAMS), each with its streams newest first."""

    def channel(self, slug):
        if slug not in STREAMS:
            raise kick.KickError(f"no Kick channel {slug!r}")
        return kick.Channel(id=100 + len(slug), slug=slug, username=slug.upper(), avatar="",
                            live=None)  # fmt: skip

    def streams(self, slug):
        return list(STREAMS[slug])

    def close(self):
        pass


def looked(client, s):
    """GTA play by the title; "IRL GTA" is real life."""
    return "GTA 6" in s.title


def owner(env) -> str:
    with env.sessions() as db:
        return db.scalar(select(User.id).where(User.username == "admin"))


def test_morning_clips_share_gta_and_footage_over_new_streams_in_the_right_preset(env):
    add_preset(env, "kick-clips")
    me = owner(env)
    names = ["ddg", "jeanemarie", "sheismichaela", "n3on", "nobody"]
    with env.sessions() as db:
        daily.save(db, me, daily.Daily(on=True, streamers=names, gta=15, footage=20))
        db.commit()
    ran = []
    result = daily.run(me, env.sessions, env.settings, client=ManyKick(), looker=looked,
                       queue=ran.append, now=DAY)  # fmt: skip
    with env.sessions() as db:
        hunts = db.scalars(select(KickHunt).where(KickHunt.owner_id == me)).all()
        assert sorted(h.id for h in hunts) == sorted(ran)  # all queued (they run one at a time)
        gta = [h for h in hunts if h.folder == "24-NOV-GTA"]
        other = [h for h in hunts if h.folder == "24-NOV-FOOTAGE"]
        assert len(gta) + len(other) == len(hunts)
        assert {h.stream_id: h.count for h in gta} == {11: 8, 12: 7}  # 15, at most 8 a stream
        # Each clip picks its layout (hunt.clip_preset): the facecam band or the whole picture.
        assert {(h.preset, h.irl_preset, h.layout) for h in hunts} == {
            (daily.FACECAM, daily.FULL, "auto")
        }
        # The live stream, a 6-minute one and ddg's 4th newest are passed over.
        assert {h.stream_id for h in other} == {13, 21, 31, 32, 42}
        each = Counter()
        for h in other:
            each[h.slug] += h.count
        assert sum(each.values()) == 20 and set(each.values()) == {5}  # shared in turn
        assert daily.load(db, me).last_result.startswith(result)
        assert all(h.auto for h in hunts)  # a schedule's: its clips wait behind people's jobs
    assert "15 GTA clips and 20 others" in result and "nobody: no Kick channel" in result
    # The next morning: no new streams, so each streamer's newest again (a hunt keeps off
    # the clips already made of a stream), filed in that day's folders.
    ran.clear()
    daily.run(me, env.sessions, env.settings, client=ManyKick(), looker=looked,
              queue=ran.append, now=DAY + timedelta(days=1))  # fmt: skip
    with env.sessions() as db:
        again = [db.get(KickHunt, h) for h in ran]
        assert {h.stream_id for h in again} == {11, 21, 31, 42}
        assert {h.folder for h in again} == {"25-NOV-GTA", "25-NOV-FOOTAGE"}


def test_share_gives_each_streamer_a_turn_and_never_overfills_a_stream():
    picked = [daily.Picked(slug, slug, 1, stream(n, "x"), False)
              for slug, n in (("a", 1), ("a", 2), ("b", 3), ("c", 4))]  # fmt: skip
    assert {p.stream.id: n for p, n in daily.share(picked, 7)} == {1: 2, 2: 1, 3: 2, 4: 2}
    assert sum(n for _, n in daily.share(picked, 100)) == 4 * daily.MOST_PER_HUNT
    assert daily.share([], 5) == []


def test_the_morning_clips_start_once_a_day_after_their_hour(env):
    me = owner(env)
    with env.sessions() as db:
        daily.save(db, me, daily.Daily(on=True, hour=8, streamers=["ddg"]))
        db.commit()
    queued = []
    assert daily.start_due(env.sessions, DAY.replace(hour=7), queued.append) == []
    assert daily.start_due(env.sessions, DAY, queued.append) == [me] == queued
    assert daily.start_due(env.sessions, DAY.replace(hour=11), queued.append) == []
    assert daily.start_due(env.sessions, DAY + timedelta(days=1), queued.append) == [me]
    with env.sessions() as db:
        daily.save(db, me, daily.load(db, me).model_copy(update={"on": False}))
        db.commit()
    assert daily.start_due(env.sessions, DAY + timedelta(days=2), queued.append) == []


def test_the_clipper_tab_reads_and_sets_the_morning_clips(env):
    assert env.client.get("/api/kick/daily").status_code == 403
    clipper_on(env)
    found = env.client.get("/api/kick/daily").json()
    assert (found["on"], found["hour"], found["gta"], found["footage"]) == (False, 8, 15, 20)
    body = {"on": True, "hour": 8, "streamers": ["https://kick.com/DDG", "@n3on", " "],
            "gta": 15, "footage": 20}  # fmt: skip
    saved = env.client.put("/api/kick/daily", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["streamers"] == ["ddg", "n3on"] and saved.json()["on"]
    assert env.client.put("/api/kick/daily", json={**body, "streamers": []}).status_code == 422
    bad = env.client.put("/api/kick/daily", json={**body, "streamers": ["kick.com/x/clips/c"]})
    assert bad.status_code == 422


def test_a_new_hunt_of_a_stream_keeps_off_the_clips_made_of_it():
    lines = [(1810.0 + i * 0.2, "OMG") for i in range(60)]
    lines += [(2410.0 + i * 0.2, "LOL") for i in range(40)]
    read = [(float(t), float(t) + 5) for t in range(0, 3600, 5)]
    (first,) = moments.find(0, 3600, 1, lines, read)
    assert 1780 < first.peak < 1830  # the bigger burst
    (again,) = moments.find(0, 3600, 1, lines, read, avoid=[(first.start, first.end)])
    assert 2380 < again.peak < 2430 and again.start > first.end + moments.APART


def test_autopilot_videos_go_in_a_folder_of_their_day(env):
    me = owner(env)
    with env.sessions() as db:
        feed = Feed(name="f", kind="link", location="https://x.test/c", presets=["p"],
                    owner_id=me, folder="GTA-KICK")  # fmt: skip
        undated = Feed(name="g", kind="link", location="https://x.test/d", presets=["p"],
                       owner_id=me, folder="Old", dated=False)  # fmt: skip
        hunts = [KickHunt(owner_id=me, slug="ddg", username="DDG", folder=f, moments=[])
                 for f in ("", "24-NOV-GTA")]  # fmt: skip
        db.add_all([feed, undated, *hunts])
        db.flush()
        made = {}
        cases = [
            ("feed", "https://x.test/1", feed), ("undated", "https://x.test/2", undated),
            ("hunt", f"kick:kick-{hunts[0].id}-0", None),
            ("named", f"kick:kick-{hunts[1].id}-0", None),
        ]  # fmt: skip
        for key, source, of in cases:
            job = Job(source=source, presets=["p"], owner_id=me)
            db.add(job)
            db.flush()
            if of is not None:
                db.add(FeedItem(feed_id=of.id, key=key, title=key, source=source, job_id=job.id))
            db.add(Output(job_id=job.id, preset="p", path=f"/x/{key}.mp4", duration=10.0,
                          planner="test", edl={}))  # fmt: skip
            db.flush()
            made[key] = job
        db.commit()
        for job in made.values():
            db.refresh(job)
            autopilot.file_outputs(db, job, now=DAY)
        db.commit()

        def path(key):
            folder = db.get(PileFolder, made[key].outputs[0].folder_id)
            names = []
            while folder is not None:
                names.insert(0, folder.name)
                folder = db.get(PileFolder, folder.parent_id) if folder.parent_id else None
            return "/".join(names)

        assert path("feed") == "GTA-KICK/24-NOV"
        assert path("undated") == "Old"
        assert path("hunt") == "Kick DDG/24-NOV"
        assert path("named") == "24-NOV-GTA"  # its name says its day already
        # The next day's go in their own folder beside it; each day's folder is made once.
        autopilot.file_outputs(db, made["feed"], now=DAY + timedelta(days=1))
        assert path("feed") == "GTA-KICK/25-NOV"
        autopilot.file_outputs(db, made["feed"], now=DAY)
        days = db.scalars(select(PileFolder).where(PileFolder.name == "24-NOV")).all()
        assert len(days) == 2  # GTA-KICK's and Kick DDG's
    assert autopilot.day_tag(datetime(2026, 12, 1)) == "01-DEC"


@pytest.mark.parametrize("name", ["23-NOV-GTA", "GTA 1-dec", "24-NOV"])
def test_a_folder_named_with_its_day_gets_no_day_folder(name):
    assert autopilot.DAY_NAMED.search(name)
    assert not autopilot.DAY_NAMED.search("GTA-KICK")


def test_a_clip_s_layout_follows_what_it_shows(env, monkeypatch):
    add_preset(env, "kick-clips")
    have = load_presets(env.settings.presets_dir)
    camera, whole = "kick-facecam-9x16", "kick-clips"
    assert hunt.layout_presets(have, camera) == (camera, whole)
    assert hunt.layout_presets(have, whole) == (camera, whole)
    # A whole-picture counterpart given wins; one that cuts a camera panel can't be it.
    assert hunt.layout_presets(have, camera, "square-1x1") == (camera, "square-1x1")
    assert hunt.layout_presets(have, whole, camera) == (camera, whole)
    assert hunt.layout_presets({k: v for k, v in have.items() if k != camera}, whole) == (
        None, whole,
    )  # fmt: skip

    shows = {"game.mp4": (True, "GTA 6"), "call.mp4": (False, "a video call")}
    monkeypatch.setattr(hunt, "shows_gameplay", lambda video, settings: shows[video.name])

    def pick(name, chosen=whole, irl=""):
        return hunt.clip_preset(Path(name), chosen, irl, None, env.settings)

    assert pick("game.mp4") == camera
    assert pick("call.mp4") == whole and pick("call.mp4", camera) == whole
    assert pick("call.mp4", camera, "square-1x1") == "square-1x1"


def test_a_hunt_makes_each_clip_in_the_layout_it_shows_unless_fixed(env, kicked, monkeypatch):
    add_preset(env, "kick-clips")
    monkeypatch.setattr(hunt, "shows_gameplay", lambda video, settings: (True, "GTA 6"))
    body = {"link": f"https://kick.com/xqc/videos/{UUID}", "start": "28:20", "end": "31:20",
            "count": 1, "preset": "kick-clips"}  # fmt: skip

    def made_with(found):
        with env.sessions() as db:
            return db.get(Job, found["moments"][0]["job_id"]).presets

    auto = env.client.post("/api/kick/hunts", json=body).json()
    assert auto["layout"] == "auto" and auto["status"] == "done", auto["error"]
    assert made_with(auto) == ["kick-facecam-9x16"]  # gameplay under a camera
    fixed = env.client.post("/api/kick/hunts", json={**body, "layout": "fixed"}).json()
    assert made_with(fixed) == ["kick-clips"]  # as chosen, whatever it shows
    bad = env.client.post("/api/kick/hunts", json={**body, "irl_preset": "nope"})
    assert bad.status_code == 422


NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)


def a_hunt(db, owner, made=NOW, **fields) -> KickHunt:
    fields = {"created_at": made, "updated_at": made, **fields}
    found = KickHunt(owner_id=owner, slug="ddg", moments=[], **fields)
    db.add(found)
    db.flush()
    return found


def test_hunts_go_one_at_a_time_and_give_way_to_peoples_jobs(env, monkeypatch):
    from agentic_editor import jobs

    me = owner(env)
    with env.sessions() as db:
        mine = a_hunt(db, me)
        db.commit()
        assert jobs.hunt_may_start(db, mine, NOW, workers=2)  # nothing else: it may go
        # Another hunt at work: this one waits; one silent for long (a crash) doesn't count.
        other = a_hunt(db, me, status="listening", updated_at=NOW - timedelta(minutes=2))
        db.commit()
        assert not jobs.hunt_may_start(db, mine, NOW, workers=2)
        other.updated_at = NOW - jobs.HUNT_STALE - timedelta(minutes=1)
        db.commit()
        assert jobs.hunt_may_start(db, mine, NOW, workers=2)
        # A person's job waits and one worker is busy: the hunt would take the last one.
        db.add_all([Job(source="https://x.test/a", presets=["p"], owner_id=me, status="running"),
                    Job(source="https://x.test/b", presets=["p"], owner_id=me)])  # fmt: skip
        db.commit()
        assert not jobs.hunt_may_start(db, mine, NOW, workers=2)
        assert jobs.hunt_may_start(db, mine, NOW, workers=3)  # a worker stays free
        later = NOW + jobs.HUNT_AGING
        assert jobs.hunt_may_start(db, mine, later, workers=2)  # it has waited long enough
        # The waiting job is a schedule's Kick clip, not a person's: the hunt may go.
        queued = db.scalar(select(Job).where(Job.status == "queued"))
        auto = a_hunt(db, me, auto=True, status="done")
        queued.source = f"kick:kick-{auto.id}-0"
        db.commit()
        assert jobs.hunt_may_start(db, mine, NOW, workers=2)
    # claim_hunt marks the one it lets go, and leaves a hunt no longer queued alone.
    monkeypatch.setattr(jobs, "sessions", lambda: env.sessions)
    monkeypatch.setattr(jobs, "get_settings", lambda: env.settings)
    assert jobs.claim_hunt(env.sessions, mine.id, NOW) == "go"
    with env.sessions() as db:
        assert db.get(KickHunt, mine.id).status == "reading"
    assert jobs.claim_hunt(env.sessions, mine.id, NOW) is None
    with env.sessions() as db:
        waiting = a_hunt(db, me)
        db.commit()
    # `mine` is at work (claiming marked it just now, by the clock).
    assert jobs.claim_hunt(env.sessions, waiting.id, datetime.now(UTC)) == jobs.BUSY
    later = []
    monkeypatch.setattr(jobs.hunt, "schedule", lambda args, delay: later.append((args, delay)))
    monkeypatch.setattr(jobs.kick_hunt, "run", lambda *a, **k: pytest.fail("ran while busy"))
    jobs.hunt.call_local(waiting.id)
    assert later == [((waiting.id,), jobs.HUNT_RETRY)]  # asks again later, holding no worker


def test_a_schedules_kick_clips_wait_behind_peoples_own_jobs(env):
    from agentic_editor import jobs

    me = owner(env)
    with env.sessions() as db:
        scheduled = a_hunt(db, me, auto=True, status="done")
        followed = a_hunt(db, me, live=True, status="done")
        asked = a_hunt(db, me, status="done")  # a person asked for these clips
        made = NOW - timedelta(minutes=5)
        for n, (h, when) in enumerate([(scheduled, made), (followed, made),
                                       (asked, NOW), (None, NOW)]):  # fmt: skip
            source = f"kick:kick-{h.id}-0" if h else f"https://x.test/{n}"
            db.add(Job(id=f"j{n}", source=source, presets=["p"], owner_id=me, created_at=when))
        db.commit()
        ids = ["j0", "j1", "j2", "j3"]
        assert jobs.automatic_jobs(db, ids) == {"j0", "j1"}
        # The person's jobs go first though the schedule's and the follow's were queued earlier.
        assert jobs.next_turn(db, NOW)[1] in {"j2", "j3"}
