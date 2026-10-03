"""Local maintenance. Backups never overwrite an existing destination."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .db import SCHEMA_VERSION
from .errors import DomainError
from .services.reports import reconcile


def read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DomainError("Database file does not exist. Run init first.")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def check_database(path: Path) -> dict:
    with closing(read_only_connection(path)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise DomainError("Database schema is not supported by this release.")
        integrity = [item[0] for item in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(item) for item in connection.execute("PRAGMA foreign_key_check")]
        ledger = reconcile(connection)
        return {"ok": integrity == ["ok"] and not foreign_keys and ledger["ok"],
                "schema_version": version, "integrity": integrity,
                "foreign_key_violations": foreign_keys, "ledger": ledger}


def backup_database(source: Path, destination: Path) -> dict:
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise DomainError("Backup destination must differ from the source database.")
    if not source.is_file():
        raise DomainError("Source database does not exist.")
    if destination.exists():
        raise DomainError("Backup destination already exists; choose a new filename.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise DomainError("Backup destination already exists; choose a new filename.") from exc
    os.close(descriptor)
    try:
        with closing(read_only_connection(source)) as reader, closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
        result = check_database(destination)
        if not result["ok"]:
            raise DomainError("Backup did not pass database and ledger verification.")
        return {"backup": str(destination), "verified": True}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
