"""Trusted on-host administration; never pass passwords on a command line."""

import argparse
import getpass
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from .db import connect, migrate
from .errors import DomainError
from .services import auth


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="stockdesk", description="StockDesk local inventory operations")
    result.add_argument("--database", type=Path, default=Path(os.environ.get("STOCKDESK_DATABASE", "instance/stockdesk.sqlite")))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create or migrate the database without seeding accounts")
    create = commands.add_parser("create-user", help="Create an account; prompt for its password")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=auth.ROLES, default="viewer")
    reset = commands.add_parser("reset-password", help="Reset an account password and revoke its sessions")
    reset.add_argument("--username", required=True)
    commands.add_parser("demo", help="Load fictional examples into an empty catalog")
    commands.add_parser("check", help="Check SQLite integrity, foreign keys and stock reconciliation")
    backup = commands.add_parser("backup", help="Create and verify a new SQLite backup")
    backup.add_argument("destination", type=Path)
    serve = commands.add_parser("serve", help="Run Uvicorn locally; no reload or public bind by default")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return result


def _password() -> str:
    password = getpass.getpass("New password (12–1024 characters): ")
    if password != getpass.getpass("Confirm new password: "):
        raise DomainError("Passwords do not match.")
    return password


def _administrator(connection: sqlite3.Connection) -> int:
    result = connection.execute("SELECT id FROM users WHERE role='admin' AND enabled=1 ORDER BY id LIMIT 1").fetchone()
    if not result:
        raise DomainError("Create an administrator account first.")
    return result[0]


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    path = args.database.resolve()
    try:
        if args.command == "init":
            with closing(connect(path)) as connection:
                migrate(connection)
            print(f"Database initialized: {path}")
            return 0
        if not path.is_file():
            raise DomainError("Database file does not exist. Run init first.")
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise DomainError("Port must be between 1 and 65535.")
            from .app import create_app
            from .config import from_env
            import uvicorn
            uvicorn.run(create_app(from_env(database=path)), host=args.host, port=args.port,
                        proxy_headers=False, access_log=False)
            return 0
        if args.command == "check":
            from .maintenance import check_database
            result = check_database(path)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "backup":
            from .maintenance import backup_database
            print(json.dumps(backup_database(path, args.destination), indent=2))
            return 0
        with closing(connect(path)) as connection:
            migrate(connection)
            if args.command == "create-user":
                password = _password()
                if not connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    if args.role != "admin":
                        raise DomainError("The first account must have the admin role.")
                    user = auth.bootstrap_user(connection, args.username, password)
                else:
                    user = auth.create_user(connection, _administrator(connection), args.username, password, args.role)
                print(f"Account created: {user['username']} ({user['role']}).")
            elif args.command == "reset-password":
                target = connection.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
                if not target:
                    raise DomainError("Account was not found.")
                auth.set_password(connection, _administrator(connection), target[0], _password())
                print("Password reset. Existing sessions have been revoked.")
            elif args.command == "demo":
                from .demo import seed_demo
                print(json.dumps(seed_demo(connection, _administrator(connection)), indent=2))
        return 0
    except (DomainError, ValueError, OSError, sqlite3.DatabaseError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled; no password was accepted.", file=sys.stderr)
        return 1
