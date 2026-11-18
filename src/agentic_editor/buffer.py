"""Sending finished reels to Buffer (buffer.com) through its GraphQL API, with a personal
API key from Buffer's Settings > API. Buffer takes no uploads: it fetches the video from a
public link, at publish time, so that link has to keep working until the post goes out.
https://developers.buffer.com"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

# Overridable to point tests at a stand-in.
API = os.environ.get("BUFFER_API_URL", "https://api.buffer.com")
Mode = Literal["addToQueue", "shareNext", "shareNow", "customScheduled"]

CHANNELS = """
query Channels($organizationId: OrganizationId!) {
  channels(input: { organizationId: $organizationId }) {
    id name displayName service type avatar isQueuePaused isDisconnected isLocked organizationId
  }
}"""
ORGANIZATIONS = "query Organizations { account { organizations { id name } } }"
CREATE = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}"""
POST_FIELDS = "id status dueAt sentAt text channelId error { message rawError } assets { source }"
POST = "query Post($id: PostId!) { post(input: { id: $id }) { " + POST_FIELDS + " } }"
POSTS = (
    "query Posts($input: PostsInput!, $after: String) {\n"
    "  posts(first: 50, after: $after, input: $input) {\n"
    "    pageInfo { hasNextPage endCursor }\n"
    "    edges { node { " + POST_FIELDS + " } }\n"
    "  }\n"
    "}"
)
DELETE = """
mutation DeletePost($id: PostId!) {
  deletePost(input: { id: $id }) {
    ... on DeletePostSuccess { id }
    ... on MutationError { message }
  }
}"""
# Posts still waiting for something: what a link to a video must keep serving.
PENDING = ["scheduled", "sending", "draft", "error", "needs_approval"]


# Buffer's API can't mark a reel as an Instagram trial reel: one meant as a trial goes in as
# a draft under this tag, and the person turns Trial on in Buffer's composer.
TRIAL_TAG = "Trial reel"
TRIAL_COLOR = "#8B5CF6"
TAGS = """
query Tags($organizationId: OrganizationId!) {
  tagsV2(first: 100, input: { organizationId: $organizationId }) { edges { node { id name } } }
}"""
CREATE_TAG = """
mutation CreateTag($input: CreateTagInput!) {
  createTag(input: $input) {
    ... on TagActionSuccess { tag { id } }
    ... on MutationError { message }
  }
}"""
CHANNEL_ORGANIZATION = """
query ChannelOrganization($id: ChannelId!) { channel(input: { id: $id }) { organizationId } }"""

# Buffer's words when an Instagram personal profile is asked to publish by itself.
PERSONAL = "personal profile"


class BufferError(RuntimeError):
    pass


@dataclass
class Posted:
    """A post as Buffer tells it: its state, when it goes out and, once it went wrong,
    Buffer's words (`error`) and the network's own (`raw_error`)."""

    id: str
    status: str = ""
    due_at: datetime | None = None
    sent_at: datetime | None = None
    error: str = ""
    raw_error: str = ""
    text: str = ""
    channel_id: str = ""
    # The links of its pictures or videos.
    assets: tuple[str, ...] = ()


def when(value: str | None) -> datetime | None:
    """A time from Buffer (ISO 8601, Z for UTC)."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def posted(node: dict) -> Posted:
    error = node.get("error") or {}
    return Posted(
        id=node["id"],
        status=node.get("status") or "",
        due_at=when(node.get("dueAt")),
        sent_at=when(node.get("sentAt")),
        error=error.get("message") or "",
        raw_error=error.get("rawError") or "",
        text=node.get("text") or "",
        channel_id=node.get("channelId") or "",
        assets=tuple(a.get("source") or "" for a in node.get("assets") or []),
    )


def media_problem(error: str, raw_error: str = "") -> bool:
    """Instagram (or another network) couldn't take the video itself: worth sending it
    again, unlike a disconnected account or a refused caption."""
    said = f"{error} {raw_error}".lower()
    return "media" in said or "container" in said or "video" in said


class Count:
    """How many requests went to Buffer: its limits are per Buffer account (150 per 15 min
    and 600 a day on the free plan), so the editor shows what it spent."""

    def __init__(self) -> None:
        self.n = 0


@dataclass
class Channel:
    id: str
    name: str
    service: str
    avatar: str | None
    paused: bool
    # The kind within its service: Buffer says profile, page, business and so on.
    kind: str = ""
    # Disconnected or locked (after a plan downgrade): Buffer can't post there.
    broken: bool = False
    # The Buffer organization it's in (its tags live there); empty in channels kept from
    # before it was asked for.
    organization: str = ""


def call(
    key: str,
    query: str,
    variables: dict | None = None,
    client: httpx.Client | None = None,
    count: Count | None = None,
) -> dict:
    http = client or httpx.Client(timeout=30)
    if count is not None:
        count.n += 1
    try:
        response = http.post(
            API,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError as exc:
        raise BufferError(f"Buffer can't be reached: {exc}") from exc
    finally:
        if client is None:
            http.close()
    if response.status_code == 401:
        raise BufferError("Buffer refused the API key: it may be wrong or expired")
    if response.status_code == 429:
        wait = response.headers.get("Retry-After", "a while")
        raise BufferError(f"Buffer is limiting requests; try again in {wait} s")
    try:
        body = response.json()
    except ValueError as exc:
        raise BufferError(f"Buffer answered {response.status_code} without data") from exc
    if body.get("errors"):
        raise BufferError("; ".join(e.get("message", "unknown error") for e in body["errors"]))
    if response.status_code >= 400 or "data" not in body:
        raise BufferError(f"Buffer answered {response.status_code}")
    return body["data"]


def channels(
    key: str, client: httpx.Client | None = None, count: Count | None = None
) -> list[Channel]:
    """Every channel in every organization the key's account belongs to."""
    found = []
    organizations = call(key, ORGANIZATIONS, client=client, count=count)["account"]["organizations"]
    for organization in organizations:
        data = call(
            key, CHANNELS, {"organizationId": organization["id"]}, client=client, count=count
        )
        for c in data["channels"]:
            found.append(
                Channel(
                    id=c["id"],
                    name=c.get("displayName") or c.get("name") or c["id"],
                    service=c.get("service") or "",
                    avatar=c.get("avatar"),
                    paused=bool(c.get("isQueuePaused")),
                    kind=c.get("type") or "",
                    broken=bool(c.get("isDisconnected") or c.get("isLocked")),
                    organization=c.get("organizationId") or organization["id"],
                )
            )
    return found


def post_input(
    channel: Channel,
    video_url: str,
    text: str,
    mode: Mode,
    due_at: datetime | None = None,
    first_comment: str = "",
    draft: bool = False,
    tags: list[str] = (),
) -> dict:
    """createPost's input. A `draft` is kept in Buffer's drafts, not scheduled."""
    data: dict = {
        "channelId": channel.id,
        "text": text,
        "schedulingType": "automatic",
        "mode": mode,
        "needsApproval": False,
        "assets": [{"video": {"url": video_url}}],
    }
    if mode == "customScheduled":
        if due_at is None:
            raise BufferError("pick a time to post it")
        data["dueAt"] = due_at.isoformat().replace("+00:00", "Z")
    if channel.service == "instagram":
        instagram: dict = {"type": "reel", "shouldShareToFeed": True}
        if first_comment.strip():
            instagram["firstComment"] = first_comment.strip()
        data["metadata"] = {"instagram": instagram}
    if draft:
        data["saveToDraft"] = True
    if tags:
        data["tagIds"] = list(tags)
    return data


def trial_tag(
    key: str, channel: Channel, client: httpx.Client | None = None, count: Count | None = None
) -> str:
    """The id of the TRIAL_TAG tag in the channel's organization, made the first time."""
    organization = channel.organization
    if not organization:
        found = call(key, CHANNEL_ORGANIZATION, {"id": channel.id}, client=client, count=count)
        organization = (found.get("channel") or {}).get("organizationId") or ""
    if not organization:
        raise BufferError("Buffer didn't say which organization the channel is in")
    edges = call(key, TAGS, {"organizationId": organization}, client=client, count=count)
    for edge in edges["tagsV2"]["edges"]:
        if edge["node"]["name"].strip().casefold() == TRIAL_TAG.casefold():
            return edge["node"]["id"]
    made = call(
        key,
        CREATE_TAG,
        {"input": {"organizationId": organization,
                   "tag": {"name": TRIAL_TAG, "color": TRIAL_COLOR}}},
        client=client,
        count=count,
    )["createTag"]  # fmt: skip
    if not made.get("tag"):
        raise BufferError(made.get("message") or "Buffer didn't make the tag")
    return made["tag"]["id"]


def create(
    key: str, data: dict, client: httpx.Client | None = None, count: Count | None = None
) -> Posted:
    """The new post, with when Buffer will send it."""
    result = call(key, CREATE, {"input": data}, client=client, count=count)["createPost"]
    if result.get("message") and not result.get("post"):
        raise BufferError(plain(result["message"]))
    return posted(result["post"])


def create_post(
    key: str, data: dict, client: httpx.Client | None = None, count: Count | None = None
) -> str:
    """The new post's id in Buffer."""
    return create(key, data, client, count).id


def post(
    key: str, post_id: str, client: httpx.Client | None = None, count: Count | None = None
) -> Posted | None:
    """How a post stands now; None when Buffer doesn't have it any more (deleted there)."""
    try:
        found = call(key, POST, {"id": post_id}, client=client, count=count).get("post")
    except BufferError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    return posted(found) if found else None


def delete_post(
    key: str, post_id: str, client: httpx.Client | None = None, count: Count | None = None
) -> None:
    result = call(key, DELETE, {"id": post_id}, client=client, count=count)["deletePost"]
    if result.get("message") and not result.get("id"):
        raise BufferError(result["message"])


def pending(
    key: str, client: httpx.Client | None = None, count: Count | None = None
) -> list[Posted]:
    """Every post of the key's account still waiting (PENDING), in all its organizations."""
    found = []
    organizations = call(key, ORGANIZATIONS, client=client, count=count)["account"]["organizations"]
    for organization in organizations:
        after = None
        while True:
            variables = {
                "input": {"organizationId": organization["id"], "filter": {"status": PENDING}},
                "after": after,
            }
            page = call(key, POSTS, variables, client=client, count=count)["posts"]
            found += [posted(edge["node"]) for edge in page.get("edges") or []]
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
    return found


def plain(message: str) -> str:
    """Buffer's error in words that say what to do."""
    if PERSONAL in message.lower():
        return (
            "this Instagram is a personal profile: Buffer posts reels only to a Creator or "
            "Business account. Switch it in Instagram (Settings > Account type), then refresh "
            "the key on the Buffer page"
        )
    return message


def personal(channel: Channel | dict) -> bool:
    """Looks like an Instagram personal profile, which Buffer can't publish to by itself."""
    get = channel.get if isinstance(channel, dict) else lambda k: getattr(channel, k)
    return get("service") == "instagram" and get("kind") == "profile"
