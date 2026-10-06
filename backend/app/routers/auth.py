"""Login, refresh, logout, current user, change password."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Request, Response

from .. import db
from ..config import get_settings
from ..errors import ApiError
from ..schemas import AuthResponse, ChangePasswordRequest, LoginRequest, MeResponse, SessionOut, UserOut
from ..security import sessions
from ..security.deps import CurrentUser, bearer_token, require_user
from ..security.passwords import validate_new_password
from ..supabase_auth import AuthError

log = logging.getLogger("tt.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

PROFILE_SQL = """
select p.id as user_id, p.email, p.full_name, p.role, p.school_id, s.name as school_name, p.specialization,
       p.phone, p.photo_url, p.is_visiting, p.is_active, p.must_change_password
from public.profiles p
left join public.schools s on s.id = p.school_id
where p.id = $1
"""


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    if host in ("127.0.0.1", "::1"):  # behind the local dev proxy -> trust its forwarded address
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return host


def user_out(row: asyncpg.Record | dict[str, Any]) -> UserOut:
    return UserOut(
        id=str(row["user_id"]),
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        school_id=str(row["school_id"]) if row["school_id"] else None,
        school_name=row["school_name"],
        specialization=row["specialization"],
        phone=row["phone"],
        photo_url=row["photo_url"],
        is_visiting=row["is_visiting"],
        is_active=row["is_active"],
        must_change_password=row["must_change_password"],
    )


def current_user_out(user: CurrentUser, *, must_change_password: bool | None = None) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role,  # type: ignore[arg-type]
        school_id=user.school_id, school_name=user.school_name, specialization=user.specialization,
        phone=user.phone, photo_url=user.photo_url, is_visiting=user.is_visiting, is_active=user.is_active,
        must_change_password=user.must_change_password if must_change_password is None else must_change_password,
    )


def _login_failure(state: Any, ip: str, email: str, exc: AuthError) -> ApiError:
    """Turns a Supabase sign-in error into a friendly API error (and counts real failures)."""
    if exc.status == 429:
        return ApiError(
            429, "UPSTREAM_BUSY", "The sign-in service is busy. Please wait a minute and try again.",
            headers={"Retry-After": "60"}, extra={"retry_after_seconds": 60},
        )
    if exc.status >= 500 or exc.code == "AUTH_UNAVAILABLE":
        return ApiError(503, "AUTH_UNAVAILABLE", "We couldn't reach the sign-in service. Please try again in a moment.")
    if exc.code == "email_not_confirmed":
        return ApiError(403, "NOT_ACTIVATED", "Your account isn't activated yet. Please contact your administrator.")
    if exc.code == "user_banned" or "banned" in exc.message.lower():
        return ApiError(403, "ACCOUNT_DISABLED", "This account has been deactivated. Please contact your administrator.")
    state.login_limiter.failure(ip, email)
    log.warning("Failed login for %s from %s (%s)", email, ip, exc.code)
    return ApiError(401, "INVALID_CREDENTIALS", "Incorrect email or password. Please check them and try again.")


# ---------------------------------------------------------------------- login
@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> AuthResponse:
    settings = get_settings()
    state = request.app.state
    ip = client_ip(request)

    state.login_limiter.check(ip, body.email)  # 429 when someone keeps guessing

    try:
        tokens = await state.auth.sign_in_password(body.email, body.password)
    except AuthError as exc:
        raise _login_failure(state, ip, body.email, exc) from None

    claims = await state.verifier.verify(tokens["access_token"])
    row = await db.fetchrow(PROFILE_SQL, uuid.UUID(claims.user_id))
    if row is None:
        await sessions.delete_supabase_session(claims.session_id)
        raise ApiError(403, "NO_PROFILE", "Your account isn't fully set up yet. Please contact your administrator.")
    if not row["is_active"]:
        await sessions.delete_supabase_session(claims.session_id)
        raise ApiError(403, "ACCOUNT_DISABLED", "This account has been deactivated. Please contact your administrator.")

    try:
        absolute_expires_at = await sessions.create(
            settings,
            user_id=claims.user_id,
            supabase_session_id=claims.session_id,
            remember_me=body.remember_me,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except Exception:
        try:
            await sessions.delete_supabase_session(claims.session_id)
        finally:
            raise

    sessions.set_cookie(response, settings, tokens["refresh_token"], body.remember_me)
    state.login_limiter.success(body.email)
    log.info("Login: %s (%s) from %s, remember_me=%s", row["email"], row["role"], ip, body.remember_me)

    session = sessions.info(
        {"remember_me": body.remember_me, "last_activity_at": sessions.now_utc(), "absolute_expires_at": absolute_expires_at},
        settings,
    )
    return AuthResponse(
        access_token=tokens["access_token"],
        expires_in=int(tokens.get("expires_in", 3600)),
        user=user_out(row),
        session=SessionOut(**session),
    )


# ---------------------------------------------------------------------- refresh (used when the page loads / token is about to expire)
@router.post("/refresh", response_model=AuthResponse)
async def refresh(request: Request, response: Response) -> AuthResponse:
    settings = get_settings()
    state = request.app.state

    refresh_token = request.cookies.get(sessions.SESSION_COOKIE)
    if not refresh_token:
        raise ApiError(401, "NO_SESSION", "Please sign in.")

    try:
        tokens = await state.auth.refresh(refresh_token)
    except AuthError as exc:
        if exc.status == 429 or exc.status >= 500 or exc.code == "AUTH_UNAVAILABLE":
            raise ApiError(503, "AUTH_UNAVAILABLE", "We couldn't reach the sign-in service. Please try again in a moment.") from None
        raise ApiError(
            401, "INVALID_SESSION", "Your session has ended. Please sign in again.", clear_session_cookie=True
        ) from None

    claims = await state.verifier.verify(tokens["access_token"])
    row = await sessions.load(claims.session_id)
    if row is None or str(row["user_id"]) != claims.user_id:
        await sessions.delete_supabase_session(claims.session_id)  # a session we never issued
        raise ApiError(401, "INVALID_SESSION", "Your session has ended. Please sign in again.", clear_session_cookie=True)

    reason = sessions.expiry_reason(row, settings)
    if reason is not None:
        if row["revoked_at"] is None:
            await sessions.revoke(claims.session_id, reason)
        raise sessions.expired_error(reason)
    if not row["is_active"]:
        await sessions.revoke(claims.session_id, "account_disabled")
        raise ApiError(
            403, "ACCOUNT_DISABLED", "This account has been deactivated. Please contact your administrator.",
            clear_session_cookie=True,
        )

    # NOTE: refreshing is NOT user activity (it can happen in the background), so it does not reset the idle clock.
    sessions.set_cookie(response, settings, tokens["refresh_token"], row["remember_me"])
    return AuthResponse(
        access_token=tokens["access_token"],
        expires_in=int(tokens.get("expires_in", 3600)),
        user=user_out(row),
        session=SessionOut(**sessions.info(row, settings)),
    )


# ---------------------------------------------------------------------- logout
@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    settings = get_settings()
    state = request.app.state

    session_id: str | None = None
    token = bearer_token(request)
    if token:
        try:
            session_id = (await state.verifier.verify(token, allow_expired=True)).session_id
        except ApiError:
            session_id = None
    if session_id is None:
        refresh_token = request.cookies.get(sessions.SESSION_COOKIE)
        if refresh_token:
            try:
                session_id = await sessions.session_id_for_refresh_token(refresh_token)
            except Exception:
                log.exception("Could not look up the session from the cookie")

    if session_id:
        try:
            await sessions.revoke(session_id, "logout")
        except Exception:
            log.exception("Could not revoke the session on logout")

    sessions.clear_cookie(response, settings)
    return {"ok": True}


# ---------------------------------------------------------------------- who am I (also the "I'm still here" heartbeat)
@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(require_user(allow_pending_password=True))) -> MeResponse:
    settings = get_settings()
    session = sessions.info(
        {"remember_me": user.remember_me, "last_activity_at": user.last_activity_at, "absolute_expires_at": user.absolute_expires_at},
        settings,
    )
    return MeResponse(user=current_user_out(user), session=SessionOut(**session))


# ---------------------------------------------------------------------- change password (also the forced first-login change)
@router.post("/change-password", response_model=MeResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(require_user(allow_pending_password=True)),
) -> MeResponse:
    settings = get_settings()
    state = request.app.state
    ip = client_ip(request)

    state.pw_limiter.check(ip, user.id)

    problem = validate_new_password(body.new_password, body.current_password, user.email)
    if problem:
        raise ApiError(422, "WEAK_PASSWORD", problem, extra={"fields": {"new_password": problem}})

    # 1) prove the person knows the current password
    try:
        check = await state.auth.sign_in_password(user.email, body.current_password)
    except AuthError as exc:
        if exc.status == 429 or exc.status >= 500 or exc.code == "AUTH_UNAVAILABLE":
            raise ApiError(503, "AUTH_UNAVAILABLE", "We couldn't reach the sign-in service. Please try again in a moment.") from None
        state.pw_limiter.failure(ip, user.id)
        raise ApiError(
            400, "CURRENT_PASSWORD_INCORRECT", "Your current password is incorrect.",
            extra={"fields": {"current_password": "Your current password is incorrect."}},
        ) from None
    try:  # that check created a throw-away Supabase session - remove it again
        throwaway = await state.verifier.verify(check["access_token"])
        await sessions.delete_supabase_session(throwaway.session_id)
    except Exception:
        log.warning("Could not remove the verification session", exc_info=True)

    # 2) set the new password (Supabase Auth stores it - we never do). Done with the person's OWN session
    #    token so that this session stays alive (the admin key would delete every session of the user).
    try:
        await state.auth.update_own_password(bearer_token(request) or "", body.new_password)
    except AuthError as exc:
        if exc.code in ("weak_password", "same_password"):
            msg = "Please choose a stronger password." if exc.code == "weak_password" else "Your new password must be different."
            raise ApiError(422, "WEAK_PASSWORD", msg, extra={"fields": {"new_password": msg}}) from None
        if exc.code == "reauthentication_needed":
            raise ApiError(
                403, "REAUTH_NEEDED",
                "For your security, please sign out, sign in again, and then change your password.",
            ) from None
        log.warning("Password update failed: %s %s", exc.status, exc.code)
        raise ApiError(503, "AUTH_UNAVAILABLE", "We couldn't update the password right now. Please try again.") from None

    # 3) first-login flag off, every other device signed out
    await db.execute("update public.profiles set must_change_password = false where id = $1", uuid.UUID(user.id), retry=True)
    await sessions.revoke_others(user.id, user.session_id, "password_changed")
    state.pw_limiter.success(user.id)
    log.info("Password changed for %s", user.email)

    session = sessions.info(
        {"remember_me": user.remember_me, "last_activity_at": sessions.now_utc(), "absolute_expires_at": user.absolute_expires_at},
        settings,
    )
    return MeResponse(user=current_user_out(user, must_change_password=False), session=SessionOut(**session))
