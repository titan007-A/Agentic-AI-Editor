import json

from agentic_editor.db import Feed, FeedItem, Job, Output, make_sessionmaker
from agentic_editor.relocate import moved, relocate

OLD = "/home/siddhant/projects/Agentic-AI-Editor"


def test_only_paths_under_the_old_root_move():
    assert moved(f"{OLD}/media/a.mp4", OLD, "/Users/s/editor") == "/Users/s/editor/media/a.mp4"
    assert moved(f"{OLD}-copy/media/a.mp4", OLD, "/Users/s/editor") == f"{OLD}-copy/media/a.mp4"
    assert moved("https://x.test/v", OLD, "/Users/s/editor") == "https://x.test/v"
    assert moved(None, OLD, "/Users/s/editor") is None


def test_videos_sources_and_feed_items_point_at_the_new_place(tmp_path):
    new = str(tmp_path)
    Session = make_sessionmaker(tmp_path / "media" / "app.db")
    with Session() as db:
        job = Job(source="https://x.test/v", presets=["p"])
        job.outputs.append(
            Output(
                preset="p",
                path=f"{OLD}/media/outputs/abc/r1/p.mp4",
                thumbnail=f"{OLD}/media/outputs/abc/r1/p.jpg",
                duration=20,
                planner="m",
                edl={"clips": [{"start": 0, "end": 20}]},
            )
        )
        feed = Feed(name="Gigs", kind="folder", location="/home/siddhant/Videos", presets=["p"])
        feed.items.append(FeedItem(key="k", source=f"{OLD}/media/inputs/gig.mp4"))
        db.add_all([job, feed])
        db.commit()
    meta = tmp_path / "media" / "sources" / "Youtube-x" / "source.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"path": f"{OLD}/media/sources/Youtube-x/source.mkv", "title": "T"}))

    result = relocate(Session, tmp_path / "media", OLD + "/", new)

    assert (result.outputs, result.sources, result.feed_items) == (1, 1, 1)
    assert result.missing_folders == ["Gigs: /home/siddhant/Videos"]
    with Session() as db:
        out = db.query(Output).one()
        assert out.path == f"{new}/media/outputs/abc/r1/p.mp4"
        assert out.thumbnail == f"{new}/media/outputs/abc/r1/p.jpg"
        assert db.query(FeedItem).one().source == f"{new}/media/inputs/gig.mp4"
    assert json.loads(meta.read_text())["path"] == f"{new}/media/sources/Youtube-x/source.mkv"
    # A second run finds nothing left to move.
    again = relocate(Session, tmp_path / "media", OLD, new)
    assert (again.outputs, again.sources, again.feed_items) == (0, 0, 0)
