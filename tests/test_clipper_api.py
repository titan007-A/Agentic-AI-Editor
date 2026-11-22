import json

import numpy as np
import pytest
import yaml

from agentic_editor import jobs
from agentic_editor.clipper import hunt, kick
from agentic_editor.db import Setting
from tests import test_api
from tests.test_api import employee
from tests.test_clipper import UUID, FakeKick

env = test_api.env  # the editor with a signed-in admin (a fixture)


def clipper_on(env, users=("admin", "riya")):
    with env.sessions() as db:
        db.merge(Setting(key="feature:kick_clipper", value=json.dumps(list(users))))
        db.commit()


@pytest.fixture
def kicked(env, monkeypatch):
    """Kick faked; a hunt runs at once (its windows written as stand-ins) and its clips'
    jobs run through the fake pipeline."""
    fake = FakeKick(seconds=3600, burst=(1810, 1822))
    monkeypatch.setattr(kick, "Client", lambda: fake)

    def fake_cut(full, start, end, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"window")

    monkeypatch.setattr(hunt, "cut", fake_cut)
    monkeypatch.setattr(hunt, "listen", lambda light, start, end: np.full(int(end - start), -40.0))
    monkeypatch.setattr(
        jobs, "hunt", lambda hunt_id: hunt.run(hunt_id, env.sessions, env.settings, client=fake)
    )
    clipper_on(env)
    return fake


def add_preset(env, name, **fields):
    data = {"name": name, "label": name, "min_duration": 10, "max_duration": 40, **fields}
    (env.settings.presets_dir / f"{name}.yaml").write_text(yaml.safe_dump(data))


def test_the_clipper_is_only_for_accounts_it_is_on_for(env):
    assert env.client.get("/api/kick/hunts").status_code == 403
    assert "kick_clipper" not in env.client.get("/api/me").json()["features"]
    clipper_on(env)
    assert env.client.get("/api/kick/hunts").json() == []
    assert "kick_clipper" in env.client.get("/api/me").json()["features"]


def test_streamers_are_added_by_name_or_link_changed_and_removed(env, kicked):
    add_preset(env, "kick-clips")
    made = env.client.post("/api/kick/streamers", json={"name": "https://kick.com/xQc"})
    assert made.status_code == 200, made.text
    s = made.json()
    assert (s["slug"], s["username"], s["preset"], s["live"]) == ("xqc", "xQc", "kick-clips", False)
    assert s["folder_shown"] == "Kick xQc"
    again = env.client.post("/api/kick/streamers", json={"name": "xqc"})
    assert again.status_code == 409
    assert env.client.post("/api/kick/streamers", json={"name": "nobody"}).status_code == 422
    changed = env.client.patch(
        f"/api/kick/streamers/{s['id']}", json={"live": True, "per_hour": 6, "folder": "GTA"}
    ).json()
    assert (changed["live"], changed["per_hour"], changed["folder_shown"]) == (True, 6, "GTA")
    assert [x["id"] for x in env.client.get("/api/kick/streamers").json()] == [s["id"]]
    streams = env.client.get("/api/kick/streams", params={"name": "xqc"}).json()
    assert streams["username"] == "xQc" and streams["streams"][0]["uuid"] == UUID
    assert streams["streams"][0]["link"] == f"https://kick.com/xqc/videos/{UUID}"
    assert env.client.delete(f"/api/kick/streamers/{s['id']}").json() == {"ok": True}
    assert env.client.get("/api/kick/streamers").json() == []


def test_a_hunt_on_a_stream_s_stretch_makes_its_clips_as_jobs(env, kicked):
    add_preset(env, "kick-clips")
    made = env.client.post(
        "/api/kick/hunts",
        json={"link": f"https://kick.com/xqc/videos/{UUID}", "start": "28:20", "end": "31:20",
              "count": 3},
    )  # fmt: skip
    assert made.status_code == 200, made.text
    found = made.json()
    assert found["status"] == "done", found["error"]
    assert (found["start"], found["end"], found["preset"]) == (1700, 1880, "kick-clips")
    first = found["moments"][0]
    assert first["start"] <= 1810 <= first["end"]
    assert first["link"] == f"https://kick.com/xqc/videos/{UUID}?t={int(first['start'])}"
    assert first["job"]["status"] == "done" and first["job"]["outputs"]
    call = next(c for c in env.calls if c["source"] == f"kick:kick-{found['id']}-0")
    assert call["presets"] == ["kick-clips"] and "Kick live stream" in call["notes"]
    job = env.client.get(f"/api/jobs/{first['job_id']}").json()
    assert job["status"] == "done"
    listed = env.client.get("/api/kick/hunts").json()
    assert [h["id"] for h in listed] == [found["id"]]
    assert env.client.delete(f"/api/kick/hunts/{found['id']}").json() == {"ok": True}
    assert env.client.get(f"/api/jobs/{first['job_id']}").status_code == 200  # clips stay


def test_a_hunt_is_refused_what_it_cant_do(env, kicked):
    add_preset(env, "only-download", download_only=True)
    link = f"https://kick.com/xqc/videos/{UUID}"
    cases = [
        ({"link": link, "start": "1:00", "end": "1:10"}, "too short"),
        ({"link": link, "start": "1:99"}, "isn't a time"),
        ({"link": link, "preset": "only-download"}, "download-only"),
        ({"link": link, "preset": "nope"}, "no preset"),
        ({"link": "https://kick.com/xqc/clips/clip_1"}, "that's a clip"),
        ({"link": "xqc"}, "isn't live now"),
    ]
    for body, said in cases:
        answer = env.client.post("/api/kick/hunts", json=body)
        assert answer.status_code == 422 and said in answer.text, (body, answer.text)


def test_a_live_streamer_s_hunt_reads_what_was_streamed_so_far(env, kicked):
    kicked.live_for = 900
    found = env.client.post("/api/kick/hunts", json={"link": "kick.com/xqc", "count": 1}).json()
    assert found["status"] == "done" and found["end"] == 900 and found["stream_id"] == 999


def test_a_streamer_without_stream_videos_is_told_so(env, kicked, monkeypatch):
    kicked.live_for = 900
    live = kicked.channel("xqc")
    live.live.source, live.vods = "", False
    monkeypatch.setattr(kicked, "channel", lambda slug: live)
    answer = env.client.post("/api/kick/hunts", json={"link": "kick.com/xqc"})
    assert answer.status_code == 422 and "VODs) off" in answer.text


def test_a_failed_hunt_is_tried_again(env, kicked, monkeypatch):
    def broken(source):
        raise kick.KickError("Kick answered 503; try again later")

    monkeypatch.setattr(kicked, "variants", broken)
    found = env.client.post("/api/kick/hunts", json={"link": f"kick.com/video/{UUID}"}).json()
    assert found["status"] == "failed" and "503" in found["error"]
    monkeypatch.undo()
    monkeypatch.setattr(kick, "Client", lambda: kicked)
    monkeypatch.setattr(hunt, "cut", lambda full, s, e, dest: (dest.parent.mkdir(parents=True,
                        exist_ok=True), dest.write_bytes(b"w")))  # fmt: skip
    monkeypatch.setattr(hunt, "listen", lambda light, start, end: np.zeros(0))
    monkeypatch.setattr(
        jobs, "hunt", lambda hunt_id: hunt.run(hunt_id, env.sessions, env.settings, client=kicked)
    )
    again = env.client.post(f"/api/kick/hunts/{found['id']}/retry").json()
    assert again["status"] == "done" and again["moments"]
    assert env.client.post(f"/api/kick/hunts/{found['id']}/retry").status_code == 409


def test_each_account_sees_its_own_streamers_and_hunts_and_the_primary_sees_all(env, kicked):
    _, riya = employee(env)
    mine = env.client.post("/api/kick/streamers", json={"name": "xqc"}).json()
    hers = riya.post("/api/kick/streamers", json={"name": "xqc"})  # her own list
    assert hers.status_code == 200, hers.text
    assert [s["id"] for s in riya.get("/api/kick/streamers").json()] == [hers.json()["id"]]
    assert riya.patch(f"/api/kick/streamers/{mine['id']}", json={"live": True}).status_code == 404
    found = riya.post("/api/kick/hunts", json={"link": f"kick.com/video/{UUID}", "count": 1})
    assert found.status_code == 200, found.text
    assert found.json()["preset"] in {p["name"] for p in riya.get("/api/presets").json()}
    seen = {h["id"] for h in env.client.get("/api/kick/hunts").json()}
    assert found.json()["id"] in seen  # the primary account sees everyone's
    assert riya.get("/api/kick/hunts").json()[0]["id"] == found.json()["id"]
