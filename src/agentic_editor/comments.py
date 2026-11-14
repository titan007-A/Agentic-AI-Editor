"""Answering the comments on connected Instagram accounts' posts (instagram.py), through
Meta's official API only.

Each turn for an account whose answering is on (InstagramChannel.reply_mode):
  1. look: its newest posts' comments made since answering was turned on, minus the
     account's own and ones it has already replied to, are kept (InstagramComment, "new");
  2. draft: the planning model writes a short reply to each in the account's words
     (reply_style), or says to skip it (spam, abuse, nothing to answer) or that a person
     should answer it (a complaint, a question needing facts it doesn't have: "look");
  3. send: in "auto" mode drafted replies go out by themselves, oldest first, at most
     reply_limit an hour and PER_TURN a turn; in "review" mode they wait for a person,
     who can change the words, send or skip.

Instagram is looked at only every few minutes (the server's scan), never more than
MAX_POSTS posts and MAX_COMMENTS comments a post a turn."""

import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_editor import instagram
from agentic_editor.db import InstagramChannel, InstagramComment
from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

MODES = ("off", "review", "auto")
MAX_POSTS = 10  # newest posts looked at a turn
MAX_COMMENTS = 50  # comments read from each
PER_TURN = 5  # replies sent by themselves a turn, at most
DRAFT_BATCH = 10  # comments the model answers at once
REPLY_CHARS = 300


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _when(stamp: str) -> datetime | None:
    """Instagram's "2026-11-14T08:15:00+0000" -> a naive UTC datetime."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def channel_of(row: InstagramChannel) -> instagram.Channel:
    return instagram.Channel(id=row.id, username=row.username, access_token=row.access_token)


def look(db: Session, row: InstagramChannel, api=instagram) -> int:
    """Keep the new comments on the account's newest posts; how many were new."""
    channel = channel_of(row)
    since = row.reply_since or _now()
    known = set(
        db.scalars(select(InstagramComment.id).where(InstagramComment.channel_id == row.id))
    )
    new = 0
    for post in api.get_recent_posts(channel, MAX_POSTS):
        if not post.get("comments_count"):
            continue
        for comment in api.get_comments(channel, str(post["id"]), MAX_COMMENTS):
            made = _when(comment.timestamp)
            if comment.id in known or (made is not None and made < since):
                continue
            if comment.username.lower() == row.username.lower():
                continue  # the account's own
            if row.username.lower() in (u.lower() for u in comment.replied_by):
                continue  # answered already, by hand on Instagram
            known.add(comment.id)
            db.add(
                InstagramComment(
                    id=comment.id, channel_id=row.id, post_id=str(post["id"]),
                    post_caption=(post.get("caption") or "")[:500],
                    post_url=post.get("permalink") or "", username=comment.username,
                    text=comment.text[:2000], commented_at=made,
                )
            )  # fmt: skip
            new += 1
    db.commit()
    return new


class _Reply(BaseModel):
    n: int
    action: str = Field(description='"reply", "skip" (spam, abuse, nothing to say) or "person"')
    reply: str = Field(default="", description="the reply, when action is reply")
    why: str = Field(default="", description="a few words on the choice")


class _Replies(BaseModel):
    replies: list[_Reply]


DRAFT = """You answer comments on the Instagram account @{username}, as the account itself.
How the account talks: {style}
For each comment below (on the post whose caption is shown), decide:
- "reply": write a short, warm, natural reply in the comment's own language, like a person
  running the page: at most two sentences, no hashtags, no links, no promises, never the
  same words twice; an emoji is fine. Thank people for nice words, answer what you can from
  the caption, joke back at a joke.
- "skip": spam, bots, links, abuse, or nothing worth answering.
- "person": a complaint, something sensitive, or a question needing facts you don't have
  (prices, dates, orders, private matters): a person should answer it.
{comments}"""

PLAIN_STYLE = "friendly and casual, like the page's own voice"


def _opened(planner):
    """The planning model for a `with`: a factory (the router's, which holds the GPU while
    open) is opened just for this; a model object is used as it is."""
    return nullcontext(planner) if hasattr(planner, "chat_json") else planner()


def draft(db: Session, row: InstagramChannel, planner) -> int:
    """Draft replies to the account's new comments; how many were drafted."""
    waiting = list(
        db.scalars(
            select(InstagramComment)
            .where(InstagramComment.channel_id == row.id, InstagramComment.status == "new")
            .order_by(InstagramComment.commented_at)
        )
    )
    done = 0
    for start in range(0, len(waiting), DRAFT_BATCH):
        group = waiting[start : start + DRAFT_BATCH]
        lines = "\n".join(
            f'{n}. on "{" ".join(c.post_caption.split())[:160]}" @{c.username} said: '
            f'"{" ".join(c.text.split())[:400]}"'
            for n, c in enumerate(group, 1)
        )
        prompt = DRAFT.format(
            username=row.username, style=row.reply_style.strip() or PLAIN_STYLE, comments=lines
        )
        try:
            with _opened(planner) as llm:
                asked = [{"role": "user", "content": prompt}]
                answer = parse(
                    _Replies, llm.chat_json(asked, _Replies.model_json_schema(), temperature=0.7)
                )
        except LLMError as exc:
            log.warning("couldn't draft replies for @%s: %s", row.username, exc)
            break
        for item in answer.replies:
            if not 1 <= item.n <= len(group):
                continue
            comment = group[item.n - 1]
            reply = " ".join(item.reply.split())[:REPLY_CHARS]
            comment.why = item.why.strip()[:200]
            if item.action == "skip":
                comment.status = "skipped"
            elif item.action == "reply" and reply:
                comment.status, comment.draft = "drafted", reply
                done += 1
            else:
                comment.status = "look"
                comment.draft = reply
        db.commit()
    return done


def sent_lately(db: Session, row: InstagramChannel) -> int:
    """Replies sent from the account in the last hour."""
    hour_ago = _now() - timedelta(hours=1)
    return db.scalar(
        select(func.count()).where(
            InstagramComment.channel_id == row.id,
            InstagramComment.status == "sent",
            InstagramComment.sent_at >= hour_ago,
        )
    )


def send(db: Session, row: InstagramChannel, comment: InstagramComment, text: str, by: str,
         api=instagram) -> None:  # fmt: skip
    """Send one reply; the comment says how it went."""
    text = " ".join(text.split())[:REPLY_CHARS]
    comment.status, comment.draft = "sending", text
    db.commit()
    try:
        comment.reply_id = api.reply_to_comment(channel_of(row), comment.id, text)
    except instagram.InstagramError as exc:
        comment.status, comment.error = "failed", str(exc)[:300]
        db.commit()
        raise
    comment.status, comment.sent_by, comment.sent_at, comment.error = "sent", by, _now(), ""
    db.commit()


def send_drafted(db: Session, row: InstagramChannel, api=instagram) -> int:
    """In auto mode: send drafted replies, oldest first, within the hourly limit."""
    room = min(PER_TURN, max(0, row.reply_limit - sent_lately(db, row)))
    if row.reply_mode != "auto" or room == 0:
        return 0
    ready = db.scalars(
        select(InstagramComment)
        .where(InstagramComment.channel_id == row.id, InstagramComment.status == "drafted")
        .order_by(InstagramComment.commented_at)
        .limit(room)
    )
    sent = 0
    for comment in list(ready):
        try:
            send(db, row, comment, comment.draft, "auto", api)
            sent += 1
        except instagram.InstagramError as exc:
            log.warning("couldn't reply to %s on @%s: %s", comment.id, row.username, exc)
            break  # Instagram said no: try again next turn rather than push on
    return sent


def turn(db: Session, row: InstagramChannel, planner: Callable | object, api=instagram) -> str:
    """One look, draft and (in auto mode) send for an account; what came of it."""
    try:
        new = look(db, row, api)
    except instagram.InstagramError as exc:
        row.checked_at, row.check_note = _now(), f"couldn't read the comments: {exc}"[:300]
        db.commit()
        return row.check_note
    drafted = draft(db, row, planner) if new or _waiting(db, row) else 0
    sent = send_drafted(db, row, api)
    note = f"{new} new comments, {drafted} replies drafted"
    if row.reply_mode == "auto":
        note += f", {sent} sent"
    row.checked_at, row.check_note = _now(), note
    db.commit()
    return note


def _waiting(db: Session, row: InstagramChannel) -> bool:
    return bool(
        db.scalar(
            select(func.count()).where(
                InstagramComment.channel_id == row.id, InstagramComment.status == "new"
            )
        )
    )
