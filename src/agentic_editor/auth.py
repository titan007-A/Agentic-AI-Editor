"""Password hashing with the standard library's scrypt, and a limit on wrong guesses."""

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


class LoginGuard:
    """Failed logins by client address. After FREE_TRIES of them within WINDOW seconds a
    client has to wait before trying again, twice as long after each further failure."""

    FREE_TRIES = 5
    WINDOW = 15 * 60
    FIRST_WAIT = 30.0

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._failures: dict[str, list[float]] = {}

    def wait(self, client: str) -> float:
        """Seconds before this client may try again; 0 when it may now."""
        now = self.clock()
        recent = self._recent(client, now)
        if len(recent) < self.FREE_TRIES:
            return 0.0
        pause = min(self.FIRST_WAIT * 2 ** (len(recent) - self.FREE_TRIES), self.WINDOW)
        return max(0.0, recent[-1] + pause - now)

    def failed(self, client: str) -> None:
        now = self.clock()
        self._failures[client] = [*self._recent(client, now), now]
        for other in [c for c in self._failures if not self._recent(c, now)]:
            del self._failures[other]

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)

    def _recent(self, client: str, now: float) -> list[float]:
        return [t for t in self._failures.get(client, []) if t > now - self.WINDOW]
