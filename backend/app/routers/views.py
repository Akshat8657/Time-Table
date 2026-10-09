"""The timetable VIEWS (plan Section 19, Phase 4) - always built from the PUBLISHED timetable, never from the drafts.

  Teacher / HOD-as-teacher     GET /api/views/my/today      today's own lectures (holidays and events shown)
                               GET /api/views/my/week       own full week: combined sessions, co-taught lectures, extra lectures
  HOD (own school) / admin     GET /api/views/options       classes and teachers that have a published timetable (for the pickers)
                               GET /api/views/class/{id}    one class, week
                               GET /api/views/teacher/{id}  one teacher, week
                               GET /api/views/master        the whole school, filters: class, teacher, day, today / week
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends

from .. import db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..services import views as vw
from ..services.changes import DayChanges, load_changes
from ..services.viewdata import ViewData, load_view_data
from ..validation import DAY_NAMES

router = APIRouter(prefix="/api/views", tags=["views"])
MANAGERS = ("admin", "hod")
TEACHING = ("teacher", "hod")


async def _data(school_id: uuid.UUID, window: tuple[date, date]) -> ViewData | None:
    term = await term_svc.active_term()
    if term is None:
        return None
    return await load_view_data(school_id, term, window)


async def _changes(data: ViewData, window: tuple[date, date]) -> DayChanges:
    return await load_changes(data.school_id, data.term["id"], window)


def _no_term() -> dict[str, Any]:
    return {"term": None, "published": False, "message": "No term is active yet."}


def _term_out(data: ViewData) -> dict[str, Any]:
    return term_svc.term_out(data.term)


def _published_info(data: ViewData, sem_ids: set[uuid.UUID] | None = None) -> dict[str, Any]:
    pubs = [p for s, p in data.pubs.items() if sem_ids is None or s in sem_ids]
    return {
        "published": bool(data.pubs),
        "publications": [
            {"semester_id": str(p["semester_id"]), "class_label": data.sems[p["semester_id"]].label if p["semester_id"] in data.sems else "",
             "version": p["version"], "published_at": p["published_at"].isoformat(), "published_by": p["published_by_name"],
             "source_draft_name": p["source_draft_name"]}
            for p in pubs
        ],
        "last_published_at": max((p["published_at"] for p in pubs), default=None).isoformat() if pubs else None,
    }


def _grid(data: ViewData) -> dict[str, Any]:
    return data.grid.as_dict()


LOWEST, HIGHEST = date(2000, 1, 1), date(2100, 12, 31)


def _sane(d: date | None, field: str) -> date | None:
    """A date far outside the calendar can only be a typing mistake (and would overflow the week arithmetic)."""
    if d is not None and not LOWEST <= d <= HIGHEST:
        raise ApiError(422, "VALIDATION_ERROR", "Please choose a date between the years 2000 and 2100.", extra={"fields": {field: "Date out of range."}})
    return d


def _monday(week_of: date | None) -> date:
    return vw.monday_of(_sane(week_of, "week_of") or vw.today_ist())


# ---------------------------------------------------------------------------------------------------- my timetable
@router.get("/my/today")
async def my_today(on: date | None = None, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    sid = await resolve_school(user, None)
    d = _sane(on, "on") or vw.today_ist()
    data = await _data(sid, (d, d))
    if data is None:
        return _no_term()
    me = uuid.UUID(user.id)
    weekday = d.isoweekday()
    working = weekday <= min(6, data.days_per_week)
    change = await _changes(data, (d, d))
    cells = (
        vw.build_cells(data, data.rows_of_teacher(me), across_classes=True, teacher_id=me, day=weekday, dates={weekday: d}, with_extras=True, change=change)
        if working else []
    )
    now = datetime.now(vw.IST)
    now_min = now.hour * 60 + now.minute
    nxt = None
    current = None
    if d == vw.today_ist():
        for c in cells:
            if c["status"] is not None or c.get("mine_absent"):
                continue
            a, b = data.period_minutes(c["start_period"], c["length"])
            if a <= now_min < b and current is None:
                current = c["key"]
            elif a > now_min and nxt is None:
                nxt = c["key"]
    weekly = vw.build_cells(data, data.rows_of_teacher(me), across_classes=True, teacher_id=me)
    return {
        "term": _term_out(data), **_published_info(data), "date": d.isoformat(), "weekday": weekday, "weekday_name": DAY_NAMES.get(weekday, "Sunday"),
        "is_today": d == vw.today_ist(), "is_working_day": working, "banners": vw.banners_for(data, d), "grid": _grid(data),
        "cells": cells, "stats": vw.stats_of(cells), "day_stats": vw.day_extra_stats(cells), "current_key": current, "next_key": nxt,
        "week_lectures": len(weekly),
    }


@router.get("/my/week")
async def my_week(week_of: date | None = None, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    sid = await resolve_school(user, None)
    monday = _monday(week_of)
    data = await _data(sid, (monday, monday + timedelta(days=6)))
    if data is None:
        return _no_term()
    me = uuid.UUID(user.id)
    dates = vw.week_dates(monday, data.days_per_week)
    change = await _changes(data, (monday, monday + timedelta(days=6)))
    cells = vw.build_cells(data, data.rows_of_teacher(me), across_classes=True, teacher_id=me, dates=dates, with_extras=True, change=change)
    mentioned = {uuid.UUID(c["id"]) for cell in cells for c in cell["classes"]}
    return {
        "term": _term_out(data), **_published_info(data, mentioned or None), "monday": monday.isoformat(), "days": vw.day_headers(data, dates),
        "grid": _grid(data), "cells": cells, "stats": vw.stats_of(cells), "day_stats": vw.day_extra_stats(cells), "owner": {"id": user.id, "name": user.full_name},
    }


# ---------------------------------------------------------------------------------------------------- HOD / admin views
@router.get("/options")
async def options(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    today = vw.today_ist()
    data = await _data(sid, (today, today))
    if data is None:
        return {**_no_term(), "classes": [], "teachers": []}
    per_teacher: dict[uuid.UUID, set] = {}
    for r in data.rows:
        for t in r.teachers:
            per_teacher.setdefault(t, set()).add(r.identity)
    return {
        "term": _term_out(data), "published": bool(data.pubs),
        "classes": [
            {"id": str(s.id), "label": s.label, "published": s.id in data.pubs,
             "version": data.pubs[s.id]["version"] if s.id in data.pubs else None,
             "published_at": data.pubs[s.id]["published_at"].isoformat() if s.id in data.pubs else None}
            for s in data.sems.values()
        ],
        "teachers": sorted(
            (
                {"id": str(p.id), "name": p.name, "role": p.role, "is_active": p.is_active, "is_visiting": p.is_visiting,
                 "lectures": len(per_teacher.get(p.id, ()))}
                for p in data.people.values()
            ),
            key=lambda t: t["name"].lower(),
        ),
    }


@router.get("/class/{semester_id}")
async def class_week(semester_id: str, week_of: date | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    monday = _monday(week_of)
    data = await _data(sem["school_id"], (monday, monday + timedelta(days=6)))
    if data is None:
        return _no_term()
    if sem["id"] not in data.sems:
        raise ApiError(409, "SEMESTER_NOT_ACTIVE", "That class is not running in the active term.")
    dates = vw.week_dates(monday, data.days_per_week)
    change = await _changes(data, (monday, monday + timedelta(days=6)))
    cells = vw.build_cells(data, data.rows_of_class(sem["id"]), across_classes=False, dates=dates, with_extras=True, sem_ids={sem["id"]}, change=change)
    return {
        "term": _term_out(data), **_published_info(data, {sem["id"]}), "monday": monday.isoformat(),
        "days": vw.day_headers(data, dates, sem["id"]), "grid": _grid(data), "cells": cells, "stats": vw.stats_of(cells), "day_stats": vw.day_extra_stats(cells),
        "owner": {"id": str(sem["id"]), "name": data.sems[sem["id"]].label, "school": data.school_name},
        "off_days": vw.day_off_map(data, [sem["id"]]),
    }


@router.get("/teacher/{teacher_id}")
async def teacher_week(teacher_id: str, week_of: date | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    tid = as_uuid(teacher_id, "teacher")
    row = await db.fetchrow("select school_id, full_name, role from public.profiles where id = $1", tid)
    if row is None or row["role"] not in ("teacher", "hod") or row["school_id"] is None:
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    if user.role != "admin" and str(row["school_id"]) != user.school_id:
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    monday = _monday(week_of)
    data = await _data(row["school_id"], (monday, monday + timedelta(days=6)))
    if data is None:
        return _no_term()
    dates = vw.week_dates(monday, data.days_per_week)
    change = await _changes(data, (monday, monday + timedelta(days=6)))
    cells = vw.build_cells(data, data.rows_of_teacher(tid), across_classes=True, teacher_id=tid, dates=dates, with_extras=True, change=change)
    mentioned = {uuid.UUID(c["id"]) for cell in cells for c in cell["classes"]}
    return {
        "term": _term_out(data), **_published_info(data, mentioned or None), "monday": monday.isoformat(), "days": vw.day_headers(data, dates),
        "grid": _grid(data), "cells": cells, "stats": vw.stats_of(cells), "day_stats": vw.day_extra_stats(cells),
        "owner": {"id": str(tid), "name": row["full_name"], "school": data.school_name},
    }


@router.get("/master")
async def master(
    school_id: str | None = None,
    scope: Literal["week", "today"] = "week",
    on: date | None = None,
    week_of: date | None = None,
    semester_id: str | None = None,
    teacher_id: str | None = None,
    day: int | None = None,
    user: CurrentUser = Depends(require_roles(*MANAGERS)),
) -> dict:
    sid = await resolve_school(user, school_id)
    if day is not None and not 1 <= day <= 6:
        raise ApiError(422, "VALIDATION_ERROR", "Day must be 1 (Monday) to 6 (Saturday).", extra={"fields": {"day": "Choose Monday to Saturday."}})
    if scope == "today":
        d = _sane(on, "on") or vw.today_ist()
        monday = vw.monday_of(d)
    else:
        monday = _monday(week_of)
        d = None
    data = await _data(sid, (monday, monday + timedelta(days=6)))
    if data is None:
        return _no_term()
    dates = vw.week_dates(monday, data.days_per_week)
    if d is not None:
        weekday = d.isoweekday()
        day = weekday if weekday in dates else 0  # 0 = Sunday / not a working day: nothing to show
    sems = set(data.sems)
    if semester_id:
        one = as_uuid(semester_id, "class")
        if one not in data.sems:
            raise ApiError(404, "NOT_FOUND", "That class was not found in this school.")
        sems = {one}
    tid = as_uuid(teacher_id, "teacher") if teacher_id else None
    if tid is not None and tid not in data.people:
        raise ApiError(404, "NOT_FOUND", "That teacher was not found in this school.")
    rows = data.rows_of_teacher(tid) if tid else data.rows
    change = await _changes(data, (monday, monday + timedelta(days=6)))
    cells = [] if day == 0 else vw.build_cells(
        data, rows, across_classes=True, teacher_id=tid, sem_ids=sems, day=day, dates=dates, with_extras=True, change=change
    )
    shown = [data.sems[s] for s in sorted(sems, key=lambda x: data.sems[x].label)]
    return {
        "term": _term_out(data), **_published_info(data), "scope": scope, "date": d.isoformat() if d else None, "monday": monday.isoformat(),
        "day": day, "days": vw.day_headers(data, dates), "grid": _grid(data),
        "classes": [{"id": str(s.id), "label": s.label, "published": s.id in data.pubs} for s in shown],
        "cells": cells, "stats": vw.stats_of(cells), "day_stats": vw.day_extra_stats(cells), "off_days": vw.day_off_map(data, [s.id for s in shown]),
        "filters": {"semester_id": semester_id, "teacher_id": teacher_id, "day": day},
        "school": {"id": str(data.school_id), "name": data.school_name},
    }
