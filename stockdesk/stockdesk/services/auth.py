"""Account policy, scrypt password hashing, throttling and session revocation."""

import hashlib
import hmac
import secrets
import sqlite3
import time

from ..db import atomic
from ..errors import DomainError, conflict
from ..validation import choice, code, flag
from .common import audit, row

ROLES = ("admin", "operator", "viewer")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 5
LOCK_SECONDS = 300


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise DomainError("Password must contain 12 to 1024 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64*1024*1024)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        # Accept only the versioned cost shipped here; reject malicious work factors.
        if (algorithm != "scrypt" or (n, r, p) != (str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P))
                or len(salt) != 32 or len(expected) != 64):
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
                                maxmem=64*1024*1024)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, UnicodeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def public_user(user: dict | sqlite3.Row) -> dict:
    return {k: user[k] for k in ("id", "username", "role", "enabled", "session_version", "created_at")}


def require_role(connection: sqlite3.Connection, actor: int, *roles: str) -> dict:
    user = row(connection, "users", actor)
    if not user["enabled"]:
        raise DomainError("Your account is disabled.", "unauthorized", 401)
    if roles and user["role"] not in roles:
        raise DomainError("Your role does not allow this action.", "forbidden", 403)
    return public_user(user)


def _insert_user(connection: sqlite3.Connection, username: str, encoded: str, role: str) -> dict:
    try:
        cursor = connection.execute("INSERT INTO users(username,password_hash,role) VALUES (?,?,?)",
                                    (username.lower(), encoded, role))
    except sqlite3.IntegrityError as exc:
        raise conflict("That username already exists.") from exc
    return public_user(row(connection, "users", cursor.lastrowid))


def bootstrap_user(connection: sqlite3.Connection, username: str, password: str) -> dict:
    username = code(username, "Username")
    encoded = hash_password(password)
    with atomic(connection):
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise conflict("An account already exists. Use an administrator account.")
        user = _insert_user(connection, username, encoded, "admin")
        audit(connection, user["id"], "bootstrap", "user", user["id"])
        return user


def create_user(connection: sqlite3.Connection, actor: int, username: str,
                password: str, role: str = "viewer") -> dict:
    username = code(username, "Username")
    role = choice(role, "Role", ROLES)
    with atomic(connection):
        require_role(connection, actor, "admin")
        user = _insert_user(connection, username, hash_password(password), role)
        audit(connection, actor, "create", "user", user["id"], {"role": role})
        return user


def set_user_access(connection: sqlite3.Connection, actor: int, user_id: int,
                    role: str, enabled: bool) -> dict:
    role = choice(role, "Role", ROLES)
    flag(enabled)
    with atomic(connection):
        require_role(connection, actor, "admin")
        user = row(connection, "users", user_id)
        if user["role"] == "admin" and user["enabled"] and (role != "admin" or not enabled):
            count = connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
            if count == 1:
                raise conflict("The last enabled administrator cannot be disabled or demoted.")
        connection.execute("UPDATE users SET role=?,enabled=?,session_version=session_version+1 WHERE id=?",
                           (role, int(enabled), user_id))
        audit(connection, actor, "access_change", "user", user_id, {"role": role, "enabled": enabled})
        return public_user(row(connection, "users", user_id))


def set_password(connection: sqlite3.Connection, actor: int, user_id: int, password: str) -> dict:
    with atomic(connection):
        require_role(connection, actor, "admin")
        row(connection, "users", user_id)
        connection.execute("UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=?",
                           (hash_password(password), user_id))
        audit(connection, actor, "password_reset", "user", user_id)
        return public_user(row(connection, "users", user_id))


def login(connection: sqlite3.Connection, username: str, password: str, *, now: float | None = None) -> dict:
    if not isinstance(username, str) or not 1 <= len(username.strip()) <= 40:
        raise DomainError("Incorrect username or password.", "unauthorized", 401)
    name = username.strip().lower()
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()
    now = time.time() if now is None else float(now)
    result = None
    locked = False
    with atomic(connection):
        connection.execute("DELETE FROM login_attempts WHERE window_start < ? AND blocked_until < ?", (now-3600, now))
        attempts = connection.execute("SELECT * FROM login_attempts WHERE name_hash=?", (name_hash,)).fetchone()
        if attempts and attempts["blocked_until"] > now:
            locked = True
        else:
            user = connection.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            valid = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
            if user and user["enabled"] and valid:
                connection.execute("DELETE FROM login_attempts WHERE name_hash=?", (name_hash,))
                result = public_user(user)
            else:
                failures = attempts["failures"] + 1 if attempts and now-attempts["window_start"] < LOCK_SECONDS else 1
                start = attempts["window_start"] if failures > 1 else now
                until = now + LOCK_SECONDS if failures >= 5 else 0
                connection.execute(
                    "INSERT INTO login_attempts(name_hash,failures,window_start,blocked_until) VALUES (?,?,?,?) "
                    "ON CONFLICT(name_hash) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until",
                    (name_hash, failures, start, until),
                )
    # Raise outside the transaction so failed-attempt counters remain committed.
    if locked:
        raise DomainError("Too many login attempts. Try again later.", "rate_limited", 429)
    if result is None:
        raise DomainError("Incorrect username or password.", "unauthorized", 401)
    return result


def revoke_sessions(connection: sqlite3.Connection, actor: int) -> None:
    """Signing out revokes all sessions for this account, including copied cookies."""
    with atomic(connection):
        require_role(connection, actor)
        connection.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (actor,))
        audit(connection, actor, "sign_out_all", "user", actor)
