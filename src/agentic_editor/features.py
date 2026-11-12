"""Features some accounts try first: each is on for the usernames listed in the setting
"feature:<name>" (a JSON list), or, until that is set, for DEFAULT_USERS."""

import json

from sqlalchemy.orm import Session

from agentic_editor.db import Setting
from agentic_editor.db import User as Account

# trial_reel: "Send as trial reel" in the Buffer dialog. fun: the leaderboard, badges and
# the rest of the playful extras. niche_finder: the Accounts tab (niche.py).
FEATURES = ("trial_reel", "fun", "niche_finder")
DEFAULT_USERS = ("siddhant",)
# Features first tried by more than DEFAULT_USERS.
DEFAULTS = {"niche_finder": ("siddhant", "aman")}


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
    return account.username in users(name, session)


def of(account: Account, session: Session) -> list[str]:
    """The features this account has."""
    return [name for name in FEATURES if enabled(account, name, session)]
