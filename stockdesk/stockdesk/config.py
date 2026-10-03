"""Explicit configuration: local defaults, fail-closed production settings."""

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path
    secret_key: str
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    production: bool = False
    currency: str = "KES"
    session_seconds: int = 8 * 60 * 60

    def __post_init__(self):
        if not isinstance(self.secret_key, str) or len(self.secret_key) < 32:
            raise ValueError("The session secret must contain at least 32 characters.")
        if not self.trusted_hosts or any(not value or "*" in value for value in self.trusted_hosts):
            raise ValueError("Set explicit trusted hosts; wildcard hosts are not supported.")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("Currency must be a three-letter uppercase label.")
        if type(self.session_seconds) is not int or self.session_seconds < 60:
            raise ValueError("Session duration must be at least 60 seconds.")


def _local_secret(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "session.key"
    if target.is_symlink():
        raise ValueError("The local session secret must not be a symbolic link.")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return target.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value + "\n")
    return value


def from_env(*, database: Path | None = None) -> Settings:
    mode = os.environ.get("STOCKDESK_ENV", "local")
    if mode not in {"local", "production"}:
        raise ValueError("STOCKDESK_ENV must be local or production.")
    database = Path(database or os.environ.get("STOCKDESK_DATABASE", "instance/stockdesk.sqlite")).resolve()
    secret = os.environ.get("STOCKDESK_SECRET_KEY", "")
    if mode == "production" and not secret:
        raise ValueError("Production requires STOCKDESK_SECRET_KEY.")
    if mode == "production" and not os.environ.get("STOCKDESK_TRUSTED_HOSTS"):
        raise ValueError("Production requires STOCKDESK_TRUSTED_HOSTS.")
    return Settings(database=database, secret_key=secret or _local_secret(database.parent),
                    trusted_hosts=tuple(item.strip() for item in os.environ.get(
                        "STOCKDESK_TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")),
                    production=mode == "production", currency=os.environ.get("STOCKDESK_CURRENCY", "KES"))
