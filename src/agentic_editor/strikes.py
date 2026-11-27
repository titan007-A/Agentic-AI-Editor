"""The strike list: songs a label blocked worldwide, took a post down for or struck one of
the pages for, one list for every account (labels act on a song wherever it is posted).
The owner's analysis of 47 alerts (2026-11-16): 45 were Russia-only mutes or blocks,
which hurt nobody and never go on the list; the one real block was "Luna" (Feid & ATL
Jacob, UMG), blocked in 228 countries, the list's first entry.

What the list and songfacts say about a job's song is its notice: blocked before, a song of
a major label (songfacts.NOTIFIED), a new song. A job never stops for it (the owner asked
for a notification); the channel autopilot holds a reel whose song is on the list.

Anyone signed in adds to it; an admin or whoever added an entry takes it off."""

import re
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor import accounts, research, songfacts
from agentic_editor.db import Setting, Strike
from agentic_editor.db import User as Account

WHATS = {"blocked": "blocked worldwide", "removed": "post removed", "strike": "copyright strike"}
FIRST = {"song": "Luna", "artist": "Feid & ATL Jacob", "label_group": "UMG", "what": "blocked",
         "note": "228 countries"}  # fmt: skip
SEEDED = "strike_list_seeded"  # set once the first entry went in: one taken off stays off
# A note about Russia alone: mutes and blocks there are the licence Meta lacks there.
RUSSIA = re.compile(r"\b(?:russia|rusia|russland)\b", re.IGNORECASE)
COUNTRIES = re.compile(r"\b\d{2,}\s*(?:countries|desh|paises|países)\b", re.IGNORECASE)


class StrikeRequest(BaseModel):
    song: str = Field(min_length=1, max_length=200)
    artist: str = Field(default="", max_length=200)
    label_group: str = "other"
    what: str = "blocked"
    note: str = Field(default="", max_length=200)
    job_id: str | None = Field(default=None, max_length=12)  # the reel it happened to

    @field_validator("song", "artist", "note")
    @classmethod
    def _tidy(cls, value: str, info) -> str:
        if info.field_name == "song" and not value.strip():
            raise ValueError("name the song")
        return " ".join(value.split())

    @field_validator("label_group")
    @classmethod
    def _group(cls, value: str) -> str:
        if value not in songfacts.GROUPS:
            raise ValueError(f"the label group is one of {', '.join(songfacts.GROUPS)}")
        return value

    @field_validator("what")
    @classmethod
    def _what(cls, value: str) -> str:
        if value not in WHATS:
            raise ValueError(f"what happened is one of {', '.join(WHATS)}")
        return value

    @field_validator("note")
    @classmethod
    def _not_russia(cls, value: str) -> str:
        if RUSSIA.search(value) and not COUNTRIES.search(value):
            raise ValueError(
                "Russia-only mutes and blocks don't go on the strike list: they happen to "
                "every reel with a song there and cost nothing elsewhere"
            )
        return value


class StrikeView(BaseModel):
    id: int
    song: str
    artist: str
    label_group: str
    what: str
    what_label: str
    note: str
    added_by: str | None  # their username; None for the list's first entry
    job_id: str | None
    created_at: datetime
    can_remove: bool


class Blocked(BaseModel):
    """The strike list's entry a song is on."""

    id: int
    song: str
    artist: str
    label_group: str
    what: str
    what_label: str
    note: str


class Notice(BaseModel):
    """What a job's (or a reel's) song is, and what to know before posting it."""

    song: str
    artist: str
    year: str | None = None
    year_from: str = ""
    label: str = ""
    group: str = ""  # songfacts.GROUPS, "" when no label was found
    group_name: str = ""  # "Sony Music"
    label_from: str = ""
    blocked: Blocked | None = None  # on the strike list
    notified: bool = False  # a label group the app warns about (songfacts.NOTIFIED)
    new: bool = False  # out within songfacts.NEW_YEARS years


def seed(session: Session) -> None:
    """The list's first entry, put in once (the server's start): taken off, it stays off."""
    if session.get(Setting, SEEDED) is None:
        session.add(Strike(**FIRST))
        session.add(Setting(key=SEEDED, value="1"))
        session.commit()


def listed(session: Session) -> list[Strike]:
    """Every entry, newest first."""
    newest = select(Strike).order_by(Strike.created_at.desc(), Strike.id.desc())
    return list(session.scalars(newest))


def on_list(entries: list[Strike], song: str, artist: str) -> Strike | None:
    """The entry for this song, by its title (without "(Live)", "feat. ...") and an artist in
    common, or any artist when one side doesn't say (research.Posted)."""
    if not song.strip():
        return None
    for entry in entries:
        if research.Posted([(entry.song, entry.artist)], []).has(song, artist):
            return entry
    return None


def blocked_view(entry: Strike) -> Blocked:
    return Blocked(
        id=entry.id, song=entry.song, artist=entry.artist, label_group=entry.label_group,
        what=entry.what, what_label=WHATS.get(entry.what, entry.what), note=entry.note,
    )  # fmt: skip


def notice(
    facts: dict | None, entries: list[Strike], this_year: int | None = None
) -> Notice | None:
    """The notice for a song's facts (songfacts.Facts as stored), None when none are known.
    Worked out when shown, so a song put on the list later warns on older jobs too."""
    if not facts or not (facts.get("song") or "").strip():
        return None
    this_year = this_year or datetime.now(UTC).year
    group = facts.get("group") or ""
    entry = on_list(entries, facts["song"], facts.get("artist") or "")
    return Notice(
        song=facts["song"],
        artist=facts.get("artist") or "",
        year=facts.get("year"),
        year_from=facts.get("year_from") or "",
        label=facts.get("label") or "",
        group=group,
        group_name=songfacts.GROUP_NAMES.get(group, ""),
        label_from=facts.get("label_from") or "",
        blocked=blocked_view(entry) if entry else None,
        notified=group in songfacts.NOTIFIED,
        new=songfacts.is_new(facts.get("year"), this_year),
    )


def may_remove(account: Account, entry: Strike) -> bool:
    mine = entry.added_by is not None and entry.added_by == account.id
    return account.role == accounts.ADMIN or mine


def install(app, *, User, DB, find_job) -> None:
    """The list's routes. `find_job(job_id, session, account)`: the job, if the account sees it."""

    def view(entry: Strike, account, names: dict[str, str]) -> StrikeView:
        return StrikeView(
            id=entry.id, song=entry.song, artist=entry.artist, label_group=entry.label_group,
            what=entry.what, what_label=WHATS.get(entry.what, entry.what), note=entry.note,
            added_by=names.get(entry.added_by) if entry.added_by else None, job_id=entry.job_id,
            created_at=entry.created_at, can_remove=may_remove(account, entry),
        )  # fmt: skip

    def usernames(session: Session) -> dict[str, str]:
        return {a.id: a.username for a in session.scalars(select(Account))}

    @app.get("/api/strikes")
    def strike_list(account: User, session: DB) -> dict:
        names = usernames(session)
        return {
            "entries": [view(e, account, names) for e in listed(session)],
            "groups": list(songfacts.GROUPS),
            "whats": WHATS,
        }

    @app.post("/api/strikes")
    def add_strike(body: StrikeRequest, account: User, session: DB) -> StrikeView:
        entries = listed(session)
        if body.job_id:
            find_job(body.job_id, session, account)
        if found := on_list(entries, body.song, body.artist):
            raise HTTPException(
                409, f"“{found.song}” by {found.artist or 'its artist'} is on the list already"
            )
        entry = Strike(**body.model_dump(), added_by=account.id)
        session.add(entry)
        session.commit()
        return view(entry, account, usernames(session))

    @app.delete("/api/strikes/{strike_id}")
    def remove_strike(strike_id: int, account: User, session: DB) -> dict:
        entry = session.get(Strike, strike_id)
        if entry is None:
            raise HTTPException(404, "no such entry")
        if not may_remove(account, entry):
            raise HTTPException(403, "only an admin or whoever added it can take it off")
        session.delete(entry)
        session.commit()
        return {"ok": True}
