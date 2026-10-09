"""Timing setup (plan Section 7): college hours / lecture length / break / working days (admin),
lunch of a school (HOD of that school, admin may override), the DAY-WISE schedule of every semester
(each weekday: its own end time, or fully OFF), and the auto-built daily period grid with a live preview."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import audit, db
from ..errors import ApiError
from ..scope import load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services.grid import build_grid, to_min
from ..services.timing import DayRule
from ..validation import DAY_NAMES, DAY_SHORT, HHMM, hm

router = APIRouter(prefix="/api/timing", tags=["timing"])
ROLES = ("admin", "hod")


@router.get("")
async def get_timing(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id) if (user.role == "hod" or school_id is not None) else None
    if sid is None:
        college, term = await asyncio.gather(timing_svc.college_row(), term_svc.active_term())
        grid = timing_svc.make_grid(college, None, None)
        return {
            "college": timing_svc.college_out(college), "school": None, "grid": grid.as_dict(), "classes": [],
            "active_term": term_svc.term_out(term) if term else None,
        }
    college, lunch, school, term, day_rows = await asyncio.gather(
        timing_svc.college_row(),
        timing_svc.school_lunch(sid),
        db.fetchrow("select name from public.schools where id = $1", sid),
        term_svc.active_term(),
        db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", sid),
    )
    grid = timing_svc.make_grid(college, *lunch)
    classes: list[dict[str, Any]] = []
    if term is not None:
        rows = await db.fetch(
            """
            select sm.id, sm.number, c.name as course_name
            from public.semesters sm join public.courses c on c.id = sm.course_id
            where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            order by c.name, sm.number
            """,
            sid, term["kind"],
        )
        rules = timing_svc.rules_from_rows(day_rows)
        for r in rows:
            classes.append(
                {
                    "semester_id": str(r["id"]),
                    "course": r["course_name"],
                    "number": r["number"],
                    "label": term_svc.semester_label(r["course_name"], r["number"]),
                    "days": timing_svc.class_days(grid, college["days_per_week"], timing_svc.rules_of(rules, r["id"])),
                }
            )
    return {
        "college": timing_svc.college_out(college),
        "school": {"id": str(sid), "name": school["name"], "lunch_start": hm(lunch[0]), "lunch_end": hm(lunch[1])},
        "grid": grid.as_dict(),
        "classes": classes,
        "active_term": term_svc.term_out(term) if term else None,
    }


# ---------------------------------------------------------------------- live preview (nothing is saved)
class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    college_start: HHMM
    college_end: HHMM
    lecture_minutes: int = Field(ge=15, le=180)
    break_minutes: int = Field(ge=0, le=30)
    lunch_start: HHMM | None = None
    lunch_end: HHMM | None = None

    @model_validator(mode="after")
    def _ok(self):
        if self.college_end <= self.college_start:
            raise ValueError("The college must close after it opens.")
        if (self.lunch_start is None) != (self.lunch_end is None):
            raise ValueError("Please give both the lunch start and the lunch end.")
        if self.lunch_start is not None and self.lunch_end <= self.lunch_start:  # type: ignore[operator]
            raise ValueError("Lunch must end after it starts.")
        return self


@router.post("/preview")
async def preview(body: PreviewIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    grid = build_grid(body.college_start, body.college_end, body.lecture_minutes, body.break_minutes, body.lunch_start, body.lunch_end)
    return {"grid": grid.as_dict()}


# ---------------------------------------------------------------------- college-wide (admin only)
class CollegeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    college_start: HHMM
    college_end: HHMM
    lecture_minutes: int = Field(ge=15, le=180)
    break_minutes: int = Field(ge=0, le=30)
    days_per_week: int

    @model_validator(mode="after")
    def _ok(self):
        if self.days_per_week not in (5, 6):
            raise ValueError("Working days must be Monday-Friday (5) or Monday-Saturday (6).")
        if self.college_end <= self.college_start:
            raise ValueError("The college must close after it opens.")
        if to_min(self.college_end) - to_min(self.college_start) < self.lecture_minutes:
            raise ValueError("At least one lecture must fit between the opening and closing time.")
        return self


async def _broken_slots(school_id: uuid.UUID | None = None, semester_id: uuid.UUID | None = None) -> int:
    """How many locked slots of the active term are not possible with the CURRENT timing."""
    from .fixed_slots import problems_for_term

    found = await problems_for_term(school_id)
    # a combined session (one row per covered class) is ONE locked slot
    return len({s["group"] for s in found if s["problems"] and (semester_id is None or s["semester_id"] == str(semester_id))})


@router.put("/college")
async def save_college(body: CollegeIn, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    async with db.transaction() as conn:
        await conn.execute(
            """
            update public.college_settings
               set college_start = $1, college_end = $2, lecture_minutes = $3, break_minutes = $4, days_per_week = $5, updated_by = $6
            """,
            body.college_start, body.college_end, body.lecture_minutes, body.break_minutes, body.days_per_week, uuid.UUID(user.id),
        )
        await audit.record(
            conn, user, "update", "college_timing", "college",
            f"College timing set to {hm(body.college_start)}-{hm(body.college_end)}, {body.lecture_minutes} min lectures, "
            f"{body.break_minutes} min break, {'Mon-Fri' if body.days_per_week == 5 else 'Mon-Sat'}",
            details=body.model_dump(mode="json"),
        )
    broken, college = await asyncio.gather(_broken_slots(), timing_svc.college_row())
    warnings = []
    if broken:
        warnings.append(f"{broken} locked slot(s) are no longer possible with the new timing. Please check the Fixed Slots screen.")
    return {"college": timing_svc.college_out(college), "warnings": warnings}


# ---------------------------------------------------------------------- lunch (HOD of the school; admin may override)
class LunchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    lunch_start: HHMM | None = None
    lunch_end: HHMM | None = None

    @model_validator(mode="after")
    def _ok(self):
        if (self.lunch_start is None) != (self.lunch_end is None):
            raise ValueError("Please give both the lunch start and the lunch end (or leave both empty for no lunch break).")
        return self


@router.put("/school")
async def save_lunch(body: LunchIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, body.school_id)
    college = await timing_svc.college_row()
    fields: dict[str, str] = {}
    if body.lunch_start is not None:
        length = to_min(body.lunch_end) - to_min(body.lunch_start)  # type: ignore[arg-type]
        if length <= 0:
            fields["lunch_end"] = "Lunch must end after it starts."
        elif length < 10 or length > 120:
            fields["lunch_end"] = "Lunch should last between 10 minutes and 2 hours."
        if body.lunch_start < college["college_start"] or body.lunch_end > college["college_end"]:  # type: ignore[operator]
            fields["lunch_start"] = f"Lunch must be inside the college hours ({hm(college['college_start'])}-{hm(college['college_end'])})."
    if fields:
        raise ApiError(422, "VALIDATION_ERROR", "Please check the lunch time.", extra={"fields": fields})
    async with db.transaction() as conn:
        await conn.execute(
            """
            insert into public.school_timing (school_id, lunch_start, lunch_end, updated_by) values ($1, $2, $3, $4)
            on conflict (school_id) do update set lunch_start = excluded.lunch_start, lunch_end = excluded.lunch_end, updated_by = excluded.updated_by
            """,
            sid, body.lunch_start, body.lunch_end, uuid.UUID(user.id),
        )
        name = await conn.fetchval("select name from public.schools where id = $1", sid)
        text_ = "no lunch break" if body.lunch_start is None else f"lunch {hm(body.lunch_start)}-{hm(body.lunch_end)}"
        await audit.record(conn, user, "update", "school_timing", sid, f"{name}: {text_}", school_id=sid)
    grid = timing_svc.make_grid(college, body.lunch_start, body.lunch_end)
    broken = await _broken_slots(sid)
    warnings = list(grid.notes)
    if broken:
        warnings.append(f"{broken} locked slot(s) are no longer possible with this lunch time. Please check the Fixed Slots screen.")
    return {"lunch_start": hm(body.lunch_start), "lunch_end": hm(body.lunch_end), "grid": grid.as_dict(), "warnings": warnings}


# ---------------------------------------------------------------------- day-wise schedule of one semester
class DayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=6)
    is_off: bool = False
    end_time: HHMM | None = None

    @model_validator(mode="after")
    def _ok(self):
        if self.is_off and self.end_time is not None:
            raise ValueError("A day that is OFF has no end time.")
        return self


class DaysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[DayIn] = Field(min_length=1, max_length=6)


def describe_week(days: list[dict[str, Any]]) -> str:
    parts = [
        f"{d['short']} OFF" if d["is_off"] else f"{d['short']} till {d['end_time']}"
        for d in days if d["working"] and (d["is_off"] or d["end_time"])
    ]
    return ", ".join(parts) if parts else "full day on all working days"


@router.put("/semesters/{semester_id}/days")
async def save_days(semester_id: str, body: DaysIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """Sets the end time (or OFF) of the given weekdays for one semester. A day with no end time and not OFF = the normal full day."""
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    term, course, (grid, college) = await asyncio.gather(
        term_svc.require_active_term(),
        db.fetchval("select name from public.courses where id = $1", sem["course_id"]),
        timing_svc.school_grid(sem["school_id"]),
    )
    label = term_svc.semester_label(course, sem["number"])
    term_svc.require_active_semester(term, sem["number"], label)

    seen: set[int] = set()
    fields: dict[str, str] = {}
    for d in body.days:
        key = f"day-{d.day}"
        if d.day in seen:
            fields[key] = "This day is listed twice."
        seen.add(d.day)
        if d.day > college["days_per_week"]:
            fields[key] = f"{DAY_NAMES[d.day]} is not a working day in the college timing."
        elif d.end_time is not None:
            if d.end_time >= college["college_end"]:
                fields[key] = f"Choose a time before the college closing time ({hm(college['college_end'])}), or leave it as a full day."
            elif grid.available_periods(d.end_time) == 0:
                fields[key] = "With that end time not even one lecture would fit."
    if fields:
        raise ApiError(422, "VALIDATION_ERROR", "Please check the end times.", extra={"fields": fields})

    async with db.transaction() as conn:
        for d in body.days:
            if d.is_off or d.end_time is not None:
                await conn.execute(
                    """
                    insert into public.semester_days (semester_id, school_id, day, is_off, end_time, updated_by)
                    values ($1, $2, $3, $4, $5, $6)
                    on conflict (semester_id, day) do update set is_off = excluded.is_off, end_time = excluded.end_time, updated_by = excluded.updated_by
                    """,
                    sem["id"], sem["school_id"], d.day, d.is_off, d.end_time, uuid.UUID(user.id),
                )
            else:
                await conn.execute("delete from public.semester_days where semester_id = $1 and day = $2", sem["id"], d.day)
        rows = await conn.fetch("select semester_id, day, is_off, end_time from public.semester_days where semester_id = $1", sem["id"])
        days = timing_svc.class_days(grid, college["days_per_week"], timing_svc.rules_of(timing_svc.rules_from_rows(rows), sem["id"]))
        await audit.record(
            conn, user, "update", "semester_days", sem["id"], f"{label}: {describe_week(days)}", school_id=sem["school_id"],
        )
    broken = await _broken_slots(sem["school_id"], sem["id"])
    warnings = []
    if broken:
        warnings.append(f"{broken} locked slot(s) of this class no longer fit the new schedule. Please check the Fixed Slots screen.")
    return {"semester_id": str(sem["id"]), "days": days, "warnings": warnings}
