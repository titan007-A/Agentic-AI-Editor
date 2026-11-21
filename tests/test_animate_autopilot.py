import json
from datetime import date, datetime

import pytest

from agentic_editor import instagram, jobs
from agentic_editor.animate import planner, schedule, studio
from agentic_editor.db import (
    Animation,
    AnimationAutopilot,
    InstagramChannel,
    Job,
    Output,
    Setting,
    User,
)
from tests import test_api
from tests.test_animate import (
    Router,
    Writer,
    fake_speak,
    long_plan,
    reply_for,
    sample_plan,
    studio_env,
)

env = test_api.env  # the editor with a signed-in admin (a fixture)


def pilot(**fields) -> AnimationAutopilot:
    base = {"id": "p1", "owner_id": "u1", "niche": "History & mysteries", "hour": 10, "per_day": 1}
    return AnimationAutopilot(**(base | fields))


# ---- when ------------------------------------------------------------------------------


def test_a_reel_is_due_at_its_hour_and_the_next_ones_a_few_hours_apart():
    p = pilot(per_day=2, last_on=None, made_on_last=0, active=True)
    assert not schedule.due(p, datetime(2026, 11, 21, 9, 59))
    assert schedule.due(p, datetime(2026, 11, 21, 10, 0))
    p.last_on, p.made_on_last = date(2026, 11, 21), 1
    assert not schedule.due(p, datetime(2026, 11, 21, 12, 30))
    assert schedule.due(p, datetime(2026, 11, 21, 13, 0))  # GAP_HOURS later
    p.made_on_last = 2
    assert not schedule.due(p, datetime(2026, 11, 21, 23, 0))  # two today: done
    assert schedule.due(p, datetime(2026, 11, 22, 10, 0))  # a new day
    p.active = False
    assert not schedule.due(p, datetime(2026, 11, 22, 10, 0))


def test_due_reels_start_with_the_next_topic_for_accounts_with_the_studio(tmp_path):
    _, Session = studio_env(tmp_path)
    with Session() as db:
        db.add_all([
            User(id="u1", username="siddhant-content", password_hash="x", role="admin"),
            User(id="u2", username="someone", password_hash="x", role="admin"),
            pilot(topics=["The Dancing Plague", "Ubar"], active=True),
            pilot(id="p2", owner_id="u2", active=True),  # no studio for this account
            pilot(id="p3", owner_id="u1-gone", active=True),
        ])  # fmt: skip
        db.commit()
    queued = []
    started = schedule.start_due(Session, datetime(2026, 11, 21, 10, 5), queued.append)
    assert started == queued and len(started) == 1
    with Session() as db:
        a = db.get(Animation, started[0])
        assert (a.idea, a.owner_id, a.autopilot_id, a.task) == (
            "The Dancing Plague",
            "u1",
            "p1",
            {"op": "auto"},
        )
        p = db.get(AnimationAutopilot, "p1")
        assert p.topics == ["Ubar"] and p.used == ["The Dancing Plague"]
        assert (p.last_on, p.made_on_last) == (date(2026, 11, 21), 1)
    assert schedule.start_due(Session, datetime(2026, 11, 21, 11, 0), queued.append) == []


# ---- the reel ------------------------------------------------------------------------


def test_an_autopilot_reel_gets_a_topic_a_draft_a_fact_check_and_its_render(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    monkeypatch.setattr(
        planner, "gather", lambda *a, **k: [planner.Found("Ubar", "u", "Ubar text")]
    )
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    rendered = []
    monkeypatch.setattr(schedule, "render", lambda aid, Session: rendered.append(aid))
    plan = long_plan()
    model = Writer(
        {"topics": ["The lost city of Ubar", "The Dancing Plague"]},
        reply_for(plan),
        {"unsupported": ["4,000 years of sand"]},
    )
    with Session() as db:
        db.add(pilot(used=["The Dancing Plague"]))
        db.add(Animation(id="a1", owner_id="u1", idea="", autopilot_id="p1", task={"op": "auto"}))
        db.commit()
    studio.run_task("a1", Session, settings, Router(model), speak=fake_speak)
    with Session() as db:
        a = db.get(Animation, "a1")
        assert a.status == "ready" and a.idea == "The lost city of Ubar"
        assert a.doubts == ["4,000 years of sand"]
        assert db.get(AnimationAutopilot, "p1").used == [
            "The Dancing Plague",
            "The lost city of Ubar",
        ]
    assert "History & mysteries" in model.asked[0][0]["content"]
    assert rendered == ["a1"]


def test_a_reel_that_could_not_be_made_says_so(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    with Session() as db:
        db.add(pilot(niche=""))
        db.add(Animation(id="a2", owner_id="u1", idea="", autopilot_id="p1", task={"op": "auto"}))
        db.commit()
    studio.run_task("a2", Session, settings, Router(Writer()), speak=fake_speak)
    with Session() as db:
        a = db.get(Animation, "a2")
        assert a.status == "failed" and a.post_status == "failed" and "no niche" in a.post_error


def test_next_topics_are_new_and_facts_are_checked_only_where_they_matter():
    writer = Writer({"topics": ["Ubar", "  The   Voynich manuscript "]})
    assert planner.next_topic(writer, "mysteries", ["ubar"]) == "The Voynich manuscript"
    with pytest.raises(planner.PlannerError):
        planner.next_topic(Writer({"topics": ["Ubar", "UBAR", "ubar"]}), "mysteries", ["Ubar"])
    assert planner.fact_check(Writer(), sample_plan(kind="story"), []) == []
    assert planner.fact_check(Writer(), sample_plan(), []) == [
        "no sources were found to check the facts against"
    ]


# ---- after the render ------------------------------------------------------------------


def made(tmp_path, Session, **pilot_fields):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    with Session() as db:
        db.add(User(id="u1", username="siddhant-content", password_hash="x", role="admin"))
        db.commit()  # the page belongs to someone first
        db.add(pilot(**({"base_url": "https://editor.example"} | pilot_fields)))
        db.add(InstagramChannel(id="ig1", owner_id="u1", username="lost.history", access_token="t"))
        db.add(Job(id="j1", source="animation:a1", presets=[], owner_id="u1", status="done"))
        db.add(Output(job_id="j1", preset="animation-glow", path=str(video), caption="A city.",
                      duration=30.0, planner="animation", edl={}))  # fmt: skip
        plan = sample_plan().model_dump(mode="json")
        db.add(Animation(id="a1", owner_id="u1", idea="Ubar", autopilot_id="p1", job_id="j1",
                         plan=plan, status="ready", post_status="rendering",
                         doubts=[]))  # fmt: skip
        db.commit()


def test_a_rendered_reel_waits_for_approval_until_it_may_post_alone(tmp_path):
    settings, Session = studio_env(tmp_path)
    made(tmp_path, Session, channel_id="ig1", auto_post=False, approved=3)
    sent = []
    send = lambda chan, link, caption, feed: sent.append((chan.username, link, caption)) or "post-9"  # noqa: E731
    schedule.after_render("a1", Session, settings, ok=True, send=send)
    with Session() as db:
        assert db.get(Animation, "a1").post_status == "awaiting" and sent == []
        p = db.get(AnimationAutopilot, "p1")
        p.auto_post, p.approved = True, 10
        db.get(Animation, "a1").post_status = "rendering"
        db.commit()
    schedule.after_render("a1", Session, settings, ok=True, send=send)
    with Session() as db:
        a = db.get(Animation, "a1")
        assert (a.post_status, a.post_id) == ("posted", "post-9")
    ((who, link, caption),) = sent
    assert who == "lost.history" and caption == "A city."
    assert link.startswith("https://editor.example/api/pub/") and link.endswith(".mp4")


def test_a_doubtful_fact_or_a_failed_post_keeps_it_for_a_person(tmp_path):
    settings, Session = studio_env(tmp_path)
    made(tmp_path, Session, channel_id="ig1", auto_post=True, approved=12)
    with Session() as db:
        db.get(Animation, "a1").doubts = ["1992"]
        db.commit()
    schedule.after_render("a1", Session, settings, ok=True, send=lambda *a: "never")
    with Session() as db:
        a = db.get(Animation, "a1")
        assert a.post_status == "awaiting"
        a.doubts, a.post_status = [], "rendering"
        db.commit()

    def refused(*args):
        raise instagram.InstagramError("the token expired")

    schedule.after_render("a1", Session, settings, ok=True, send=refused)
    with Session() as db:
        a = db.get(Animation, "a1")
        assert a.post_status == "awaiting" and "token expired" in a.post_error
    schedule.after_render("a1", Session, settings, ok=False, error="the render failed: boom")
    with Session() as db:
        assert db.get(Animation, "a1").post_status == "failed"


def test_approving_posts_it_and_counts(tmp_path):
    settings, Session = studio_env(tmp_path)
    made(tmp_path, Session, channel_id="ig1", approved=4)
    with Session() as db:
        a = db.get(Animation, "a1")
        schedule.approve(db, a, "https://editor.example", settings, send=lambda *x: "post-1")
        db.commit()
        assert (a.post_status, a.post_id) == ("posted", "post-1")
        assert db.get(AnimationAutopilot, "p1").approved == 5


# ---- the API ---------------------------------------------------------------------------


def test_the_autopilot_is_set_up_and_auto_post_waits_for_enough_approvals(env, monkeypatch):
    with env.sessions() as db:
        db.merge(Setting(key="feature:animation_studio", value=json.dumps(["*"])))
        me = db.query(User).filter_by(role="admin").first()
        db.add(
            InstagramChannel(id="ig1", owner_id=me.id, username="lost.history", access_token="t")
        )
        db.commit()
    got = env.client.get("/api/animation-autopilot").json()
    assert got["exists"] is False and got["channels"] == [{"id": "ig1", "username": "lost.history"}]
    body = {"niche": "History  & mysteries", "topics": [" Ubar ", "", "Voynich"], "hour": 9,
            "channel_id": "ig1", "style": "story"}  # fmt: skip
    saved = env.client.put("/api/animation-autopilot", json=body)
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()["topics"] == ["Ubar", "Voynich"]
        and saved.json()["niche"] == "History & mysteries"
    )
    refused = env.client.put("/api/animation-autopilot", json=body | {"auto_post": True})
    assert refused.status_code == 409 and "approve 10" in refused.json()["detail"]
    other = env.client.put("/api/animation-autopilot", json=body | {"channel_id": "not-mine"})
    assert other.status_code == 404
    with env.sessions() as db:
        assert db.query(AnimationAutopilot).one().base_url.startswith("http")

    queued = []
    monkeypatch.setattr(jobs, "animate", queued.append)
    started = env.client.post("/api/animation-autopilot/now").json()
    assert started["idea"] == "Ubar" and started["autopilot_id"] and queued == [started["id"]]
    assert env.client.get("/api/animation-autopilot").json()["topics"] == ["Voynich"]
    assert env.client.post(f"/api/animations/{started['id']}/approve").status_code == 409
    assert env.client.post(f"/api/animations/{started['id']}/skip").status_code == 409
    with env.sessions() as db:
        db.get(Animation, started["id"]).post_status = "awaiting"
        db.commit()
    skipped = env.client.post(f"/api/animations/{started['id']}/skip").json()
    assert skipped["post_status"] == "skipped"
