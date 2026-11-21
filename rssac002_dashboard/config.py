"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def load_env_file(path: Path | str = ".env") -> None:
    """Read KEY=VALUE lines from a .env file into the environment. Variables already set win."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if not separator or not name or name.startswith("#"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("RSSAC002_DB", "data/dashboard.sqlite3")))
    # Output of `rssac002-dashboard hash-password`. Without it, sources can only be managed from the CLI.
    admin_password_hash: str = field(default_factory=lambda: os.getenv("RSSAC002_ADMIN_PASSWORD_HASH", "").strip())
    # Signs admin session cookies. A random one is generated per start when unset, which logs
    # admins out on restart.
    session_secret: str = field(
        default_factory=lambda: os.getenv("RSSAC002_SESSION_SECRET", "").strip() or secrets.token_urlsafe(32)
    )
    session_hours: float = field(default_factory=lambda: float(os.getenv("RSSAC002_SESSION_HOURS", "8")))
    cookie_secure: bool = field(default_factory=lambda: _flag("RSSAC002_COOKIE_SECURE"))
    # How often every source is checked for new reports. 0 disables automatic scans.
    scan_interval_hours: float = field(default_factory=lambda: float(os.getenv("RSSAC002_SCAN_INTERVAL_HOURS", "6")))
    # Requests one client may make per minute to the API and the published file tree; a zip download
    # counts as forty. 0 turns the limit off.
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RSSAC002_RATE_LIMIT", "240")))
    # Set when a reverse proxy on the same machine passes the client's address in X-Forwarded-For.
    # Without it every visitor would share the proxy's address, and its request budget.
    trust_proxy: bool = field(default_factory=lambda: _flag("RSSAC002_TRUST_PROXY"))
    # Most report files one zip download may hold.
    max_archive_reports: int = field(default_factory=lambda: int(os.getenv("RSSAC002_MAX_ARCHIVE_REPORTS", "6000")))
