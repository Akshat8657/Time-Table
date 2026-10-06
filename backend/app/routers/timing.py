"""Timing setup (plan Section 7): college hours / lecture length / break / working days (admin),
lunch of a school (HOD of that school, admin may override), semester-wise early release (HOD / admin),
and the auto-built daily period grid with a live preview."""
from __future__ import annotations

import uuid
from datetime import time
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
from ..validation import HHMM, hm

router = APIRouter(prefix="/api/timing", tags=["timing"])
ROLES = ("admin", "hod")


async def _classes(school_id: uuid.UUID, grid) -> list[dict[str, Any]]:
    term = await term_svc.active_term()
    if term is None:
        return []
    rows = await db.fetch(
        """
        select sm.id, sm.number, sm.early_release_time, c.name as course_name
        from public.semesters sm join public.courses c on c.id = sm.course_id
        where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
        order by c.name, sm.number
        """,
        school_id, term["kind"],
    )
    return [
        {
            "semester_id": str(r["id"]),
            "course": r["course_name"],
            "number": r["number"],
            "label": term_svc.semester_label(r["course_name"], r["number"]),
            "early_release_time": hm(r["early_release_time"]),
            "periods_available": grid.available_periods(r["early_release_time"]),
        }
        for r in rows
    ]


@router.get("")
async def get_timing(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    college = await timing_svc.college_row()
    school: dict[str, Any] | None = None
    classes: list[dict[str, Any]] = []
    if user.role == "hod" or school_id is not None:
        sid = await resolve_school(user, school_id)
        ls, le = await timing_svc.school_lunch(sid)
        name = await db.fetchval("select name from public.schools where id = $1", sid)
        school = {"id": str(sid), "name": name, "lunch_start": hm(ls), "lunch_end": hm(le)}
        grid = timing_svc.make_grid(college, ls, le)
        classes = await _classes(sid, grid)
    else:
        grid = timing_svc.make_grid(college, None, None)
    term = await term_svc.active_term()
    return {
        "college": timing_svc.college_out(college),
        "school": school,
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


async def _slot_problem_count(school_id: uuid.UUID | None = None) -> int:
    """How many locked slots of the active term are not possible with the CURRENT timing."""
    from .fixed_slots import problems_for_term

    return len([s for s in await problems_for_term(school_id) if s["problems"]])


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
    broken = await _slot_problem_count()
    warnings = []
    if broken:
        warnings.append(f"{broken} locked slot(s) are no longer possible with the new timing. Please check the Fixed Slots screen.")
    return {"college": timing_svc.college_out(await timing_svc.college_row()), "warnings": warnings}


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
    broken = await _slot_problem_count(sid)
    warnings = list(grid.notes)
    if broken:
        warnings.append(f"{broken} locked slot(s) are no longer possible with this lunch time. Please check the Fixed Slots screen.")
    return {"lunch_start": hm(body.lunch_start), "lunch_end": hm(body.lunch_end), "grid": grid.as_dict(), "warnings": warnings}


# ---------------------------------------------------------------------- early release (per semester)
class EarlyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    early_release_time: HHMM | None = None


@router.patch("/semesters/{semester_id}")
async def set_early_release(semester_id: str, body: EarlyIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    term = await term_svc.require_active_term()
    course = await db.fetchval("select name from public.courses where id = $1", sem["course_id"])
    label = term_svc.semester_label(course, sem["number"])
    term_svc.require_active_semester(term, sem["number"], label)

    grid, college = await timing_svc.school_grid(sem["school_id"])
    value: time | None = body.early_release_time
    if value is not None:
        if value >= college["college_end"]:
            raise ApiError(
                422, "VALIDATION_ERROR", f"Early release must be before the college closing time ({hm(college['college_end'])}).",
                extra={"fields": {"early_release_time": "Choose a time before closing, or clear it for a full day."}},
            )
        if grid.available_periods(value) == 0:
            raise ApiError(
                422, "VALIDATION_ERROR", "With that time, not even one lecture would fit for this class.",
                extra={"fields": {"early_release_time": "Too early - no lecture fits before it."}},
            )
    async with db.transaction() as conn:
        await conn.execute("update public.semesters set early_release_time = $2 where id = $1", sem["id"], value)
        await audit.record(
            conn, user, "update", "early_release", sem["id"],
            f"{label}: " + (f"early release at {hm(value)}" if value else "runs the full day"), school_id=sem["school_id"],
        )
    broken = [s for s in await _problems(sem["school_id"]) if s["problems"] and s["semester_id"] == str(sem["id"])]
    warnings = []
    if broken:
        warnings.append(f"{len(broken)} locked slot(s) of this class are after the new early-release time. Please check the Fixed Slots screen.")
    return {"early_release_time": hm(value), "periods_available": grid.available_periods(value), "warnings": warnings}


async def _problems(school_id):
    from .fixed_slots import problems_for_term

    return await problems_for_term(school_id)
