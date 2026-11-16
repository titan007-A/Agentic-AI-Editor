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
# the preset's length or keep all of it (JobRequest.trim).
FEATURES = ("trial_reel", "fun", "niche_finder", "trim_choice")
DEFAULT_USERS = ("siddhant",)
# Features first tried by other accounts than DEFAULT_USERS.
DEFAULTS = {"niche_finder": ("siddhant", "aman"), "trim_choice": ("aman",)}
# In a feature's list: every account, now and later (new admins and employees too).
EVERYONE = "*"


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
