"""FastAPI dependencies that protect every route: valid token + live session + right role."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi import Depends, Request

from ..config import get_settings
from ..errors import ApiError, forbidden
from . import sessions


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    full_name: str
    role: str
    school_id: str | None
    school_name: str | None
    specialization: str | None
    phone: str | None
    photo_url: str | None
    is_visiting: bool
    is_active: bool
    must_change_password: bool
    # session facts
    session_id: str
    remember_me: bool
    last_activity_at: datetime
    absolute_expires_at: datetime


def bearer_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return None
    return value


async def authenticate(request: Request, *, allow_pending_password: bool, touch: bool = True) -> CurrentUser:
    token = bearer_token(request)
    if token is None:
        raise ApiError(401, "NOT_AUTHENTICATED", "Please sign in to continue.")

    settings = get_settings()
    claims = await request.app.state.verifier.verify(token)  # genuine, unexpired, right project

    row = await sessions.load(claims.session_id)
    if row is None or str(row["user_id"]) != claims.user_id:
        raise ApiError(
            401, "INVALID_SESSION", "Your session has ended. Please sign in again.", clear_session_cookie=True
        )

    reason = sessions.expiry_reason(row, settings)
    if reason is not None:
        if row["revoked_at"] is None:
            await sessions.revoke(claims.session_id, reason)
        raise sessions.expired_error(reason)

    if not row["is_active"]:
        await sessions.revoke(claims.session_id, "account_disabled")
        raise ApiError(
            403, "ACCOUNT_DISABLED",
            "This account has been deactivated. Please contact your administrator.",
            clear_session_cookie=True,
        )

    last_activity_at = row["last_activity_at"]
    now = sessions.now_utc()
    if touch and now - last_activity_at > sessions.TOUCH_EVERY:
        await sessions.touch(claims.session_id)
        last_activity_at = now

    user = CurrentUser(
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
        session_id=claims.session_id,
        remember_me=row["remember_me"],
        last_activity_at=last_activity_at,
        absolute_expires_at=row["absolute_expires_at"],
    )
    if user.must_change_password and not allow_pending_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "Please set a new password before continuing.")
    return user


def require_user(*, allow_pending_password: bool = False, touch: bool = True) -> Callable[..., CurrentUser]:
    async def dependency(request: Request) -> CurrentUser:
        return await authenticate(request, allow_pending_password=allow_pending_password, touch=touch)

    return dependency


def require_roles(*roles: str, touch: bool = True) -> Callable[..., CurrentUser]:
    """Route guard: signed in + live session + password already changed + role in `roles`."""
    allowed = set(roles)

    async def dependency(user: CurrentUser = Depends(require_user(touch=touch))) -> CurrentUser:
        if user.role not in allowed:
            raise forbidden()
        return user

    return dependency
