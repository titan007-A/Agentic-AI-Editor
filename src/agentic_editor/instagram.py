"""Instagram through Meta's "Instagram API with Unified Login" (graph.facebook.com).

The person connecting logs in with Instagram or with Facebook, picks the business portfolio
that owns their Instagram professional account and the accounts to share, and approves the
permissions of the app's login configuration (META_CONFIG_ID, made in the App Dashboard
under Use cases > Manage messaging & content on Instagram > API setup with Unified login;
it must be one marked "Instagram Login Compatible"):

  1. /auth/instagram  -> www.facebook.com/dialog/oauth?client_id&config_id&prompt=login...
     (prompt=login always offers the Instagram login: without it someone already signed in
     to Facebook goes straight on as that Facebook account)
  2. callback code    -> GET graph.facebook.com/<v>/oauth/access_token with the META app ID
     and secret: a Business Integration System User access token, which belongs to the
     client's portfolio and covers every Instagram account they shared
  3. /me/assigned_instagram_accounts -> the accounts; /me?fields=client_business_id
  4. /debug_token     -> when the token expires (never, for some configurations)

Every call then goes to graph.facebook.com with that token and the Instagram account's ID:
posting a reel (/<ig>/media, /<container>?fields=status_code, /<ig>/media_publish), the
account's posts (/<ig>/media), their comments (/<media>/comments) and replies
(/<comment>/replies). Without App Review (Standard Access) only people with a role on the
app (admins, developers, Instagram Testers) can connect; others need Advanced Access.

The Instagram-Login-only flow (api.instagram.com, graph.instagram.com) is not used: it
needs its own Instagram app ID, which an app that already exists can't get any more, and
its tokens don't work on graph.facebook.com (mixing the two was why connecting failed)."""

import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
DIALOG = "https://www.facebook.com/dialog/oauth"
VERSION = "v27.0"
TIMEOUT = 20.0
# Poll a reel's container every N seconds, up to MAX_WAIT.
POLL_INTERVAL = 10
MAX_WAIT = 360
# Set by tests to answer requests without the network.
TRANSPORT: httpx.BaseTransport | None = None


class InstagramError(RuntimeError):
    pass


@dataclass
class Channel:
    id: str  # Instagram professional account ID
    username: str
    name: str = ""
    avatar: str | None = None
    # The Business Integration System User access token that covers it.
    access_token: str = ""
    expires_at: datetime | None = None  # None: doesn't expire


@dataclass
class Connection:
    """What one login gave: the token, the client's portfolio, the accounts it covers."""

    token: str
    expires_at: datetime | None
    business_id: str
    channels: list[Channel] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def auth_url(app_id: str, redirect_uri: str, state: str, config_id: str) -> str:
    """The Unified Login page: Instagram or Facebook login, then the portfolio and the
    accounts to share."""
    if not config_id:
        raise InstagramError(
            "META_CONFIG_ID is missing: set it to the Unified Login configuration's ID"
        )
    params = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "config_id": config_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "prompt": "login",
        }
    )
    return f"{DIALOG}?{params}"


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """The login's code -> a Business Integration System User access token."""
    data = _get(
        "oauth/access_token",
        client_id=app_id,
        client_secret=app_secret,
        redirect_uri=redirect_uri,
        code=code,
    )
    return data["access_token"]


def token_expiry(app_id: str, app_secret: str, token: str) -> datetime | None:
    """When a token stops working; None when it doesn't expire (or Meta won't say)."""
    try:
        data = _get("debug_token", input_token=token, access_token=f"{app_id}|{app_secret}")
    except InstagramError as exc:
        log.warning("couldn't ask when the Instagram token expires: %s", exc)
        return None
    expires = int(data.get("data", {}).get("expires_at") or 0)
    return datetime.fromtimestamp(expires, UTC).replace(tzinfo=None) if expires else None


def connect(app_id: str, app_secret: str, redirect_uri: str, code: str) -> Connection:
    """Everything a finished login gives: token, expiry, portfolio and shared accounts."""
    token = exchange_code(app_id, app_secret, redirect_uri, code)
    business = _get("me", fields="client_business_id", access_token=token)
    found = Connection(
        token=token,
        expires_at=token_expiry(app_id, app_secret, token),
        business_id=str(business.get("client_business_id") or ""),
    )
    for account in _pages("me/assigned_instagram_accounts", token, fields="id,username"):
        about = _get(account["id"], fields="username,name,profile_picture_url", access_token=token)
        found.channels.append(
            Channel(
                id=str(account["id"]),
                username=about.get("username") or account.get("username") or account["id"],
                name=about.get("name", ""),
                avatar=about.get("profile_picture_url"),
                access_token=token,
                expires_at=found.expires_at,
            )
        )
    return found


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def post_reel(
    channel: Channel,
    video_url: str,
    caption: str = "",
    share_to_feed: bool = True,
) -> str:
    """Post a reel and return its Instagram post ID."""
    container_id = _post(
        f"{channel.id}/media",
        media_type="REELS",
        video_url=video_url,
        caption=caption,
        share_to_feed="true" if share_to_feed else "false",
        access_token=channel.access_token,
    )["id"]
    _wait_for_container(channel, container_id)
    return _post(
        f"{channel.id}/media_publish", creation_id=container_id, access_token=channel.access_token
    )["id"]


def _wait_for_container(channel: Channel, container_id: str) -> None:
    deadline = time.monotonic() + MAX_WAIT
    while time.monotonic() < deadline:
        data = _get(container_id, fields="status_code,status", access_token=channel.access_token)
        status = data.get("status_code", "")
        log.debug("container %s status: %s", container_id, status)
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise InstagramError(f"Instagram rejected the video: {data.get('status', '?')}")
        time.sleep(POLL_INTERVAL)
    raise InstagramError(f"video processing timed out after {MAX_WAIT} s")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@dataclass
class Comment:
    id: str
    text: str
    username: str = ""
    timestamp: str = ""
    post_id: str = ""
    # Who already replied to it (usernames), as Instagram lists the replies.
    replied_by: list[str] = field(default_factory=list)


def get_recent_posts(channel: Channel, limit: int = 10) -> list[dict]:
    """The account's newest posts: id, caption, permalink, timestamp, comments_count."""
    data = _get(
        f"{channel.id}/media",
        fields="id,caption,media_type,timestamp,permalink,comments_count",
        limit=limit,
        access_token=channel.access_token,
    )
    return data.get("data", [])


def get_comments(channel: Channel, post_id: str, limit: int = 50) -> list[Comment]:
    """A post's top-level comments, newest first, each with who replied to it."""
    data = _get(
        f"{post_id}/comments",
        fields="id,text,username,timestamp,replies{username}",
        limit=limit,
        access_token=channel.access_token,
    )
    return [
        Comment(
            id=str(c["id"]),
            text=c.get("text", ""),
            username=c.get("username", ""),
            timestamp=c.get("timestamp", ""),
            post_id=post_id,
            replied_by=[r.get("username", "") for r in c.get("replies", {}).get("data", [])],
        )
        for c in data.get("data", [])
    ]


def reply_to_comment(channel: Channel, comment_id: str, message: str) -> str:
    """Reply to a comment, as the account. Returns the reply's ID."""
    reply = _post(f"{comment_id}/replies", message=message, access_token=channel.access_token)
    return reply["id"]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=TRANSPORT)


def _url(path: str) -> str:
    return f"{GRAPH}/{VERSION}/{path.lstrip('/')}"


def _get(path: str, **params) -> dict:
    with _client() as client:
        resp = client.get(_url(path), params=params)
    _raise(resp)
    return resp.json()


def _post(path: str, **data) -> dict:
    with _client() as client:
        resp = client.post(_url(path), data=data)
    _raise(resp)
    return resp.json()


def _pages(path: str, token: str, **params) -> list[dict]:
    """Every item of a listing, following its "next" links (up to 20 pages)."""
    items: list[dict] = []
    data = _get(path, access_token=token, **params)
    for _ in range(20):
        items += data.get("data", [])
        following = data.get("paging", {}).get("next")
        if not following:
            break
        with _client() as client:
            resp = client.get(following)
        _raise(resp)
        data = resp.json()
    return items


def _raise(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or resp.text[:200]
        code = err.get("code")
    except Exception:
        msg, code = resp.text[:200], None
    where = f" (#{code})" if code else ""
    raise InstagramError(f"Instagram API {resp.status_code}{where}: {msg}")
