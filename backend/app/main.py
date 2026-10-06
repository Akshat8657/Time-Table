"""TimeTablePro API.

All business logic lives here (Python). The browser only shows screens and calls this API.
Every route (except login / refresh / logout / health) needs a valid token AND the right role.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, pgerrors
from .config import explain_config_error, get_settings
from .errors import ApiError
from .migrations import pending_versions
from .routers import auth as auth_routes
from .routers import (
    assignments, calendar, dashboards, fixed_slots, health, imports, profile, structure, subjects, terms, timing, users,
)
from .security import sessions
from .security.jwt_verify import TokenVerifier
from .security.rate_limit import AttemptLimiter
from .supabase_auth import AuthError, SupabaseAuth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("tt")

try:
    settings = get_settings()
except (ValidationError, ValueError) as exc:
    print(explain_config_error(exc))
    sys.exit(1)


# ---------------------------------------------------------------------- start-up / shut-down
async def _startup_checks(auth: SupabaseAuth) -> None:
    try:
        async with db.pool().acquire() as conn:
            pending = await pending_versions(conn)
        if pending:
            log.error(
                "DATABASE UPDATE BAAKI HAI (%s). Backend folder me chalao:  python -m scripts.migrate",
                ", ".join(pending),
            )
    except Exception:
        log.exception("Could not check the database version")
    try:
        remote = await auth.settings()
        if not remote.get("disable_signup", False):
            log.warning(
                "SECURITY: public sign-up is ON in Supabase. Turn it OFF: "
                "Authentication -> Sign In / Providers -> 'Allow new users to sign up'."
            )
    except AuthError:
        log.warning("Could not read the Supabase auth settings (is the internet on?)")


async def _housekeeping() -> None:
    while True:
        try:
            ended = await sessions.cleanup_expired(settings)
            if ended:
                log.info("Ended %d login session(s) nobody came back to", ended)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Session clean-up failed")
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    auth = SupabaseAuth(settings)
    app.state.auth = auth
    app.state.verifier = TokenVerifier(settings, auth)
    app.state.login_limiter = AttemptLimiter(settings, prefix="login")
    app.state.pw_limiter = AttemptLimiter(settings, prefix="pw")
    await _startup_checks(auth)
    task = asyncio.create_task(_housekeeping())
    log.info("TimeTablePro API is ready")
    try:
        yield
    finally:
        task.cancel()
        await auth.aclose()
        await db.close_pool()


docs = "/api/docs" if settings.enable_api_docs else None
app = FastAPI(
    title="TimeTablePro API",
    lifespan=lifespan,
    docs_url=docs,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.enable_api_docs else None,
)


# ---------------------------------------------------------------------- helpers for error replies
def _error_response(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    clear_cookie: bool = False,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if extra:
        body.update(extra)
    response = JSONResponse(body, status_code=status, headers=headers)
    if clear_cookie:
        sessions.clear_cookie(response, settings)
    return response


def _field_message(err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    if kind in ("missing", "string_too_short"):
        return "This field is required."
    if kind == "string_too_long":
        return "That is too long."
    if kind == "value_error":
        return str(err.get("ctx", {}).get("error", "Invalid value")).removeprefix("Value error, ")
    if kind == "extra_forbidden":
        return "Unexpected field."
    return "Invalid value."


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        exc.status_code, exc.code, exc.message, headers=exc.headers, extra=exc.extra,
        clear_cookie=exc.clear_session_cookie,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # NOTE: we never echo the submitted values back (they could contain a password).
    fields: dict[str, str] = {}
    for err in exc.errors():
        parts = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        fields.setdefault(".".join(parts) or "_", _field_message(err))
    return _error_response(422, "VALIDATION_ERROR", "Please check the details you entered.", extra={"fields": fields})


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return _error_response(404, "NOT_FOUND", "That page doesn't exist.")
    if exc.status_code == 405:
        return _error_response(405, "METHOD_NOT_ALLOWED", "That action isn't allowed here.")
    return _error_response(exc.status_code, "HTTP_ERROR", "The request could not be completed.")


@app.exception_handler(asyncpg.PostgresError)
async def _database_error(request: Request, exc: asyncpg.PostgresError) -> JSONResponse:
    # last safety net: the database's own rules (unique / foreign key / check / lock) become friendly messages
    err = pgerrors.translate(exc)
    return _error_response(err.status_code, err.code, err.message, extra=err.extra)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _error_response(500, "SERVER_ERROR", "Something went wrong on our side. Please try again.")


# ---------------------------------------------------------------------- security middleware
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_guard(request: Request, call_next):
    # Browsers always send Origin on POST/PUT/PATCH/DELETE. Anything not coming from OUR site is refused
    # (blocks cross-site request forgery, even from other localhost ports).
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in settings.origins:
            log.warning("Blocked %s %s from foreign origin %s", request.method, request.url.path, origin)
            return _error_response(403, "BAD_ORIGIN", "This request was blocked for your safety.")
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if docs is None:  # the API only returns JSON; the docs page (if enabled) needs scripts
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


# CORS is locked to localhost origins only. (Added after the guard => runs before it.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Retry-After"],
    max_age=600,
)
# Only these Host names are served (protects against DNS-rebinding tricks).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)

app.include_router(health.router)
app.include_router(auth_routes.router)
app.include_router(dashboards.router)
app.include_router(terms.router)
app.include_router(structure.router)
app.include_router(users.router)
app.include_router(profile.router)
app.include_router(subjects.router)
app.include_router(assignments.router)
app.include_router(timing.router)
app.include_router(fixed_slots.router)
app.include_router(calendar.router)
app.include_router(imports.router)
