"""Explicit SQLite transactions and versioned schema installation."""

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

SCHEMA_VERSION = 1


def connect(path: str | Path) -> sqlite3.Connection:
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


@contextmanager
def atomic(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Nest with savepoints; serialize writers before reading their stock state."""
    nested = connection.in_transaction
    savepoint = "sp_" + uuid.uuid4().hex
    connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        if nested:
            connection.execute(f"ROLLBACK TO {savepoint}")
            connection.execute(f"RELEASE {savepoint}")
        else:
            connection.rollback()
        raise
    else:
        if nested:
            connection.execute(f"RELEASE {savepoint}")
        else:
            connection.commit()


@contextmanager
def read_snapshot(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Keep multi-query reports consistent without acquiring a writer lock."""
    if connection.in_transaction:
        yield connection
        return
    connection.execute("BEGIN")
    try:
        yield connection
    finally:
        # These callers read only; rollback also releases the snapshot on failure.
        connection.rollback()


def migrate(connection: sqlite3.Connection) -> None:
    """Execute complete statements without executescript's implicit COMMIT."""
    with atomic(connection):
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        if current == SCHEMA_VERSION:
            return
        sql = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        statement = ""
        for line in sql.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise RuntimeError("Migration contains an incomplete SQL statement.")
