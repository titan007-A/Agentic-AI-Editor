import io
import json
import os
import shutil
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from agentic_editor import api, jobs, pipeline, style
from agentic_editor.api import create_app
from agentic_editor.auth import hash_password
from agentic_editor.backends import Backends
from agentic_editor.db import Event, Job, Output, Research, make_sessionmaker
from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset
from agentic_editor.settings import ROOT, Settings

PASSWORD = "correct horse"


@pytest.fixture
def env(tmp_path, monkeypatch):
    presets = tmp_path / "presets"
    shutil.copytree(ROOT / "presets", presets)
    settings = Settings(
        media_dir=tmp_path / "media",
        presets_dir=presets,
        database=tmp_path / "app.db",
        web_dir=tmp_path / "web",
        password_hash=hash_password(PASSWORD),
        secret_key="test-secret",
    )
    sessions = make_sessionmaker(settings.database)
    calls = []

    def fake_run(source, presets, settings, backends, notes, progress, run_id=None):
        calls.append(
            {
                "source": source,
                "presets": [p.name for p in presets],
                "labels": [p.label for p in presets],
                "notes": notes,
            }
        )
        progress("download", f"fetching {source}")
        progress("plan", "planning")
        outputs = []
        for preset in presets:
            path = settings.media_dir / f"{preset.name}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake video")
            edl = EDL(clips=[Clip(start=1, end=21)], title="A title")
            outputs.append(pipeline.Output(preset.name, path, edl, "fake-model"))
            progress("done", f"{preset.name} -> {path}")
        return pipeline.Result("Sample song", outputs)

    env = SimpleNamespace(calls=calls, settings=settings, run=fake_run, sessions=sessions)
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    monkeypatch.setattr(
        jobs, "process", lambda job_id: jobs.run_job(job_id, sessions, settings, env.run)
    )

    def fake_learn(source, settings, backends, name, progress):
        progress("read", "reading the text on 6 frames")
        if "broken" in source:
            raise ValueError("no picture in it")
        preset = Preset(
            name=name or "shorts-9x16", label="Talking short", min_duration=10, max_duration=30
        )
        return style.Draft(preset, ["Captions: none"])

    monkeypatch.setattr(
        jobs,
        "learn_style",
        lambda draft_id: jobs.run_learning(draft_id, sessions, settings, fake_learn),
    )
    with TestClient(create_app(settings, sessions, start_worker=False)) as client:
        env.client = client
        login(client)
        yield env


def start(client, sources=("https://x.test/v",), presets=("square-1x1",)):
    response = client.post("/api/jobs", json={"sources": list(sources), "presets": list(presets)})
    assert response.status_code == 200, response.text
    return response.json()


def login(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_everything_needs_a_login(env):
    env.client.post("/api/logout")
    assert env.client.get("/api/jobs").status_code == 401
    assert env.client.get("/api/presets").status_code == 401


def test_wrong_password_is_rejected(env):
    env.client.post("/api/logout")
    assert env.client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert env.client.get("/api/me").status_code == 401


def test_wrong_passwords_one_after_another_have_to_wait(env, monkeypatch):
    monkeypatch.setattr(api, "FAILED_LOGIN_DELAY", 0)
    now = [1000.0]
    env.client.app.state.logins.clock = lambda: now[0]
    env.client.post("/api/logout")
    for _ in range(5):
        assert env.client.post("/api/login", json={"password": "nope"}).status_code == 401
    # Even the right password waits, or a guesser could tell when it found it.
    refused = env.client.post("/api/login", json={"password": PASSWORD})
    assert refused.status_code == 429 and refused.headers["retry-after"] == "30"
    now[0] += 31
    assert env.client.post("/api/login", json={"password": "nope"}).status_code == 401
    now[0] += 31
    assert env.client.post("/api/login", json={"password": PASSWORD}).status_code == 429
    now[0] += 30  # twice as long after another wrong one
    assert env.client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_usage_notice_is_accepted_once(env):
    assert env.client.get("/api/me").json()["notice_accepted"] is False
    env.client.post("/api/notice")
    assert env.client.get("/api/me").json()["notice_accepted"] is True


def test_batch_creates_one_job_per_link_and_records_outputs(env):
    links = ["https://example.com/a", "https://example.com/b", "https://example.com/a"]
    created = start(env.client, links, ["vocal-music-9x16"])
    assert len(created) == 2
    assert [c["source"] for c in env.calls] == ["https://example.com/a", "https://example.com/b"]

    job = env.client.get(f"/api/jobs/{created[0]['id']}").json()
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["title"] == "Sample song"
    assert [o["preset"] for o in job["outputs"]] == ["vocal-music-9x16"]
    assert job["outputs"][0]["planner"] == "fake-model"


def test_progress_streams_as_server_sent_events(env):
    job_id = start(env.client)[0]["id"]
    with env.client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        body = "".join(response.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [e.get("stage") for e in events[:-1]] == ["download", "plan", "done", "finished"]
    assert events[-1] == {"status": "done"}


def test_a_job_that_waited_for_the_gpu_is_back_at_its_stage(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        db.add(Job(id="j1", source="https://x.test/v", presets=["square-1x1"]))
        db.commit()
    track = jobs.Tracker(Session, "j1", 1)

    def stage():
        with Session() as db:
            return db.get(Job, "j1").stage

    track("transcribe", "transcribing")
    track("wait", "waiting for the GPU to transcribe")
    assert stage() == "wait"
    track("resume", "got the GPU after 42s")
    assert stage() == "transcribe"
    with Session() as db:
        logged = db.query(Event).filter_by(job_id="j1").order_by(Event.id).all()
    assert [(e.stage, e.message) for e in logged][-1] == ("transcribe", "got the GPU after 42s")


def test_rejects_unknown_presets_and_non_links(env):
    jobs_url = "/api/jobs"
    unknown = {"sources": ["https://x.test"], "presets": ["nope"]}
    assert env.client.post(jobs_url, json=unknown).status_code == 422
    local_path = {"sources": ["/etc/passwd"], "presets": ["square-1x1"]}
    assert env.client.post(jobs_url, json=local_path).status_code == 422


def test_a_batch_with_one_bad_source_starts_nothing(env):
    batch = {"sources": ["https://x.test/a", "not a link"], "presets": ["square-1x1"]}
    assert env.client.post("/api/jobs", json=batch).status_code == 422
    assert env.client.get("/api/jobs").json() == [] and env.calls == []


def test_failed_run_marks_the_job_failed(env):
    def broken(*args, **kwargs):
        raise RuntimeError("download blocked")

    env.run = broken
    job = env.client.get(f"/api/jobs/{start(env.client)[0]['id']}").json()
    assert job["status"] == "failed"
    assert "download blocked" in job["error"]


def test_regenerate_reuses_the_source_with_new_notes(env):
    first = start(env.client)[0]
    again = env.client.post(
        f"/api/jobs/{first['id']}/regenerate", json={"preset": "shorts-9x16", "notes": "shorter"}
    ).json()
    assert again["parent_id"] == first["id"]
    assert {k: v for k, v in env.calls[-1].items() if k != "labels"} == {
        "source": "https://x.test/v",
        "presets": ["shorts-9x16"],
        "notes": "shorter",
    }


def test_regenerating_keeps_the_earlier_instruction(env):
    response = env.client.post(
        "/api/jobs",
        json={"sources": ["https://x.test/v"], "presets": ["square-1x1"], "notes": "warm look"},
    )
    first = response.json()[0]
    env.client.post(
        f"/api/jobs/{first['id']}/regenerate", json={"preset": "square-1x1", "notes": "start later"}
    )
    assert env.calls[-1]["notes"] == "warm look\nstart later"


def test_preset_edits_are_validated_before_saving(env):
    presets = env.client.get("/api/presets").json()
    preset = next(p for p in presets if p["name"] == "square-1x1")
    preset["max_duration"] = 5
    assert env.client.put("/api/presets/square-1x1", json=preset).status_code == 422

    preset["max_duration"] = 45
    assert env.client.put("/api/presets/square-1x1", json=preset).status_code == 200
    assert "max_duration: 45" in (env.settings.presets_dir / "square-1x1.yaml").read_text()


def test_lists_the_bundled_fonts(env):
    assert "OpenSans-ExtraBold" in env.client.get("/api/fonts").json()


def test_uploads_are_stored_by_content_hash_under_their_own_name(env):
    def upload(name, data):
        return env.client.post("/api/uploads", files={"file": (name, data)})

    first = upload("clip.mp4", b"same bytes").json()
    second = upload("other.mp4", b"same bytes").json()
    assert first["source"] != second["source"]
    assert first["source"].split("/")[0] == second["source"].split("/")[0]
    assert first["source"].endswith("/clip.mp4")
    assert upload("notes.txt", b"x").status_code == 400


def test_an_upload_keeps_the_name_it_was_sent_with_as_its_title(env):
    from agentic_editor.pipeline import resolve_source

    sent = env.client.post(
        "/api/uploads", files={"file": ("Felicità — Al Bano & Romina.mp4", b"video bytes")}
    ).json()
    assert resolve_source(sent["source"], env.settings).title == "Felicità — Al Bano & Romina"


def test_upload_references_cannot_escape_the_upload_folder(env):
    for ref in ["upload:../../etc/passwd", "upload:0123456789abcdef0123/../x.mp4"]:
        body = {"sources": [ref], "presets": ["square-1x1"]}
        assert env.client.post("/api/jobs", json=body).status_code == 422


def test_rendered_video_downloads_with_a_readable_name(env):
    job_id = start(env.client)[0]["id"]
    output_id = env.client.get(f"/api/jobs/{job_id}").json()["outputs"][0]["id"]
    response = env.client.get(f"/api/outputs/{output_id}/video?download=true")
    assert response.content == b"fake video"
    assert 'filename="Sample-song-square-1x1.mp4"' in response.headers["content-disposition"]


def test_cards_get_a_small_thumbnail_and_downloads_the_full_one(env):
    from PIL import Image

    job_id = start(env.client)[0]["id"]
    output_id = env.client.get(f"/api/jobs/{job_id}").json()["outputs"][0]["id"]
    full = env.settings.media_dir / "square-1x1.jpg"
    Image.new("RGB", (1080, 1920), "tan").save(full)
    with env.sessions() as db:
        db.get(Output, output_id).thumbnail = str(full)
        db.commit()
    small = env.client.get(f"/api/outputs/{output_id}/thumbnail?width=480")
    assert Image.open(io.BytesIO(small.content)).size == (480, 853)
    assert "max-age" in small.headers["cache-control"]
    whole = env.client.get(f"/api/outputs/{output_id}/thumbnail")
    assert Image.open(io.BytesIO(whole.content)).size == (1080, 1920)
    assert env.client.get(f"/api/outputs/{output_id}/thumbnail?width=5000").status_code == 422


def test_subtitles_download_next_to_the_video(env):
    job_id = start(env.client)[0]["id"]
    output = env.client.get(f"/api/jobs/{job_id}").json()["outputs"][0]
    assert output["has_subtitles"] is False
    assert env.client.get(f"/api/outputs/{output['id']}/subtitles").status_code == 404

    (env.settings.media_dir / "square-1x1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    output = env.client.get(f"/api/jobs/{job_id}").json()["outputs"][0]
    assert output["has_subtitles"] is True
    response = env.client.get(f"/api/outputs/{output['id']}/subtitles")
    assert response.text.endswith("hi\n")
    assert 'filename="Sample-song-square-1x1.srt"' in response.headers["content-disposition"]


def add_folder_feed(env, folder, **fields):
    body = {"name": "Drafts", "kind": "folder", "location": str(folder), "presets": ["square-1x1"]}
    return env.client.post("/api/feeds", json={**body, **fields})


def test_a_folder_feed_edits_what_it_holds_into_the_ready_pile(env, tmp_path):
    folder = tmp_path / "drafts"
    folder.mkdir()
    for name in ("one.mp4", "two.mov"):
        (folder / name).write_bytes(b"x")
        os.utime(folder / name, (1000, 1000))
    response = add_folder_feed(env, folder, include_existing=True, notes="warm look")
    assert response.status_code == 200, response.text
    feed = response.json()
    assert feed["counts"] == {"done": 2} and feed["last_result"] == "started 2 new video(s)"
    assert {c["source"] for c in env.calls} == {str(folder / "one.mp4"), str(folder / "two.mov")}
    assert all(c["notes"] == "warm look" for c in env.calls)

    ready = env.client.get("/api/piles/ready").json()
    assert len(ready) == 2 and all(len(j["outputs"]) == 1 for j in ready)
    # Checking again starts nothing: no video is edited twice.
    again = env.client.post(f"/api/feeds/{feed['id']}/check").json()
    assert again["last_result"] == "started 0 new video(s)" and len(env.calls) == 2


def test_a_feed_moved_somewhere_else_notes_what_is_already_there(env, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    for folder in (first, second):
        folder.mkdir()
        (folder / "old.mp4").write_bytes(b"x")
        os.utime(folder / "old.mp4", (1000, 1000))
    feed = add_folder_feed(env, first).json()
    body = {"name": "Drafts", "kind": "folder", "location": str(second), "presets": ["square-1x1"]}
    assert env.client.put(f"/api/feeds/{feed['id']}", json=body).status_code == 200
    again = env.client.post(f"/api/feeds/{feed['id']}/check").json()
    assert again["last_result"].startswith("1 video(s) already there") and env.calls == []


def test_an_instagram_account_feed_is_looked_through_without_holding_the_page(env, monkeypatch):
    from agentic_editor import autopilot

    reels = [autopilot.Entry("Instagram-OLD", "Adele old", "https://www.instagram.com/reel/OLD/")]
    gate = threading.Event()

    def instagram_entries(handle):
        assert handle == "adele___official"
        gate.wait(5)  # the search engines take their time: the page has its answer by then
        return list(reels)

    monkeypatch.setattr(autopilot, "instagram_entries", instagram_entries)
    body = {
        "name": "adele",
        "kind": "link",
        "location": "https://www.instagram.com/adele___official?cplk=NWl5aXJoMmxpMDV1",
        "presets": ["square-1x1"],
    }
    response = env.client.post("/api/feeds", json=body)
    assert response.status_code == 200, response.text
    feed = response.json()
    assert feed["last_result"] == "looking for the account's reels; this takes a minute"

    def result():
        for _ in range(100):
            now = env.client.get("/api/feeds").json()[0]["last_result"]
            if not now.startswith("looking"):
                return now
            time.sleep(0.05)
        raise AssertionError("the check never finished")

    gate.set()
    assert result() == "1 video(s) already there; only new ones will be edited"
    reels.insert(
        0, autopilot.Entry("Instagram-NEW", "Adele new", "https://www.instagram.com/reel/NEW/")
    )
    env.client.post(f"/api/feeds/{feed['id']}/check")
    assert result() == "started 1 new video(s)"
    for _ in range(100):  # the job starts once the check has noted it
        if env.calls:
            break
        time.sleep(0.05)
    assert [c["source"] for c in env.calls] == ["https://www.instagram.com/reel/NEW/"]


def test_outputs_with_doubts_need_a_look_until_the_editor_says_otherwise(env):
    def doubtful(source, presets, settings, backends, notes, progress, run_id):
        result = env_run(source, presets, settings, backends, notes, progress)
        result.outputs[0].confidence, result.outputs[0].doubts = 0.8, ["8s without voice at 18s"]
        return result

    env_run = env.run
    env.run = doubtful
    start(env.client)
    assert env.client.get("/api/piles").json() == {"ready": 0, "look": 1}
    (job,) = env.client.get("/api/piles/look").json()
    output_id = job["outputs"][0]["id"]

    env.client.post(f"/api/outputs/{output_id}/verdict", json={"verdict": "approved"})
    assert env.client.get("/api/piles").json() == {"ready": 1, "look": 0}
    env.client.post(f"/api/outputs/{output_id}/verdict", json={"verdict": "posted"})
    assert env.client.get("/api/piles").json() == {"ready": 0, "look": 0}


def add_outputs(env, count: int) -> None:
    with env.sessions() as db:
        for i in range(count):
            job = Job(source=f"https://x.test/{i}", presets=["square-1x1"], status="done")
            db.add(job)
            db.flush()
            doubts = ["8s without voice"] if i % 2 else []
            db.add(Output(job_id=job.id, preset="square-1x1", path=f"/x/{i}.mp4", duration=20,
                          planner="fake-model", edl={}, doubts=doubts))  # fmt: skip
        db.commit()


def queries(env, url: str) -> int:
    engine, count = env.sessions.kw["bind"], [0]

    def counted(*args):
        count[0] += 1

    event.listen(engine, "before_cursor_execute", counted)
    assert env.client.get(url).status_code == 200
    event.remove(engine, "before_cursor_execute", counted)
    return count[0]


def test_lists_take_the_same_few_queries_however_long_they_are(env):
    urls = ["/api/jobs", "/api/piles", "/api/piles/ready", "/api/piles/look", "/api/feeds"]
    add_outputs(env, 3)
    few = [queries(env, url) for url in urls]
    add_outputs(env, 30)
    assert [queries(env, url) for url in urls] == few


def test_the_piles_sort_outputs_as_each_output_says(env):
    with env.sessions() as db:
        job = Job(source="https://x.test/v", presets=["square-1x1"], status="done")
        db.add(job)
        db.flush()
        for verdict in (None, "approved", "posted", "dismissed"):
            for doubts in ([], ["a doubt"]):
                for issues in ([], ["an issue"]):
                    db.add(Output(job_id=job.id, preset="square-1x1", path="/x.mp4", duration=1,
                                  planner="m", edl={}, verdict=verdict, doubts=doubts,
                                  issues=issues))  # fmt: skip
        db.commit()
        outputs = db.query(Output).all()
        expected = {pile: {o.id for o in outputs if o.pile == pile} for pile in ("ready", "look")}
    for pile, ids in expected.items():
        listed = env.client.get(f"/api/piles/{pile}").json()
        assert {j["outputs"][0]["id"] for j in listed} == ids
    counts = env.client.get("/api/piles").json()
    assert counts == {pile: len(ids) for pile, ids in expected.items()}


def test_feeds_are_checked_before_they_are_saved(env, tmp_path):
    assert add_folder_feed(env, tmp_path / "missing").status_code == 422
    link = {"name": "x", "kind": "link", "location": "not a link", "presets": ["square-1x1"]}
    assert env.client.post("/api/feeds", json=link).status_code == 422
    assert add_folder_feed(env, tmp_path, active_from=23).status_code == 422
    assert add_folder_feed(env, tmp_path, active_from=7, active_to=7).status_code == 422
    assert add_folder_feed(env, tmp_path, presets=["nope"]).status_code == 422


def learned(client, **body) -> dict:
    response = client.post("/api/styles", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    return client.get(f"/api/styles/{response.json()['id']}").json()


def test_a_style_is_learned_into_a_draft_preset_that_is_not_saved(env):
    draft = learned(env.client, source="https://x.test/reel")
    assert draft["status"] == "done" and draft["notes"] == ["Captions: none"]
    # Named after its label, without taking the name of a preset that exists.
    assert draft["preset"]["name"] == "shorts-9x16-2"
    assert "shorts-9x16-2" not in {p["name"] for p in env.client.get("/api/presets").json()}
    assert learned(env.client, source="https://x.test/r", name="mine")["preset"]["name"] == "mine"


def test_a_reel_that_cannot_be_learned_from_says_why(env):
    draft = learned(env.client, source="https://x.test/broken")
    assert draft["status"] == "failed" and "no picture in it" in draft["error"]
    assert env.client.post("/api/styles", json={"source": "/home/x.mp4"}).status_code == 422
    bad_name = {"source": "https://x.test/r", "name": "Not A Name"}
    assert env.client.post("/api/styles", json=bad_name).status_code == 422
    assert env.client.get("/api/styles/nope").status_code == 404


def test_a_job_with_some_versions_failed_is_done_and_says_which(env):
    made = env.run

    def partly(*args, run_id=None):
        result = made(*args)
        result.failures.append("vocal-music-9x16: RuntimeError: encoder crashed")
        return result

    env.run = partly
    created = start(env.client, presets=["square-1x1"])
    job = env.client.get(f"/api/jobs/{created[0]['id']}").json()
    assert job["status"] == "done" and len(job["outputs"]) == 1
    assert job["error"] == "vocal-music-9x16: RuntimeError: encoder crashed"


def employee(env, name="riya", password="employee-pass-1"):
    """An employee made by the admin, and a client signed in as them."""
    made = env.client.post("/api/admin/users", json={"username": name, "password": password})
    assert made.status_code == 200, made.text
    client = TestClient(env.client.app)
    login = client.post("/api/login", json={"username": name, "password": password})
    assert login.status_code == 200, login.text
    return made.json(), client


def test_the_admin_makes_employees_who_start_with_their_own_copy_of_the_presets(env):
    account, riya = employee(env)
    assert account["role"] == "member" and account["presets"] > 0
    assert riya.get("/api/me").json()["role"] == "member"
    mine = {p["name"]: p for p in riya.get("/api/presets").json()}
    preset = mine["square-1x1"] | {"label": "Riya's square"}
    assert riya.put("/api/presets/square-1x1", json=preset).status_code == 200
    # The admin's own preset is untouched; theirs is changed, and the admin can see it.
    admins = {p["name"]: p for p in env.client.get("/api/presets").json()}
    assert admins["square-1x1"]["label"] != "Riya's square"
    theirs = env.client.get(f"/api/presets?owner={account['id']}").json()
    assert {p["name"]: p for p in theirs}["square-1x1"]["label"] == "Riya's square"


def test_the_admin_edits_and_deletes_an_employees_presets(env):
    account, riya = employee(env)
    preset = env.client.get(f"/api/presets?owner={account['id']}").json()[0]
    edited = preset | {"label": "Set by the admin"}
    url = f"/api/presets/{preset['name']}?owner={account['id']}"
    assert env.client.put(url, json=edited).status_code == 200
    labels = {p["name"]: p["label"] for p in riya.get("/api/presets").json()}
    assert labels[preset["name"]] == "Set by the admin"
    assert env.client.delete(url).status_code == 200
    assert preset["name"] not in {p["name"] for p in riya.get("/api/presets").json()}


def test_an_employee_cant_reach_anyone_elses_presets_or_the_people_page(env):
    first, _ = employee(env, "riya")
    _, aman = employee(env, "aman")
    me = env.client.get("/api/me").json()
    for owner in (first["id"], me["id"]):
        assert aman.get(f"/api/presets?owner={owner}").status_code == 403
    assert aman.get("/api/admin/users").status_code == 403
    made = aman.post("/api/admin/users", json={"username": "x-man", "password": "12345678"})
    assert made.status_code == 403


def test_an_employee_sees_only_their_own_jobs_and_the_admin_sees_who_made_each(env):
    _, riya = employee(env)
    admins_job = start(env.client)[0]["id"]
    preset = {p["name"]: p for p in riya.get("/api/presets").json()}["square-1x1"]
    riya.put("/api/presets/square-1x1", json=preset | {"label": "Riya's square"})
    riyas_job = start(riya)[0]["id"]
    assert env.calls[-1]["labels"] == ["Riya's square"]  # run with her own preset
    assert [j["id"] for j in riya.get("/api/jobs").json()] == [riyas_job]
    assert riya.get(f"/api/jobs/{admins_job}").status_code == 404
    output = env.client.get(f"/api/jobs/{admins_job}").json()["outputs"][0]["id"]
    assert riya.get(f"/api/outputs/{output}/video").status_code == 404
    seen = {j["id"]: j["owner"] for j in env.client.get("/api/jobs").json()}
    assert seen[riyas_job] == "riya" and seen[admins_job] == "admin"
    assert riya.get("/api/jobs").json()[0]["owner"] is None


def test_a_new_password_signs_the_employee_out_everywhere(env):
    account, riya = employee(env)
    reset = env.client.post(
        f"/api/admin/users/{account['id']}/password", json={"password": "brand-new-pass"}
    )
    assert reset.status_code == 200
    assert riya.get("/api/me").status_code == 401
    again = riya.post("/api/login", json={"username": "riya", "password": "brand-new-pass"})
    assert again.status_code == 200


def test_removing_an_employee_keeps_their_jobs_for_the_admin_unless_asked(env):
    account, riya = employee(env)
    job = start(riya)[0]["id"]
    assert env.client.delete(f"/api/admin/users/{account['id']}").status_code == 200
    assert riya.get("/api/me").status_code == 401
    assert env.client.get(f"/api/jobs/{job}").json()["owner"] == "admin"
    other, aman = employee(env, "aman")
    gone = start(aman)[0]["id"]
    assert env.client.delete(f"/api/admin/users/{other['id']}?delete_work=true").status_code == 200
    assert env.client.get(f"/api/jobs/{gone}").status_code == 404


def test_the_web_makes_only_employees_and_never_removes_an_admin(env):
    me = env.client.get("/api/me").json()
    assert env.client.delete(f"/api/admin/users/{me['id']}").status_code == 422
    made = env.client.post("/api/admin/users", json={"username": "boss", "password": "12345678"})
    assert made.json()["role"] == "member"
    short = env.client.post("/api/admin/users", json={"username": "kid", "password": "short"})
    assert short.status_code == 422


def test_only_the_admin_watches_a_folder_on_the_computer(env, tmp_path):
    _, riya = employee(env)
    feed = {"name": "mine", "kind": "folder", "location": str(tmp_path), "presets": ["square-1x1"]}
    assert riya.post("/api/feeds", json=feed).status_code == 403


def test_a_job_or_feed_takes_one_preset(env):
    body = {"sources": ["https://x.test/v"], "presets": ["square-1x1", "shorts-9x16"]}
    assert env.client.post("/api/jobs", json=body).status_code == 422
    feed = {
        "name": "two",
        "kind": "link",
        "location": "https://x.test/c",
        "presets": ["square-1x1", "shorts-9x16"],
    }
    assert env.client.post("/api/feeds", json=feed).status_code == 422


def test_the_admin_sends_a_preset_to_every_employee(env):
    _, riya = employee(env, "riya")
    _, aman = employee(env, "aman")
    riyas = {p["name"]: p for p in riya.get("/api/presets").json()}["square-1x1"]
    riya.put("/api/presets/square-1x1", json=riyas | {"label": "Riya's own"})
    mine = {p["name"]: p for p in env.client.get("/api/presets").json()}["square-1x1"]
    env.client.put("/api/presets/square-1x1", json=mine | {"label": "New house style"})
    sent = env.client.post("/api/presets/square-1x1/share").json()
    assert sorted(sent["sent_to"]) == ["aman", "riya"]
    for client in (riya, aman):
        labels = {p["name"]: p["label"] for p in client.get("/api/presets").json()}
        assert labels["square-1x1"] == "New house style"
    assert riya.post("/api/presets/square-1x1/share").status_code == 403
    assert env.client.post("/api/presets/no-such/share").status_code == 404


class CaptionRouter:
    """A ModelRouter whose planner is never asked: the caption steps are faked."""

    def planner(self):
        from contextlib import nullcontext

        return nullcontext(SimpleNamespace(context=None))


def fake_captions(monkeypatch, env, fail=False):
    from agentic_editor import caption

    def identify(llm, title, description, headline):
        if fail:
            raise caption.CaptionError("the video doesn't say which song it is")
        return "Felicità", "Al Bano & Romina Power"

    monkeypatch.setattr(caption, "identify", identify)
    monkeypatch.setattr(caption, "gather", lambda *args: caption.Sources())
    monkeypatch.setattr(
        caption,
        "write",
        lambda *args, **kwargs: caption.Written(
            "Un éxito de 1982 🎵", [{"title": "Felicità", "url": "u"}], []
        ),
    )
    monkeypatch.setattr(
        jobs,
        "write_caption",
        lambda output_id: jobs.run_caption(output_id, env.sessions, env.settings, CaptionRouter()),
    )


def test_a_caption_is_written_when_asked_for_from_the_video(env, monkeypatch):
    fake_captions(monkeypatch, env)
    (job,) = start(env.client)
    output = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]
    assert output["caption_status"] is None  # square-1x1 doesn't write one by itself
    response = env.client.post(f"/api/outputs/{output['id']}/caption")
    assert response.status_code == 200
    written = env.client.get(f"/api/outputs/{output['id']}").json()["outputs"][0]
    assert written["caption_status"] == "done" and written["caption"] == "Un éxito de 1982 🎵"
    assert written["caption_sources"] == [{"title": "Felicità", "url": "u"}]
    assert written["caption_notes"] == []


def test_a_preset_that_wants_captions_gets_one_after_every_render(env, monkeypatch):
    fake_captions(monkeypatch, env)
    (job,) = start(env.client, presets=("song-stories-es-9x16",))
    output = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]
    assert output["caption_status"] == "done"


def test_a_reel_that_names_its_song_writes_the_caption_about_it(env, monkeypatch):
    from agentic_editor import caption

    fake_captions(monkeypatch, env, fail=True)  # the model would have to guess: it can't
    asked = []
    monkeypatch.setattr(
        caption,
        "gather",
        lambda song, artist, *args: asked.append((song, artist)) or caption.Sources(),
    )
    run = env.run

    def named_run(*args, **kwargs):
        result = run(*args, **kwargs)
        for out in result.outputs:
            out.song = ("Felicità", "Al Bano & Romina Power")
        return result

    env.run = named_run
    (job,) = start(env.client, presets=("song-stories-es-9x16",))
    output = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]
    assert output["caption_status"] == "done"
    assert (output["caption_song"], output["caption_artist"]) == asked[0]
    assert asked == [("Felicità", "Al Bano & Romina Power")]


def test_a_caption_that_cant_be_written_says_why(env, monkeypatch):
    fake_captions(monkeypatch, env, fail=True)
    (job,) = start(env.client)
    output_id = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]["id"]
    env.client.post(f"/api/outputs/{output_id}/caption")
    output = env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]
    assert output["caption_status"] == "failed"
    assert output["caption_notes"] == ["the video doesn't say which song it is"]


def test_a_song_named_by_hand_is_the_one_the_caption_is_about(env, monkeypatch):
    from agentic_editor import caption

    fake_captions(monkeypatch, env, fail=True)
    asked = []
    monkeypatch.setattr(
        caption,
        "gather",
        lambda song, artist, *args: asked.append((song, artist)) or caption.Sources(),
    )
    (job,) = start(env.client)
    output_id = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]["id"]
    env.client.post(f"/api/outputs/{output_id}/caption")
    assert (
        env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]["caption_status"]
        == "failed"
    )

    named = {"song": "“Piano Man”", "artist": "Billy Joel"}
    assert env.client.post(f"/api/outputs/{output_id}/caption", json=named).status_code == 200
    output = env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]
    assert output["caption_status"] == "done" and asked == [("Piano Man", "Billy Joel")]
    assert (output["caption_song"], output["caption_artist"]) == ("Piano Man", "Billy Joel")
    # Written again, it stays about that song.
    env.client.post(f"/api/outputs/{output_id}/caption")
    assert asked[-1] == ("Piano Man", "Billy Joel")


def test_a_song_named_without_its_artist_is_refused(env, monkeypatch):
    fake_captions(monkeypatch, env)
    (job,) = start(env.client)
    output_id = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]["id"]
    response = env.client.post(f"/api/outputs/{output_id}/caption", json={"song": "Stronger"})
    assert response.status_code == 422


def test_the_song_the_model_found_is_shown(env, monkeypatch):
    fake_captions(monkeypatch, env)
    (job,) = start(env.client)
    output_id = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]["id"]
    env.client.post(f"/api/outputs/{output_id}/caption")
    output = env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]
    assert (output["caption_song"], output["caption_artist"]) == (
        "Felicità",
        "Al Bano & Romina Power",
    )


def test_a_topic_instead_of_a_song(env, monkeypatch):
    from agentic_editor import caption

    fake_captions(monkeypatch, env, fail=True)
    asked = []
    monkeypatch.setattr(
        caption, "gather_topic", lambda topic, *args: asked.append(topic) or caption.Sources()
    )
    (job,) = start(env.client)
    output_id = env.client.get(f"/api/jobs/{job['id']}").json()["outputs"][0]["id"]
    both = {"song": "Piano Man", "artist": "Billy Joel", "topic": "Burj Khalifa"}
    assert env.client.post(f"/api/outputs/{output_id}/caption", json=both).status_code == 422
    env.client.post(f"/api/outputs/{output_id}/caption", json={"topic": "Burj Khalifa"})
    output = env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]
    assert output["caption_status"] == "done" and asked == ["Burj Khalifa"]
    assert output["caption_topic"] == "Burj Khalifa" and output["caption_song"] == ""
    # Naming a song again turns it back into a song's caption.
    song = {"song": "Piano Man", "artist": "Billy Joel"}
    env.client.post(f"/api/outputs/{output_id}/caption", json=song)
    output = env.client.get(f"/api/outputs/{output_id}").json()["outputs"][0]
    assert output["caption_topic"] == "" and output["caption_song"] == "Piano Man"


def test_a_job_that_couldnt_name_the_song_says_which_presets_need_it(env):
    made = env.run

    def unnamed(*args, **kwargs):
        raise RuntimeError(
            "voicehq-9x16: UnnamedSong: 'Video by x' doesn't say the song and artist; "
            "add lines 'song: ...' and 'artist: ...' to the notes"
        )

    env.run = unnamed
    (job,) = start(env.client, presets=("voicehq-9x16",))
    failed = env.client.get(f"/api/jobs/{job['id']}").json()
    assert failed["status"] == "failed" and failed["unnamed"] == ["voicehq-9x16"]

    # The job page sends the name as notes lines; the rest of the work goes on.
    env.run = made
    named = {"preset": "voicehq-9x16", "notes": "song: Piano Man\nartist: Billy Joel"}
    again = env.client.post(f"/api/jobs/{job['id']}/regenerate", json=named).json()
    done = env.client.get(f"/api/jobs/{again['id']}").json()
    assert done["status"] == "done" and done["unnamed"] == []
    assert env.calls[-1]["notes"] == "song: Piano Man\nartist: Billy Joel"


class ResearchRouter:
    def planner(self):
        from contextlib import nullcontext

        from tests.test_research import AccountPlanner

        return nullcontext(AccountPlanner())


# What the fake search engines find, whatever is asked: (account, caption, likes).
REELS = {
    "ADAMS1": ("attic", "Bryan Adams - Summer of 69 live", 900),
    "ADAMS2": ("rockblog", "Bryan Adams, Heaven", 300),
    "OTHER1": ("cats", "cat videos", 90_000),
    "OWN001": ("voiceshq", "Bryan Adams vocals only", 5_000),
}


def fake_research(monkeypatch, env, broken=False):
    """Searches that always find the same four reels: two about Bryan Adams, cats, and
    one of @voiceshq's own."""
    from tests.test_research import hit

    def find(query, count):
        if broken:
            raise TimeoutError
        return [hit(code) for code in REELS]

    def info(url):
        channel, caption, likes = REELS[url.rstrip("/").rsplit("/", 1)[1]]
        return {"uploader": channel.title(), "channel": channel, "description": caption,
                "like_count": likes}  # fmt: skip

    monkeypatch.setattr(jobs.research, "PAUSE", 0)
    monkeypatch.setattr(
        jobs,
        "find_reels",
        lambda research_id: jobs.run_research(
            research_id, env.sessions, env.settings, ResearchRouter(), find, info
        ),
    )
    monkeypatch.setattr(
        jobs,
        "read_account",
        lambda account_id: jobs.run_account_learning(
            account_id, env.sessions, env.settings, ResearchRouter(), find, info
        ),
    )
    monkeypatch.setattr(
        jobs,
        "ask_questions",
        lambda research_id: jobs.run_questions(
            research_id, env.sessions, env.settings, ResearchRouter(), find, info
        ),
    )
    monkeypatch.setattr(
        jobs,
        "answer_chat",
        lambda message_id: jobs.run_chat(message_id, env.sessions, env.settings, ResearchRouter()),
    )


def test_the_researcher_asks_first_and_the_answers_become_part_of_the_research(env, monkeypatch):
    fake_research(monkeypatch, env)
    started = env.client.post("/api/research", json={"description": "Bryan Adams"}).json()
    asking = env.client.get(f"/api/research/{started['id']}").json()
    assert asking["status"] == "asking" and asking["message"] == "a few questions first"
    assert asking["questions"] == [
        {"question": "Live or studio?", "choices": ["Live", "Studio"]},
        {"question": "Which era?", "choices": ["80s", "90s"]},
    ]
    answers = {
        "answers": [
            {"question": "Live or studio?", "answer": "Live"},
            {"question": "Which era?", "answer": ""},  # left blank: not kept
            {"question": "Not asked", "answer": "x"},  # not one of its questions
        ]
    }
    answered = env.client.post(f"/api/research/{started['id']}/answers", json=answers)
    assert answered.status_code == 200, answered.text
    found = env.client.get(f"/api/research/{started['id']}").json()
    assert found["status"] == "done" and found["answers"] == [
        {"question": "Live or studio?", "answer": "Live"}
    ]
    again = env.client.post(f"/api/research/{started['id']}/answers", json=answers)
    assert again.status_code == 409  # answered already

    # Skipped: no answers, searched all the same.
    other = env.client.post("/api/research", json={"description": "Queen"}).json()
    skipped = env.client.post(f"/api/research/{other['id']}/answers", json={}).json()
    assert skipped["answers"] == []
    assert env.client.get(f"/api/research/{other['id']}").json()["status"] == "done"


def test_the_chat_answers_from_the_reasoning_or_starts_a_follow_up(env, monkeypatch):
    fake_research(monkeypatch, env)
    body = {"description": "Bryan Adams live", "skip_questions": True}
    found = env.client.post("/api/research", json=body).json()
    asked = env.client.post(f"/api/research/{found['id']}/chat", json={"text": "why #1?"})
    assert asked.status_code == 200, asked.text
    chat = env.client.get(f"/api/research/{found['id']}").json()["messages"]
    assert [(m["role"], m["text"]) for m in chat] == [
        ("user", "why #1?"),
        ("researcher", "#1 fits best: fits."),
    ]
    assert chat[0]["answered"] and chat[1]["follow_up_id"] is None

    env.client.post(f"/api/research/{found['id']}/chat", json={"text": "older concerts"})
    chat = env.client.get(f"/api/research/{found['id']}").json()["messages"]
    assert chat[-1]["text"] == "Looking for older Bryan Adams concerts."
    follow_up = env.client.get(f"/api/research/{chat[-1]['follow_up_id']}").json()
    assert follow_up["parent_id"] == found["id"]
    assert follow_up["description"] == "Bryan Adams live concerts from the 1980s"
    # It never shows the reels the first one did.
    assert follow_up["status"] == "done" and follow_up["items"] == []
    assert follow_up["left_out"]["shown before"] == 3

    asking = env.client.post("/api/research", json={"description": "Queen"}).json()
    early = env.client.post(f"/api/research/{asking['id']}/chat", json={"text": "hi"})
    assert early.status_code == 409  # chat opens once it is done


def test_a_research_ranks_reels_that_fit_first_and_never_shows_one_twice(env, monkeypatch):
    fake_research(monkeypatch, env)
    started = env.client.post(
        "/api/research", json={"description": "Bryan Adams live", "skip_questions": True}
    )
    assert started.status_code == 200, started.text
    found = env.client.get(f"/api/research/{started.json()['id']}").json()
    # The cat video doesn't fit: left out, not shown at the bottom.
    assert found["status"] == "done"
    assert found["message"] == "found 3 reels; left out 1 that didn't fit"
    assert [i["code"] for i in found["items"]] == ["OWN001", "ADAMS1", "ADAMS2"]
    first = found["items"][1]
    assert first["url"] == "https://www.instagram.com/reel/ADAMS1/"
    assert first["relevance"] == 0.9 and first["likes"] == 900 and not first["used"]
    assert (first["account"], first["handle"]) == ("Attic", "attic")
    assert first["song"] == "Bryan Adams - Summer of 69 live"  # as the fake model names it
    assert found["queries"][0] == "Bryan Adams live" and found["considered"] == 4
    assert found["brief"] == "Bryan Adams singing live"

    # Edited from the Researcher: marked used there. The next research brings only new
    # reels: the edited one and the ones shown before are left out.
    start(env.client, sources=(first["url"],))
    again = env.client.get(f"/api/research/{found['id']}").json()
    assert again["items"][1]["used"]
    second = env.client.post(
        "/api/research", json={"description": "Bryan Adams", "skip_questions": True}
    ).json()
    second = env.client.get(f"/api/research/{second['id']}").json()
    assert second["items"] == [] and second["used"] == 1
    assert second["message"] == "found 0 reels; left out 1 that didn't fit and 2 shown before"
    assert second["left_out"] == {"edited": 1, "shown before": 2, "didn't fit": 1}

    listed = env.client.get("/api/research").json()
    assert [r["id"] for r in listed] == [second["id"], found["id"]]
    assert listed[1]["found"] == 3 and listed[1]["items"] == []
    assert env.client.delete(f"/api/research/{found['id']}").status_code == 200
    assert env.client.get(f"/api/research/{found['id']}").status_code == 404


def test_an_instagram_account_is_learned_and_researched_for_with_its_presets(env, monkeypatch):
    fake_research(monkeypatch, env)
    bad = env.client.post("/api/research/accounts", json={"handle": "x y", "presets": ["a"]})
    assert bad.status_code == 422
    unknown = {"handle": "@voiceshq", "presets": ["no-such"]}
    assert env.client.post("/api/research/accounts", json=unknown).status_code == 422
    body = {"handle": "https://www.instagram.com/VoicesHQ/", "presets": ["voicehq-9x16"]}
    added = env.client.post("/api/research/accounts", json=body)
    assert added.status_code == 200, added.text
    assert env.client.post("/api/research/accounts", json=body).status_code == 409
    (account,) = env.client.get("/api/research/accounts").json()
    assert account["handle"] == "voiceshq" and account["presets"] == ["voicehq-9x16"]
    assert account["learn_status"] == "done" and account["own"] == 1
    assert account["summary"].startswith("Isolated vocals") and account["learned_at"]
    assert account["best"][0]["code"] == "OWN001"

    # No description needed: the account says what to find. Its own reel stays out, and
    # each reel says which preset it suits and why.
    assert env.client.post("/api/research", json={"description": ""}).status_code == 422
    started = env.client.post(
        "/api/research", json={"account_id": account["id"], "skip_questions": True}
    ).json()
    found = env.client.get(f"/api/research/{started['id']}").json()
    assert found["handle"] == "voiceshq" and found["account_id"] == account["id"]
    assert "OWN001" not in [i["code"] for i in found["items"]]
    assert found["left_out"] == {"the account's own": 1, "didn't fit": 1}
    item = found["items"][0]
    assert (item["preset"], item["preset_why"], item["matches"]) == (
        "voicehq-9x16",
        "vocals only",
        "vocals",
    )

    changed = env.client.put(
        f"/api/research/accounts/{account['id']}", json={"presets": ["podcast-9x16"]}
    )
    assert changed.json()["presets"] == ["podcast-9x16"]
    learned = env.client.post(f"/api/research/accounts/{account['id']}/learn").json()
    assert learned["learn_status"] == "queued"  # as the page saw it; the fake ran it already
    assert env.client.delete(f"/api/research/accounts/{account['id']}").status_code == 200
    # The research keeps the account's name.
    kept = env.client.get(f"/api/research/{found['id']}").json()
    assert kept["handle"] == "voiceshq" and kept["account_id"] is None

    # An account is its owner's alone, the admin's too.
    _, riya = employee(env)
    mine = env.client.post("/api/research/accounts", json=body).json()
    assert riya.get("/api/research/accounts").json() == []
    assert riya.post("/api/research", json={"account_id": mine["id"]}).status_code == 404
    assert riya.delete(f"/api/research/accounts/{mine['id']}").status_code == 404


def test_feedback_on_reels_teaches_later_researches(env, monkeypatch):
    fake_research(monkeypatch, env)
    body = {"handle": "voiceshq", "presets": ["voicehq-9x16"]}
    account = env.client.post("/api/research/accounts", json=body).json()
    started = env.client.post(
        "/api/research", json={"account_id": account["id"], "skip_questions": True}
    ).json()
    found = env.client.get(f"/api/research/{started['id']}").json()
    good, bad = found["items"]
    assert (good["verdict"], good["verdict_reason"], good["hidden"]) == (None, "", False)

    def feedback(item, **body):
        return env.client.post(f"/api/research/items/{item['id']}/feedback", json=body)

    assert feedback(good, verdict="good", reason="ignored").json()["items"][0]["verdict"] == "good"
    told = feedback(bad, verdict="bad", reason=" Wrong music ", hide_account=True).json()
    after = told["items"][1]
    assert (after["verdict"], after["verdict_reason"], after["hidden"]) == (
        "bad",
        "Wrong music",
        True,
    )
    assert told["items"][0]["verdict_reason"] == "" and not told["items"][0]["hidden"]
    assert feedback({"id": 999}, verdict="good").status_code == 404
    assert env.client.get("/api/research/hidden").json()[0]["handle"] == "rockblog"

    # A later research for the account is told what was liked and what wasn't; one for a
    # description alone only what wasn't, its topic being another; both leave hidden out.
    with env.sessions() as db:
        owner = db.get(Research, found["id"]).owner_id
        later = Research(description="", owner_id=owner, account_id=account["id"])
        alone = Research(description="x", owner_id=owner)
        db.add_all([later, alone])
        db.commit()
        assert jobs.taste(db, later) == {
            "liked": ["Bryan Adams - Summer of 69 live (@attic)"],
            "disliked": ["Bryan Adams, Heaven (@rockblog): Wrong music"],
            "hidden": {"rockblog"},
        }
        assert jobs.taste(db, alone) == {"liked": [], "disliked": [], "hidden": {"rockblog"}}

    # Taken back, and unhidden; an employee can't give feedback on the admin's reels.
    assert feedback(good, verdict=None).json()["items"][0]["verdict"] is None
    assert env.client.delete("/api/research/hidden/rockblog").status_code == 200
    assert env.client.delete("/api/research/hidden/rockblog").status_code == 404
    assert env.client.get(f"/api/research/{found['id']}").json()["items"][1]["hidden"] is False
    _, riya = employee(env)
    assert (
        riya.post(
            f"/api/research/items/{good['id']}/feedback", json={"verdict": "good"}
        ).status_code
        == 404
    )


def test_a_research_searches_instagram_and_youtube_shorts_as_asked(env, monkeypatch):
    fake_research(monkeypatch, env)
    asked = []
    find = jobs.research.search

    def search(queries, find_, pause=None, progress=None, sites=("instagram",)):
        asked.append(sites)
        return find(queries, find_, pause, progress, sites)

    monkeypatch.setattr(jobs.research, "search", search)
    both = env.client.post("/api/research", json={"description": "Queen", "skip_questions": True})
    assert both.json()["sites"] == ["instagram", "shorts"] and asked[-1] == ("instagram", "shorts")
    body = {"description": "Queen", "skip_questions": True, "sites": ["shorts", "shorts"]}
    shorts = env.client.post("/api/research", json=body).json()
    assert shorts["sites"] == ["shorts"] and asked[-1] == ("shorts",)
    for sites in ([], ["tiktok"]):
        body = {"description": "Queen", "sites": sites}
        assert env.client.post("/api/research", json=body).status_code == 422


def test_an_account_can_be_researched_for_every_morning(env, monkeypatch):
    from datetime import datetime

    fake_research(monkeypatch, env)
    body = {"handle": "voiceshq", "presets": ["voicehq-9x16"]}
    account = env.client.post("/api/research/accounts", json=body).json()
    assert account["daily_hour"] is None
    url = f"/api/research/accounts/{account['id']}/daily"
    assert env.client.put(url, json={"hour": 24}).status_code == 422
    assert env.client.put(url, json={"hour": 7}).json()["daily_hour"] == 7

    def morning(day, hour):
        return jobs.start_daily(env.sessions, datetime(2026, 10, day, hour, 30))

    assert morning(30, 6) == []  # not yet
    (first,) = morning(30, 9)  # the server was off at 7: it starts once it is back
    assert morning(30, 10) == []  # once a day
    found = env.client.get(f"/api/research/{first}").json()
    assert found["daily"] and found["status"] == "done" and found["handle"] == "voiceshq"
    assert found["sites"] == ["instagram", "shorts"]
    assert len(morning(31, 7)) == 1  # the next morning
    assert env.client.get("/api/research/accounts").json()[0]["daily_on"] == "2026-10-31"

    assert env.client.put(url, json={"hour": None}).json()["daily_hour"] is None
    assert jobs.start_daily(env.sessions, datetime(2026, 11, 1, 12)) == []  # off
    _, riya = employee(env)
    assert riya.put(url, json={"hour": 7}).status_code == 404


def test_an_account_takes_reels_from_its_source_pages(env, monkeypatch):
    fake_research(monkeypatch, env)
    body = {"handle": "voiceshq", "presets": ["voicehq-9x16"]}
    account = env.client.post("/api/research/accounts", json=body).json()
    assert account["sources"] == []
    url = f"/api/research/accounts/{account['id']}/sources"
    given = [
        "@BestVocals",
        "https://www.youtube.com/@ChiaraTomao/shorts",
        "https://www.instagram.com/bestvocals/",  # the same page again
    ]
    saved = env.client.put(url, json={"sources": given}).json()
    assert saved["sources"] == ["instagram:bestvocals", "youtube:@ChiaraTomao"]
    bad = env.client.put(url, json={"sources": ["https://www.youtube.com/watch?v=x"]})
    assert bad.status_code == 422 and "watch?v=x" in bad.json()["detail"]
    assert env.client.put(url, json={"sources": ["@a"] * 11}).status_code == 422
    with env.sessions() as db:
        found = Research(description="", owner_id=None, account_id=account["id"])
        db.add(found)
        db.commit()
        context = jobs.research_context(
            found.id, env.sessions, env.settings, None, None, None, None
        )
    assert context.sources == ["instagram:bestvocals", "youtube:@ChiaraTomao"]


def test_a_research_the_engines_turned_away_fails_with_why(env, monkeypatch):
    fake_research(monkeypatch, env, broken=True)
    started = env.client.post(
        "/api/research", json={"description": "Bryan Adams live", "skip_questions": True}
    ).json()
    failed = env.client.get(f"/api/research/{started['id']}").json()
    assert failed["status"] == "failed" and "didn't answer" in failed["error"]


def test_an_employee_sees_only_their_own_researches(env, monkeypatch):
    fake_research(monkeypatch, env)
    mine = env.client.post("/api/research", json={"description": "Queen Live Aid"}).json()
    _, riya = employee(env)
    theirs = riya.post("/api/research", json={"description": "Bryan Adams live"}).json()
    assert [r["id"] for r in riya.get("/api/research").json()] == [theirs["id"]]
    assert riya.get(f"/api/research/{mine['id']}").status_code == 404
    assert riya.delete(f"/api/research/{mine['id']}").status_code == 404
    # The admin sees both, with whose each is.
    listed = {r["id"]: r["owner"] for r in env.client.get("/api/research").json()}
    assert listed[theirs["id"]] == "riya"
