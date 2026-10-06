"""Assignment: which teacher teaches which subject (= which class) in the active term (plan Sections 5.2, 17).

A subject has a PRIMARY teacher and may have an optional CO-TEACHER (shared subjects): both appear on the timetable
and every clash check applies to both.

HARD RULE: a subject can only be given to a teacher who is allowed to teach it. The API refuses anything else,
and the database has foreign keys that refuse it too.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from .subjects import LIST_SQL, subject_out

router = APIRouter(prefix="/api/assignments", tags=["assignment"])
ROLES = ("admin", "hod")

# weekly lectures of a person: credits of the subjects they teach (primary OR co-teacher; PE / SOL credits do not count)
# + the length of their locked slots that are not already part of one of those subjects
LOAD_SQL = """
select p.id,
       coalesce((select sum(s.credits) from public.assignments a join public.subjects s on s.id = a.subject_id
                 where (a.teacher_id = p.id or a.co_teacher_id = p.id) and a.term_id = $2 and s.special_kind is null), 0) as credits_load,
       coalesce((select sum(f.length) from public.fixed_slots f left join public.subjects s on s.id = f.subject_id
                 where (f.teacher_id = p.id or f.co_teacher_id = p.id) and f.term_id = $2
                   and (f.subject_id is null or s.special_kind is not null)), 0) as locked_load
from public.profiles p
where p.school_id = $1
"""


async def _loads(school_id: uuid.UUID, term_id: uuid.UUID) -> dict[Any, int]:
    rows = await db.fetch(LOAD_SQL, school_id, term_id)
    return {r["id"]: int(r["credits_load"]) + int(r["locked_load"]) for r in rows}


@router.get("")
async def list_assignments(
    school_id: str | None = None,
    course_id: str | None = None,
    semester_id: str | None = None,
    q: str | None = None,
    user: CurrentUser = Depends(require_roles(*ROLES)),
) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"subjects": [], "teachers": [], "active_term": None}

    sql = LIST_SQL + " and (sm.number % 2 = 1) = ($3 = 'odd')"
    args: list[Any] = [sid, term["id"], term["kind"]]
    if course_id:
        args.append(as_uuid(course_id, "course"))
        sql += f" and c.id = ${len(args)}"
    if semester_id:
        args.append(as_uuid(semester_id, "class"))
        sql += f" and s.semester_id = ${len(args)}"
    if q and q.strip():
        args.append(f"%{q.strip()[:80].replace('%', ' ').replace('_', ' ')}%")
        sql += (f" and (s.name ilike ${len(args)} or s.code ilike ${len(args)} or coalesce(p.full_name, '') ilike ${len(args)}"
                f" or coalesce(cp.full_name, '') ilike ${len(args)})")
    rows, eligible_rows, teachers, loads = await asyncio.gather(
        db.fetch(sql + " order by c.name, sm.number, s.is_elective, s.name limit 1000", *args),
        db.fetch(
            """
            select ts.subject_id, p.id, p.full_name from public.teacher_subjects ts
            join public.profiles p on p.id = ts.teacher_id and p.is_active
            where ts.school_id = $1 order by p.full_name
            """,
            sid,
        ),
        db.fetch(
            "select id, full_name, role, is_visiting, visiting_days, max_weekly_hours from public.profiles "
            "where school_id = $1 and is_active order by lower(full_name)",
            sid,
        ),
        _loads(sid, term["id"]),
    )
    eligible: dict[Any, list[dict]] = {}
    for e in eligible_rows:
        eligible.setdefault(e["subject_id"], []).append({"id": str(e["id"]), "name": e["full_name"]})
    subjects = []
    for r in rows:
        item = subject_out(r)
        item["eligible_teachers"] = eligible.get(r["id"], [])
        subjects.append(item)
    return {
        "active_term": term_svc.term_out(term),
        "subjects": subjects,
        "teachers": [
            {
                "id": str(t["id"]), "name": t["full_name"], "role": t["role"], "is_visiting": t["is_visiting"],
                "visiting_days": list(t["visiting_days"] or []), "max_weekly_hours": t["max_weekly_hours"],
                "weekly_load": loads.get(t["id"], 0),
            }
            for t in teachers
        ],
    }


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    teacher_id: str
    co_teacher_id: str | None = None      # optional second teacher; null / missing = no co-teacher


@router.put("")
async def assign(body: AssignIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    subject = await load_in_scope("subjects", body.subject_id, user, what="subject")
    term, sem = await asyncio.gather(
        term_svc.require_active_term(),
        db.fetchrow(
            "select sm.number, c.name from public.semesters sm join public.courses c on c.id = sm.course_id where sm.id = $1",
            subject["semester_id"],
        ),
    )
    label = term_svc.semester_label(sem["name"], sem["number"])
    term_svc.require_active_semester(term, sem["number"], label)

    teacher_id = as_uuid(body.teacher_id, "teacher")
    co_id = as_uuid(body.co_teacher_id, "co-teacher") if body.co_teacher_id else None
    if co_id is not None and co_id == teacher_id:
        raise ApiError(
            422, "VALIDATION_ERROR", "The teacher and the co-teacher must be two different people.",
            extra={"fields": {"co_teacher_id": "Choose a different person."}},
        )
    wanted = [teacher_id] + ([co_id] if co_id else [])
    people_rows, able_rows = await asyncio.gather(
        db.fetch(
            "select id, full_name, school_id, is_active, is_visiting, max_weekly_hours from public.profiles "
            "where id = any($1::uuid[]) and role in ('teacher', 'hod')",
            wanted,
        ),
        db.fetch("select teacher_id from public.teacher_subjects where subject_id = $1 and teacher_id = any($2::uuid[])", subject["id"], wanted),
    )
    people = {r["id"]: r for r in people_rows}
    able = {r["teacher_id"] for r in able_rows}
    for tid, field in ((teacher_id, "teacher_id"), (co_id, "co_teacher_id")):
        if tid is None:
            continue
        person = people.get(tid)
        if person is None or str(person["school_id"]) != str(subject["school_id"]):
            raise ApiError(404, "NOT_FOUND", "That teacher was not found in this school.")
        if not person["is_active"]:
            raise ApiError(409, "TEACHER_INACTIVE", f"{person['full_name']} is deactivated and cannot be assigned.")
        if tid not in able:
            raise ApiError(
                422, "TEACHER_CANNOT_TEACH",
                f"{person['full_name']} cannot teach '{subject['name']}'. Add this subject to the teacher's "
                "'subjects they can teach' first (Teachers screen).",
                extra={"fields": {field: "This teacher is not allowed to teach this subject."}},
            )

    teacher = people[teacher_id]
    async with db.transaction() as conn:
        existing = await conn.fetchrow(
            "select id from public.assignments where term_id = $1 and subject_id = $2 for update", term["id"], subject["id"]
        )
        if existing is None:
            row = await conn.fetchrow(
                "insert into public.assignments (school_id, term_id, subject_id, teacher_id, co_teacher_id, created_by) "
                "values ($1, $2, $3, $4, $5, $6) returning id",
                subject["school_id"], term["id"], subject["id"], teacher_id, co_id, uuid.UUID(user.id),
            )
            action = "create"
            summary = f"Assigned {teacher['full_name']} to {subject['name']} ({label})"
        else:
            row = existing
            await conn.execute("update public.assignments set teacher_id = $2, co_teacher_id = $3 where id = $1", existing["id"], teacher_id, co_id)
            action = "update"
            summary = f"Changed the teacher of {subject['name']} ({label}) to {teacher['full_name']}"
        if co_id:
            summary += f" with co-teacher {people[co_id]['full_name']}"
        await audit.record(
            conn, user, action, "assignment", row["id"], summary, school_id=subject["school_id"],
            details={"term": term_svc.term_name(term["kind"], term["year"]), "subject": subject["code"]},
        )

    loads = await _loads(subject["school_id"], term["id"])  # after the commit, so the new assignment is counted
    warnings: list[str] = []
    for tid in wanted:
        p = people[tid]
        load = loads.get(tid, 0)
        if p["is_visiting"] and p["max_weekly_hours"] and load > p["max_weekly_hours"]:
            warnings.append(
                f"{p['full_name']} is visiting faculty with a limit of {p['max_weekly_hours']} hours per week, "
                f"but now has {load} hours of lectures."
            )
    return {"id": str(row["id"]), "warnings": warnings}


@router.delete("/{assignment_id}")
async def unassign(assignment_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("assignments", assignment_id, user, what="assignment")
    term = await term_svc.require_active_term()
    if old["term_id"] != term["id"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "Assignments of an old term are kept as history and cannot be removed.")
    info = await db.fetchrow(
        "select s.name, s.code, p.full_name from public.subjects s, public.profiles p where s.id = $1 and p.id = $2",
        old["subject_id"], old["teacher_id"],
    )
    async with db.transaction() as conn:
        await conn.execute("delete from public.assignments where id = $1", old["id"])
        await audit.record(
            conn, user, "delete", "assignment", old["id"],
            f"Removed {info['full_name']} from {info['name']} ({info['code']})", school_id=old["school_id"],
        )
    return {"deleted": True}
