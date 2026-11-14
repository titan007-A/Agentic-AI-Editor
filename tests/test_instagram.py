"""Instagram through Meta's Unified Login, and answering comments, against a stand-in
graph.facebook.com (no network)."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agentic_editor import comments, instagram
from agentic_editor.db import InstagramChannel, InstagramComment, User, make_sessionmaker


class Meta:
    """graph.facebook.com as far as the app uses it: one client portfolio sharing two
    accounts, posts with comments, replies."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.replies: list[tuple[str, str]] = []
        self.comments = {
            "p1": [
                {"id": "c1", "text": "love this!", "username": "fan1",
                 "timestamp": self.stamp(minutes=-5)},
                {"id": "c2", "text": "buy followers at spam.biz", "username": "bot",
                 "timestamp": self.stamp(minutes=-4)},
                {"id": "c3", "text": "nice", "username": "mypage",
                 "timestamp": self.stamp(minutes=-3)},
                {"id": "c4", "text": "where is this?", "username": "fan2",
                 "timestamp": self.stamp(minutes=-2),
                 "replies": {"data": [{"username": "mypage"}]}},
                {"id": "old", "text": "from last year", "username": "fan3",
                 "timestamp": self.stamp(minutes=-60 * 24 * 400)},
            ]
        }  # fmt: skip
        self.fail_replies = False

    @staticmethod
    def stamp(minutes: int) -> str:
        when = datetime.now(UTC) + timedelta(minutes=minutes)
        return when.strftime("%Y-%m-%dT%H:%M:%S+0000")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        assert url.netloc == "graph.facebook.com", url.netloc
        assert url.path.startswith(f"/{instagram.VERSION}/"), url.path
        path = url.path.split("/", 2)[2]
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        if request.method == "POST":
            params |= {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, path, params))
        if path == "oauth/access_token":
            if params.get("client_secret") != "app-secret" or params.get("code") != "good":
                return self.error(400, "Invalid verification code format.", 100)
            return self.ok({"access_token": "SUAT", "token_type": "bearer"})
        if path == "debug_token":
            return self.ok({"data": {"expires_at": 0, "is_valid": True}})
        assert params.get("access_token") == "SUAT", params
        if path == "me":
            return self.ok({"id": "su1", "client_business_id": "biz9"})
        if path == "me/assigned_instagram_accounts":
            return self.ok({"data": [{"id": "ig1", "username": "mypage"},
                                     {"id": "ig2", "username": "otherpage"}]})  # fmt: skip
        if path in ("ig1", "ig2"):
            name = {"ig1": "mypage", "ig2": "otherpage"}[path]
            return self.ok({"username": name, "name": name.title(), "profile_picture_url": None})
        if path == "ig1/media":
            return self.ok({"data": [
                {"id": "p1", "caption": "Sunset at the beach", "permalink": "https://ig/p1",
                 "comments_count": 5},
                {"id": "p2", "caption": "quiet one", "comments_count": 0},
            ]})  # fmt: skip
        if path.endswith("/comments"):
            return self.ok({"data": self.comments.get(path.split("/")[0], [])})
        if path.endswith("/replies") and request.method == "POST":
            if self.fail_replies:
                return self.error(400, "Action is blocked for now", 368)
            self.replies.append((path.split("/")[0], params["message"]))
            return self.ok({"id": f"r{len(self.replies)}"})
        return self.error(404, f"unknown path {path}", 100)

    @staticmethod
    def ok(body: dict) -> httpx.Response:
        return httpx.Response(200, json=body)

    @staticmethod
    def error(status: int, message: str, code: int) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": message, "code": code}})


@pytest.fixture
def meta(monkeypatch):
    fake = Meta()
    monkeypatch.setattr(instagram, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def test_the_login_page_offers_instagram_login_with_the_unified_configuration():
    url = urlparse(instagram.auth_url("123", "https://x.test/auth/instagram/callback", "st", "777"))
    asked = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "www.facebook.com" and url.path == "/dialog/oauth"
    assert asked == {
        "client_id": "123", "config_id": "777", "response_type": "code", "state": "st",
        "redirect_uri": "https://x.test/auth/instagram/callback", "prompt": "login",
        "override_default_response_type": "true",
    }  # fmt: skip
    with pytest.raises(instagram.InstagramError, match="META_CONFIG_ID"):
        instagram.auth_url("123", "https://x.test/cb", "st", "")


def test_a_login_gives_every_shared_account_on_graph_facebook(meta):
    found = instagram.connect("123", "app-secret", "https://x.test/cb", "good")
    assert found.token == "SUAT" and found.business_id == "biz9" and found.expires_at is None
    assert [(c.id, c.username, c.access_token) for c in found.channels] == [
        ("ig1", "mypage", "SUAT"),
        ("ig2", "otherpage", "SUAT"),
    ]
    exchange = next(p for m, path, p in meta.calls if path == "oauth/access_token")
    assert exchange["client_id"] == "123"  # the Meta app's ID, not an Instagram app's
    with pytest.raises(instagram.InstagramError, match=r"400 \(#100\)"):
        instagram.connect("123", "app-secret", "https://x.test/cb", "bad")


class Drafter:
    """The planning model: replies to fans, skips the bot, hands questions to a person."""

    def __init__(self):
        self.prompts = []

    def chat_json(self, messages, schema, **kw):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        out = []
        for line in prompt.splitlines():
            if not line[:1].isdigit() or ". on " not in line:
                continue
            n = int(line.split(".")[0])
            if "spam" in line:
                out.append({"n": n, "action": "skip", "why": "spam"})
            elif "?" in line:
                out.append({"n": n, "action": "person", "reply": "", "why": "a question"})
            else:
                out.append(
                    {"n": n, "action": "reply", "reply": f"Thank you {n}! 💛", "why": "kind"}
                )
        return {"replies": out}


def channel(db, mode="review", since_minutes=-30, limit=15):
    db.add(User(id="u1", username="owner", password_hash="x", role="admin"))
    db.flush()
    row = InstagramChannel(
        id="ig1", owner_id="u1", username="mypage", access_token="SUAT", reply_mode=mode,
        reply_style="short and sunny", reply_limit=limit,
        reply_since=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=since_minutes),
    )  # fmt: skip
    db.add(row)
    db.commit()
    return row


def test_in_review_mode_replies_are_drafted_and_wait_for_a_person(tmp_path, meta):
    Session = make_sessionmaker(tmp_path / "app.db")
    model = Drafter()
    with Session() as db:
        row = channel(db, "review")
        note = comments.turn(db, row, model)
        got = {c.id: c for c in db.query(InstagramComment)}
        # Its own comment, one it already answered, and older ones are left alone.
        assert set(got) == {"c1", "c2"}
        assert got["c1"].status == "drafted" and got["c1"].draft == "Thank you 1! 💛"
        assert got["c1"].post_url == "https://ig/p1" and "Sunset" in got["c1"].post_caption
        assert got["c2"].status == "skipped"
        assert meta.replies == []  # nothing sent by itself
        assert "short and sunny" in model.prompts[0] and "@mypage" in model.prompts[0]
        assert note.startswith("2 new comments, 1 replies drafted") and row.check_note == note
        # A second turn finds nothing new and asks the model nothing.
        comments.turn(db, row, model)
        assert len(model.prompts) == 1
        # A person sends it, in their own words.
        comments.send(db, row, got["c1"], "  Thanks a lot!  ", "person")
        assert meta.replies == [("c1", "Thanks a lot!")]
        assert got["c1"].status == "sent" and got["c1"].sent_by == "person"


def test_auto_mode_sends_by_itself_within_its_hourly_limit(tmp_path, meta):
    Session = make_sessionmaker(tmp_path / "app.db")
    meta.comments["p1"] += [
        {"id": f"x{i}", "text": f"great {i}", "username": f"f{i}", "timestamp": meta.stamp(-1)}
        for i in range(4)
    ]
    with Session() as db:
        row = channel(db, "auto", limit=3)
        note = comments.turn(db, row, Drafter())
        assert len(meta.replies) == 3 and note.endswith("3 sent")
        statuses = [c.status for c in db.query(InstagramComment).order_by(InstagramComment.id)]
        assert statuses.count("sent") == 3 and statuses.count("drafted") == 2
        assert all(c.sent_by == "auto" for c in db.query(InstagramComment) if c.status == "sent")
        # The hour's limit is reached: the next turn sends none.
        comments.turn(db, row, Drafter())
        assert len(meta.replies) == 3


def test_a_question_waits_for_a_person_even_in_auto_mode(tmp_path, meta):
    Session = make_sessionmaker(tmp_path / "app.db")
    meta.comments["p1"][3].pop("replies")  # "where is this?" not answered yet
    with Session() as db:
        row = channel(db, "auto")
        comments.turn(db, row, Drafter())
        question = db.get(InstagramComment, "c4")
        assert question.status == "look" and ("c4", "") not in meta.replies
        assert [c for c, _ in meta.replies] == ["c1"]


def test_instagram_saying_no_stops_the_turn_and_keeps_the_reply(tmp_path, meta):
    Session = make_sessionmaker(tmp_path / "app.db")
    meta.fail_replies = True
    with Session() as db:
        row = channel(db, "auto")
        comments.turn(db, row, Drafter())
        failed = db.get(InstagramComment, "c1")
        assert failed.status == "failed" and "Action is blocked" in failed.error
        assert failed.draft == "Thank you 1! 💛"


def test_comments_from_before_answering_was_on_are_left_alone(tmp_path, meta):
    Session = make_sessionmaker(tmp_path / "app.db")
    with Session() as db:
        row = channel(db, "review", since_minutes=0)
        assert comments.turn(db, row, Drafter()).startswith("0 new comments")


def test_the_web_connects_answers_and_sends(tmp_path, meta, monkeypatch):
    """Through the app: the callback keeps both shared accounts; answering is turned on,
    a turn runs; a person sends a changed draft and skips another."""
    from fastapi.testclient import TestClient

    from agentic_editor import jobs
    from agentic_editor.api import create_app
    from agentic_editor.auth import hash_password
    from agentic_editor.db import Setting
    from agentic_editor.settings import Settings

    settings = Settings(
        media_dir=tmp_path / "media", database=tmp_path / "app.db", web_dir=tmp_path / "web",
        password_hash=hash_password("pw-123456"), secret_key="s", meta_app_id="123",
        meta_app_secret="app-secret", meta_config_id="777",
        public_url="https://pjmedia.test",
    )  # fmt: skip
    Session = make_sessionmaker(settings.database)
    model = Drafter()
    ran = []

    def answer(channel_id):
        ran.append(channel_id)
        router = type("R", (), {"planner": model})()
        return jobs.answer_comments_now(channel_id, Session, settings, router=router)

    monkeypatch.setattr(jobs, "answer_comments", answer)
    with TestClient(create_app(settings, Session, start_worker=False)) as client:
        assert client.post("/api/login", json={"password": "pw-123456"}).status_code == 200
        start = client.get("/auth/instagram", follow_redirects=False)
        login = urlparse(start.headers["location"])
        asked = parse_qs(login.query)
        assert asked["config_id"] == ["777"] and asked["prompt"] == ["login"]
        assert asked["redirect_uri"] == ["https://pjmedia.test/auth/instagram/callback"]
        state = asked["state"][0]
        wrong = client.get("/auth/instagram/callback?code=good&state=nope", follow_redirects=False)
        assert "invalid_state" in wrong.headers["location"]
        back = client.get(f"/auth/instagram/callback?code=good&state={state}",
                          follow_redirects=False)  # fmt: skip
        assert back.headers["location"] == "/instagram?ig_connected=mypage%2Cotherpage"
        listed = {c["username"]: c for c in client.get("/api/instagram/channels").json()}
        assert set(listed) == {"mypage", "otherpage"} and listed["mypage"]["reply_mode"] == "off"
        assert listed["mypage"]["token_expires_at"] is None
        with Session() as db:
            assert db.get(InstagramChannel, "ig1").business_id == "biz9"
            assert not db.query(Setting).filter(Setting.key.like("ig_oauth_state:%")).count()

        assert client.post("/api/instagram/channels/ig1/check").status_code == 409  # off
        on = client.patch(
            "/api/instagram/channels/ig1",
            json={"reply_mode": "review", "reply_style": "warm", "reply_limit": 5},
        )
        assert on.json()["reply_mode"] == "review" and on.json()["reply_limit"] == 5  # fmt: skip
        with Session() as db:  # answer from a little before, so the fan's comment counts
            row = db.get(InstagramChannel, "ig1")
            row.reply_since = row.reply_since - timedelta(minutes=30)
            db.commit()
        assert client.post("/api/instagram/channels/ig1/check").json() == {"ok": True}
        assert ran == ["ig1"]
        open_ = client.get("/api/instagram/comments").json()
        assert [(c["id"], c["status"], c["channel"]) for c in open_] == [
            ("c1", "drafted", "mypage")
        ]
        assert client.get("/api/instagram/channels").json()[0]["waiting"] == 1
        sent = client.post("/api/instagram/comments/c1/send", json={"text": "Thank you!!"})
        assert sent.json()["status"] == "sent" and meta.replies == [("c1", "Thank you!!")]
        assert client.post("/api/instagram/comments/c1/send", json={"text": "x"}).status_code == 409
        assert [c["id"] for c in client.get("/api/instagram/comments?show=sent").json()] == ["c1"]
        assert [c["id"] for c in client.get("/api/instagram/comments?show=skipped").json()] == [
            "c2"
        ]
        assert client.post("/api/instagram/comments/nope/skip").status_code == 404
        assert (
            client.patch("/api/instagram/channels/ig1", json={"reply_limit": 0}).status_code == 422
        )
        # Someone else can't see or touch them.
        client.post("/api/logout")
        assert client.get("/api/instagram/comments").status_code == 401
