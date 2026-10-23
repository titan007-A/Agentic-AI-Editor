import json
import os
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentic_editor import jobs, pipeline, style
from agentic_editor.api import create_app
from agentic_editor.auth import hash_password
from agentic_editor.backends import Backends
from agentic_editor.db import Event, Job, make_sessionmaker
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

    def fake_run(source, presets, settings, backends, notes, progress):
        calls.append({"source": source, "presets": [p.name for p in presets], "notes": notes})
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

    env = SimpleNamespace(calls=calls, settings=settings, run=fake_run)
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


def test_usage_notice_is_accepted_once(env):
    assert env.client.get("/api/me").json()["notice_accepted"] is False
    env.client.post("/api/notice")
    assert env.client.get("/api/me").json()["notice_accepted"] is True


def test_batch_creates_one_job_per_link_and_records_outputs(env):
    links = ["https://example.com/a", "https://example.com/b", "https://example.com/a"]
    created = start(env.client, links, ["vocal-music-9x16", "square-1x1"])
    assert len(created) == 2
    assert [c["source"] for c in env.calls] == ["https://example.com/a", "https://example.com/b"]

    job = env.client.get(f"/api/jobs/{created[0]['id']}").json()
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["title"] == "Sample song"
    assert [o["preset"] for o in job["outputs"]] == ["vocal-music-9x16", "square-1x1"]
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


def test_failed_run_marks_the_job_failed(env):
    def broken(*args):
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
    assert env.calls[-1] == {
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


def test_outputs_with_doubts_need_a_look_until_the_editor_says_otherwise(env):
    def doubtful(source, presets, settings, backends, notes, progress):
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


def test_feeds_are_checked_before_they_are_saved(env, tmp_path):
    assert add_folder_feed(env, tmp_path / "missing").status_code == 422
    link = {"name": "x", "kind": "link", "location": "not a link", "presets": ["square-1x1"]}
    assert env.client.post("/api/feeds", json=link).status_code == 422
    assert add_folder_feed(env, tmp_path, active_from=23).status_code == 422
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
