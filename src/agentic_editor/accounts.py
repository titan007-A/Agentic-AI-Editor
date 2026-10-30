"""Accounts: the admin, who manages everyone, and the employees (members) they create.

Each employee has presets of their own, a copy of the admin's taken when the account is
made, in a folder under the presets folder; the admin's are the presets folder itself.
"""

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_editor.auth import hash_password, verify_password
from agentic_editor.db import Feed, Job, Research, StyleDraft, User

ADMIN, MEMBER = "admin", "member"
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
MIN_PASSWORD = 8
PEOPLE = "people"  # folder of the employees' presets, under the presets folder
# A hash to check against when there is no such user, so a wrong name takes as long to
# answer as a wrong password and doesn't tell which accounts exist.
_NOBODY = hash_password("no such user")


class AccountError(ValueError):
    pass


def normal(username: str) -> str:
    return username.strip().lower()


def find(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == normal(username)))


def admins(session: Session) -> list[User]:
    return list(session.scalars(select(User).where(User.role == ADMIN)))


def presets_dir(user: User | None, root: Path) -> Path:
    """Where this account's presets live. None, a job from before accounts, is the
    admin's. Folders are named by id, which is always safe in a path."""
    if user is None or user.role == ADMIN:
        return root
    return root / PEOPLE / user.id


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"the password needs at least {MIN_PASSWORD} characters")


def create(
    session: Session,
    username: str,
    password: str | None,
    role: str,
    presets_root: Path,
    password_hash: str | None = None,
) -> User:
    """A new account. A member starts with a copy of the admin's presets."""
    name = normal(username)
    if not USERNAME.fullmatch(name):
        raise AccountError(
            "the username needs 3-32 lowercase letters, digits, dots, dashes or underscores, "
            "starting with a letter or digit"
        )
    if role not in (ADMIN, MEMBER):
        raise AccountError(f"the role is {ADMIN} or {MEMBER}")
    if find(session, name):
        raise AccountError(f"there is already an account called {name}")
    if password_hash is None:
        check_password(password or "")
        password_hash = hash_password(password or "")
    user = User(username=name, password_hash=password_hash, role=role)
    session.add(user)
    session.flush()
    if role == MEMBER:
        folder = presets_dir(user, presets_root)
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(presets_root.glob("*.yaml")):
            shutil.copy2(path, folder / path.name)
    session.commit()
    return user


def set_password(session: Session, user: User, password: str) -> None:
    """A new password, which also signs the account out everywhere."""
    check_password(password)
    user.password_hash = hash_password(password)
    user.session_version += 1
    session.commit()


def set_role(session: Session, user: User, role: str, presets_root: Path) -> None:
    if role not in (ADMIN, MEMBER):
        raise AccountError(f"the role is {ADMIN} or {MEMBER}")
    if user.role == ADMIN and role == MEMBER and len(admins(session)) <= 1:
        raise AccountError("this is the only admin; make another one first")
    if role == MEMBER and user.role == ADMIN:
        folder = presets_dir(User(id=user.id, role=MEMBER), presets_root)
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(presets_root.glob("*.yaml")):
            if not (folder / path.name).exists():
                shutil.copy2(path, folder / path.name)
    user.role = role
    user.session_version += 1
    session.commit()


def remove(
    session: Session, user: User, presets_root: Path, keep_work_for: User | None = None
) -> None:
    """Remove an account and its presets. Its jobs, feeds, drafts and researches go to
    `keep_work_for` when given (the admin removing it), or are deleted with it."""
    if user.role == ADMIN and len(admins(session)) <= 1:
        raise AccountError("this is the only admin; make another one first")
    for kind in (Job, Feed, StyleDraft, Research):
        for item in session.scalars(select(kind).where(kind.owner_id == user.id)):
            if keep_work_for is not None:
                item.owner_id = keep_work_for.id
            else:
                session.delete(item)
    if user.role == MEMBER:
        shutil.rmtree(presets_dir(user, presets_root), ignore_errors=True)
    session.delete(user)
    session.commit()


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = find(session, username)
    if user is None:
        verify_password(password, _NOBODY)
        return None
    return user if verify_password(password, user.password_hash) else None


def seen(session: Session, user: User) -> None:
    user.last_seen_at = datetime.now(UTC)
    session.commit()
