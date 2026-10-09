"""PDF / Excel downloads of the PUBLISHED timetable (plan Section 19, Phase 4).

  GET /api/exports/class/{semester_id}?format=pdf|xlsx     one class                         (HOD own school, admin)
  GET /api/exports/teacher/{teacher_id}?format=pdf|xlsx    one teacher                       (HOD own school, admin)
  GET /api/exports/master?format=...&semester_id=&teacher_id=&day=     the whole school       (HOD own school, admin)
  GET /api/exports/my?format=pdf|xlsx                      my own timetable                   (teacher, HOD)
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .. import db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import exporter as ex
from ..services import terms as term_svc
from ..services import views as vw
from ..services.viewdata import ViewData, load_view_data
from ..validation import DAY_NAMES

router = APIRouter(prefix="/api/exports", tags=["exports"])
MANAGERS = ("admin", "hod")
TEACHING = ("teacher", "hod")
MIME = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
Format = Literal["pdf", "xlsx"]


async def _data(school_id: uuid.UUID) -> ViewData:
    term = await term_svc.require_active_term()
    data = await load_view_data(school_id, term)
    if not data.pubs:
        raise ApiError(409, "NOT_PUBLISHED", "Nothing is published yet - publish the timetable first, then export it.")
    return data


def _reply(content: bytes, fmt: str, stem: str) -> Response:
    return Response(content, media_type=MIME[fmt], headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'})


def _stem(data: ViewData, *parts: str) -> str:
    return ex.file_stem("Timetable", *parts, data.term["kind"], str(data.term["year"]))


@router.get("/class/{semester_id}")
async def export_class(semester_id: str, format: Format = "pdf", user: CurrentUser = Depends(require_roles(*MANAGERS))) -> Response:
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    data = await _data(sem["school_id"])
    if sem["id"] not in data.sems:
        raise ApiError(409, "SEMESTER_NOT_ACTIVE", "That class is not running in the active term.")
    if sem["id"] not in data.pubs:
        raise ApiError(409, "NOT_PUBLISHED", f"{data.sems[sem['id']].label} is not published yet.")
    cells = vw.build_cells(data, data.rows_of_class(sem["id"]), across_classes=False, sem_ids={sem["id"]})
    today = vw.today_ist()
    build = ex.class_pdf if format == "pdf" else ex.class_xlsx
    content = await asyncio.to_thread(build, data, sem["id"], cells, today)
    return _reply(content, format, _stem(data, data.sems[sem["id"]].label))


async def _teacher_export(data: ViewData, tid: uuid.UUID, fmt: str) -> Response:
    cells = vw.build_cells(data, data.rows_of_teacher(tid), across_classes=True, teacher_id=tid)
    build = ex.teacher_pdf if fmt == "pdf" else ex.teacher_xlsx
    content = await asyncio.to_thread(build, data, tid, cells, vw.today_ist())
    return _reply(content, fmt, _stem(data, "Teacher", data.person_name(tid) or "teacher"))


@router.get("/teacher/{teacher_id}")
async def export_teacher(teacher_id: str, format: Format = "pdf", user: CurrentUser = Depends(require_roles(*MANAGERS))) -> Response:
    tid = as_uuid(teacher_id, "teacher")
    row = await db.fetchrow("select school_id, role from public.profiles where id = $1", tid)
    if row is None or row["role"] not in ("teacher", "hod") or row["school_id"] is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    return await _teacher_export(await _data(row["school_id"]), tid, format)


@router.get("/my")
async def export_my(format: Format = "pdf", user: CurrentUser = Depends(require_roles(*TEACHING))) -> Response:
    sid = await resolve_school(user, None)
    return await _teacher_export(await _data(sid), uuid.UUID(user.id), format)


@router.get("/master")
async def export_master(
    format: Format = "pdf",
    school_id: str | None = None,
    semester_id: str | None = None,
    teacher_id: str | None = None,
    day: int | None = None,
    user: CurrentUser = Depends(require_roles(*MANAGERS)),
) -> Response:
    sid = await resolve_school(user, school_id)
    data = await _data(sid)
    sems = set(data.sems)
    notes: list[str] = []
    if semester_id:
        one = as_uuid(semester_id, "class")
        if one not in data.sems:
            raise ApiError(404, "NOT_FOUND", "That class was not found in this school.")
        sems = {one}
        notes.append(data.sems[one].label)
    tid = as_uuid(teacher_id, "teacher") if teacher_id else None
    if tid is not None:
        if tid not in data.people:
            raise ApiError(404, "NOT_FOUND", "That teacher was not found in this school.")
        notes.append("Teacher: " + (data.person_name(tid) or ""))
    days = list(range(1, min(6, data.days_per_week) + 1))
    if day is not None:
        if day not in days:
            raise ApiError(422, "VALIDATION_ERROR", "That day is not a working day.", extra={"fields": {"day": "Choose a working day."}})
        days = [day]
        notes.append(DAY_NAMES[day])
    rows = data.rows_of_teacher(tid) if tid else data.rows
    cells = vw.build_cells(data, rows, across_classes=True, teacher_id=tid, sem_ids=sems)
    classes = [data.sems[s] for s in sorted(sems, key=lambda x: data.sems[x].label)]
    text = " | ".join(notes)
    build = ex.master_pdf if format == "pdf" else ex.master_xlsx
    content = await asyncio.to_thread(build, data, cells, classes, days, text)
    return _reply(content, format, _stem(data, "School Master", data.school_name))
