"""Application factory. Nothing starts a server merely by importing this module."""

import logging
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from . import __version__
from .config import Settings, from_env
from .errors import DomainError
from .security import SecurityMiddleware, csrf_token

logger = logging.getLogger("stockdesk")


def money(value: int) -> str:
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(int(value)), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    app = FastAPI(title="StockDesk API", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.state.templates.env.filters["money"] = money
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="stockdesk_session",
                       max_age=settings.session_seconds, same_site="lax", https_only=settings.production)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.add_middleware(SecurityMiddleware, production=settings.production)

    def error_response(request: Request, error: DomainError):
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)
        if error.status == 401 and request.url.path != "/login":
            return RedirectResponse("/login", status_code=303)
        template = "login.html" if request.url.path == "/login" else "error.html"
        return app.state.templates.TemplateResponse(request=request, name=template,
            context={"user": None, "csrf_token": csrf_token(request), "error": error.message,
                     "title": "Request not completed", "currency": settings.currency}, status_code=error.status)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return error_response(request, DomainError("The request has missing or invalid fields."))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(request, DomainError("The requested page or method is not available.", "http_error", error.status_code))

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, error: sqlite3.IntegrityError):
        logger.warning("Database integrity constraint rejected a request: %s", type(error).__name__)
        return error_response(request, DomainError("The change conflicts with an existing record.", "conflict", 409))

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, error: sqlite3.OperationalError):
        logger.error("Database request failed: %s", type(error).__name__)
        return error_response(request, DomainError("The database is unavailable. Retry after checking the service.", "database_unavailable", 503))

    from .api import router as api_router
    from .web import router as web_router
    app.include_router(api_router)
    app.include_router(web_router)
    return app
