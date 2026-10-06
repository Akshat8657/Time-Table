"""Loads the timing settings from the database and answers "is this slot possible?" (plan Sections 7, 8)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import time
from typing import Any

import asyncpg

from .. import db
from ..validation import DAY_NAMES, DAY_SHORT, hm
from .grid import Grid, build_grid, to_hm, to_min


@dataclass(frozen=True)
class DayRule:
    """One weekday of one semester: fully OFF, or ending early (None = the normal college closing time)."""
    is_off: bool = False
    end_time: time | None = None


FULL_DAY = DayRule()


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
    college, lunch = await asyncio.gather(college_row(), school_lunch(school_id))
    return make_grid(college, *lunch), college


# ---------------------------------------------------------------------- day-wise rules per semester
async def day_rules(school_id: uuid.UUID) -> dict[tuple[uuid.UUID, int], DayRule]:
    rows = await db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", school_id)
    return {(r["semester_id"], r["day"]): DayRule(r["is_off"], r["end_time"]) for r in rows}


def rules_from_rows(rows: list[asyncpg.Record]) -> dict[tuple[uuid.UUID, int], DayRule]:
    return {(r["semester_id"], r["day"]): DayRule(r["is_off"], r["end_time"]) for r in rows}


def class_days(grid: Grid, days_per_week: int, rules: dict[int, DayRule]) -> list[dict[str, Any]]:
    """The week of one class for the screens: per day working?, off?, end time, how many periods it can use."""
    out = []
    for day in range(1, 7):
        rule = rules.get(day, FULL_DAY)
        working = day <= days_per_week
        out.append(
            {
                "day": day,
                "name": DAY_NAMES[day],
                "short": DAY_SHORT[day],
                "working": working,
                "is_off": rule.is_off,
                "end_time": hm(rule.end_time),
                "periods_available": 0 if (not working or rule.is_off) else grid.available_periods(rule.end_time),
            }
        )
    return out


def rules_of(all_rules: dict[tuple[uuid.UUID, int], DayRule], semester_id: uuid.UUID) -> dict[int, DayRule]:
    return {day: rule for (sid, day), rule in all_rules.items() if sid == semester_id}


# ---------------------------------------------------------------------- locked-slot rules
def _span(grid: Grid, first: int, length: int) -> str:
    a, b = grid.period(first), grid.period(first + length - 1)
    return f"{to_hm(a.start)}-{to_hm(b.end)}" if a and b else ""


def slot_problems(
    grid: Grid, days_per_week: int, rule: DayRule | None, day: int, start_period: int, length: int
) -> list[str]:
    """Why a locked slot is NOT possible (empty list = fine).

    A double block (2 consecutive periods) may sit around the short break or around LUNCH - that is allowed."""
    rule = rule or FULL_DAY
    problems: list[str] = []
    if day > days_per_week:
        working = "Monday to Friday" if days_per_week == 5 else "Monday to Saturday"
        problems.append(f"{DAY_NAMES[day]} is not a working day (the college works {working}).")
        return problems
    if rule.is_off:
        problems.append(f"This class has no lectures on {DAY_NAMES[day]} (the day is marked OFF).")
        return problems
    last = start_period + length - 1
    total = len(grid.periods)
    if start_period < 1 or last > total:
        if total == 0:
            problems.append("The timing settings leave no lecture periods at all.")
        elif length > 1:
            problems.append(
                f"The day only has {total} period(s) ({to_hm(grid.periods[0].start)}-{to_hm(grid.periods[-1].end)}), "
                f"so periods {start_period}-{last} do not exist."
            )
        else:
            problems.append(f"The day only has {total} period(s), so period {start_period} does not exist.")
        return problems

    if rule.end_time is not None:
        limit = to_min(rule.end_time)
        late = [n for n in range(start_period, last + 1) if grid.periods[n - 1].end > limit]
        if late:
            p = grid.periods[late[0] - 1]
            problems.append(
                f"Period {late[0]} ({to_hm(p.start)}-{to_hm(p.end)}) is after this class's end time on {DAY_NAMES[day]} ({to_hm(limit)})."
            )
    return problems


def slot_notes(grid: Grid, start_period: int, length: int) -> list[str]:
    """Friendly, non-blocking remarks about a slot."""
    notes: list[str] = []
    lunch = grid.lunch_inside(start_period, length) if length > 1 else None
    if lunch is not None:
        notes.append(f"This block runs around the lunch break ({to_hm(lunch[0])}-{to_hm(lunch[1])}). That is allowed.")
    return notes


def time_range(grid: Grid, start_period: int, length: int) -> str | None:
    return _span(grid, start_period, length) or None
