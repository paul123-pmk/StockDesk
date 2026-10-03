"""HTTP security boundaries, independent of any particular business endpoint."""

import hmac
import secrets
import sqlite3
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from starlette.responses import JSONResponse

from .db import SCHEMA_VERSION, connect
from .errors import DomainError
from .services.auth import require_role


class SecurityMiddleware:
    """Bound the entire request body, including requests without Content-Length."""

    def __init__(self, app, *, production: bool = False, maximum: int = 256*1024):
        self.app = app
        self.production = production
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy", b"default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
                    (b"cache-control", b"no-store"),
                ])
                if self.production:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        async def reject(status, code, message):
            response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
            await response(scope, receive, secured_send)

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                lengths = [int(value) for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
                if any(value < 0 for value in lengths) or len(set(lengths)) > 1:
                    raise ValueError
            except ValueError:
                return await reject(400, "bad_request", "Invalid request length.")
            if lengths and lengths[0] > self.maximum:
                return await reject(413, "too_large", "Request body exceeds 256 KiB.")
            chunks = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.maximum:
                    return await reject(413, "too_large", "Request body exceeds 256 KiB.")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            return await self.app(scope, replay, secured_send)
        return await self.app(scope, receive, secured_send)


def db_connection(request: Request) -> Iterator[sqlite3.Connection]:
    path = request.app.state.settings.database
    if not path.is_file():
        raise DomainError("Database is not initialized. Run the init command.", "not_ready", 503)
    connection = connect(path)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise DomainError("Database schema needs attention. Run the check command.", "not_ready", 503)
        yield connection
    finally:
        connection.close()


DB = Annotated[sqlite3.Connection, Depends(db_connection)]


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def check_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf")
    if not isinstance(expected, str) or not isinstance(supplied, str) or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise DomainError("Refresh the page and submit the form again.", "csrf_failed", 403)


def current_user(request: Request, db: DB) -> dict:
    user_id = request.session.get("user_id")
    issued_at = request.session.get("issued_at", 0)
    if type(user_id) is not int:
        raise DomainError("Sign in to continue.", "unauthorized", 401)
    if not isinstance(issued_at, (int, float)) or time.time()-issued_at > request.app.state.settings.session_seconds:
        request.session.clear()
        raise DomainError("Your session expired. Sign in again.", "unauthorized", 401)
    try:
        user = require_role(db, user_id)
    except DomainError as exc:
        request.session.clear()
        raise DomainError("Sign in to continue.", "unauthorized", 401) from exc
    if request.session.get("session_version") != user["session_version"]:
        request.session.clear()
        raise DomainError("Your account changed. Sign in again.", "unauthorized", 401)
    return user


User = Annotated[dict, Depends(current_user)]


def api_writer(request: Request, user: User) -> dict:
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    return user


Writer = Annotated[dict, Depends(api_writer)]


def establish_session(request: Request, user: dict) -> None:
    request.session.clear()
    request.session.update(user_id=user["id"], session_version=user["session_version"],
                           issued_at=time.time(), csrf=secrets.token_urlsafe(32))
