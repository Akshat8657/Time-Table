"""School-locking helpers.  RULE: an HOD only ever sees and changes rows of his OWN school - decided here, on the server."""
from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from . import db
from .errors import ApiError, forbidden
from .security.deps import CurrentUser

# table names below are fixed constants in our own code (never user input)
_SCHOOL_TABLES = {
    "courses", "semesters", "subjects", "elective_groups", "assignments", "fixed_slots", "calendar_entries",
}


def as_uuid(value: Any, what: str = "id") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(422, "VALIDATION_ERROR", f"That {what} is not valid.") from None


async def resolve_school(user: CurrentUser, school_id: Any | None) -> uuid.UUID:
    """Which school does this request work on?

    HOD / teacher : always their own. Asking for another school is refused (403).
    Admin         : must name the school; it has to exist.
    """
    if user.role in ("hod", "teacher"):
        if user.school_id is None:
            raise ApiError(403, "NO_SCHOOL", "Your account is not linked to a school yet. Please contact the administrator.")
        own = uuid.UUID(user.school_id)
        if school_id is not None and as_uuid(school_id, "school") != own:
            raise forbidden("You can only work with your own school.")
        return own
    if school_id is None:
        raise ApiError(422, "SCHOOL_REQUIRED", "Please choose a school first.")
    sid = as_uuid(school_id, "school")
    if not await db.fetchval("select exists(select 1 from public.schools where id = $1)", sid):
        raise ApiError(404, "NOT_FOUND", "That school was not found.")
    return sid


async def load_in_scope(table: str, row_id: Any, user: CurrentUser, *, what: str = "item") -> asyncpg.Record:
    """Loads one row by id. For an HOD, rows of another school simply 'do not exist' (404)."""
    assert table in _SCHOOL_TABLES
    row = await db.fetchrow(f"select * from public.{table} where id = $1", as_uuid(row_id, what))
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"That {what} was not found.")
    row_school = row["school_id"]
    if user.role != "admin" and (row_school is None or str(row_school) != user.school_id):
        raise ApiError(404, "NOT_FOUND", f"That {what} was not found.")
    return row


def require_writer(user: CurrentUser) -> None:
    """Only admin and HOD may change school data."""
    if user.role not in ("admin", "hod"):
        raise forbidden()
