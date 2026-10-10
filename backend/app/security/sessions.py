"""Server-side login sessions on top of Supabase Auth sessions.

Supabase Auth gives us the identity and the tokens. This module adds what the free plan does not:
  * auto-expiry after inactivity (configurable, default 8 hours)
  * "Remember me" (much longer window + a persistent cookie)
  * a hard maximum lifetime per session
  * instant revocation on logout / password change (the Supabase session is deleted too)

The browser cookie holds ONLY the opaque Supabase refresh token - no email, no name, no personal data.
"""
from __future__ import annotations

import asyncio
import hashlib

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from fastapi import Response

from .. import db
from ..config import Settings
from ..errors import ApiError

log = logging.getLogger("tt.sessions")

SESSION_COOKIE = "tt_session"
COOKIE_PATH = "/api/auth"
TOUCH_EVERY = timedelta(seconds=30)
_EXPIRED_REASONS = {"idle", "absolute", "expired"}

SESSION_SQL = """
select s.supabase_session_id, s.user_id, s.remember_me, s.created_at, s.last_activity_at,
       s.absolute_expires_at, s.revoked_at, s.revoked_reason, s.refresh_hash, s.prev_refresh_hash, s.rotated_at,
       p.email, p.full_name, p.role, p.school_id, sc.name as school_name, p.specialization, p.phone,
       p.photo_url, p.is_visiting, p.is_active, p.must_change_password
from public.app_sessions s
join public.profiles p on p.id = s.user_id
left join public.schools sc on sc.id = p.school_id
where s.supabase_session_id = $1
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def idle_limit(settings: Settings, remember: bool) -> timedelta:
    return settings.remember_idle if remember else settings.idle_timeout


def absolute_limit(settings: Settings, remember: bool) -> timedelta:
    return settings.remember_absolute_max if remember else settings.absolute_max


# ---------------------------------------------------------------------- create / read
async def create(
    settings: Settings,
    *,
    user_id: str,
    supabase_session_id: str,
    remember_me: bool,
    ip: str | None,
    user_agent: str | None,
    refresh_token: str | None = None,
) -> datetime:
    """Records a new login session. Returns its absolute expiry time."""
    absolute_expires_at = now_utc() + absolute_limit(settings, remember_me)
    await db.execute(
        """
        insert into public.app_sessions (supabase_session_id, user_id, remember_me, absolute_expires_at, ip, user_agent, refresh_hash, rotated_at)
        values ($1, $2, $3, $4, $5, $6, $7, now())
        on conflict (supabase_session_id) do nothing
        """,
        uuid.UUID(supabase_session_id), uuid.UUID(user_id), remember_me, absolute_expires_at,
        (ip or "")[:64] or None, (user_agent or "")[:300] or None, token_hash(refresh_token) if refresh_token else None,
        retry=True,
    )
    return absolute_expires_at


# ---------------------------------------------------------------------- refresh-token reuse detection
REFRESH_GRACE = timedelta(seconds=10)   # two tabs refreshing at the very same moment both present the same (old) cookie


def token_hash(token: str) -> str:
    """Only a fingerprint of the refresh token is ever stored - never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_verdict(row: Any, presented: str, now: datetime) -> str:
    """'current' (the newest token), 'race' (the previous one, within a few seconds of the swap), 'unknown' (sessions made
    before this check existed) or 'stolen' (an older token turned up again - someone else has a copy of the cookie)."""
    current, previous, rotated_at = row.get("refresh_hash"), row.get("prev_refresh_hash"), row.get("rotated_at")
    h = token_hash(presented)
    if current is None:
        return "unknown"
    if h == current:
        return "current"
    if previous is not None and h == previous and rotated_at is not None and now - rotated_at <= REFRESH_GRACE:
        return "race"
    return "stolen"


async def rotate(supabase_session_id: str, new_refresh_token: str) -> None:
    await db.execute(
        "update public.app_sessions set prev_refresh_hash = refresh_hash, refresh_hash = $2, rotated_at = now() "
        "where supabase_session_id = $1 and refresh_hash is distinct from $2",
        uuid.UUID(supabase_session_id), token_hash(new_refresh_token),
    )


async def load(supabase_session_id: str) -> asyncpg.Record | None:
    return await db.fetchrow(SESSION_SQL, uuid.UUID(supabase_session_id))


# ---------------------------------------------------------------------- short in-memory cache of checked sessions
# Every API call must prove the session is alive. With the database ~0.5 s away that check would double the time of
# every click, so a checked session is remembered for a few seconds. EVERY way of ending / changing a session in this
# program (logout, deactivate, password reset / change, expiry clean-up) clears the cache at once - see invalidate_*.
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_inflight: dict[str, "asyncio.Future"] = {}
_CACHE_MAX = 2000


async def load_cached(settings: Settings, supabase_session_id: str) -> dict[str, Any] | asyncpg.Record | None:
    ttl = settings.session_cache_seconds
    if ttl <= 0:
        return await load(supabase_session_id)
    hit = _cache.get(supabase_session_id)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    # a screen fires several calls at once: they share ONE database check instead of each making its own
    pending = _inflight.get(supabase_session_id)
    if pending is not None:
        return await asyncio.shield(pending)
    task = asyncio.ensure_future(load(supabase_session_id))
    _inflight[supabase_session_id] = task
    try:
        row = await asyncio.shield(task)
    finally:
        _inflight.pop(supabase_session_id, None)
    if row is None:
        _cache.pop(supabase_session_id, None)
        return None
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    entry = dict(row)
    _cache[supabase_session_id] = (time.monotonic(), entry)
    return entry


def invalidate_session(supabase_session_id: str) -> None:
    _cache.pop(str(supabase_session_id), None)


def invalidate_user(user_id: str) -> None:
    uid = str(user_id)
    for key in [k for k, (_, row) in _cache.items() if str(row["user_id"]) == uid]:
        _cache.pop(key, None)


def clear_cache() -> None:
    _cache.clear()


def expiry_reason(row: asyncpg.Record, settings: Settings, now: datetime | None = None) -> str | None:
    """None = still valid. Otherwise: 'idle' | 'absolute' | 'revoked'."""
    now = now or now_utc()
    if row["revoked_at"] is not None:
        return "idle" if row["revoked_reason"] in _EXPIRED_REASONS else "revoked"
    if now >= row["absolute_expires_at"]:
        return "absolute"
    if now - row["last_activity_at"] > idle_limit(settings, row["remember_me"]):
        return "idle"
    return None


def expired_error(reason: str) -> ApiError:
    if reason == "idle":
        return ApiError(
            401, "SESSION_EXPIRED",
            "You were signed out because you were inactive for a while. Please sign in again.",
            extra={"reason": "idle"}, clear_session_cookie=True,
        )
    if reason == "absolute":
        return ApiError(
            401, "SESSION_EXPIRED",
            "Your session reached its maximum length. Please sign in again.",
            extra={"reason": "absolute"}, clear_session_cookie=True,
        )
    return ApiError(
        401, "SESSION_REVOKED", "You have been signed out. Please sign in again.", clear_session_cookie=True
    )


def info(row: asyncpg.Record | dict[str, Any], settings: Settings, *, last_activity_at: datetime | None = None) -> dict[str, int | bool]:
    """Session facts the browser needs (durations, not clock times, so clock differences never matter)."""
    now = now_utc()
    remember = bool(row["remember_me"])
    idle = idle_limit(settings, remember)
    last = last_activity_at or row["last_activity_at"]
    idle_left = idle - (now - last)
    abs_left = row["absolute_expires_at"] - now
    return {
        "remember_me": remember,
        "idle_timeout_seconds": int(idle.total_seconds()),
        "idle_expires_in_seconds": max(0, int(idle_left.total_seconds())),
        "absolute_expires_in_seconds": max(0, int(abs_left.total_seconds())),
    }


# ---------------------------------------------------------------------- update / revoke
async def touch(supabase_session_id: str) -> None:
    hit = _cache.get(str(supabase_session_id))
    if hit is not None:
        hit[1]["last_activity_at"] = now_utc()
    await db.execute(
        "update public.app_sessions set last_activity_at = now() where supabase_session_id = $1 and revoked_at is null",
        uuid.UUID(supabase_session_id),
        retry=True,
    )


async def revoke(supabase_session_id: str, reason: str) -> None:
    """Ends one session everywhere: our record AND the Supabase session (so its tokens stop working)."""
    sid = uuid.UUID(supabase_session_id)
    invalidate_session(supabase_session_id)
    async with db.transaction() as conn:
        await conn.execute(
            "update public.app_sessions set revoked_at = coalesce(revoked_at, now()), "
            "revoked_reason = coalesce(revoked_reason, $2) where supabase_session_id = $1",
            sid, reason,
        )
        await conn.execute("delete from auth.sessions where id = $1", sid)
    invalidate_session(supabase_session_id)  # again AFTER the commit: a request in between could have re-cached the old row


async def delete_supabase_session(supabase_session_id: str) -> None:
    invalidate_session(supabase_session_id)
    await db.execute("delete from auth.sessions where id = $1", uuid.UUID(supabase_session_id), retry=True)


async def revoke_others(user_id: str, keep_session_id: str, reason: str) -> None:
    """Sign the user out of every OTHER device (used after a password change)."""
    uid, keep = uuid.UUID(user_id), uuid.UUID(keep_session_id)
    invalidate_user(user_id)
    async with db.transaction() as conn:
        await conn.execute(
            "update public.app_sessions set revoked_at = now(), revoked_reason = $3 "
            "where user_id = $1 and supabase_session_id <> $2 and revoked_at is null",
            uid, keep, reason,
        )
        await conn.execute("delete from auth.sessions where user_id = $1 and id <> $2", uid, keep)
    invalidate_user(user_id)


async def revoke_all_for_user(user_id: str, reason: str) -> None:
    uid = uuid.UUID(user_id)
    invalidate_user(user_id)
    async with db.transaction() as conn:
        await conn.execute(
            "update public.app_sessions set revoked_at = now(), revoked_reason = $2 "
            "where user_id = $1 and revoked_at is null",
            uid, reason,
        )
        await conn.execute("delete from auth.sessions where user_id = $1", uid)
    invalidate_user(user_id)


async def session_id_for_refresh_token(refresh_token: str) -> str | None:
    row = await db.fetchrow("select session_id from auth.refresh_tokens where token = $1 limit 1", refresh_token)
    return str(row["session_id"]) if row and row["session_id"] else None


async def cleanup_expired(settings: Settings) -> int:
    """Housekeeping: really end sessions nobody came back to, and forget very old records."""
    clear_cache()
    tag = await db.execute(
        """
        with expired as (
          update public.app_sessions s
             set revoked_at = now(), revoked_reason = 'expired'
           where s.revoked_at is null
             and ( s.absolute_expires_at < now()
                   or s.last_activity_at < now() - (case when s.remember_me then $1::interval else $2::interval end) )
          returning s.supabase_session_id
        )
        delete from auth.sessions where id in (select supabase_session_id from expired)
        """,
        settings.remember_idle, settings.idle_timeout,
    )
    await db.execute("delete from public.app_sessions where revoked_at < now() - interval '14 days'")
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------- cookie
def set_cookie(response: Response, settings: Settings, refresh_token: str, remember_me: bool) -> None:
    """Remember me  -> persistent cookie.   Otherwise -> a session cookie that dies with the browser."""
    response.set_cookie(
        SESSION_COOKIE,
        refresh_token,
        max_age=int(settings.remember_idle.total_seconds()) if remember_me else None,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.cookie_samesite,
    )


def clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path=COOKIE_PATH, httponly=True, secure=settings.secure_cookies, samesite=settings.cookie_samesite
    )
