"""Admin login: one password, a signed session cookie, and a required header on changes.

The cookie is SameSite=Strict and every state-changing request must also carry a custom
header. Browsers do not let another site add that header without a CORS preflight, which
this app never grants, so cross-site requests cannot perform admin actions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

COOKIE_NAME = "rssac002_admin"
REQUEST_HEADER = "x-dashboard-request"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    encoded = (base64.b64encode(part).decode() for part in (salt, digest))
    # Colons, not dollar signs: the value goes into .env files, where "$" starts a variable.
    return "scrypt:{n}:{r}:{p}:".format(**_SCRYPT) + ":".join(encoded)


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class Sessions:
    def __init__(self, secret: str, lifetime_seconds: float):
        self._key = secret.encode()
        self.lifetime = int(lifetime_seconds)

    def _sign(self, expires: str) -> str:
        return hmac.new(self._key, f"admin:{expires}".encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expires = str(int(time.time()) + self.lifetime)
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        expires, signature = token.split(".", 1)
        if not expires.isdigit() or not hmac.compare_digest(signature, self._sign(expires)):
            return False
        return int(expires) > time.time()


class LoginThrottle:
    """Refuses further attempts for a while after repeated wrong passwords."""

    def __init__(self, attempts: int = 5, window_seconds: int = 60):
        self.attempts, self.window = attempts, window_seconds
        self._failures: list[float] = []

    def blocked(self) -> bool:
        cutoff = time.monotonic() - self.window
        self._failures = [moment for moment in self._failures if moment > cutoff]
        return len(self._failures) >= self.attempts

    def record_failure(self) -> None:
        self._failures.append(time.monotonic())
