import json
from datetime import UTC, datetime

import httpx
import pytest

from agentic_editor import buffer


def fake(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_channels_of_every_organization_are_listed_with_the_key():
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        body = json.loads(request.content)
        if "Organizations" in body["query"]:
            return httpx.Response(
                200, json={"data": {"account": {"organizations": [{"id": "o1", "name": "Me"}]}}}
            )
        assert body["variables"] == {"organizationId": "o1"}
        channel = {
            "id": "c1",
            "name": "ig",
            "displayName": "My page",
            "service": "instagram",
            "avatar": None,
            "isQueuePaused": False,
            "type": "business",
            "isDisconnected": False,
            "isLocked": True,
        }
        assert "isLocked" in body["query"] and "type" in body["query"]
        return httpx.Response(200, json={"data": {"channels": [channel]}})

    count = buffer.Count()
    found = buffer.channels("secret-key", client=fake(handler), count=count)
    assert [(c.id, c.name, c.service) for c in found] == [("c1", "My page", "instagram")]
    assert found[0].kind == "business" and found[0].broken is True
    assert set(seen) == {"Bearer secret-key"} and count.n == 2


def test_a_personal_instagram_is_named_as_one():
    assert buffer.personal({"service": "instagram", "kind": "profile"})
    assert not buffer.personal(buffer.Channel("c", "p", "instagram", None, False, "business"))
    assert not buffer.personal({"service": "facebook", "kind": "profile"})
    said = "Instagram personal profile channels require notification scheduling."
    assert "Creator or Business" in buffer.plain(said)
    assert buffer.plain("Failed to fetch media") == "Failed to fetch media"


def test_a_reel_post_for_instagram_carries_the_video_link_and_reel_metadata():
    channel = buffer.Channel("c1", "My page", "instagram", None, False)
    due = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    data = buffer.post_input(
        channel, "https://x.test/v.mp4", "Caption", "customScheduled", due, "first!"
    )
    assert data["assets"] == [{"video": {"url": "https://x.test/v.mp4"}}]
    assert data["dueAt"] == "2026-11-03T09:30:00Z" and data["needsApproval"] is False
    assert data["metadata"] == {
        "instagram": {"type": "reel", "shouldShareToFeed": True, "firstComment": "first!"}
    }
    other = buffer.Channel("c2", "Page", "facebook", None, False)
    assert "metadata" not in buffer.post_input(other, "https://x.test/v.mp4", "", "addToQueue")
    with pytest.raises(buffer.BufferError):
        buffer.post_input(channel, "https://x.test/v.mp4", "", "customScheduled")


def test_buffer_refusals_come_back_as_readable_errors():
    def says(status, body):
        return fake(lambda request: httpx.Response(status, json=body))

    with pytest.raises(buffer.BufferError, match="API key"):
        buffer.create_post("k", {}, client=says(401, {}))
    with pytest.raises(buffer.BufferError, match="Failed to fetch"):
        mutation = {"data": {"createPost": {"message": "Failed to fetch media"}}}
        buffer.create_post("k", {}, client=says(200, mutation))
    personal = {"data": {"createPost": {"message": "Instagram personal profile channels x"}}}
    with pytest.raises(buffer.BufferError, match="Creator or Business"):
        buffer.create_post("k", {}, client=says(200, personal))
    with pytest.raises(buffer.BufferError, match="bad field"):
        buffer.create_post("k", {}, client=says(200, {"errors": [{"message": "bad field"}]}))
    ok = {"data": {"createPost": {"post": {"id": "p1"}}}}
    assert buffer.create_post("k", {}, client=says(200, ok)) == "p1"


def test_a_trial_reel_is_a_draft_under_the_trial_tag_made_once():
    channel = buffer.Channel("c1", "My page", "instagram", None, False, organization="o1")
    data = buffer.post_input(channel, "https://x.test/v.mp4", "", "addToQueue", draft=True,
                             tags=["t1"])  # fmt: skip
    assert data["saveToDraft"] is True and data["tagIds"] == ["t1"]
    assert "saveToDraft" not in buffer.post_input(channel, "https://x.test/v.mp4", "", "addToQueue")
    tags: list[dict] = []
    asked = []

    def handler(request):
        body = json.loads(request.content)
        asked.append(body["query"].split()[1].split("(")[0] + "(")
        if "tagsV2" in body["query"]:
            assert body["variables"] == {"organizationId": "o1"}
            return httpx.Response(200, json={"data": {"tagsV2": {"edges": [
                {"node": t} for t in tags]}}})  # fmt: skip
        if "createTag" in body["query"]:
            made = body["variables"]["input"]
            assert made["organizationId"] == "o1" and made["tag"]["name"] == buffer.TRIAL_TAG
            tags.append({"id": "t9", "name": "Trial Reel"})
            return httpx.Response(200, json={"data": {"createTag": {"tag": {"id": "t9"}}}})
        assert "ChannelOrganization" in body["query"]
        return httpx.Response(200, json={"data": {"channel": {"organizationId": "o1"}}})

    assert buffer.trial_tag("k", channel, client=fake(handler)) == "t9"
    assert buffer.trial_tag("k", channel, client=fake(handler)) == "t9"  # found, not made again
    assert asked == ["Tags(", "CreateTag(", "Tags("]
    # A channel kept from before organizations were asked for: asked first.
    old = buffer.Channel("c1", "My page", "instagram", None, False)
    assert buffer.trial_tag("k", old, client=fake(handler)) == "t9"
    assert asked[-2:] == ["ChannelOrganization(", "Tags("]
