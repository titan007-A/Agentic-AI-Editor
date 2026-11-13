"""Direct Instagram posting via the Instagram Platform API (graph.instagram.com).

OAuth flow:
  1. /auth/instagram  -> redirect to api.instagram.com/oauth/authorize
  2. callback code    -> short-lived token (api.instagram.com/oauth/access_token)
  3. short-lived      -> long-lived token, 60 days (graph.instagram.com/access_token)
  4. long-lived       -> refresh before 60 days (graph.instagram.com/refresh_access_token)

Posting a reel:
  1. POST  /{ig_user_id}/media          -> container id
  2. GET   /{container_id}?status_code  -> poll until FINISHED (up to ~5 min)
  3. POST  /{ig_user_id}/media_publish  -> live post id
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.instagram.com"
AUTH = "https://api.instagram.com/oauth"
# Business Login for Instagram OAuth (used when app has Facebook Login for Business product).
FB_OAUTH = "https://www.facebook.com/dialog/oauth"
FB_GRAPH = "https://graph.facebook.com"
VERSION = "v21.0"
TIMEOUT = 20.0
# Poll container status every N seconds, up to MAX_WAIT.
POLL_INTERVAL = 10
MAX_WAIT = 360


class InstagramError(RuntimeError):
    pass


@dataclass
class Channel:
    id: str  # IG user ID
    username: str
    name: str = ""
    avatar: str | None = None
    # Long-lived access token (60 days).
    access_token: str = ""
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

SCOPES = ",".join(
    [
        "instagram_business_basic",
        "instagram_business_content_publish",
        "instagram_business_manage_comments",
        "instagram_business_manage_messages",
    ]
)


def auth_url(app_id: str, redirect_uri: str, state: str, config_id: str = "") -> str:
    """The URL the user visits to authorize the app on their IG account.

    With config_id: Unified Login via www.facebook.com/dialog/oauth (Meta's official flow).
    Without config_id: Instagram-native via api.instagram.com/oauth/authorize.
    """
    import urllib.parse

    if config_id:
        # Unified Login API — Meta requires Facebook dialog URL for config_id-based OAuth
        params = urllib.parse.urlencode(
            {
                "client_id": app_id,
                "config_id": config_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{FB_OAUTH}?{params}"
    else:
        params = urllib.parse.urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{AUTH}/authorize?{params}"


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Exchange the OAuth code for a short-lived Instagram token (Instagram Platform flow)."""
    resp = httpx.post(
        f"{AUTH}/access_token",
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json()["access_token"]


def long_lived_token(app_secret: str, short_token: str) -> tuple[str, datetime]:
    """Exchange a short-lived Instagram token for a long-lived one (60 days)."""
    from datetime import timedelta

    resp = httpx.get(
        f"{GRAPH}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    data = resp.json()
    expires_in = data.get("expires_in", 5183944)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in)
    return data["access_token"], expires_at


def fb_exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Exchange the OAuth code for a short-lived user token (Facebook Login for Business flow)."""
    resp = httpx.post(
        f"{FB_GRAPH}/{VERSION}/oauth/access_token",
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json()["access_token"]


def fb_long_lived_token(app_id: str, app_secret: str, short_token: str) -> tuple[str, datetime]:
    """Exchange a short-lived Facebook user token for a long-lived one (60 days)."""
    from datetime import timedelta

    resp = httpx.get(
        f"{FB_GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    data = resp.json()
    expires_in = data.get("expires_in", 5183944)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in)
    return data["access_token"], expires_at


def get_ig_channels(long_user_token: str) -> list["Channel"]:
    """Get all Instagram Business/Creator accounts linked to the user's Facebook pages."""
    resp = httpx.get(
        f"{FB_GRAPH}/{VERSION}/me/accounts",
        params={
            "fields": (
                "id,name,access_token,"
                "instagram_business_account{id,username,name,profile_picture_url}"
            ),
            "access_token": long_user_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    channels = []
    for page in resp.json().get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            channels.append(
                Channel(
                    id=ig["id"],
                    username=ig.get("username", ig["id"]),
                    name=ig.get("name", page.get("name", "")),
                    avatar=ig.get("profile_picture_url"),
                    access_token=page["access_token"],
                )
            )
    return channels


def refresh_token(token: str) -> tuple[str, datetime]:
    """Refresh a long-lived token (call before it expires)."""
    resp = httpx.get(
        f"{GRAPH}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=TIMEOUT,
    )
    _raise(resp)
    data = resp.json()
    from datetime import timedelta

    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        seconds=data.get("expires_in", 5183944)
    )
    return data["access_token"], expires_at


def get_account(token: str) -> Channel:
    """The IG account the token belongs to."""
    resp = httpx.get(
        f"{GRAPH}/me",
        params={
            "fields": "id,username,name,profile_picture_url",
            "access_token": token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    d = resp.json()
    return Channel(
        id=d["id"],
        username=d.get("username", d["id"]),
        name=d.get("name", ""),
        avatar=d.get("profile_picture_url"),
        access_token=token,
    )


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
    container_id = _create_container(channel, video_url, caption, share_to_feed)
    _wait_for_container(channel, container_id)
    return _publish(channel, container_id)


def _create_container(channel: Channel, video_url: str, caption: str, share_to_feed: bool) -> str:
    resp = httpx.post(
        f"{GRAPH}/{VERSION}/{channel.id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": channel.access_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json()["id"]


def _wait_for_container(channel: Channel, container_id: str) -> None:
    deadline = time.monotonic() + MAX_WAIT
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{GRAPH}/{VERSION}/{container_id}",
            params={"fields": "status_code,status", "access_token": channel.access_token},
            timeout=TIMEOUT,
        )
        _raise(resp)
        data = resp.json()
        status = data.get("status_code", "")
        log.debug("container %s status: %s", container_id, status)
        if status == "FINISHED":
            return
        if status == "ERROR":
            detail = data.get("status", "unknown error")
            raise InstagramError(f"Instagram rejected the video: {detail}")
        time.sleep(POLL_INTERVAL)
    raise InstagramError(f"video processing timed out after {MAX_WAIT} s")


def _publish(channel: Channel, container_id: str) -> str:
    resp = httpx.post(
        f"{GRAPH}/{VERSION}/{channel.id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": channel.access_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json()["id"]


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


def get_recent_posts(channel: Channel, limit: int = 10) -> list[dict]:
    """Recent posts for the channel."""
    resp = httpx.get(
        f"{GRAPH}/{VERSION}/{channel.id}/media",
        params={
            "fields": "id,caption,media_type,timestamp,thumbnail_url,permalink",
            "limit": limit,
            "access_token": channel.access_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json().get("data", [])


def get_comments(channel: Channel, post_id: str, limit: int = 50) -> list[Comment]:
    """Comments on a post."""
    resp = httpx.get(
        f"{GRAPH}/{VERSION}/{post_id}/comments",
        params={
            "fields": "id,text,username,timestamp",
            "limit": limit,
            "access_token": channel.access_token,
        },
        timeout=TIMEOUT,
    )
    _raise(resp)
    return [
        Comment(
            id=c["id"],
            text=c.get("text", ""),
            username=c.get("username", ""),
            timestamp=c.get("timestamp", ""),
            post_id=post_id,
        )
        for c in resp.json().get("data", [])
    ]


def reply_to_comment(channel: Channel, comment_id: str, message: str) -> str:
    """Reply to a comment. Returns the new reply's ID."""
    resp = httpx.post(
        f"{GRAPH}/{VERSION}/{comment_id}/replies",
        data={"message": message, "access_token": channel.access_token},
        timeout=TIMEOUT,
    )
    _raise(resp)
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _raise(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
        msg = err.get("message") or err.get("error_user_msg") or resp.text[:200]
    except Exception:
        msg = resp.text[:200]
    raise InstagramError(f"Instagram API {resp.status_code}: {msg}")
