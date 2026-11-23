import json

import pytest

from agentic_editor import jobs
from agentic_editor.animate import planner, routes, studio
from agentic_editor.db import Animation, Setting
from tests import test_api
from tests.test_animate import Router, Writer, fake_speak, long_plan, reply_for
from tests.test_api import employee, login

env = test_api.env  # the editor with a signed-in admin (a fixture)


def studio_on(env, users=("admin", "riya")):
    with env.sessions() as db:
        db.merge(Setting(key="feature:animation_studio", value=json.dumps(list(users))))
        db.commit()


@pytest.fixture
def writer(env, monkeypatch):
    """The planner model answers from a queue of replies; work runs at once, voices faked,
    frames not drawn (the render writes a stand-in file)."""
    plan = long_plan()
    model = Writer(*[reply_for(plan)] * 6)
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(
        jobs,
        "animate",
        lambda animation_id: studio.run_task(
            animation_id, env.sessions, env.settings, Router(model), speak=fake_speak
        ),
    )

    def draw(plan, tl, voices, out_dir, fonts_dir, models_dir, progress, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "animation.mp4").write_bytes(b"fake video")
        return out_dir / "animation.mp4", None, []

    real = studio.render_job
    monkeypatch.setattr(
        studio, "render_job", lambda *a, **k: real(*a, **{**k, "speak": fake_speak, "draw": draw})
    )
    studio_on(env)
    return model


def test_the_studio_is_only_for_accounts_it_is_on_for(env):
    assert env.client.get("/api/animations").status_code == 403
    me = env.client.get("/api/me").json()
    assert "animation_studio" not in me["features"]
    studio_on(env)
    assert env.client.get("/api/animations").json() == []
    assert "animation_studio" in env.client.get("/api/me").json()["features"]


def test_one_line_becomes_a_draft_that_can_be_edited_changed_undone_and_rendered(env, writer):
    made = env.client.post(
        "/api/animations", json={"idea": "the lost city of Ubar", "length": "short"}
    )
    assert made.status_code == 200, made.text
    a = made.json()
    assert a["status"] == "ready" and a["title"] == "The Lost City" and a["scenes"] == 8
    assert a["player"]["scenes"][-1]["kind"] == "cta" and a["duration"] > 10
    assert a["plan"]["voice"] == "bm_george" and not a["can_undo"]

    # Edited by hand: kept, the old one kept for undo; a broken one refused.
    plan = a["plan"]
    plan["scenes"][0]["narration"] = "This city vanished without a trace."
    edited = env.client.put(f"/api/animations/{a['id']}", json={"plan": plan}).json()
    assert edited["plan"]["scenes"][0]["narration"] == "This city vanished without a trace."
    assert edited["can_undo"] and edited["status"] == "ready"
    assert edited["player"]["scenes"][0]["words"][0]["text"] == "This"
    bad = env.client.put(f"/api/animations/{a['id']}", json={"plan": {"scenes": "none"}})
    assert bad.status_code == 422

    # Style and voice change at once, without writing.
    paper = env.client.patch(
        f"/api/animations/{a['id']}", json={"style": "paper", "voice": "calm"}
    ).json()
    assert paper["plan"]["style"] == "paper" and paper["plan"]["voice"] == "af_heart"
    assert paper["player"]["style"] == "paper"

    # Changed in words, one scene again, then undone.
    assert (
        env.client.post(f"/api/animations/{a['id']}/change", json={"request": " "}).status_code
        == 422
    )
    changed = env.client.post(
        f"/api/animations/{a['id']}/change", json={"request": "darker"}
    ).json()
    assert changed["status"] == "ready" and "darker" in writer.asked[-1][-1]["content"]
    writer.replies.insert(
        0,
        {
            "narration": "Nobody believed it.",
            "visual": {"kind": "quote", "text": "Nobody believed it.", "by": "A scholar"},
        },
    )
    redone = env.client.post(f"/api/animations/{a['id']}/change", json={"scene": 2}).json()
    assert redone["plan"]["scenes"][2]["visual"]["kind"] == "quote"
    assert (
        env.client.post(f"/api/animations/{a['id']}/change", json={"scene": 30}).status_code == 422
    )
    undone = env.client.post(f"/api/animations/{a['id']}/undo").json()
    assert undone["plan"]["scenes"][2]["visual"]["kind"] != "quote"

    # Rendered as an ordinary job: the video in Jobs with the reel's caption, ready to post.
    rendered = env.client.post(f"/api/animations/{a['id']}/render").json()
    assert rendered["render"]["status"] == "done" and rendered["render"]["output_id"]
    job = env.client.get(f"/api/jobs/{rendered['render']['job_id']}").json()
    (output,) = job["outputs"]
    assert output["preset_label"] == "Animation · Clean Paper"
    assert output["caption_status"] == "done" and output["caption"] == undone["plan"]["caption"]
    assert job["title"] == "The Lost City"
    # Writing the caption again from the card takes the reel's caption as it stands.
    again = env.client.post(f"/api/outputs/{output['id']}/caption").json()
    assert again["outputs"][0]["caption"] == undone["plan"]["caption"]

    listed = env.client.get("/api/animations").json()
    assert [x["id"] for x in listed] == [a["id"]] and listed[0]["render"]["status"] == "done"
    assert env.client.delete(f"/api/animations/{a['id']}").json() == {"ok": True}
    assert env.client.get(f"/api/animations/{a['id']}").status_code == 404
    assert env.client.get(f"/api/jobs/{job['id']}").status_code == 200  # its video stays


def test_busy_animations_take_no_new_work(env, writer, monkeypatch):
    monkeypatch.setattr(jobs, "animate", lambda animation_id: None)  # stays queued
    a = env.client.post("/api/animations", json={"idea": "the lost city"}).json()
    assert a["status"] == "queued"
    assert (
        env.client.post(f"/api/animations/{a['id']}/change", json={"request": "x"}).status_code
        == 409
    )
    assert env.client.post(f"/api/animations/{a['id']}/render").status_code == 409
    assert env.client.delete(f"/api/animations/{a['id']}").status_code == 409


def test_someone_elses_animations_are_theirs_alone_but_the_primary_sees_all(env, writer):
    _, riya = employee(env)
    hers = riya.post("/api/animations", json={"idea": "patience", "kind": "lesson"}).json()
    mine = env.client.post("/api/animations", json={"idea": "the lost city"}).json()
    assert [a["id"] for a in riya.get("/api/animations").json()] == [hers["id"]]
    assert riya.get(f"/api/animations/{mine['id']}").status_code == 404
    assert riya.delete(f"/api/animations/{mine['id']}").status_code == 404
    seen = {a["id"] for a in env.client.get("/api/animations").json()}
    assert seen == {hers["id"], mine["id"]}  # the admin here is the primary account


def test_a_restart_fails_work_in_progress_and_queues_lost_work_again(env, monkeypatch):
    with env.sessions() as db:
        db.add_all([
            Animation(id="busy1", idea="x", status="drafting", task={"op": "draft"}),
            Animation(id="wait1", idea="y", status="queued", task={"op": "draft"}),
        ])  # fmt: skip
        db.commit()
    queued = []
    monkeypatch.setattr(jobs, "animate", queued.append)
    jobs.recover(env.sessions)
    with env.sessions() as db:
        busy = db.get(Animation, "busy1")
        assert busy.status == "failed" and busy.error == jobs.INTERRUPTED and busy.task is None
    assert queued == ["wait1"]


def test_the_player_and_its_fonts_are_served_to_signed_in_people(env):
    page = env.client.get("/api/animation-player/player.html")
    assert page.status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    # Its script and style are named by their content, so a cached old player (Cloudflare
    # keeps static files four hours) can never draw a newer style as Neon Glow.
    js, css = routes.asset_version("player.js"), routes.asset_version("player.css")
    assert f'src="player.js?v={js}"' in page.text and f'href="player.css?v={css}"' in page.text
    named = env.client.get(f"/api/animation-player/player.js?v={js}")
    assert named.headers["content-type"].startswith("text/javascript")
    assert "immutable" in named.headers["cache-control"]
    stale = env.client.get("/api/animation-player/player.js?v=old")
    assert stale.headers["cache-control"] == "no-store"
    assert "story: {" in named.text and "kinetic: {" in named.text
    assert env.client.get("/api/animation-player/Inter-SemiBold.ttf").status_code == 200
    assert env.client.get("/api/animation-player/..%2Fapp.db").status_code == 404
    assert env.client.get("/api/animation-player/secret.ttf").status_code == 404
    env.client.post("/api/logout")
    assert env.client.get("/api/animation-player/player.html").status_code == 401
    login(env.client)


def test_voices_are_listed_with_the_owners_three_kinds_first(env):
    studio_on(env)
    voices = env.client.get("/api/animation-voices").json()
    assert [v["group"] for v in voices[:3]] == ["narrator"] * 3
    assert {
        "id": "af_heart",
        "name": "Heart",
        "group": "calm",
        "about": "warm, calm American woman",
    } in voices
    bad = env.client.post("/api/animations", json={"idea": "x y z", "voice": "nobody"})
    assert bad.status_code == 422


def test_painted_pictures_are_served_by_name_and_switched_per_reel(
    env, writer, tmp_path, monkeypatch
):
    from agentic_editor.animate import images

    folder = tmp_path / "pictures"
    folder.mkdir()
    monkeypatch.setattr(images, "folder", lambda settings: folder)
    name = "0123456789abcdef01234567.png"
    (folder / name).write_bytes(b"\x89PNG fake")
    got = env.client.get(f"/api/animation-images/{name}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert "immutable" in got.headers["cache-control"]
    assert env.client.get("/api/animation-images/..%2Fapp.png").status_code == 404
    assert env.client.get("/api/animation-images/" + "f" * 24 + ".png").status_code == 404
    made = env.client.post(
        "/api/animations", json={"idea": "the lost city", "images": False}
    ).json()
    assert made["images"] is False
    on = env.client.patch(f"/api/animations/{made['id']}", json={"images": True}).json()
    assert on["images"] is True and on["status"] == "ready"


def test_world_map_reels_have_ideas_tiles_and_their_own_writer(env, monkeypatch, tmp_path):
    from PIL import Image

    from agentic_editor.animate import maps
    from tests.test_animate_map import MapModel, map_reply

    # No map on this machine: nothing to fetch in a test, no country checked or flown to.
    monkeypatch.setattr(studio, "ensure_map", lambda settings, say=None: [])
    monkeypatch.setattr(maps, "table", lambda models_dir: {})
    monkeypatch.setattr(maps, "tiles_dir", lambda models_dir: tmp_path / "tiles")
    model = MapModel(map_reply(), map_reply())
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(
        jobs,
        "animate",
        lambda animation_id: studio.run_task(
            animation_id, env.sessions, env.settings, Router(model), speak=fake_speak
        ),
    )
    studio_on(env)
    ideas = env.client.get("/api/animation-ideas?style=map").json()["ideas"]
    assert len(ideas) == 6 and all("every country" in i for i in ideas)
    assert env.client.get("/api/animation-ideas?style=glow").json() == {"ideas": []}
    made = env.client.post(
        "/api/animations", json={"idea": ideas[0], "style": "map", "length": "short"}
    )
    assert made.status_code == 200, made.text
    a = made.json()
    assert a["status"] == "ready" and a["plan"]["style"] == "map", a["error"]
    assert a["plan"]["scenes"][1]["visual"]["kind"] == "country"
    assert a["player"]["map"]["scenes"][1] is None  # no map here: the card shows, no flying
    for _ in range(5):  # a topic made is never offered again
        assert ideas[0] not in env.client.get("/api/animation-ideas?style=map").json()["ideas"]
    # A reel of another style can be shown on the map, and back.
    glow = env.client.patch(f"/api/animations/{a['id']}", json={"style": "glow"}).json()
    assert glow["plan"]["style"] == "glow" and "map" not in glow["player"]
    # The Earth's tiles: by version, level and name only.
    (tmp_path / "tiles" / "0").mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "tiles" / "0" / "3_1.jpg")
    tile = env.client.get(f"/api/animation-map/v{maps.VERSION}/0/3_1.jpg")
    assert tile.status_code == 200 and tile.headers["content-type"] == "image/jpeg"
    assert "immutable" in tile.headers["cache-control"]
    for bad in (
        f"v{maps.VERSION + 1}/0/3_1.jpg",
        f"v{maps.VERSION}/9/3_1.jpg",
        f"v{maps.VERSION}/0/3_1.png",
        f"v{maps.VERSION}/0/4_1.jpg",
        f"v{maps.VERSION}/0/..%2F0%2F3_1.jpg",
    ):
        assert env.client.get(f"/api/animation-map/{bad}").status_code == 404, bad
