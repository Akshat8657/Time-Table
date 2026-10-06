"""Active-term helpers. At any time the college runs ONLY odd or ONLY even semesters (plan Section 4)."""
from __future__ import annotations

from typing import Any

import asyncpg

from .. import db
from ..errors import ApiError


def term_name(kind: str, year: int) -> str:
    return f"{kind.capitalize()} {year}"


def parity_matches(kind: str, semester_number: int) -> bool:
    return (semester_number % 2 == 1) == (kind == "odd")


def term_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "year": row["year"],
        "name": term_name(row["kind"], row["year"]),
        "is_active": row["is_active"],
        "activated_at": row["activated_at"].isoformat() if row["activated_at"] else None,
    }


async def active_term() -> asyncpg.Record | None:
    return await db.fetchrow("select * from public.academic_terms where is_active")


async def require_active_term() -> asyncpg.Record:
    term = await active_term()
    if term is None:
        raise ApiError(
            409, "NO_ACTIVE_TERM",
            "No active term is selected yet. Please open 'Active Term' and choose Odd or Even + the year first.",
        )
    return term


def require_active_semester(term: asyncpg.Record, semester_number: int, label: str | None = None) -> None:
    """A class (semester) may only be used while its odd/even kind is the running term."""
    if not parity_matches(term["kind"], semester_number):
        running = "1, 3, 5, 7" if term["kind"] == "odd" else "2, 4, 6"
        raise ApiError(
            409, "SEMESTER_NOT_ACTIVE",
            f"{label or f'Semester {semester_number}'} is not running in the active term "
            f"({term_name(term['kind'], term['year'])}). Only semesters {running} can be used right now.",
        )


# --- one SQL snippet reused everywhere: semester + course + parity, optionally only the running ones
SEMESTER_SELECT = """
select sm.id, sm.school_id, sm.course_id, sm.number,
       c.name as course_name, c.code as course_code, c.is_active as course_active
from public.semesters sm
join public.courses c on c.id = sm.course_id
"""


def semester_label(course_name: str, number: int) -> str:
    return f"{course_name} - Sem {number}"
