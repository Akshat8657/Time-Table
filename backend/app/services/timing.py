"""Loads the timing settings from the database and answers "is this slot possible?" (plan Sections 7, 8)."""
from __future__ import annotations

import uuid
from datetime import time
from typing import Any

import asyncpg

from .. import db
from ..validation import DAY_NAMES, hm
from .grid import Grid, build_grid, to_hm, to_min


async def college_row() -> asyncpg.Record:
    return await db.fetchrow("select * from public.college_settings")


def college_out(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "college_start": hm(row["college_start"]),
        "college_end": hm(row["college_end"]),
        "lecture_minutes": row["lecture_minutes"],
        "break_minutes": row["break_minutes"],
        "days_per_week": row["days_per_week"],
    }


async def school_lunch(school_id: uuid.UUID | None) -> tuple[time | None, time | None]:
    if school_id is None:
        return None, None
    row = await db.fetchrow("select lunch_start, lunch_end from public.school_timing where school_id = $1", school_id)
    return (row["lunch_start"], row["lunch_end"]) if row else (None, None)


def make_grid(college: asyncpg.Record | dict[str, Any], lunch_start: time | None, lunch_end: time | None) -> Grid:
    return build_grid(
        college["college_start"], college["college_end"], college["lecture_minutes"], college["break_minutes"],
        lunch_start, lunch_end,
    )


async def school_grid(school_id: uuid.UUID) -> tuple[Grid, asyncpg.Record]:
    college = await college_row()
    ls, le = await school_lunch(school_id)
    return make_grid(college, ls, le), college


def _span(grid: Grid, first: int, length: int) -> str:
    a, b = grid.period(first), grid.period(first + length - 1)
    return f"{to_hm(a.start)}-{to_hm(b.end)}" if a and b else ""


def slot_problems(
    grid: Grid, days_per_week: int, early_release: time | None, day: int, start_period: int, length: int
) -> list[str]:
    """Why a locked slot is NOT possible (empty list = fine)."""
    problems: list[str] = []
    if day > days_per_week:
        working = "Monday to Friday" if days_per_week == 5 else "Monday to Saturday"
        problems.append(f"{DAY_NAMES[day]} is not a working day (the college works {working}).")
    last = start_period + length - 1
    total = len(grid.periods)
    if start_period < 1 or last > total:
        if total == 0:
            problems.append("The timing settings leave no lecture periods at all.")
        else:
            problems.append(
                f"The day only has {total} period(s) ({to_hm(grid.periods[0].start)}-{to_hm(grid.periods[-1].end)}), "
                f"so periods {start_period}-{last} do not exist."
                if length > 1 else f"The day only has {total} period(s), so period {start_period} does not exist."
            )
        return problems

    if early_release is not None:
        limit = to_min(early_release)
        late = [n for n in range(start_period, last + 1) if grid.periods[n - 1].end > limit]
        if late:
            p = grid.periods[late[0] - 1]
            problems.append(
                f"Period {late[0]} ({to_hm(p.start)}-{to_hm(p.end)}) is after this class's early-release time ({to_hm(limit)})."
            )
    if length > 1 and not grid.is_continuous(start_period, length):
        for n in range(start_period, last):
            a, b = grid.periods[n - 1], grid.periods[n]
            if b.start - a.end > grid.break_minutes:
                if grid.lunch and a.end <= grid.lunch[0] and grid.lunch[1] <= b.start:
                    problems.append(
                        f"Periods {n} and {n + 1} are separated by the lunch break ({to_hm(grid.lunch[0])}-{to_hm(grid.lunch[1])}), "
                        "so a double block cannot be placed here."
                    )
                else:
                    problems.append(f"Periods {n} and {n + 1} are not back-to-back, so a double block cannot be placed here.")
                break
    return problems


def time_range(grid: Grid, start_period: int, length: int) -> str | None:
    return _span(grid, start_period, length) or None
