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
    id name displayName service avatar isQueuePaused
  }
}"""
ORGANIZATIONS = "query Organizations { account { organizations { id name } } }"
CREATE = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}"""


class BufferError(RuntimeError):
    pass


@dataclass
class Channel:
    id: str
    name: str
    service: str
    avatar: str | None
    paused: bool


def call(
    key: str, query: str, variables: dict | None = None, client: httpx.Client | None = None
) -> dict:
    http = client or httpx.Client(timeout=30)
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


def channels(key: str, client: httpx.Client | None = None) -> list[Channel]:
    """Every channel in every organization the key's account belongs to."""
    found = []
    organizations = call(key, ORGANIZATIONS, client=client)["account"]["organizations"]
    for organization in organizations:
        data = call(key, CHANNELS, {"organizationId": organization["id"]}, client=client)
        for c in data["channels"]:
            found.append(
                Channel(
                    id=c["id"],
                    name=c.get("displayName") or c.get("name") or c["id"],
                    service=c.get("service") or "",
                    avatar=c.get("avatar"),
                    paused=bool(c.get("isQueuePaused")),
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
) -> dict:
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
    return data


def create_post(key: str, data: dict, client: httpx.Client | None = None) -> str:
    """The new post's id in Buffer."""
    result = call(key, CREATE, {"input": data}, client=client)["createPost"]
    if result.get("message") and not result.get("post"):
        raise BufferError(result["message"])
    return result["post"]["id"]
