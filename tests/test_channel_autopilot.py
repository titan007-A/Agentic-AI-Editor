"""The channel autopilot (channel/pilot.py, channel/routes.py): a page that finds, checks,
edits, checks again and posts its own reels, with Meta's Graph API faked."""

import json
import shutil
import subprocess
import time
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from sqlalchemy import select

from agentic_editor import autopilot, download, instagram, jobs, research, shazam
from agentic_editor.analyze import transcript_file
from agentic_editor.channel import pilot
from agentic_editor.db import (
    AccountSnapshot,
    ChannelPilot,
    ChannelPost,
    InstagramChannel,
    Job,
    Output,
    PileFolder,
    Research,
    ResearchAccount,
    ResearchItem,
    Setting,
    User,
)
from agentic_editor.edl import EDL, Clip
from agentic_editor.transcript import Transcript, Word
from tests import test_api
from tests.test_api import employee
from tests.test_channel_checks import split_reel, vs_preset

env = test_api.env  # the editor with a signed-in admin (a fixture)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
PRESET = "vocals-vs-instruments-9x16"
HISTORY = [
    {"id": "m1", "caption": "🎵 Self Control — Laura Branigan - 1984\n\nHer biggest hit.",
     "like_count": 4047, "comments_count": 58, "timestamp": "2026-08-27T16:01:00+0000",
     "permalink": "https://www.instagram.com/reel/DSELF000001/", "media_product_type": "REELS"},
    {"id": "m2", "caption": "Michael Jackson - Billie Jean", "like_count": 10166,
     "comments_count": 61, "timestamp": "2026-09-14T19:03:00+0000",
     "permalink": "https://www.instagram.com/reel/DBILLIE0001/", "media_product_type": "REELS"},
]  # fmt: skip


class Graph:
    """Meta's Graph API as the app uses it: the page's posts, a reel's container, publish,
    a post's numbers. `insights`: False answers #10 as Meta does for a login without the
    insights permission."""

    def __init__(self, insights=False, refuse_post=False):
        self.calls: list[tuple[str, str, dict]] = []
        self.insights = insights
        self.refuse_post = refuse_post

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/{instagram.VERSION}/")
        if request.method == "POST":
            sent = dict(httpx.QueryParams(request.content.decode()))
        else:
            sent = dict(request.url.params)
        self.calls.append((request.method, path, sent))
        if request.method == "POST" and path == "ig1/media":
            if self.refuse_post:
                return httpx.Response(
                    400, json={"error": {"message": "Media upload failed", "code": 9004}}
                )
            return httpx.Response(200, json={"id": "c1"})
        if path == "c1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path == "ig1/media_publish":
            return httpx.Response(200, json={"id": "18001"})
        if path == "ig1/media":
            return httpx.Response(200, json={"data": HISTORY})
        if path.endswith("/insights"):
            if not self.insights:
                return httpx.Response(400, json={"error": {
                    "message": "Application does not have permission for this action",
                    "code": 10}})  # fmt: skip
            return httpx.Response(200, json={"data": [
                {"name": "views", "values": [{"value": 5200}]},
                {"name": "reach", "total_value": {"value": 4100}},
                {"name": "saved", "values": [{"value": 31}]},
            ]})  # fmt: skip
        if "like_count" in sent.get("fields", ""):
            return httpx.Response(200, json={
                "like_count": 120, "comments_count": 4,
                "permalink": "https://www.instagram.com/reel/DNEW0000001/"})  # fmt: skip
        return httpx.Response(404, json={"error": {"message": f"no {path}", "code": 100}})


@pytest.fixture
def graph(monkeypatch):
    fake = Graph()
    monkeypatch.setattr(instagram, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(instagram, "POLL_INTERVAL", 0)
    return fake


def admin_id(env) -> str:
    with env.sessions() as db:
        return db.scalar(select(User.id).where(User.username == "admin"))


def page(env, preset_fields=None) -> str:
    """The feature on for the admin, the vs preset in their presets and @vocalvsinstrument
    connected; the admin's id."""
    owner = admin_id(env)
    env.settings.cache_dir = env.settings.media_dir.parent / "cache"
    data = vs_preset(**(preset_fields or {})).model_dump(mode="json")
    (env.settings.presets_dir / f"{PRESET}.yaml").write_text(yaml.safe_dump(data))
    with env.sessions() as db:
        db.merge(Setting(key="feature:channel_autopilot", value=json.dumps(["admin"])))
        db.add(InstagramChannel(id="ig1", owner_id=owner, username="vocalvsinstrument",
                                access_token="token"))  # fmt: skip
        db.commit()
    return owner


def save(env, **fields):
    body = {"preset": PRESET, "research_account_id": "new"} | fields
    return env.client.put("/api/channel-autopilot/ig1", json=body)


class Queue:
    """The queue tasks, noted instead of run."""

    def __init__(self):
        self.calls = []
        for name in ("work", "post", "read", "learn", "find"):
            setattr(self, name, lambda *args, _name=name: self.calls.append((_name, *args)))


# ---- the page's settings --------------------------------------------------------------


def test_the_autopilot_is_only_for_accounts_it_is_on_for(env):
    assert env.client.get("/api/channel-autopilot").status_code == 403
    page(env)
    found = env.client.get("/api/channel-autopilot").json()
    assert [c["username"] for c in found["channels"]] == ["vocalvsinstrument"]
    assert found["channels"][0]["pilot"] is None
    assert PRESET in [p["name"] for p in found["presets"]]
    # The owner's data: five a day at the channel's best hours, dry run until switched off.
    assert found["defaults"]["post_times"] == ["02:30", "04:00", "06:00", "18:30", "21:30"]
    assert found["defaults"]["posts_per_day"] == 5
    assert found["defaults"]["dry_run"] is True and found["defaults"]["needs"] == 20


def test_saving_makes_it_in_dry_run_with_a_research_account_for_the_page(env):
    page(env)
    saved = save(env, focus="  distinctive   voices ")
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view["dry_run"] is True and view["active"] is True and view["needs"] == 20
    assert view["research_handle"] == "vocalvsinstrument" and view["focus"] == "distinctive voices"
    with env.sessions() as db:
        account = db.scalar(select(ResearchAccount))
        assert account.handle == "vocalvsinstrument" and account.presets == [PRESET]
        assert db.scalar(select(ChannelPilot)).base_url.startswith("http")
    # Saved again: the same account, nothing made twice.
    assert save(env, research_account_id=view["research_account_id"]).status_code == 200
    with env.sessions() as db:
        assert len(db.scalars(select(ResearchAccount)).all()) == 1


def test_wrong_settings_are_refused(env):
    page(env)
    assert save(env, preset="nope").status_code == 422
    assert save(env, preset="").status_code == 422  # running without a preset
    assert save(env, posts_per_day=3, post_times=["18:30"]).status_code == 422
    assert save(env, post_times=["25:00", "1", "2", "3", "4"]).status_code == 422
    assert save(env, posts_per_day=7).status_code == 422
    assert (
        env.client.put("/api/channel-autopilot/other", json={"preset": PRESET}).status_code == 404
    )


def test_another_persons_page_and_videos_are_not_theirs(env):
    page(env)
    save(env)
    with env.sessions() as db:
        db.add(ChannelPost(id=7, pilot_id=db.scalar(select(ChannelPilot.id)), code="yt:x",
                           url="u", status="awaiting"))  # fmt: skip
        db.commit()
    _, riya = employee(env, "riya", presets=[])
    with env.sessions() as db:
        db.merge(Setting(key="feature:channel_autopilot", value=json.dumps(["admin", "riya"])))
        db.commit()
    assert riya.get("/api/channel-autopilot").json()["channels"] == []
    assert riya.post("/api/channel-posts/7/approve").status_code == 404
    assert riya.put("/api/channel-autopilot/ig1", json={"preset": PRESET}).status_code == 404


# ---- the day: research, picking ------------------------------------------------------


def make_pilot(db, owner, **fields) -> ChannelPilot:
    account = ResearchAccount(owner_id=owner, handle="vocalvsinstrument", presets=[PRESET])
    db.add(account)
    db.flush()
    found = ChannelPilot(
        id="p1", owner_id=owner, channel_id="ig1", preset=PRESET,
        research_account_id=account.id, **({"posts_per_day": 1, "post_times": ["18:30"]} | fields),
    )  # fmt: skip
    db.add(found)
    db.flush()
    return found


def item(rank, code, song, artist, fit, score=0.5, duration=240.0):
    return ResearchItem(
        rank=rank, code=f"yt:{code}", url=f"https://www.youtube.com/watch?v={code}",
        caption=f"{artist} - {song} (Official Video)", song=f"{song} - {artist}", artist=artist,
        relevance=fit, score=score, duration=duration,
    )  # fmt: skip


def test_the_days_research_starts_at_its_hour_once_and_learns_the_page_first(env):
    owner = page(env)
    queue = Queue()
    with env.sessions() as db:
        make_pilot(db, owner, history=[{"caption": HISTORY[0]["caption"]}])
        db.commit()
    assert pilot.tick(env.sessions, datetime(2026, 11, 25, 6, 59), queue) == []
    did = pilot.tick(env.sessions, datetime(2026, 11, 25, 7, 5), queue)
    assert len(did) == 1 and queue.calls[0][0] == "learn"
    _, pilot_id, research_id = queue.calls[0]
    with env.sessions() as db:
        found = db.get(Research, research_id)
        assert found.daily and found.account_id and found.status == "queued"
        assert found.sites == list(research.DAILY_SITES)
        assert db.get(ChannelPilot, pilot_id).research_on == date(2026, 11, 25)
        assert db.get(ResearchAccount, found.account_id).daily_on == date(2026, 11, 25)
    assert pilot.tick(env.sessions, datetime(2026, 11, 25, 9, 0), queue) == []  # once a day


def test_the_accounts_own_morning_research_is_used_rather_than_a_second(env):
    owner = page(env)
    with env.sessions() as db:
        p = make_pilot(db, owner)
        morning = Research(description="", owner_id=owner, account_id=p.research_account_id,
                           daily=True)  # fmt: skip
        db.add(morning)
        db.get(ResearchAccount, p.research_account_id).daily_on = date.today()
        db.commit()
        morning_id = morning.id
    queue = Queue()
    pilot.tick(env.sessions, datetime.now().replace(hour=23), queue)
    assert queue.calls == []
    with env.sessions() as db:
        assert db.get(ChannelPilot, "p1").research_id == morning_id


def test_the_best_fitting_new_songs_are_picked_and_the_rest_noted(env):
    owner = page(env)
    with env.sessions() as db:
        p = make_pilot(db, owner, history=[{"caption": HISTORY[0]["caption"]}])
        found = Research(id="r1", description="", owner_id=owner, status="done")
        found.items = [
            item(0, "AAAAAAAAAA1", "Wuthering Heights", "Kate Bush", 0.9, 0.9),
            item(1, "AAAAAAAAAA2", "Self Control", "Laura Branigan", 0.9, 0.8),  # posted
            item(2, "AAAAAAAAAA3", "Gloria", "Laura Branigan", 0.6, 0.7),  # fits too little
            item(3, "AAAAAAAAAA4", "Heart of Glass", "Blondie", 0.8, 0.6),
            item(4, "AAAAAAAAAA5", "Wuthering Heights", "Kate Bush", 0.9, 0.5),  # again
        ]
        db.add(found)
        p.research_id, p.research_on = "r1", date(2026, 11, 25)
        db.commit()
    queue = Queue()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 9, 0), queue)
    assert ("work", "p1") in queue.calls
    with env.sessions() as db:
        rows = {r.code: r for r in db.scalars(select(ChannelPost))}
        assert rows["yt:AAAAAAAAAA1"].status == "queued"
        assert rows["yt:AAAAAAAAAA4"].status == "queued"
        assert rows["yt:AAAAAAAAAA2"].status == "skipped"
        assert "posted or is waiting" in rows["yt:AAAAAAAAAA2"].reason
        assert rows["yt:AAAAAAAAAA3"].reason == "fits 6/10; needs 8"
        spare = rows["yt:AAAAAAAAAA5"]  # the same song as one picked: kept as a spare
        assert spare.status == "queued" and spare.spare
        assert db.get(ChannelPilot, "p1").picked_id == "r1"
    queue.calls.clear()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 9, 5), queue)  # picked once
    with env.sessions() as db:
        assert len(db.scalars(select(ChannelPost)).all()) == 5


def test_the_best_fitting_are_picked_first_whatever_their_reach(env):
    owner = page(env)
    with env.sessions() as db:
        p = make_pilot(db, owner, posts_per_day=1)  # two wanted: six picked
        found = Research(id="r3", description="", owner_id=owner, status="done")
        found.items = [
            item(n, f"FIT{n:08d}", f"Song {n}", f"Singer {n}", fit, score)
            for n, (fit, score) in enumerate([(0.8, 0.9), (1.0, 0.3), (0.9, 0.5)])
        ]
        db.add(found)
        db.flush()
        pilot.pick(db, p, found)
        db.flush()
        order = db.scalars(select(ChannelPost.code).order_by(ChannelPost.id)).all()
        assert order == ["yt:FIT00000001", "yt:FIT00000002", "yt:FIT00000000"]


def test_a_video_known_too_short_isnt_picked(env):
    owner = page(env)
    with env.sessions() as db:
        p = make_pilot(db, owner, posts_per_day=1)
        found = Research(id="r4", description="", owner_id=owner, status="done")
        found.items = [
            item(0, "UMBRELLA001", "Umbrella", "Rihanna", 1.0, 0.9, duration=30.0),
            item(1, "UMBRELLA002", "Umbrella", "Rihanna", 1.0, 0.5, duration=275.0),
        ]
        db.add(found)
        db.flush()
        assert pilot.pick(db, p, found, env.settings) == 1
        db.flush()
        rows = {r.code: r for r in db.scalars(select(ChannelPost))}
        assert rows["yt:UMBRELLA001"].reason == "30 s: too short for a reel (needs 40 s)"
        assert rows["yt:UMBRELLA002"].status == "queued" and not rows["yt:UMBRELLA002"].spare


def test_a_spare_copy_is_checked_only_when_the_better_one_fails(env, monkeypatch):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner, posts_per_day=1)
        best = queued_post(db, owner, code="yt:VALERIE0001", song="Valerie", artist="Amy Winehouse")
        other = queued_post(db, owner, code="yt:OTHER000001", song="Rehab", artist="Amy Winehouse")
        spare = queued_post(
            db, owner, code="yt:VALERIE0002", song="Valerie", artist="Amy Winehouse"
        )
        db.get(ChannelPost, spare).spare = True
        db.commit()
    order = []

    def check(post_id, Session, settings, router, enqueue, *rest):
        order.append(post_id)
        with Session() as db:
            row = db.get(ChannelPost, post_id)
            # The best copy of Valerie is too short; Rehab passes.
            row.status = "skipped" if post_id == best else "editing"
            db.commit()

    monkeypatch.setattr(pilot, "check_video", check)
    pilot.work("p1", env.sessions, env.settings, router=object(), workers=2)
    assert order == [best, other, spare]  # the spare last, after the others


def test_a_spare_whose_better_copy_passed_isnt_checked(env):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner)
        passed = queued_post(
            db, owner, code="yt:VALERIE0001", song="Valerie", artist="Amy Winehouse"
        )
        db.get(ChannelPost, passed).status = "editing"
        spare = queued_post(
            db, owner, code="yt:VALERIE0002", song="Valerie", artist="Amy Winehouse"
        )
        db.get(ChannelPost, spare).spare = True
        db.commit()
    never = lambda *a: pytest.fail("downloaded")  # noqa: E731
    assert pilot.check_video(spare, env.sessions, env.settings, None, never, fetch=never) == (
        "unused"
    )
    with env.sessions() as db:
        assert db.get(ChannelPost, spare).reason == "another copy of “Valerie” passed its checks"


@needs_ffmpeg
def test_a_video_shazam_turns_away_goes_back_in_line_not_out(env, source, monkeypatch):
    owner = page(env)
    with env.sessions() as db:
        post_id = queued_post(db, owner)
        db.commit()

    def busy(clip):
        raise shazam.Busy(time.monotonic() + 600)

    status = pilot.check_video(post_id, env.sessions, env.settings, None,
                               lambda job: pytest.fail("edited"), fetch=fetching(source),
                               ask=busy, look=lambda s, a: None)  # fmt: skip
    assert status == "queued"
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        assert row.retry_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=9)
        assert row.reason.startswith("Shazam asked to slow down")
        # Not taken again before then.
        assert pilot.next_unit(db, "p1") is None


def test_too_few_passed_brings_another_research_the_same_day_up_to_three(env):
    owner = page(env)
    queue = Queue()
    with env.sessions() as db:
        p = make_pilot(db, owner, posts_per_day=2)
        first = Research(
            id="rA",
            description="",
            owner_id=owner,
            status="done",
            created_at=datetime(2026, 11, 25, 7, 0) - pilot_offset(),
        )
        db.add(first)
        p.research_id, p.picked_id = "rA", "rA"
        p.research_on, p.researches_on = date(2026, 11, 25), 1
        db.add(ChannelPost(pilot_id="p1", code="yt:x", url="u", status="queued"))
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 9, 0), queue)  # one still to check
    assert not [c for c in queue.calls if c[0] == "learn"]
    with env.sessions() as db:
        db.query(ChannelPost).update({"status": "skipped"})
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 9, 5), queue)
    learned = [c for c in queue.calls if c[0] == "learn"]
    assert len(learned) == 1
    with env.sessions() as db:
        p = db.get(ChannelPilot, "p1")
        assert p.researches_on == 2 and p.research_id == learned[0][2]
        assert "research 2 of today" in p.note
        db.get(Research, p.research_id).status = "done"
        p.picked_id = p.research_id
        p.researches_on = pilot.MOST_RESEARCHES
        db.commit()
    queue.calls.clear()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 23, 0), queue)  # three a day at most
    assert not [c for c in queue.calls if c[0] == "learn"]


def pilot_offset() -> timedelta:
    """Local time minus UTC here: stored times are UTC."""
    return datetime.now() - datetime.now(UTC).replace(tzinfo=None)


def test_a_singer_fills_two_places_at_most(env):
    owner = page(env)
    with env.sessions() as db:
        p = make_pilot(db, owner, posts_per_day=5)
        found = Research(id="r2", description="", owner_id=owner, status="done")
        found.items = [
            item(n, f"DONNA{n:06d}", f"Song {n}", "Donna Summer", 1.0, 1 - n / 10) for n in range(4)
        ] + [item(9, "JANIS0000001", "Me and Bobby McGee", "Janis Joplin", 1.0, 0.1)]
        db.add(found)
        db.flush()
        assert pilot.pick(db, p, found) == 3
        db.flush()
        queued = db.scalars(select(ChannelPost.code).where(ChannelPost.status == "queued"))
        assert sorted(queued) == ["yt:DONNA000000", "yt:DONNA000001", "yt:JANIS0000001"]


def test_a_research_gives_no_song_the_page_posted_or_has_waiting():
    class Rater:
        def chat_json(self, messages, schema, **kwargs):
            if "queries" in schema["properties"]:
                return {"brief": "songs", "music": True, "queries": ["iconic voices"]}
            lines = [x for x in messages[-1]["content"].split("\n") if x[:1].isdigit()]
            songs = {"SELF": ("Self Control", "Laura Branigan"), "KATE": ("Wuthering Heights",
                     "Kate Bush"), "TOTO": ("Africa", "Toto")}  # fmt: skip
            return {"ratings": [
                {"n": n, "relevance": 9, "why": "", "song": songs[key][0], "artist":
                 songs[key][1], "matches": "", "preset": "", "preset_why": "", "usable": True}
                for n, line in enumerate(lines, 1)
                for key in songs if key in line
            ]}  # fmt: skip

    def find(query, count):
        return [{"href": f"https://www.instagram.com/reel/{c}0000001/", "title": c, "body": ""}
                for c in ("SELF", "KATE", "TOTO")]  # fmt: skip

    def info(url):
        return {"uploader": "x", "description": research.code_of(url), "like_count": 10}

    context = research.Context(
        handle="vocalvsinstrument", posted=[HISTORY[0]["caption"]], avoid=[("Africa", "Toto")],
        best=["🎵 Self Control — Laura Branigan - 1984 (4047 likes)"], surprise=True,
    )  # fmt: skip
    llm = Rater()
    result = research.run("", lambda: nullcontext(llm), set(), set(), lambda m, v: None,
                          find=find, info=info, pause=0, context=context)  # fmt: skip
    assert [r.song for r in result.reels] == ["Wuthering Heights - Kate Bush"]
    assert result.left_out["posted before"] == 2
    told = research._taste(context)
    assert "never these songs" in told and "Africa - Toto" in told
    # The focus, the best posts and surprise reach both the planning and the rating.
    llm.prompts = []
    research.run("Iconic distinctive voices", lambda: nullcontext(Recorder(llm)), set(), set(),
                 lambda m, v: None, find=find, info=info, pause=0, context=context)  # fmt: skip
    planned, rated = llm.prompts[0], llm.prompts[-1]
    for prompt in (planned, rated):
        assert "Iconic distinctive voices" in prompt
        assert "Self Control — Laura Branigan - 1984 (4047 likes)" in prompt
    assert research.SURPRISE_PLAN in planned and research.SURPRISE_RATE in rated
    assert "Africa - Toto" in planned


class Recorder:
    """The model, with each prompt kept."""

    def __init__(self, llm):
        self.llm = llm

    def chat_json(self, messages, schema, **kwargs):
        self.llm.prompts.append(messages[-1]["content"])
        return self.llm.chat_json(messages, schema, **kwargs)


# ---- the checks before the edit --------------------------------------------------------


@pytest.fixture
def source(tmp_path):
    """A downloaded video: a minute of moving picture and sound."""
    video = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=10:duration=60", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=60", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)  # fmt: skip
    return video


def queued_post(db, owner, code="yt:AAAAAAAAAA1", song="Wuthering Heights", artist="Kate Bush"):
    p = db.get(ChannelPilot, "p1") or make_pilot(db, owner)
    row = ChannelPost(pilot_id=p.id, owner_id=owner, code=code, url="https://youtu.be/x",
                      song=song, artist=artist, relevance=0.9, status="queued")  # fmt: skip
    db.add(row)
    db.flush()
    return row.id


def fetching(video, title="Kate Bush - Wuthering Heights (Official Music Video)", fails=False):
    def fetch(url, folder):
        if fails:
            raise RuntimeError("Video unavailable")
        return download.Source(path=video, title=title, uploader="Kate Bush", url=url,
                               description="")  # fmt: skip

    return fetch


def hearing(*songs):
    answers = list(songs)

    def ask(clip):
        found = answers.pop(0) if answers else None
        return {"title": found[0], "subtitle": found[1]} if found else None

    return ask


@needs_ffmpeg
def test_a_video_that_passes_every_check_is_edited_with_the_song_shazam_heard(
    env, source, monkeypatch
):
    owner = page(env)
    with env.sessions() as db:
        post_id = queued_post(db, owner)
        db.commit()
    vocals = env.settings.cache_dir / "sung.wav"
    vocals.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=60", str(vocals)], check=True)  # fmt: skip
    monkeypatch.setattr(pilot, "isolate_vocals", lambda *a, **k: (vocals, False))
    queued = []
    kate = ("Wuthering Heights", "Kate Bush")
    status = pilot.check_video(
        post_id, env.sessions, env.settings, router=None, enqueue=queued.append,
        fetch=fetching(source), ask=hearing(kate, kate, kate), look=lambda s, a: None,
    )  # fmt: skip
    assert status == "editing"
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        job = db.get(Job, row.job_id)
        assert queued == [job.id]
        assert job.presets == [PRESET] and job.owner_id == owner
        assert job.notes == "song: Wuthering Heights\nartist: Kate Bush"
        assert (row.heard_song, row.heard_artist) == kate
        assert [c["name"] for c in row.checks] == [
            "posted before",
            "original",
            "length",
            "one song",
            "named",
            "posted before",
            "vocals",
        ]
        assert all(c["ok"] for c in row.checks)
        assert jobs.automatic_jobs(db, [job.id]) == {job.id}  # behind people's own jobs


@needs_ffmpeg
@pytest.mark.parametrize(
    ("heard", "title", "why"),
    [
        ([None, None, None], None, "Shazam knew it at 0 of 3 points"),
        ([("Wuthering Heights", "Kate Bush"), ("Babooshka", "Kate Bush"), None], None,
         "different songs"),
        ([("Running Up That Hill", "Kate Bush")] * 3, None, "doesn't name “Running Up That Hill”"),
        ([("Self Control", "Laura Branigan")] * 3, "Laura Branigan - Self Control",
         "posted or is waiting"),
    ],
)  # fmt: skip
def test_a_video_shazam_doubts_is_skipped_never_guessed(env, source, heard, title, why):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner, history=[{"caption": HISTORY[0]["caption"]}])
        post_id = queued_post(db, owner, song="", artist="")
        db.commit()
    fetch = fetching(source, title) if title else fetching(source)
    status = pilot.check_video(
        post_id, env.sessions, env.settings, None, lambda job: pytest.fail("edited"),
        fetch=fetch, ask=hearing(*heard), look=lambda s, a: None,
    )  # fmt: skip
    assert status == "skipped"
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        assert why in row.reason and row.job_id is None
        assert not row.checks[-1]["ok"]


@needs_ffmpeg
def test_a_video_that_cant_be_downloaded_or_is_too_short_is_skipped(env, source):
    owner = page(env)
    with env.sessions() as db:
        failing = queued_post(db, owner)
        short = queued_post(db, owner, code="yt:BBBBBBBBBB1", song="Babooshka")
        db.get(ChannelPost, short).duration = 20.0
        db.commit()
    never = lambda job: pytest.fail("edited")  # noqa: E731
    assert pilot.check_video(failing, env.sessions, env.settings, None, never,
                             fetch=fetching(source, fails=True)) == "skipped"  # fmt: skip
    assert pilot.check_video(short, env.sessions, env.settings, None, never,
                             fetch=fetching(source)) == "skipped"  # fmt: skip
    with env.sessions() as db:
        assert "couldn't download it: Video unavailable" in db.get(ChannelPost, failing).reason
        assert db.get(ChannelPost, short).reason.startswith("20 s (needs 40-600 s)")


def test_the_work_checks_one_video_at_a_time_and_only_as_many_as_needed(env, monkeypatch):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner, posts_per_day=1)  # two days: two reels wanted
        ids = [queued_post(db, owner, code=f"yt:C{n}", song=f"Song {n}") for n in range(4)]
        db.commit()
    checked = []

    def check(post_id, Session, settings, router, enqueue, *rest):
        checked.append(post_id)
        with Session() as db:
            db.get(ChannelPost, post_id).status = "editing"
            db.commit()

    monkeypatch.setattr(pilot, "check_video", check)
    assert pilot.work("p1", env.sessions, env.settings, router=object(), workers=2) == "done"
    assert checked == ids[:2]
    with env.sessions() as db:
        assert [db.get(ChannelPost, i).status for i in ids[2:]] == ["unused", "unused"]


def test_the_work_waits_while_peoples_own_jobs_need_the_workers(env, monkeypatch):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner)
        queued_post(db, owner)
        db.add_all(
            [
                Job(source="a", presets=[], notes=None, status="running", owner_id=owner),
                Job(source="b", presets=[], notes=None, status="queued", owner_id=owner),
            ]
        )
        db.commit()  # fmt: skip
    monkeypatch.setattr(pilot, "check_video", lambda *a, **k: pytest.fail("checked"))
    assert pilot.work("p1", env.sessions, env.settings, router=object(), workers=2) == "later"
    pilot._deferred.clear()


# ---- the edit, then the checks on the reel ----------------------------------------------


def edited(db, post_id, path, caption="🎵 Wuthering Heights — Kate Bush - 1978\n\nA fact."):
    row = db.get(ChannelPost, post_id)
    job = Job(source=row.url, presets=[PRESET], notes=None, owner_id=row.owner_id,
              status="done")  # fmt: skip
    db.add(job)
    db.flush()
    edl = EDL(clips=[Clip(start=0, end=10)], title="Wuthering Heights - Kate Bush")
    job.outputs.append(Output(
        preset=PRESET, path=str(path), duration=10.0, title=edl.title, planner="m",
        edl=edl.model_dump(mode="json"), confidence=1.0, doubts=[], issues=[],
        caption=caption, caption_status="done", caption_notes=[],
    ))  # fmt: skip
    row.job_id, row.status = job.id, "editing"
    db.flush()
    return job


def analysed(env, work_dir: Path, router) -> None:
    """What the edit's analysis left in the video's work dir: its vocals (a pause at
    3.25-3.75 s) and their words, the widest gap between them at 3.2-3.8 s."""
    work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=10", "-af",
                    "volume=enable='between(t,3.25,3.75)':volume=0", str(work_dir / "vocals.wav")],
                   check=True)  # fmt: skip
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(work_dir / "vocals.wav"), "-f",
                    "lavfi", "-i", "sine=frequency=70:duration=10", "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:normalize=0[m]", "-map", "[m]",
                    str(work_dir / "audio.wav")], check=True)  # fmt: skip  # the mix
    words = [Word(text="out", start=0.5, end=3.2), Word(text="on", start=3.8, end=6.0),
             Word(text="the", start=6.2, end=9.0)]  # fmt: skip
    transcript_file(work_dir, "vocals", router, None).write_text(
        Transcript(language="en", words=words).model_dump_json()
    )


def fake_router(env):
    """What the reel checks ask of the models' router: where the edit kept its words."""
    return SimpleNamespace(
        backends=SimpleNamespace(transcriber="faster-whisper"), settings=env.settings
    )


@pytest.fixture
def short_vs(env):
    """The vs preset for 10 s reels, so a test reel renders in a moment."""
    return page(env, {"min_duration": 5, "max_duration": 15})


@needs_ffmpeg
def test_a_reel_that_passes_every_check_waits_for_approval_then_its_post_time(
    env, short_vs, tmp_path
):
    owner = short_vs
    reel = split_reel(tmp_path, vs_preset(min_duration=5, max_duration=15), 3.5, 10.0)
    router = fake_router(env)
    work_dir = env.settings.cache_dir / ("f" * 20)
    analysed(env, work_dir, router)
    with env.sessions() as db:
        post_id = queued_post(db, owner)
        row = db.get(ChannelPost, post_id)
        row.heard_song, row.heard_artist, row.work_dir = (
            "Wuthering Heights",
            "Kate Bush",
            str(work_dir),
        )
        edited(db, post_id, reel)
        db.commit()
    queue = Queue()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 10, 0), queue)
    assert ("work", "p1") in queue.calls
    with env.sessions() as db:
        assert db.get(ChannelPost, post_id).status == "reviewing"
    assert pilot.review(post_id, env.sessions, env.settings, router) == "awaiting"
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        names = [c["name"] for c in row.checks]
        assert names == ["confidence", "caption", "reel length", "panels", "labels", "switch",
                         "voice", "leak", "loudness"]  # fmt: skip
        assert all(c["ok"] for c in row.checks), row.checks
        # Its pile folder says whose it is.
    # A person approves it: it counts, and it posts at the next time.
    assert env.client.post(f"/api/channel-posts/{post_id}/approve").json()["status"] == "ready"
    with env.sessions() as db:
        assert db.get(ChannelPilot, "p1").approved == 1


@needs_ffmpeg
def test_any_doubt_about_the_reel_leaves_it_for_a_person_and_on_the_piles(env, short_vs, tmp_path):
    owner = short_vs
    preset = vs_preset(min_duration=5, max_duration=15)
    reel = split_reel(tmp_path, preset, 3.5, 10.0, leaky=True)  # instruments left in
    router = fake_router(env)
    work_dir = env.settings.cache_dir / ("e" * 20)
    analysed(env, work_dir, router)
    with env.sessions() as db:
        post_id = queued_post(db, owner)
        db.get(ChannelPost, post_id).work_dir = str(work_dir)
        job = edited(db, post_id, reel, caption="")
        job.outputs[0].caption_status = "failed"
        db.get(ChannelPost, post_id).status = "reviewing"
        db.get(ChannelPost, post_id).output_id = job.outputs[0].id
        db.commit()
        output_id = job.outputs[0].id
    assert pilot.review(post_id, env.sessions, env.settings, router) == "look"
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        failed = {c["name"] for c in row.checks if not c["ok"]}
        assert failed == {"caption", "leak"}
        assert row.reason.startswith("caption: no caption")
        output = db.get(Output, output_id)
        assert output.pile == "look"
        assert any(d.startswith("Autopilot leak: instruments about") for d in output.doubts)


def test_an_edit_that_failed_or_was_deleted_is_a_skip_and_a_caption_is_waited_for(env):
    owner = page(env)
    with env.sessions() as db:
        failed, deleted, writing = (queued_post(db, owner, code=f"yt:D{n}") for n in range(3))
        job = Job(source="x", presets=[PRESET], notes=None, status="failed", error="boom")
        db.add(job)
        db.flush()
        db.get(ChannelPost, failed).job_id = job.id
        db.get(ChannelPost, deleted).job_id = "gone"
        done = edited(db, writing, Path("/nowhere.mp4"))
        done.outputs[0].caption_status = "writing"
        for i in (failed, deleted):
            db.get(ChannelPost, i).status = "editing"
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 10, 0), Queue())
    with env.sessions() as db:
        assert db.get(ChannelPost, failed).reason == "the edit failed: boom"
        assert db.get(ChannelPost, deleted).reason == "its edit was deleted"
        assert db.get(ChannelPost, writing).status == "editing"  # its caption first


def test_the_autopilots_reels_go_in_the_pages_folder_on_the_piles(env):
    owner = page(env)
    with env.sessions() as db:
        post_id = queued_post(db, owner)
        job = edited(db, post_id, Path("/x.mp4"))
        autopilot.file_outputs(db, job, now=datetime(2026, 11, 25, 10))
        folder = db.get(PileFolder, job.outputs[0].folder_id)
        assert folder.name == "25-NOV"
        assert db.get(PileFolder, folder.parent_id).name == "@vocalvsinstrument autopilot"


# ---- posting ---------------------------------------------------------------------------


def ready_reel(env, tmp_path, owner, **pilot_fields):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"a reel")
    with env.sessions() as db:
        if db.get(ChannelPilot, "p1") is None:
            make_pilot(db, owner, **pilot_fields)
        post_id = queued_post(db, owner)
        job = edited(db, post_id, video)
        row = db.get(ChannelPost, post_id)
        row.status, row.output_id = "ready", job.outputs[0].id
        row.heard_song, row.heard_artist = "Wuthering Heights", "Kate Bush"
        db.commit()
        return post_id, job.outputs[0].id


def test_a_post_time_posts_the_oldest_ready_reel_once_within_its_grace(env, tmp_path):
    owner = page(env)
    first, _ = ready_reel(env, tmp_path, owner, posts_per_day=2, post_times=["18:30", "21:30"])
    second, _ = ready_reel(env, tmp_path, owner)
    queue = Queue()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 18, 29), queue)
    assert not [c for c in queue.calls if c[0] == "post"]
    pilot.tick(env.sessions, datetime(2026, 11, 25, 18, 31), queue)
    assert ("post", first) in queue.calls
    with env.sessions() as db:
        assert db.get(ChannelPost, first).status == "due"
    queue.calls.clear()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 18, 40), queue)  # nothing since: done
    assert not [c for c in queue.calls if c[0] == "post"]
    pilot.tick(env.sessions, datetime(2026, 11, 25, 22, 31), queue)  # 21:30 + an hour: missed
    assert not [c for c in queue.calls if c[0] == "post"]
    pilot.tick(env.sessions, datetime(2026, 11, 26, 18, 45), queue)
    assert ("post", second) in queue.calls


def test_a_post_time_posts_nothing_beyond_the_days_limit_or_while_paused(env, tmp_path):
    owner = page(env)
    ready_reel(env, tmp_path, owner, posts_per_day=1, post_times=["18:30"])
    with env.sessions() as db:
        db.add(ChannelPost(pilot_id="p1", code="yt:old", url="u", status="posted",
                           posted_at=datetime(2026, 11, 25, 9, 0)))  # fmt: skip
        db.commit()
    queue = Queue()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 18, 31), queue)
    assert not [c for c in queue.calls if c[0] == "post"]
    with env.sessions() as db:
        db.get(ChannelPilot, "p1").active = False
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 26, 18, 31), queue)
    assert not [c for c in queue.calls if c[0] == "post"]


def test_dry_runs_dont_use_up_the_days_real_posts(env, tmp_path):
    owner = page(env)
    post_id, _ = ready_reel(env, tmp_path, owner, posts_per_day=1, post_times=["18:30"],
                            dry_run=False)  # fmt: skip
    with env.sessions() as db:
        db.add(ChannelPost(pilot_id="p1", code="yt:dry", url="u", status="posted", dry_run=True,
                           posted_at=datetime(2026, 11, 25, 9, 0)))  # fmt: skip
        db.commit()
    queue = Queue()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 18, 31), queue)
    assert ("post", post_id) in queue.calls


def due(env, post_id):
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        row.status, row.posted_at = "due", datetime(2026, 11, 25, 13, 0)
        db.commit()


def test_a_post_goes_at_once_on_its_own_thread_not_behind_the_queues_edits(env, monkeypatch):
    posted = []
    monkeypatch.setattr(pilot, "post", lambda post_id, Session, settings: posted.append(post_id))
    monkeypatch.setattr(jobs, "sessions", lambda: env.sessions)
    pilot.default_queue().post(7).join(5)
    assert posted == [7]


def test_in_dry_run_nothing_goes_to_instagram(env, tmp_path, graph):
    owner = page(env)
    post_id, output_id = ready_reel(env, tmp_path, owner)
    due(env, post_id)
    assert pilot.post(post_id, env.sessions, env.settings) == "posted"
    assert graph.calls == []
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        assert row.status == "posted" and row.dry_run and row.post_id == f"dry-run-{post_id}"
        assert row.caption.startswith("🎵 Wuthering Heights")
        assert db.get(Output, output_id).verdict is None  # still the owner's to post
        assert "dry run: would have posted “Wuthering Heights” by Kate Bush" in (
            db.get(ChannelPilot, "p1").note
        )
    assert pilot.post(post_id, env.sessions, env.settings) == "not due"  # never twice


def test_a_real_post_goes_through_the_apps_own_instagram_connection(env, tmp_path, graph):
    owner = page(env)
    post_id, output_id = ready_reel(env, tmp_path, owner, dry_run=False,
                                    base_url="https://editor.example")  # fmt: skip
    due(env, post_id)
    assert pilot.post(post_id, env.sessions, env.settings) == "posted"
    container = next(c for c in graph.calls if c[:2] == ("POST", "ig1/media"))[2]
    assert container["media_type"] == "REELS" and container["access_token"] == "token"
    assert container["video_url"].startswith("https://editor.example/api/pub/")
    assert container["caption"].startswith("🎵 Wuthering Heights — Kate Bush - 1978")
    assert ("POST", "ig1/media_publish") in [c[:2] for c in graph.calls]
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        assert row.status == "posted" and not row.dry_run and row.post_id == "18001"
        assert row.permalink == "https://www.instagram.com/reel/DNEW0000001/"
        assert db.get(Output, output_id).verdict == "posted"
        assert db.get(InstagramChannel, "ig1").last_post_at is not None
        pub = env.settings.media_dir / "pub" / container["video_url"].rsplit("/", 1)[1]
        assert pub.read_bytes() == b"a reel"


def test_a_failed_post_pauses_the_autopilot_and_says_why(env, tmp_path, monkeypatch):
    owner = page(env)
    fake = Graph(refuse_post=True)
    monkeypatch.setattr(instagram, "TRANSPORT", httpx.MockTransport(fake))
    post_id, output_id = ready_reel(env, tmp_path, owner, dry_run=False,
                                    base_url="https://editor.example")  # fmt: skip
    due(env, post_id)
    assert pilot.post(post_id, env.sessions, env.settings) == "failed"
    with env.sessions() as db:
        p = db.get(ChannelPilot, "p1")
        assert not p.active and "Media upload failed" in p.paused_reason
        row = db.get(ChannelPost, post_id)
        assert row.status == "failed" and "Media upload failed" in row.reason
        assert db.get(Output, output_id).verdict is None
    view = env.client.get("/api/channel-autopilot").json()["channels"][0]["pilot"]
    assert "posting “Wuthering Heights” by Kate Bush failed" in view["paused_reason"]
    # Try again sends it at the next post time; resuming clears the note.
    assert env.client.post(f"/api/channel-posts/{post_id}/retry").json()["status"] == "ready"
    resumed = save(env, active=True, research_account_id=None).json()
    assert resumed["active"] and resumed["paused_reason"] == ""


def test_once_enough_were_approved_waiting_reels_post_alone(env, tmp_path):
    owner = page(env)
    post_id, _ = ready_reel(env, tmp_path, owner, needs=1)
    with env.sessions() as db:
        db.get(ChannelPost, post_id).status = "awaiting"
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 10, 0), Queue())
    with env.sessions() as db:
        assert db.get(ChannelPost, post_id).status == "awaiting"
        db.get(ChannelPilot, "p1").approved = 1
        db.commit()
    pilot.tick(env.sessions, datetime(2026, 11, 25, 10, 5), Queue())
    with env.sessions() as db:
        assert db.get(ChannelPost, post_id).status == "ready"


# ---- learning ----------------------------------------------------------------------------


def test_the_pages_posts_are_learned_from_likes_without_the_insights_permission(env, graph):
    owner = page(env)
    with env.sessions() as db:
        make_pilot(db, owner)
        db.commit()

    class Reader:
        def chat_json(self, messages, schema, **kwargs):
            assert "Billie Jean" in messages[-1]["content"]
            return {"summary": "Iconic singers, isolated vocals."}

    note = pilot.learn("p1", env.sessions, planner=lambda: nullcontext(Reader()))
    assert "read 2 posts" in note and "no insights" in note
    with env.sessions() as db:
        p = db.get(ChannelPilot, "p1")
        assert p.insights_allowed is False and len(p.history) == 2
        assert p.history[0]["likes"] == 4047
        account = db.get(ResearchAccount, p.research_account_id)
        assert account.own == ["DSELF000001", "DBILLIE0001"]
        assert account.best[0]["code"] == "DBILLIE0001"  # the most liked first
        assert account.summary == "Iconic singers, isolated vocals." and account.learned_at
        assert db.scalar(select(AccountSnapshot)).reels[0]["likes"] == 4047
        # A research for the account is told its best posts and never gives their songs.
        taste = jobs.taste(db, Research(owner_id=owner, account_id=account.id, description=""))
        assert taste["best"][0].startswith("Michael Jackson - Billie Jean (10166 likes")
        assert research.Posted(taste["avoid"], taste["posted"]).has("Billie Jean", "M. Jackson")


def test_a_posts_numbers_are_read_a_day_and_two_days_after(env, tmp_path, monkeypatch):
    owner = page(env)
    fake = Graph(insights=True)
    monkeypatch.setattr(instagram, "TRANSPORT", httpx.MockTransport(fake))
    post_id, _ = ready_reel(env, tmp_path, owner, dry_run=False)
    posted = datetime(2026, 11, 25, 13, 0)
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        row.status, row.post_id, row.posted_at = "posted", "18001", posted
        db.commit()
    queue = Queue()
    pilot.tick(env.sessions, pilot.local(posted + timedelta(hours=23)), queue)
    assert not [c for c in queue.calls if c[0] == "read"]
    assert pilot.read_numbers("p1", env.sessions, now=posted + timedelta(hours=25)) == 1
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        assert row.insights == {"views": 5200, "reach": 4100, "saved": 31, "likes": 120,
                                "comments": 4, "hours": 25}  # fmt: skip
        assert db.get(ChannelPilot, "p1").insights_allowed is True
    assert pilot.read_numbers("p1", env.sessions, now=posted + timedelta(hours=30)) == 0
    assert pilot.read_numbers("p1", env.sessions, now=posted + timedelta(hours=49)) == 1
    shown = env.client.get("/api/channel-autopilot/ig1/posts?show=posted").json()
    assert shown[0]["insights"]["views"] == 5200


def test_without_the_insights_permission_likes_and_comments_are_read(env, tmp_path, graph):
    owner = page(env)
    post_id, _ = ready_reel(env, tmp_path, owner, dry_run=False)
    with env.sessions() as db:
        row = db.get(ChannelPost, post_id)
        row.status, row.post_id = "posted", "18001"
        row.posted_at = datetime(2026, 11, 25, 13, 0)
        db.commit()
    assert pilot.read_numbers("p1", env.sessions, now=datetime(2026, 11, 26, 14, 0)) == 1
    with env.sessions() as db:
        assert db.get(ChannelPost, post_id).insights == {"likes": 120, "comments": 4, "hours": 25}
        assert db.get(ChannelPilot, "p1").insights_allowed is False


# ---- the page's controls ----------------------------------------------------------------


def test_find_now_starts_todays_research_after_reading_the_page(env, monkeypatch):
    page(env)
    asked = []
    monkeypatch.setattr(jobs, "channel_learn", lambda *args: asked.append(args))
    assert env.client.post("/api/channel-autopilot/ig1/find").status_code == 409  # not saved
    save(env)
    found = env.client.post("/api/channel-autopilot/ig1/find")
    assert found.status_code == 200, found.text
    ((_, research_id),) = asked
    assert found.json()["research"]["id"] == research_id
    assert env.client.post("/api/channel-autopilot/ig1/find").status_code == 409  # running
    save(env, active=False, research_account_id=None)
    with env.sessions() as db:
        db.get(Research, research_id).status = "done"
        db.commit()
    assert env.client.post("/api/channel-autopilot/ig1/find").status_code == 409  # paused


def test_videos_are_listed_by_where_they_are_and_can_be_skipped(env, tmp_path):
    owner = page(env)
    post_id, _ = ready_reel(env, tmp_path, owner)
    with env.sessions() as db:
        db.add(ChannelPost(pilot_id="p1", code="yt:s", url="u", status="skipped",
                           reason="fits 6/10; needs 8"))  # fmt: skip
        db.commit()
    queue = env.client.get("/api/channel-autopilot/ig1/posts?show=queue").json()
    assert [p["id"] for p in queue] == [post_id] and queue[0]["output_id"]
    skipped = env.client.get("/api/channel-autopilot/ig1/posts?show=skipped").json()
    assert skipped[0]["reason"] == "fits 6/10; needs 8"
    assert env.client.post(f"/api/channel-posts/{post_id}/approve").status_code == 409
    assert env.client.post(f"/api/channel-posts/{post_id}/skip").json()["status"] == "skipped"
    assert env.client.post(f"/api/channel-posts/{post_id}/retry").status_code == 409


def test_a_restart_sends_checks_again_and_pauses_on_a_post_cut_off(env, tmp_path):
    owner = page(env)
    checking, _ = ready_reel(env, tmp_path, owner)
    waiting, _ = ready_reel(env, tmp_path, owner)
    sending, _ = ready_reel(env, tmp_path, owner)
    with env.sessions() as db:
        db.get(ChannelPost, checking).status = "checking"
        db.get(ChannelPost, waiting).status = "due"
        db.get(ChannelPost, sending).status = "posting"
        db.commit()
    jobs.recover(env.sessions)
    with env.sessions() as db:
        assert db.get(ChannelPost, checking).status == "queued"
        assert db.get(ChannelPost, waiting).status == "ready"
        assert db.get(ChannelPost, sending).status == "failed"
        p = db.get(ChannelPilot, "p1")
        assert not p.active and "Look on Instagram" in p.paused_reason
