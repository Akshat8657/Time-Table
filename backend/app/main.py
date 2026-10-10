"""TimeTablePro API.

All business logic lives here (Python). The browser only shows screens and calls this API.
Every route (except login / refresh / logout / health) needs a valid token AND the right role.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, pgerrors, readcache
from .config import explain_config_error, get_settings
from .errors import ApiError
from .migrations import pending_versions
from .routers import auth as auth_routes
from .routers import (
    absence, adjust, assignments, assistant, audit_log, calendar, dashboards, drafts, exports, fixed_slots, generation, health, hours, imports, leave,
    notifications, prefs, profile, publish, reports, structure, subjects, subs, terms, timing, users, views,
)
from .security import sessions
from .services import gemini
from .security.jwt_verify import TokenVerifier
from .security.rate_limit import AttemptLimiter
from .supabase_auth import AuthError, SupabaseAuth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
# The timetable generator runs in a worker thread and is pure number-crunching. A shorter switch interval lets the web
# requests get the interpreter back sooner while a generation run is busy (no effect when nothing else is running).
sys.setswitchinterval(0.001)
log = logging.getLogger("tt")

try:
    settings = get_settings()
except (ValidationError, ValueError) as exc:
    print(explain_config_error(exc))
    sys.exit(1)

if settings.is_production and settings.production_problems():
    # fail safe: a production server with laptop settings must not start at all
    print("=" * 64)
    print(" APP_ENV=production, lekin .env me ye settings abhi theek nahi hain:")
    for problem in settings.production_problems():
        print("   - " + problem)
    print(" PRODUCTION_CHECKLIST.md dekho, .env theek karo, phir dobara start karo.")
    print("=" * 64)
    sys.exit(1)


# ---------------------------------------------------------------------- start-up / shut-down
async def _startup_checks(auth: SupabaseAuth) -> None:
    try:
        async with db.acquire() as conn:
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


async def _keepalive() -> None:
    """Every ~100 s touch the kept-warm database connections (the pooler would silently drop idle ones)."""
    while True:
        try:
            await asyncio.sleep(100)
            await db.keep_warm()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Database keep-alive failed")


async def _substitution_sweep() -> None:
    """Every ~60 s: close auto-offer rounds whose deadline (or the lecture start) has passed and alert the HOD to assign."""
    from datetime import datetime, timezone

    from .services import subflow

    while True:
        try:
            await asyncio.sleep(60)
            closed = await subflow.sweep(datetime.now(timezone.utc))
            if closed:
                readcache.bump()
                log.info("Closed %d substitution offer round(s) nobody accepted", closed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Substitution sweep failed")


async def _preference_sweep() -> None:
    """Every ~60 s: remind the teachers who have not filled yet (once per window) and close a window whose deadline has passed."""
    from datetime import datetime, timezone

    from .services import prefflow

    while True:
        try:
            await asyncio.sleep(60)
            done = await prefflow.sweep(datetime.now(timezone.utc))
            if done["closed"] or done["reminded"]:
                readcache.bump()
                log.info("Preference windows: closed %d, reminders sent to %d teacher(s)", done["closed"], done["reminded"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Preference window sweep failed")


async def _warm_cache() -> None:
    """Right after start-up, read the published timetables of every school once, so the first people to open their screens
    do not all wait for the database at the same moment."""
    from datetime import timedelta

    from .services import terms as term_svc
    from .services import views as vw
    from .services.viewdata import load_view_data

    try:
        term = await term_svc.active_term()
        if term is None:
            return
        today = vw.today_ist()
        monday = vw.monday_of(today)
        for row in await db.fetch("select id from public.schools where is_active"):
            for window in (None, (today, today), (monday, monday + timedelta(days=6))):
                await load_view_data(row["id"], term, window)
    except Exception:  # noqa: BLE001 - only a head start, never a reason to fail
        log.exception("Cache warm-up skipped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.keep_warm()  # open the spare connections now, not on somebody's first click
    auth = SupabaseAuth(settings)
    app.state.auth = auth
    app.state.verifier = TokenVerifier(settings, auth)
    app.state.login_limiter = AttemptLimiter(settings, prefix="login")
    app.state.pw_limiter = AttemptLimiter(settings, prefix="pw")
    await _startup_checks(auth)
    # a generation run only lives inside one server process: anything still 'running' after a restart is over
    await db.execute(
        "update public.generation_runs set status = 'failed', error = 'The server restarted while this run was working.', "
        "finished_at = now() where status = 'running'"
    )
    task = asyncio.create_task(_housekeeping())
    keepalive = asyncio.create_task(_keepalive())
    sweep = asyncio.create_task(_substitution_sweep())
    pref_sweep = asyncio.create_task(_preference_sweep())
    warm = asyncio.create_task(_warm_cache())
    log.info("TimeTablePro API is ready")
    try:
        yield
    finally:
        task.cancel()
        keepalive.cancel()
        sweep.cancel()
        pref_sweep.cancel()
        warm.cancel()
        await auth.aclose()
        await gemini.close()
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
    if exc.status_code == 413:
        return _error_response(413, "TOO_LARGE", "That is too much data for one request. Please send a smaller file.")
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
# POSTs that change nothing the timetable screens read (signing in, the bell, push devices, AI questions, probes)
_NO_DATA_CHANGE = ("/api/auth/", "/api/notifications/", "/api/push/", "/api/assistant/chat", "/api/assistant/choose",
                   "/api/assistant/cancel", "/api/hours/suggest", "/api/fixed-slots/check")


@app.middleware("http")
async def security_guard(request: Request, call_next):
    # Browsers always send Origin on POST/PUT/PATCH/DELETE. Anything not coming from OUR site is refused
    # (blocks cross-site request forgery, even from other localhost ports).
    # a NUL character is never part of real input (it only ever comes from someone probing): refuse it politely instead of
    # letting the database choke on it
    if "\x00" in unquote(request.url.query):
        return _error_response(422, "VALIDATION_ERROR", "Please check what you typed - it contains a character that is not allowed.")
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in settings.origins:
            log.warning("Blocked %s %s from foreign origin %s", request.method, request.url.path, origin)
            return _error_response(403, "BAD_ORIGIN", "This request was blocked for your safety.")
    started = time.monotonic()
    response = await call_next(request)
    if request.method in _UNSAFE_METHODS and response.status_code < 400 and not request.url.path.startswith(_NO_DATA_CHANGE):
        readcache.bump()   # something changed: cached timetable data is stale from now on
    took = time.monotonic() - started
    if took > 4.0:  # a slow screen is a bug: leave a trace (path only - never the query string or any data)
        log.warning("SLOW request: %s %s took %.1f s", request.method, request.url.path, took)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if settings.is_production:  # HTTPS only from now on (browsers remember it for a year)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if docs is None:  # the API only returns JSON; the docs page (if enabled) needs scripts
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


class BodyLimit:
    """Refuses request bodies bigger than MAX_REQUEST_MB before they are read into memory (a 1 GB upload cannot tie up the
    server). A declared Content-Length is checked at once; a streamed (chunked) body is read up to the limit and refused
    as soon as it goes over (browsers always declare the length, so this only ever meets scripts)."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _UNSAFE_METHODS:
            return await self.app(scope, receive, send)
        too_big = _error_response(413, "TOO_LARGE", "That is too much data for one request. Please send a smaller file.")
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    return await too_big(scope, receive, send)
            except ValueError:
                return await _error_response(400, "BAD_REQUEST", "The request could not be read.")(scope, receive, send)
            return await self.app(scope, receive, send)
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                return await too_big(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        pending = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

        async def replay():
            return pending.pop() if pending else await receive()

        await self.app(scope, replay, send)


# CORS is locked to localhost origins only. (Added after the guard => runs before it.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Retry-After", "Content-Disposition"],
    max_age=600,
)
# Only these Host names are served (protects against DNS-rebinding tricks).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)
# The outermost layer: an over-sized body is refused before anything else reads it.
app.add_middleware(BodyLimit, max_bytes=settings.max_request_bytes)

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
app.include_router(drafts.router)
app.include_router(drafts.conflicts_router)
app.include_router(generation.router)
app.include_router(publish.router)
app.include_router(views.router)
app.include_router(exports.router)
app.include_router(hours.router)
app.include_router(absence.router)
app.include_router(leave.router)
app.include_router(subs.router)
app.include_router(adjust.router)
app.include_router(prefs.router)
app.include_router(reports.router)
app.include_router(audit_log.router)
app.include_router(assistant.router)
app.include_router(notifications.router)
app.include_router(notifications.push_router)
app.include_router(calendar.router)
app.include_router(imports.router)
