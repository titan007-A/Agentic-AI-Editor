import sqlite3

from agentic_editor.db import Feed, make_sessionmaker


def test_feeds_from_before_the_first_look_flag_keep_their_first_look(tmp_path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as old:
        old.execute(
            "CREATE TABLE feeds (id INTEGER PRIMARY KEY, name TEXT, kind VARCHAR(16), "
            "location TEXT, presets JSON, notes TEXT, enabled BOOLEAN, active_from INTEGER, "
            "active_to INTEGER, batch INTEGER, include_existing BOOLEAN, last_checked DATETIME, "
            "last_result TEXT, created_at DATETIME)"
        )
        old.execute(
            "INSERT INTO feeds (name, kind, location, presets, enabled, batch, include_existing) "
            "VALUES ('old', 'folder', '/x', '[\"p\"]', 1, 3, 0)"
        )
    Session = make_sessionmaker(path)
    with Session() as db:
        db.add(Feed(name="new", kind="folder", location="/y", presets=["p"]))
        db.commit()
        assert {f.name: f.looked for f in db.query(Feed)} == {"old": True, "new": False}
