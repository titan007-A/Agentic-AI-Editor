"""Features some accounts try first: each is on for the usernames listed in the setting
"feature:<name>" (a JSON list), or, until that is set, for DEFAULT_USERS. "*" in the list
turns it on for every account, those made later included."""

import json

from sqlalchemy.orm import Session

from agentic_editor.db import Setting
from agentic_editor.db import User as Account

# trial_reel: "Send as trial reel" in the Buffer dialog. fun: the leaderboard, badges and
# the rest of the playful extras. niche_finder: the Accounts tab (niche.py). trim_choice:
# starting an edit with a preset that isn't about a song asks whether to cut the video to
# the preset's length or keep all of it (JobRequest.trim). youtube_songs: the Researcher
# finds whole songs' YouTube videos on the singers' and labels' channels (research.SONGS).
# dm_queue: the Accounts tab's message queue: one saved message, copied as each found
# account's Instagram chat opens, one after another (the person sends each one).
# animation_studio: the Animations tab, narrated animated reels written from one line
# (animate/). kick_clipper: the Kick Clipper tab, 15-30 s clips cut from Kick streams'
# moments, live ones too (clipper/). channel_autopilot: the Channel autopilot tab, an
# Instagram page that finds, edits, checks and posts its own reels (channel/).
FEATURES = (
    "trial_reel", "fun", "niche_finder", "trim_choice", "youtube_songs", "dm_queue",
    "animation_studio", "kick_clipper", "channel_autopilot",
)  # fmt: skip
DEFAULT_USERS = ("siddhant",)
# Features first tried by other accounts than DEFAULT_USERS.
# In a feature's list: every account, now and later (new admins and employees too).
EVERYONE = "*"
DEFAULTS = {
    "niche_finder": ("siddhant", "aman"),
    "trim_choice": ("aman",),
    # Everyone: the owner wants most of a research's results from YouTube, whole songs too.
    "youtube_songs": (EVERYONE,),
    "dm_queue": ("siddhant-content",),
    "animation_studio": ("siddhant", "siddhant-content"),
    "kick_clipper": ("siddhant", "employee"),
    "channel_autopilot": ("siddhant", "siddhant-content"),
}


def users(name: str, session: Session) -> list[str]:
    default = list(DEFAULTS.get(name, DEFAULT_USERS))
    row = session.get(Setting, f"feature:{name}")
    if row is None:
        return default
    try:
        listed = json.loads(row.value)
    except ValueError:
        return default
    return [u for u in listed if isinstance(u, str)]


def enabled(account: Account, name: str, session: Session) -> bool:
    listed = users(name, session)
    return EVERYONE in listed or account.username in listed


def of(account: Account, session: Session) -> list[str]:
    """The features this account has."""
    return [name for name in FEATURES if enabled(account, name, session)]
