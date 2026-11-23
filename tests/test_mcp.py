"""Claude over MCP (animate/mcp_tools, mcp_access): keys and the sign-in page's OAuth, the
tools taking an animation from one line to a rendered video through the real HTTP
endpoint, and what a connection may not do (other accounts, posting unless allowed)."""

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
import pytest
from mcp.client.auth import OAuthClientProvider
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from sqlalchemy import select

from agentic_editor import accounts, instagram, jobs, mcp_access
from agentic_editor.animate import mcp_tools, planner, studio
from agentic_editor.api import create_app
from agentic_editor.auth import hash_password
from agentic_editor.backends import Backends
from agentic_editor.db import (
    Animation,
    InstagramChannel,
    McpClient,
    McpToken,
    Setting,
    User,
    make_sessionmaker,
)
from agentic_editor.settings import ROOT, Settings
from tests.test_animate import Router, Writer, fake_speak, long_plan, reply_for

BASE = "http://testserver"
PASSWORD = "correct horse"
TOOLS = {
    "list_animations", "get_animation", "wait_for_animation", "list_voices",
    "create_animation", "change_animation", "edit_animation_plan", "set_style_and_voice",
    "repaint_pictures", "undo_last_change", "retry_animation", "render_animation",
    "skip_autopilot_reel", "list_instagram_pages", "post_to_instagram",
    "approve_autopilot_reel",
}  # fmt: skip


def painted_reply() -> dict:
    """A draft whose scenes each have a picture to paint."""
    reply = reply_for(long_plan())
    for n, scene in enumerate(reply["scenes"], 1):
        scene["visual"]["art"] = f"a desert city at dusk, view {n}"
    return reply


def fake_paint(pictures, style, settings, progress):
    """A picture's file named by what it shows and its seed, as images.name_of names them."""
    return [
        hashlib.sha256(f"{p.prompt}|{p.seed}".encode()).hexdigest()[:24] + ".png" for p in pictures
    ]


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The editor as the server runs it (not started yet: `run` does), with the studio's work
    done at once by fakes: a planner answering from a queue, voices, painting and drawing."""
    presets = tmp_path / "presets"
    shutil.copytree(ROOT / "presets", presets)
    settings = Settings(
        media_dir=tmp_path / "media", cache_dir=tmp_path / "cache", presets_dir=presets,
        database=tmp_path / "app.db", web_dir=tmp_path / "web",
        password_hash=hash_password(PASSWORD), secret_key="test-secret",
    )  # fmt: skip
    sessions = make_sessionmaker(settings.database)
    writer = Writer(*[painted_reply()] * 8)
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(jobs, "get_backends", lambda: Backends("cpu", "cpu", "x264", "h264"))
    monkeypatch.setattr(
        jobs,
        "animate",
        lambda animation_id: studio.run_task(
            animation_id, sessions, settings, Router(writer), speak=fake_speak, paint=fake_paint
        ),
    )
    monkeypatch.setattr(jobs, "process", lambda job_id: jobs.run_job(job_id, sessions, settings))

    def draw(plan, tl, voices, out_dir, fonts_dir, models_dir, progress, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "animation.mp4").write_bytes(b"fake video")
        return out_dir / "animation.mp4", None, []

    real = studio.render_job
    monkeypatch.setattr(
        studio, "render_job", lambda *a, **k: real(*a, **{**k, "speak": fake_speak, "draw": draw})
    )
    app = create_app(settings, sessions, start_worker=False)
    with sessions() as db:
        riya = accounts.create(db, "riya", "riya-pass-123", accounts.MEMBER, presets)
        db.merge(Setting(key="feature:animation_studio", value=json.dumps(["admin", "riya"])))
        db.commit()
        admin = accounts.find(db, "admin")
        ids = SimpleNamespace(admin=admin.id, riya=riya.id)
    return SimpleNamespace(app=app, settings=settings, sessions=sessions, writer=writer, ids=ids)


def run(site, scenario) -> None:
    """`scenario(web, transport)` with the server started: `web` signed in as the admin,
    `transport` reaching the same server for more clients."""

    async def main():
        async with site.app.router.lifespan_context(site.app):
            transport = httpx2.ASGITransport(app=site.app)
            async with httpx2.AsyncClient(transport=transport, base_url=BASE) as web:
                assert (await web.post("/api/login", json={"password": PASSWORD})).is_success
                await scenario(web, transport)

    anyio.run(main)


@asynccontextmanager
async def claude(transport, key: str, mode: str = "legacy"):
    """Claude's MCP client on the server's endpoint with a key, as Claude Code connects."""
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers) as http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode=mode) as client,
    ):
        yield client


async def call(client, name: str, **arguments) -> dict:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


async def refused(client, name: str, **arguments) -> str:
    result = await client.call_tool(name, arguments)
    assert result.is_error, result.content[0].text
    return result.content[0].text


async def make_key(web, posting: bool = False) -> str:
    made = await web.post("/api/mcp/keys", json={"label": "Claude Code", "posting": posting})
    assert made.status_code == 200, made.text
    return made.json()["key"]


def other_animation(site) -> str:
    with site.sessions() as db:
        db.add(Animation(id="hers1", owner_id=site.ids.riya, idea="patience", status="ready"))
        db.commit()
    return "hers1"


def test_claude_takes_an_animation_from_one_line_to_a_rendered_video(site):
    hers = other_animation(site)

    async def scenario(web, transport):
        made = (await web.post("/api/mcp/keys", json={"label": "My laptop"})).json()
        key = made["key"]
        assert key.startswith("aie_") and made["url"] == f"{BASE}/mcp"
        (listed,) = made["connections"]
        assert listed["label"] == "My laptop" and listed["tail"] == key[-4:]
        assert not listed["posting"] and key not in json.dumps(made["connections"])

        async with claude(transport, key) as c:
            assert {t.name for t in (await c.list_tools()).tools} == TOOLS

            a = await call(c, "create_animation", idea="the lost city of Ubar", length="short")
            assert a["status"] == "ready" and a["title"] == "The Lost City"
            assert a["studio_url"] == f"{BASE}/animations/?id={a['id']}"
            scenes = a["script"]["scenes"]
            assert [s["number"] for s in scenes] == list(range(1, 9))
            assert scenes[0]["shows"]["picture"] == "a desert city at dusk, view 1"
            assert scenes[0]["shows"]["painted"] is True and "player" not in a
            assert a["next"].startswith("Ready to review")

            waited = await call(c, "wait_for_animation", animation_id=a["id"], max_seconds=1)
            assert waited["still_working"] is False and waited["status"] == "ready"
            listed = await call(c, "list_animations")
            assert [x["id"] for x in listed["animations"]] == [a["id"]]  # not riya's

            changed = await call(c, "change_animation", animation_id=a["id"], request="darker")
            assert changed["can_undo"] and "darker" in site.writer.asked[-1][-1]["content"]
            site.writer.replies.insert(
                0,
                {
                    "narration": "Nobody believed it.",
                    "visual": {"kind": "quote", "text": "Nobody believed it.", "by": "A scholar"},
                },
            )
            redone = await call(c, "change_animation", animation_id=a["id"], scene_number=3)
            assert redone["script"]["scenes"][2]["shows"]["kind"] == "quote"

            paper = await call(
                c, "set_style_and_voice", animation_id=a["id"], style="paper", voice="calm"
            )
            assert paper["script"]["style"] == "paper" and paper["script"]["voice"] == "af_heart"

            before = await call(c, "get_animation", animation_id=a["id"], include_raw_plan=True)
            images = [s["visual"]["image"] for s in before["plan"]["scenes"]]
            await call(c, "repaint_pictures", animation_id=a["id"], scene_numbers=[1])
            after = await call(c, "get_animation", animation_id=a["id"], include_raw_plan=True)
            again = [s["visual"]["image"] for s in after["plan"]["scenes"]]
            assert again[0] != images[0] and again[1:] == images[1:]

            plan = after["plan"]
            plan["scenes"][0]["narration"] = "This city vanished without a trace."
            edited = await call(c, "edit_animation_plan", animation_id=a["id"], plan=plan)
            assert edited["script"]["scenes"][0]["narration"].startswith("This city vanished")
            plan["scenes"] = "none"
            assert "valid" in await refused(
                c, "edit_animation_plan", animation_id=a["id"], plan=plan
            )
            undone = await call(c, "undo_last_change", animation_id=a["id"])
            assert (
                undone["script"]["scenes"][0]["narration"]
                != edited["script"]["scenes"][0]["narration"]
            )

            rendered = await call(c, "render_animation", animation_id=a["id"])
            assert rendered["render"]["status"] == "done"
            assert rendered["video_url"].startswith(f"{BASE}/api/outputs/")
            assert rendered["next"].startswith("Rendered")
            assert "only a failed draft" in await refused(
                c, "retry_animation", animation_id=a["id"]
            )

            # The links open for the person signed in to the site.
            video = await web.get(rendered["video_url"].removeprefix(BASE))
            assert video.status_code == 200 and video.content == b"fake video"

            # Only the account's own reels, though the admin here sees riya's on the web.
            web_list = (await web.get("/api/animations")).json()
            assert hers in {x["id"] for x in web_list}
            assert "no such animation" in await refused(c, "get_animation", animation_id=hers)
            assert "no such animation" in await refused(
                c, "change_animation", animation_id=hers, request="x"
            )
            # Posting is off unless the person said so.
            assert "posting is off" in await refused(
                c, "post_to_instagram", animation_id=a["id"], page="x", confirm=True
            )
            assert "posting is off" in await refused(c, "list_instagram_pages")
            assert await refused(c, "create_animation", idea="x")  # too short

        with site.sessions() as db:
            row = db.scalar(select(McpToken))
            assert row.token_hash == mcp_access.digest(key) and key not in json.dumps(
                {"h": row.token_hash, "t": row.tail}
            )
            assert row.last_used_at is not None

    run(site, scenario)


def test_the_newer_protocol_finds_the_caller_too(site):
    async def scenario(web, transport):
        async with claude(transport, await make_key(web), mode="auto") as c:
            assert (await call(c, "list_animations")) == {"animations": []}
            voices = (await call(c, "list_voices"))["voices"]
            assert {"id": "af_heart", "name": "Heart", "group": "calm"} | {
                "about": "warm, calm American woman"
            } in voices

    run(site, scenario)


def test_without_a_good_key_the_endpoint_points_to_the_sign_in(site):
    async def scenario(web, transport):
        bare = await web.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        where = f"{BASE}/.well-known/oauth-protected-resource/mcp"
        assert bare.status_code == 401
        assert bare.headers["www-authenticate"] == f'Bearer resource_metadata="{where}"'
        bad = await web.post("/mcp", json={}, headers={"Authorization": "Bearer aie_nope"})
        assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]

        resource = (await web.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert resource["resource"] == f"{BASE}/mcp" and resource["authorization_servers"] == [BASE]
        server = (await web.get("/.well-known/oauth-authorization-server")).json()
        assert server["issuer"] == BASE  # exactly the address given, no trailing slash
        assert server["authorization_endpoint"] == f"{BASE}/authorize"
        assert server["token_endpoint"] == f"{BASE}/token"
        assert server["registration_endpoint"] == f"{BASE}/register"
        assert server["code_challenge_methods_supported"] == ["S256"]
        assert "none" in server["token_endpoint_auth_methods_supported"]

    run(site, scenario)


def test_many_unknown_keys_from_one_address_have_to_wait(site):
    now = [1000.0]
    site.app.state.mcp_guard.clock = lambda: now[0]

    async def scenario(web, transport):
        guess = {"Authorization": "Bearer aie_guess"}
        for _ in range(mcp_access.TokenGuard.FREE_TRIES):
            assert (await web.post("/mcp", json={}, headers=guess)).status_code == 401
        waits = await web.post("/mcp", json={}, headers=guess)
        assert waits.status_code == 429 and int(waits.headers["retry-after"]) > 0
        now[0] += 3600
        assert (await web.post("/mcp", json={}, headers=guess)).status_code == 401

    run(site, scenario)


def test_a_connection_needs_the_studio_and_ends_with_a_new_password(site):
    async def scenario(web, transport):
        key = await make_key(web)
        with site.sessions() as db:
            db.merge(Setting(key="feature:animation_studio", value=json.dumps(["riya"])))
            db.commit()
        assert (await web.post("/api/mcp/keys", json={})).status_code == 403
        assert (await web.get("/api/mcp/connections")).status_code == 403
        off = await web.post("/mcp", json={}, headers={"Authorization": f"Bearer {key}"})
        assert off.status_code == 403
        with site.sessions() as db:
            db.merge(Setting(key="feature:animation_studio", value=json.dumps(["admin"])))
            db.commit()
        async with claude(transport, key) as c:
            await call(c, "list_animations")
        with site.sessions() as db:
            accounts.set_password(db, db.get(User, site.ids.admin), "another-pass-123")
        gone = await web.post("/mcp", json={}, headers={"Authorization": f"Bearer {key}"})
        assert gone.status_code == 401

    run(site, scenario)


def test_connections_are_each_accounts_own_and_go_with_it(site):
    async def scenario(web, transport):
        mine = (await web.post("/api/mcp/keys", json={"label": "admin's"})).json()
        mine_id = mine["connections"][0]["id"]
        async with httpx2.AsyncClient(transport=transport, base_url=BASE) as riya:
            login = {"username": "riya", "password": "riya-pass-123"}
            assert (await riya.post("/api/login", json=login)).is_success
            hers = (await riya.post("/api/mcp/keys", json={"label": "riya's"})).json()
            assert [c["label"] for c in hers["connections"]] == ["riya's"]
            assert (await riya.delete(f"/api/mcp/connections/{mine_id}")).status_code == 404
            patched = await riya.patch(f"/api/mcp/connections/{mine_id}", json={"posting": True})
            assert patched.status_code == 404
            # Her key sees her reels only.
            other_animation(site)
            async with claude(transport, hers["key"]) as c:
                assert [a["id"] for a in (await call(c, "list_animations"))["animations"]] == [
                    "hers1"
                ]
        listed = (await web.get("/api/mcp/connections")).json()["connections"]
        assert [c["label"] for c in listed] == ["admin's"]
        with site.sessions() as db:
            accounts.remove(db, db.get(User, site.ids.riya), site.settings.presets_dir)
            assert [r.label for r in db.scalars(select(McpToken))] == ["admin's"]
        assert (await web.delete(f"/api/mcp/connections/{mine_id}")).json() == {"ok": True}
        assert (await web.get("/api/mcp/connections")).json()["connections"] == []

    run(site, scenario)


def test_reels_being_written_at_once_are_capped(site, monkeypatch):
    async def scenario(web, transport):
        async with claude(transport, await make_key(web)) as c:
            ready = await call(c, "create_animation", idea="the lost city of Ubar")
            monkeypatch.setattr(jobs, "animate", lambda animation_id: None)  # they stay queued
            for n in range(mcp_tools.MAX_WORKING):
                made = await call(c, "create_animation", idea=f"lost city number {n}")
                assert made["status"] == "queued" and made["next"].startswith("Still working")
            assert "being written already" in await refused(
                c, "create_animation", idea="one more lost city"
            )
            # Any other work waits too: changes, voices, edits, undo, retry.
            assert "being written already" in await refused(
                c, "change_animation", animation_id=ready["id"], request="darker"
            )
            assert "being written already" in await refused(
                c, "set_style_and_voice", animation_id=ready["id"], voice="calm"
            )
            busy = (await call(c, "list_animations"))["animations"][0]
            assert "isn't ready" in await refused(c, "render_animation", animation_id=busy["id"])

    run(site, scenario)


class Store:
    """Where an MCP client keeps its OAuth client and tokens."""

    tokens = client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client):
        self.client = client


def oauth_client(transport, web, store, name="Claude", answer=None):
    """An MCP client that connects through OAuth like claude.ai: it registers, the person's
    browser (`web`, signed in) is sent to the page asking them and answers there."""
    answered = {}

    async def browser(url: str) -> None:
        sent = await web.get(url.removeprefix(BASE))
        assert sent.status_code == 302
        place = urlparse(sent.headers["location"])
        assert place.path == "/connect/"
        ask = parse_qs(place.query)["request"][0]
        shown = (await web.get(f"/api/mcp/requests/{ask}")).json()
        assert shown == {
            "app": name, "goes_to": "claude.ai", "known": True, "account": "admin", "studio": True
        }  # fmt: skip
        reply = (await web.post(f"/api/mcp/requests/{ask}", json=answer or {"allow": True})).json()
        back = urlparse(reply["redirect"])
        assert (
            f"{back.scheme}://{back.netloc}{back.path}" == "https://claude.ai/api/mcp/auth_callback"
        )
        query = parse_qs(back.query)
        answered["query"] = query
        answered["code"] = AuthorizationCodeResult(
            code=query.get("code", [""])[0], state=query.get("state", [None])[0]
        )

    async def callback() -> AuthorizationCodeResult:
        return answered["code"]

    auth = OAuthClientProvider(
        f"{BASE}/mcp",
        OAuthClientMetadata(
            client_name=name,
            redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        store,
        browser,
        callback,
    )
    return httpx2.AsyncClient(transport=transport, base_url=BASE, auth=auth), answered


def test_claude_ai_connects_through_the_sign_in_page(site):
    async def scenario(web, transport):
        store = Store()
        http, _ = oauth_client(transport, web, store)
        async with (
            http,
            Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as c,
        ):
            assert (await call(c, "list_animations")) == {"animations": []}
            assert "posting is off" in await refused(c, "list_instagram_pages")
        (connection,) = (await web.get("/api/mcp/connections")).json()["connections"]
        assert connection["kind"] == "oauth" and connection["app"] == "Claude"
        assert not connection["posting"]
        token = store.tokens
        assert token.access_token.startswith("aie_") and token.refresh_token

        # The app refreshes its token: a new pair, and the old ones stop working.
        client = store.client
        fresh = await web.post(
            "/token",
            data={
                "grant_type": "refresh_token", "refresh_token": token.refresh_token,
                "client_id": client.client_id, "client_secret": client.client_secret,
            },
        )  # fmt: skip
        assert fresh.status_code == 200, fresh.text
        new = fresh.json()
        assert new["access_token"] != token.access_token
        old = await web.post(
            "/mcp", json={}, headers={"Authorization": f"Bearer {token.access_token}"}
        )
        assert old.status_code == 401
        async with claude(transport, new["access_token"]) as c:
            await call(c, "list_animations")
        again = await web.post(
            "/token",
            data={
                "grant_type": "refresh_token", "refresh_token": token.refresh_token,
                "client_id": client.client_id, "client_secret": client.client_secret,
            },
        )  # fmt: skip
        assert again.status_code == 400

        # An access token runs out (the app refreshes it); disconnecting ends it for good.
        with site.sessions() as db:
            row = db.scalar(select(McpToken))
            row.expires_at = row.expires_at - timedelta(days=2)
            db.commit()
        late = await web.post(
            "/mcp", json={}, headers={"Authorization": f"Bearer {new['access_token']}"}
        )
        assert late.status_code == 401
        await web.delete(f"/api/mcp/connections/{connection['id']}")
        with site.sessions() as db:
            assert db.scalar(select(McpToken)) is None
            assert db.get(McpClient, client.client_id) is not None

    run(site, scenario)


def test_saying_no_on_the_sign_in_page_sends_the_app_away_empty_handed(site):
    async def scenario(web, transport):
        http, answered = oauth_client(transport, web, Store(), answer={"allow": False})
        with pytest.raises(BaseException):  # noqa: B017 - the client gives up its connection
            async with (
                http,
                Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy"),
            ):
                pass
        assert answered["query"]["error"] == ["access_denied"]
        assert "code" not in answered["query"]
        with site.sessions() as db:
            assert db.scalar(select(McpToken)) is None

    run(site, scenario)


def test_the_sign_in_page_needs_the_person_signed_in_and_a_live_request(site):
    async def scenario(web, transport):
        assert (await web.get("/api/mcp/requests/nothing")).status_code == 404
        await web.post("/api/logout")
        assert (await web.get("/api/mcp/requests/nothing")).status_code == 401
        answer = await web.post("/api/mcp/requests/nothing", json={"allow": True})
        assert answer.status_code == 401

    run(site, scenario)


def test_posting_needs_the_persons_leave_and_a_confirmation(site, monkeypatch):
    sent = []

    def post_reel(channel, video_url, caption, share_to_feed):
        sent.append((channel.username, video_url, caption))
        return "post-1"

    monkeypatch.setattr(instagram, "post_reel", post_reel)
    with site.sessions() as db:
        db.add_all([
            InstagramChannel(
                id="ig1", owner_id=site.ids.admin, username="lostcities", access_token="t"
            ),
            InstagramChannel(id="ig2", owner_id=site.ids.riya, username="riyas", access_token="t"),
        ])  # fmt: skip
        db.commit()

    # Instagram fetches the video from the public address; Claude's links and sign-in use
    # the address it reached the site at.
    site.settings.public_url = "https://public.example"

    async def scenario(web, transport):
        server = (await web.get("/.well-known/oauth-authorization-server")).json()
        assert server["issuer"] == BASE
        made = (await web.post("/api/mcp/keys", json={"posting": True})).json()
        assert made["url"] == f"{BASE}/mcp"
        async with claude(transport, made["key"]) as c:
            a = await call(c, "create_animation", idea="the lost city of Ubar")
            assert "render it first" in await refused(
                c, "post_to_instagram", animation_id=a["id"], page="lostcities", confirm=True
            )
            await call(c, "render_animation", animation_id=a["id"])
            pages = await call(c, "list_instagram_pages")
            assert pages == {"pages": [{"id": "ig1", "username": "lostcities"}]}
            assert "confirm=true" in await refused(
                c, "post_to_instagram", animation_id=a["id"], page="lostcities"
            )
            assert "no Instagram page" in await refused(
                c, "post_to_instagram", animation_id=a["id"], page="riyas", confirm=True
            )
            assert sent == []
            posted = await call(
                c, "post_to_instagram", animation_id=a["id"], page="@LostCities", confirm=True
            )
            assert posted == {"posted": True, "post_id": "post-1", "channel": "lostcities"}
            ((page, link, caption),) = sent
            assert page == "lostcities" and link.startswith("https://public.example/api/pub/")
            assert caption == "A city swallowed by sand. #history"

            # The person turns posting off again on the Animations tab.
            key_id = made["connections"][0]["id"]
            await web.patch(f"/api/mcp/connections/{key_id}", json={"posting": False})
            assert "posting is off" in await refused(
                c, "post_to_instagram", animation_id=a["id"], page="lostcities", confirm=True
            )
            assert "posting is off" in await refused(
                c, "approve_autopilot_reel", animation_id=a["id"], confirm=True
            )

    run(site, scenario)


def test_an_autopilot_reel_is_approved_only_when_confirmed(site, monkeypatch):
    async def scenario(web, transport):
        async with claude(transport, await make_key(web, posting=True)) as c:
            a = await call(c, "create_animation", idea="the lost city of Ubar")
            assert "confirm=true" in await refused(
                c, "approve_autopilot_reel", animation_id=a["id"]
            )
            assert "waiting for approval" in await refused(
                c, "approve_autopilot_reel", animation_id=a["id"], confirm=True
            )
            assert "waiting for approval" in await refused(
                c, "skip_autopilot_reel", animation_id=a["id"]
            )

    run(site, scenario)


def test_over_stdio_it_acts_as_the_account_named_and_nobody_else(site):
    tools = site.app.state.mcp_tools
    other_animation(site)

    async def main():
        async with Client(tools.server) as c:  # in-process: no HTTP request, as over stdio
            assert "not signed in" in await refused(c, "list_animations")
            tools.local = mcp_access.Caller(site.ids.riya, None, False)
            listed = await call(c, "list_animations")
            assert [a["id"] for a in listed["animations"]] == ["hers1"]
            assert (
                listed["animations"][0]["studio_url"]
                == "http://localhost:8000/animations/?id=hers1"
            )

    anyio.run(main)


def test_aiedit_mcp_serves_the_tools_over_stdio(site, tmp_path):
    other_animation(site)
    env = {
        **os.environ,
        "DATABASE": str(site.settings.database),
        "QUEUE_DATABASE": str(tmp_path / "queue.db"),
        "MEDIA_DIR": str(site.settings.media_dir),
        "CACHE_DIR": str(site.settings.cache_dir),
        "PRESETS_DIR": str(site.settings.presets_dir),
        "PASSWORD_HASH": site.settings.password_hash,
    }
    command = [sys.executable, "-m", "agentic_editor.cli", "mcp", "--user"]
    refused_run = subprocess.run(
        [*command, "nobody"], env=env, capture_output=True, text=True, timeout=120
    )
    assert refused_run.returncode == 2 and "no account called nobody" in refused_run.stderr

    async def main():
        server = StdioServerParameters(command=command[0], args=[*command[1:], "riya"], env=env)
        async with Client(stdio_client(server), mode="legacy") as c:
            listed = await call(c, "list_animations")
            assert [a["id"] for a in listed["animations"]] == ["hers1"]
            made = await call(c, "create_animation", idea="a lesson about patience")
            assert made["status"] == "queued"  # for the server's worker

    anyio.run(main)
    with site.sessions() as db:
        assert db.get(Animation, made_id(db)).owner_id == site.ids.riya


def made_id(db) -> str:
    return db.scalar(select(Animation.id).where(Animation.id != "hers1"))


def register(web, **fields):
    """An app registering itself, as claude.ai does (dynamic client registration)."""
    body = {
        "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
    } | fields  # fmt: skip
    return web.post("/register", json=body)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


async def ask_to_connect(web, client, **extra) -> tuple[str, str]:
    """The app's /authorize: the request the page asks about, and the PKCE verifier."""
    verifier, challenge = pkce()
    query = {
        "response_type": "code", "client_id": client["client_id"], "state": "s1",
        "redirect_uri": client["redirect_uris"][0], "code_challenge": challenge,
        "code_challenge_method": "S256",
    } | extra  # fmt: skip
    sent = await web.get("/authorize", params=query)
    assert sent.status_code == 302, sent.text
    return parse_qs(urlparse(sent.headers["location"]).query)["request"][0], verifier


async def trade(web, client, code, verifier):
    return await web.post(
        "/token",
        data={
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": client["redirect_uris"][0], "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )  # fmt: skip


def test_an_app_may_only_be_sent_back_to_a_web_address(site):
    async def scenario(web, transport):
        for bad in (
            "javascript://claude.ai/%0afetch('/api/mcp/keys')//",
            "data:text/html,hi",
            "http://evil.example/callback",
            "https://claude.ai@evil.example/callback",
        ):
            refused = await register(web, redirect_uris=[bad])
            assert refused.status_code == 400, bad
            assert refused.json()["error"] == "invalid_redirect_uri"
        too_many = [f"https://claude.ai/cb{n}" for n in range(mcp_access.MAX_REDIRECTS + 1)]
        assert (await register(web, redirect_uris=too_many)).status_code == 400
        local = await register(web, redirect_uris=["http://localhost:4567/callback"])
        assert local.status_code == 201
        with site.sessions() as db:
            assert [c.info.get("redirect_uris") for c in db.scalars(select(McpClient))] == [
                ["http://localhost:4567/callback"]
            ]

        # An app that isn't Claude is named as such on the page.
        other = (
            await register(web, client_name="Other", redirect_uris=["https://x.example/cb"])
        ).json()
        ask, _ = await ask_to_connect(web, other)
        shown = (await web.get(f"/api/mcp/requests/{ask}")).json()
        assert shown["goes_to"] == "x.example" and shown["known"] is False

        page = await web.get("/connect/")
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["content-security-policy"] == "frame-ancestors 'none'"

    run(site, scenario)


def test_a_request_is_answered_once_and_a_code_dies_with_a_new_password(site):
    async def scenario(web, transport):
        client = (await register(web)).json()
        ask, verifier = await ask_to_connect(web, client, scope="animations")
        allowed = (await web.post(f"/api/mcp/requests/{ask}", json={"allow": True})).json()
        again = await web.post(f"/api/mcp/requests/{ask}", json={"allow": True})
        assert again.status_code == 404
        code = parse_qs(urlparse(allowed["redirect"]).query)["code"][0]
        with site.sessions() as db:
            accounts.set_password(db, db.get(User, site.ids.admin), "another-pass-123")
        late = await trade(web, client, code, verifier)
        assert late.status_code == 400 and late.json()["error"] == "invalid_grant"

    run(site, scenario)


def test_tokens_carry_only_the_one_scope_and_expired_ones_still_revoke(site):
    async def scenario(web, transport):
        client = (await register(web, scope="animations everything")).json()
        ask, verifier = await ask_to_connect(web, client, scope="animations everything")
        allowed = (await web.post(f"/api/mcp/requests/{ask}", json={"allow": True})).json()
        code = parse_qs(urlparse(allowed["redirect"]).query)["code"][0]
        tokens = (await trade(web, client, code, verifier)).json()
        assert tokens["scope"] == "animations"
        with site.sessions() as db:
            row = db.scalar(select(McpToken))
            row.expires_at = row.expires_at - timedelta(days=2)
            db.commit()
        revoked = await web.post(
            "/revoke",
            data={
                "token": tokens["access_token"], "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )  # fmt: skip
        assert revoked.status_code == 200
        with site.sessions() as db:
            assert db.scalar(select(McpToken)) is None

    run(site, scenario)


def test_a_connection_unused_for_long_has_to_sign_in_again(site):
    async def scenario(web, transport):
        client = (await register(web)).json()
        ask, verifier = await ask_to_connect(web, client)
        allowed = (await web.post(f"/api/mcp/requests/{ask}", json={"allow": True})).json()
        code = parse_qs(urlparse(allowed["redirect"]).query)["code"][0]
        tokens = (await trade(web, client, code, verifier)).json()
        with site.sessions() as db:
            row = db.scalar(select(McpToken))
            row.expires_at = row.expires_at - timedelta(seconds=mcp_access.REFRESH_SECONDS)
            db.commit()
        stale = await web.post(
            "/token",
            data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": client["client_id"], "client_secret": client["client_secret"],
            },
        )  # fmt: skip
        assert stale.status_code == 400

    run(site, scenario)


def test_registering_and_asking_are_limited_per_address(site, monkeypatch):
    async def scenario(web, transport):
        for _ in range(mcp_access.REGISTRATIONS_PER_HOUR):
            assert (await register(web)).status_code == 201
        assert (await register(web)).status_code == 429
        with site.sessions() as db:
            client = db.scalar(select(McpClient)).info
        _, challenge = pkce()
        query = {
            "response_type": "code", "client_id": client["client_id"], "state": "s" * 600,
            "redirect_uri": client["redirect_uris"][0], "code_challenge": challenge,
        }  # fmt: skip
        too_long = await web.get("/authorize", params=query)
        assert too_long.status_code == 302
        assert "error=invalid_request" in too_long.headers["location"]

    run(site, scenario)


def test_expired_tokens_from_a_shared_address_never_lock_out_good_ones(site):
    now = [1000.0]
    site.app.state.mcp_guard.clock = lambda: now[0]

    async def scenario(web, transport):
        client = (await register(web)).json()
        ask, verifier = await ask_to_connect(web, client)
        allowed = (await web.post(f"/api/mcp/requests/{ask}", json={"allow": True})).json()
        code = parse_qs(urlparse(allowed["redirect"]).query)["code"][0]
        expired = (await trade(web, client, code, verifier)).json()["access_token"]
        with site.sessions() as db:
            row = db.scalar(select(McpToken))
            row.expires_at = row.expires_at - timedelta(days=2)
            db.commit()
        for _ in range(mcp_access.TokenGuard.FREE_TRIES + 5):
            late = await web.post("/mcp", json={}, headers={"Authorization": f"Bearer {expired}"})
            assert late.status_code == 401
        key = await make_key(web)
        async with claude(transport, key) as c:
            await call(c, "list_animations")
        # Unknown keys do count; then even a good key from there is let in.
        for _ in range(mcp_access.TokenGuard.FREE_TRIES):
            await web.post("/mcp", json={}, headers={"Authorization": "Bearer aie_guess"})
        guessing = await web.post("/mcp", json={}, headers={"Authorization": "Bearer aie_guess"})
        assert guessing.status_code == 429
        async with claude(transport, key) as c:
            await call(c, "list_animations")

    run(site, scenario)


def test_a_new_password_removes_every_connection(site):
    async def scenario(web, transport):
        await make_key(web)
        await make_key(web)
        with site.sessions() as db:
            accounts.set_password(db, db.get(User, site.ids.admin), "another-pass-123")
            assert db.scalar(select(McpToken)) is None

    run(site, scenario)
