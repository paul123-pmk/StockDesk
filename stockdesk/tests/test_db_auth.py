import secrets
import sqlite3

import pytest

from conftest import load


def test_migrations_are_repeatable(db):
    load("stockdesk.db").migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_future_schema_refused(db):
    db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        load("stockdesk.db").migrate(db)


def test_nested_transaction_rolls_back_inner_only(db):
    atomic = load("stockdesk.db").atomic
    with atomic(db):
        db.execute("INSERT INTO warehouses (code,name) VALUES ('A','A')")
        with pytest.raises(RuntimeError):
            with atomic(db):
                db.execute("INSERT INTO warehouses (code,name) VALUES ('B','B')")
                raise RuntimeError("abort")
    assert [r[0] for r in db.execute("SELECT code FROM warehouses")] == ["A"]


def test_transaction_rolls_back_on_outer_failure(db):
    with pytest.raises(RuntimeError):
        with load("stockdesk.db").atomic(db):
            db.execute("INSERT INTO warehouses (code,name) VALUES ('A','A')")
            raise RuntimeError("abort")
    assert db.execute("SELECT COUNT(*) FROM warehouses").fetchone()[0] == 0


def test_bootstrap_only_once(db, owner, password):
    with pytest.raises(ValueError, match="already"):
        load("stockdesk.services.auth").bootstrap_user(db, "another", password)


def test_password_hash_not_plaintext_or_returned(db, owner, password):
    stored = db.execute("SELECT password_hash FROM users WHERE id=?", (owner["id"],)).fetchone()[0]
    assert password not in stored
    assert stored.startswith("scrypt$")
    assert "password_hash" not in owner


def test_login_is_case_insensitive(db, owner, password):
    user = load("stockdesk.services.auth").login(db, "OWNER", password, now=10)
    assert user["id"] == owner["id"]


def test_bad_login_generic_for_unknown_and_wrong_password(db, owner):
    auth = load("stockdesk.services.auth")
    messages = []
    for name in ("owner", "nonexistent"):
        with pytest.raises(ValueError) as error:
            auth.login(db, name, "wrong-value", now=10)
        messages.append(str(error.value))
    assert messages[0] == messages[1]


def test_login_locks_and_recovers(db, owner, password):
    auth = load("stockdesk.services.auth")
    for _ in range(5):
        with pytest.raises(ValueError):
            auth.login(db, "owner", "wrong-value", now=100)
    with pytest.raises(ValueError) as error:
        auth.login(db, "owner", password, now=101)
    assert error.value.status == 429
    assert auth.login(db, "owner", password, now=401)["id"] == owner["id"]


def test_only_admin_creates_users(db, owner, viewer, password):
    with pytest.raises(ValueError) as error:
        load("stockdesk.services.auth").create_user(db, viewer["id"], "new-user", password, "admin")
    assert error.value.status == 403


def test_duplicate_username_is_conflict(db, owner, password):
    with pytest.raises(ValueError) as error:
        load("stockdesk.services.auth").create_user(db, owner["id"], "OWNER", password, "viewer")
    assert error.value.status == 409


@pytest.mark.parametrize("role,enabled", [("viewer", True), ("operator", True), ("admin", False)])
def test_last_admin_cannot_be_removed(db, owner, role, enabled):
    with pytest.raises(ValueError, match="last"):
        load("stockdesk.services.auth").set_user_access(db, owner["id"], owner["id"], role, enabled)


def test_demote_admin_when_second_admin_exists(db, owner, password):
    auth = load("stockdesk.services.auth")
    second = auth.create_user(db, owner["id"], "other-admin", password, "admin")
    changed = auth.set_user_access(db, owner["id"], owner["id"], "operator", True)
    assert changed["role"] == "operator"
    assert changed["session_version"] > owner["session_version"]
    assert auth.require_role(db, second["id"], "admin")["id"] == second["id"]


def test_disabled_user_cannot_login_or_act(db, owner, operator, password):
    auth = load("stockdesk.services.auth")
    auth.set_user_access(db, owner["id"], operator["id"], "operator", False)
    with pytest.raises(ValueError):
        auth.login(db, "operator", password)
    with pytest.raises(ValueError):
        auth.require_role(db, operator["id"], "operator")


def test_password_reset_revokes_old_sessions(db, owner, password):
    auth = load("stockdesk.services.auth")
    new = "replacement-" + secrets.token_urlsafe(18)
    updated = auth.set_password(db, owner["id"], owner["id"], new)
    assert updated["session_version"] == owner["session_version"] + 1
    with pytest.raises(ValueError):
        auth.login(db, "owner", password)
    assert auth.login(db, "owner", new)["id"] == owner["id"]


@pytest.mark.parametrize("password", ["", "short", "a" * 1025, None])
def test_short_or_oversized_password_rejected(db, password):
    with pytest.raises(ValueError):
        load("stockdesk.services.auth").bootstrap_user(db, "owner", password)


def test_audit_events_are_immutable(db, owner):
    for sql in ("DELETE FROM audit_events", "UPDATE audit_events SET action='changed'"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(sql)


def test_password_hash_records_supported_work_factor(password):
    auth = load("stockdesk.services.auth")
    encoded = auth.hash_password(password)
    parts = encoded.split("$")
    assert parts[:4] == ["scrypt", "16384", "8", "5"]
    assert auth.verify_password(password, encoded)
    assert not auth.verify_password(password, encoded.replace("$16384$", "$1073741824$"))
