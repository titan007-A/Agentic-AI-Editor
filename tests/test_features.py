import json

from agentic_editor import features
from agentic_editor.db import Setting, User, make_sessionmaker


def test_a_feature_on_for_everyone_reaches_accounts_made_later(tmp_path):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        siddhant = User(username="siddhant", password_hash="x", role="admin")
        riya = User(username="riya", password_hash="x", role="member")
        db.add_all([siddhant, riya])
        db.commit()
        # Until it is set: the accounts trying it first.
        assert features.enabled(siddhant, "fun", db) and not features.enabled(riya, "fun", db)
        db.add(Setting(key="feature:fun", value=json.dumps(["riya"])))
        db.commit()
        assert features.enabled(riya, "fun", db) and not features.enabled(siddhant, "fun", db)
        db.get(Setting, "feature:fun").value = json.dumps([features.EVERYONE])
        db.commit()
        later = User(username="newadmin", password_hash="x", role="admin")
        db.add(later)
        db.commit()
        assert all(features.enabled(a, "fun", db) for a in (siddhant, riya, later))
        assert features.of(later, db) == ["fun"]  # the others still as they were
