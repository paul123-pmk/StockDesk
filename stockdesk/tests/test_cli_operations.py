import json
import secrets
import sqlite3
from contextlib import closing

import pytest

from conftest import load
from test_inventory import movement


def test_environment_defaults_create_private_local_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("STOCKDESK_ENV", "STOCKDESK_DATABASE", "STOCKDESK_SECRET_KEY", "STOCKDESK_TRUSTED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    settings = load("stockdesk.config").from_env()
    assert settings.database == tmp_path / "instance" / "stockdesk.sqlite"
    assert settings.secret_key == load("stockdesk.config").from_env().secret_key
    assert len(settings.secret_key) >= 32


def test_production_requires_external_secret_and_hosts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STOCKDESK_ENV", "production")
    monkeypatch.delenv("STOCKDESK_SECRET_KEY", raising=False)
    monkeypatch.delenv("STOCKDESK_TRUSTED_HOSTS", raising=False)
    with pytest.raises(ValueError, match="SECRET"):
        load("stockdesk.config").from_env()
    monkeypatch.setenv("STOCKDESK_SECRET_KEY", secrets.token_urlsafe(32))
    with pytest.raises(ValueError, match="HOSTS"):
        load("stockdesk.config").from_env()
    monkeypatch.setenv("STOCKDESK_TRUSTED_HOSTS", "stockdesk.example.invalid")
    assert load("stockdesk.config").from_env().production is True


@pytest.mark.parametrize("override", [{"secret_key": "short"}, {"trusted_hosts": ("*",)},
                                     {"currency": "anything"}, {"session_seconds": 0}])
def test_invalid_settings_rejected(tmp_path, override):
    values = dict(database=tmp_path/"db.sqlite", secret_key=secrets.token_urlsafe(32))
    values.update(override)
    with pytest.raises(ValueError):
        load("stockdesk.config").Settings(**values)


def test_cli_initialization_is_repeatable(tmp_path, capsys):
    path = tmp_path / "init.sqlite"
    cli = load("stockdesk.cli")
    assert cli.main(["--database", str(path), "init"]) == 0
    assert cli.main(["--database", str(path), "init"]) == 0
    assert "initialized" in capsys.readouterr().out
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_cli_user_creation_uses_prompt_not_arguments(tmp_path, monkeypatch):
    cli = load("stockdesk.cli")
    path = tmp_path / "cli.sqlite"
    cli.main(["--database", str(path), "init"])
    password = "synthetic-" + secrets.token_urlsafe(18)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: password)
    assert cli.main(["--database", str(path), "create-user", "--username", "owner", "--role", "admin"]) == 0
    assert cli.main(["--database", str(path), "create-user", "--username", "viewer", "--role", "viewer"]) == 0
    conn = load("stockdesk.db").connect(path)
    try:
        assert load("stockdesk.services.auth").login(conn, "viewer", password)["role"] == "viewer"
    finally:
        conn.close()


def test_cli_password_confirmation_must_match(tmp_path, monkeypatch):
    cli = load("stockdesk.cli")
    path = tmp_path / "cli.sqlite"
    cli.main(["--database", str(path), "init"])
    answers = iter(["synthetic-first-value", "synthetic-second-value"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))
    assert cli.main(["--database", str(path), "create-user", "--username", "owner", "--role", "admin"]) == 1


def test_demo_creates_balanced_synthetic_records_once(db, owner):
    demo = load("stockdesk.demo")
    result = demo.seed_demo(db, owner["id"])
    assert result["products"] >= 10
    assert load("stockdesk.services.reports").reconcile(db)["ok"] is True
    with pytest.raises(ValueError, match="empty"):
        demo.seed_demo(db, owner["id"])


def test_backup_restores_database_and_ledger(db, db_path, tmp_path, owner, world):
    movement(db, owner, world)
    path = tmp_path / "backup.sqlite"
    load("stockdesk.maintenance").backup_database(db_path, path)
    restored = load("stockdesk.db").connect(path)
    try:
        assert load("stockdesk.services.reports").reconcile(restored)["ok"] is True
        assert restored.execute("SELECT quantity FROM balances").fetchone()[0] == 25
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_backup_refuses_overwrite_or_same_path(db, db_path, tmp_path):
    maintenance = load("stockdesk.maintenance")
    with pytest.raises(ValueError):
        maintenance.backup_database(db_path, db_path)
    target = tmp_path / "existing.sqlite"
    target.write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError):
        maintenance.backup_database(db_path, target)
    assert target.read_text(encoding="utf-8") == "keep me"


def test_cli_check_outputs_json_and_failure_when_uninitialized(tmp_path, db, db_path, capsys):
    cli = load("stockdesk.cli")
    assert cli.main(["--database", str(db_path), "check"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["--database", str(tmp_path/"absent.sqlite"), "check"]) == 1
    assert not (tmp_path/"absent.sqlite").exists()


def test_cli_reset_password_revokes_sessions(db, db_path, owner, monkeypatch):
    cli = load("stockdesk.cli")
    password = "synthetic-new-" + secrets.token_urlsafe(18)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: password)
    assert cli.main(["--database", str(db_path), "reset-password", "--username", "owner"]) == 0
    user = load("stockdesk.services.auth").login(db, "owner", password)
    assert user["session_version"] == owner["session_version"] + 1
