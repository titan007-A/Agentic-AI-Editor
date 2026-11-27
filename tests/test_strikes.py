import json
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

from agentic_editor import download, jobs, pipeline, research, songfacts, strikes
from agentic_editor.channel import checks
from agentic_editor.db import Event, Job, Strike
from agentic_editor.edl import EDL, Clip
from tests import test_api
from tests.test_api import employee, start

env = test_api.env  # the editor with a signed-in admin (a fixture)

FLOWERS = {"song": "Flowers", "artist": "Miley Cyrus", "year": "2023", "year_from": "MusicBrainz",
           "label": "Columbia", "group": "Sony", "label_from": "MusicBrainz"}  # fmt: skip
OLD = {"song": "Tum Hi Ho", "artist": "Arijit Singh", "year": "2013", "year_from": "MusicBrainz",
       "label": "T‐Series", "group": "other", "label_from": "MusicBrainz"}  # fmt: skip


def entries(client) -> list[dict]:
    return client.get("/api/strikes").json()["entries"]


def test_the_list_starts_with_luna_and_every_account_sees_the_same_list(env):
    (luna,) = entries(env.client)
    assert (luna["song"], luna["artist"], luna["label_group"], luna["what"], luna["note"]) == (
        "Luna", "Feid & ATL Jacob", "UMG", "blocked", "228 countries",
    )  # fmt: skip
    assert luna["what_label"] == "blocked worldwide" and luna["can_remove"]  # the admin may
    _, riya = employee(env, presets=[])
    assert [e["song"] for e in entries(riya)] == ["Luna"]
    assert not entries(riya)[0]["can_remove"]
    # Taken off, it isn't put back when the server starts again.
    assert env.client.delete(f"/api/strikes/{luna['id']}").json() == {"ok": True}
    with env.sessions() as db:
        strikes.seed(db)
        assert strikes.listed(db) == []


def test_anyone_adds_a_song_and_only_they_or_an_admin_take_it_off(env):
    _, riya = employee(env, "riya", presets=[])
    _, aman = employee(env, "aman", presets=[])
    body = {"song": "La Muerte del Palomo", "artist": "Pedro Fernández", "label_group": "Sony",
            "what": "removed", "note": "post taken down"}  # fmt: skip
    added = riya.post("/api/strikes", json=body)
    assert added.status_code == 200, added.text
    entry = added.json()
    assert entry["added_by"] == "riya" and entry["can_remove"]
    assert [e["song"] for e in entries(aman)] == ["La Muerte del Palomo", "Luna"]  # newest first
    assert aman.delete(f"/api/strikes/{entry['id']}").status_code == 403
    assert env.client.get("/api/strikes").json()["entries"][0]["can_remove"]  # the admin
    assert riya.delete(f"/api/strikes/{entry['id']}").json() == {"ok": True}
    assert riya.delete(f"/api/strikes/{entry['id']}").status_code == 404


def test_a_song_already_listed_or_a_russia_only_mute_is_refused(env):
    same = env.client.post("/api/strikes", json={"song": "LUNA (Remix)", "artist": "Feid"})
    assert same.status_code == 409 and "already" in same.json()["detail"]
    russia = {"song": "Burbujas de amor", "artist": "Juan Luis Guerra", "note": "muted in Russia"}
    assert env.client.post("/api/strikes", json=russia).status_code == 422
    worldwide = {**russia, "note": "blocked in 228 countries, Russia too"}
    assert env.client.post("/api/strikes", json=worldwide).status_code == 200
    for wrong in ({"label_group": "EMI"}, {"what": "muted"}, {"song": " "}):
        body = {"song": "Otra", "artist": "Alguien", **wrong}
        assert env.client.post("/api/strikes", json=body).status_code == 422


def told_its_song(facts: dict):
    """A fake edit that learns its song as pipeline.song_known tells it."""

    def run(source, presets, settings, backends, notes, progress, run_id=None):
        progress("download", f"fetching {source}")
        progress("song", json.dumps(facts))
        progress("plan", "planning")
        path = settings.media_dir / f"{presets[0].name}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake video")
        edl = EDL(clips=[Clip(start=1, end=21)], title="A title")
        return pipeline.Result("Flowers", [pipeline.Output(presets[0].name, path, edl, "fake")])

    return run


def test_a_job_says_whose_song_it_is_and_carries_on(env):
    env.run = told_its_song(FLOWERS)
    job = env.client.get(f"/api/jobs/{start(env.client)[0]['id']}").json()
    assert job["status"] == "done" and len(job["outputs"]) == 1  # a notice, never a stop
    song = job["song"]
    assert (song["song"], song["group"], song["group_name"], song["label"]) == (
        "Flowers", "Sony", "Sony Music", "Columbia",
    )  # fmt: skip
    assert song["notified"] and song["blocked"] is None
    assert song["new"] == (int(FLOWERS["year"]) >= datetime.now(UTC).year - 3)
    listed = next(j for j in env.client.get("/api/jobs").json() if j["id"] == job["id"])
    assert listed["song"] == song
    with env.sessions() as db:
        said = db.query(Event).filter_by(job_id=job["id"], stage="song").one()
        assert said.message == "song: Flowers by Miley Cyrus, 2023, Columbia (Sony)"
        assert db.get(Job, job["id"]).stage == "done"


def test_an_independent_old_song_is_only_named(env):
    env.run = told_its_song(OLD)
    song = env.client.get(f"/api/jobs/{start(env.client)[0]['id']}").json()["song"]
    assert song["group"] == "other" and not song["notified"] and not song["new"]
    env.run = told_its_song({**OLD, "year": None, "year_from": ""})
    assert env.client.get(f"/api/jobs/{start(env.client)[0]['id']}").json()["song"]["year"] is None


def test_a_song_put_on_the_list_later_warns_on_its_jobs_too(env):
    env.run = told_its_song(FLOWERS)
    job_id = start(env.client)[0]["id"]
    assert env.client.get(f"/api/jobs/{job_id}").json()["song"]["blocked"] is None
    # The reel got blocked: "Block aaya" puts the job's song on the list.
    body = {"song": "Flowers", "artist": "Miley Cyrus", "label_group": "Sony", "what": "blocked",
            "note": "228 countries", "job_id": job_id}  # fmt: skip
    added = env.client.post("/api/strikes", json=body).json()
    assert added["job_id"] == job_id
    blocked = env.client.get(f"/api/jobs/{job_id}").json()["song"]["blocked"]
    assert (blocked["song"], blocked["what_label"], blocked["note"]) == (
        "Flowers", "blocked worldwide", "228 countries",
    )  # fmt: skip


def test_the_block_button_needs_a_job_the_person_sees(env):
    job_id = start(env.client)[0]["id"]
    _, riya = employee(env, presets=[])
    body = {"song": "Flowers", "artist": "Miley Cyrus", "job_id": job_id}
    assert riya.post("/api/strikes", json=body).status_code == 404
    with env.sessions() as db:
        assert [s.song for s in strikes.listed(db)] == ["Luna"]


def test_a_job_without_its_song_has_no_notice(env):
    job = env.client.get(f"/api/jobs/{start(env.client)[0]['id']}").json()
    assert job["status"] == "done" and job["song"] is None


def test_the_tracker_keeps_the_song_without_changing_the_stage(env):
    with env.sessions() as db:
        db.add(Job(id="j1", source="https://x.test/v", presets=["a"]))
        db.commit()
    track = jobs.Tracker(env.sessions, "j1", 1)
    track("plan", "planning")
    track("song", json.dumps(FLOWERS))
    with env.sessions() as db:
        job = db.get(Job, "j1")
        assert job.song_facts == FLOWERS and job.stage == "plan"
    track("render", "rendering")
    assert track.stage == "render"


def source(tmp_path, description="", title="Video by someone"):
    return download.Source(path=tmp_path / "v.mp4", title=title, description=description)


def test_the_edit_tells_the_songs_label_and_year_as_the_video_says_them(tmp_path):
    told = []
    settings = SimpleNamespace(cache_dir=tmp_path / "cache")
    said = "Provided to YouTube by Sony Music Entertainment\n\n℗ 2022 Sony Music India"
    pipeline.song_known("Kesariya", "Arijit Singh", source(tmp_path, said), tmp_path / "w",
                        None, settings, lambda *args: told.append(args))  # fmt: skip
    ((stage, message),) = told
    facts = json.loads(message)
    assert stage == "song" and facts["group"] == "Sony" and facts["label_from"] == "YouTube"
    assert (facts["year"], facts["year_from"]) == ("2022", "YouTube")
    told.clear()
    caption = "Maldita primavera (1981) - Yuri"
    pipeline.song_known("Maldita primavera", "Yuri", source(tmp_path, caption), tmp_path / "w",
                        None, settings, lambda *args: told.append(args))  # fmt: skip
    assert json.loads(told[0][1])["year_from"] == "the post"
    told.clear()
    pipeline.song_known("Maldita primavera", "Yuri", source(tmp_path, caption), tmp_path / "w",
                        "1982", settings, lambda *args: told.append(args))  # fmt: skip
    assert json.loads(told[0][1])["year"] == "1982"  # the person's year wins


def test_a_lookup_that_fails_never_stops_the_edit(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(songfacts, "look_up", broken)
    told = []
    settings = SimpleNamespace(cache_dir=tmp_path)
    pipeline.song_known("A", "B", source(tmp_path), tmp_path, None, settings, told.append)
    assert told == []


def test_a_caption_that_finds_the_song_tells_the_job(env, tmp_path):
    with env.sessions() as db:
        db.add(Job(id="j2", source="https://x.test/v", presets=["a"]))
        db.commit()
    reel = tmp_path / "out" / "a.mp4"
    reel.parent.mkdir()
    (reel.parent / "source.json").write_text(json.dumps({"uploader": "Sony Music India"}))
    jobs.song_from_caption("j2", "Kesariya", "Arijit Singh", reel, env.sessions, env.settings)
    with env.sessions() as db:
        facts = db.get(Job, "j2").song_facts
        assert (facts["song"], facts["group"], facts["label_from"]) == (
            "Kesariya", "Sony", "the channel",
        )  # fmt: skip
        before = db.query(Event).filter_by(job_id="j2").count()
    # The same song again (as Wikipedia names it) changes nothing; another replaces it.
    jobs.song_from_caption("j2", "Kesariya (From Brahmastra)", "Arijit Singh", reel,
                           env.sessions, env.settings)  # fmt: skip
    with env.sessions() as db:
        assert db.query(Event).filter_by(job_id="j2").count() == before
    jobs.song_from_caption("j2", "Tum Hi Ho", "Arijit Singh", reel, env.sessions, env.settings)
    with env.sessions() as db:
        assert db.get(Job, "j2").song_facts["song"] == "Tum Hi Ho"
        # The edit knew the song but not its label (MusicBrainz was busy): asked again.
        db.get(Job, "j2").song_facts = {**FLOWERS, "label": "", "group": "", "label_from": ""}
        db.commit()
    (reel.parent / "source.json").write_text(json.dumps({"uploader": "Columbia Records"}))
    jobs.song_from_caption("j2", "Flowers", "Miley Cyrus", reel, env.sessions, env.settings)
    with env.sessions() as db:
        assert db.get(Job, "j2").song_facts["group"] == "Sony"


def test_a_research_never_gives_a_song_on_the_strike_list():
    class Rater:
        def chat_json(self, messages, schema, **kwargs):
            if "queries" in schema["properties"]:
                return {"brief": "songs", "music": True, "queries": ["reggaeton"]}
            lines = [x for x in messages[-1]["content"].split("\n") if x[:1].isdigit()]
            songs = {"LUNA": ("LUNA", "Feid"), "KATE": ("Wuthering Heights", "Kate Bush")}
            return {"ratings": [
                {"n": n, "relevance": 9, "why": "", "song": songs[key][0], "artist":
                 songs[key][1], "matches": "", "preset": "", "preset_why": "", "usable": True}
                for n, line in enumerate(lines, 1)
                for key in songs if key in line
            ]}  # fmt: skip

    def find(query, count):
        return [{"href": f"https://www.instagram.com/reel/{c}0000001/", "title": c, "body": ""}
                for c in ("LUNA", "KATE")]  # fmt: skip

    def info(url):
        return {"uploader": "x", "description": research.code_of(url), "like_count": 10}

    context = research.Context(strikes=[("Luna", "Feid & ATL Jacob")])
    result = research.run("", lambda: nullcontext(Rater()), set(), set(), lambda m, v: None,
                          find=find, info=info, pause=0, context=context)  # fmt: skip
    assert [r.song for r in result.reels] == ["Wuthering Heights - Kate Bush"]
    assert result.left_out["on the strike list"] == 1


def test_a_researchs_songs_are_looked_up_once_and_only_while_there_is_time(tmp_path, monkeypatch):
    asked = []

    def look_up(song, artist, **kwargs):
        asked.append((song, kwargs["ask"]))
        return songfacts.Facts(song, artist, "2024", "MusicBrainz", "Atlantic", "Warner")

    monkeypatch.setattr(songfacts, "look_up", look_up)
    now = [0.0]

    def clock():
        now[0] += 50.0
        return now[0]

    reels = [research.Reel(code=f"c{n}", url="", query="") for n in range(3)]
    songs = [(reels[0], "A", "x", None), (reels[1], "B", "y", None), (reels[2], "", "", None)]
    found = jobs.songs_facts(songs, SimpleNamespace(cache_dir=tmp_path), lambda *a: None,
                             clock=clock)  # fmt: skip
    assert asked == [("A", True), ("B", False)]  # past the time, only what is kept
    item = jobs.with_facts(jobs.item_of(reels[0], 0), found[id(reels[0])])
    assert (item.year, item.label, item.label_group) == ("2024", "Atlantic", "Warner")
    assert id(reels[2]) not in found


def test_a_researchs_reels_show_their_songs_year_label_and_strikes(env):
    from agentic_editor.db import Research, ResearchItem

    with env.sessions() as db:
        found = Research(id="r1", description="", status="done")
        found.items = [
            ResearchItem(rank=0, code="a", url="u", song="Flowers - Miley Cyrus",
                         artist="Miley Cyrus", year=str(datetime.now(UTC).year - 1),
                         label="Columbia", label_group="Sony"),
            ResearchItem(rank=1, code="b", url="u", song="LUNA - Feid", artist="Feid",
                         year="2023", label="Universal Music Latin Entertainment",
                         label_group="UMG"),
            ResearchItem(rank=2, code="c", url="u", song="Tum Hi Ho", artist="Arijit Singh",
                         year="2013"),
        ]  # fmt: skip
        db.add(found)
        db.commit()
    items = env.client.get("/api/research/r1").json()["items"]
    assert [(i["new"], i["label_group"]) for i in items] == [(True, "Sony"), (
        datetime.now(UTC).year - 2023 <= 3, "UMG"), (False, "")]  # fmt: skip
    assert [bool(i["blocked"]) for i in items] == [False, True, False]


def notice_of(facts: dict, *listed: tuple[str, str]):
    rows = [Strike(id=n, song=s, artist=a, label_group="UMG", what="blocked", note="")
            for n, (s, a) in enumerate(listed)]  # fmt: skip
    return strikes.notice(facts, rows, this_year=2026)


def test_the_channel_autopilot_holds_a_reel_of_a_strike_list_song():
    held = checks.song_rights(notice_of({"song": "Luna", "artist": "Feid"},
                                        ("Luna", "Feid & ATL Jacob")), known=False)  # fmt: skip
    assert held.name == "strike list" and not held.ok
    assert held.detail == "“Luna” by Feid & ATL Jacob was blocked worldwide before (UMG): pick " \
        "another song"  # fmt: skip
    said = checks.song_rights(notice_of(FLOWERS), known=True)
    assert said.ok and said.name == "song rights"
    assert said.detail == "Sony Music's song (Columbia); 2023, a new song"
    assert checks.song_rights(notice_of(OLD), known=True).detail == "T‐Series; 2013"
    # Shazam's name alone (no edit facts): only the strike list is checked.
    assert checks.song_rights(notice_of({"song": "Flowers", "artist": "x"}), known=False) is None
    assert checks.song_rights(None, known=True) is None
