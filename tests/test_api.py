import json
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentic_editor import jobs, pipeline
from agentic_editor.api import create_app
from agentic_editor.auth import hash_password
from agentic_editor.backends import Backends
from agentic_editor.db import make_sessionmaker
from agentic_editor.edl import EDL, Clip
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
