"""Dashboards (one per role). Every route is locked to the right roles; an HOD only ever sees his own school."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends

from .. import db
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..validation import hm

router = APIRouter(prefix="/api", tags=["dashboards"])


async def _upcoming(school_id: uuid.UUID | None, *, all_schools: bool = False, limit: int = 4) -> list[dict[str, Any]]:
    """The next holidays / events: college-wide ones + (the HOD's school | every school for the admin)."""
    if all_schools:
        rows = await db.fetch(
            """
            select e.title, e.kind, e.date_from, e.date_to, e.full_day, e.start_time, e.end_time, e.school_id, s.name as school_name
            from public.calendar_entries e left join public.schools s on s.id = e.school_id
            where e.date_to >= (now() at time zone 'Asia/Kolkata')::date order by e.date_from, e.title limit $1
            """,
            limit,
        )
    else:
        rows = await db.fetch(
            """
            select e.title, e.kind, e.date_from, e.date_to, e.full_day, e.start_time, e.end_time, e.school_id, s.name as school_name
            from public.calendar_entries e left join public.schools s on s.id = e.school_id
            where e.date_to >= (now() at time zone 'Asia/Kolkata')::date and (e.school_id is null or e.school_id = $2)
            order by e.date_from, e.title limit $1
            """,
            limit, school_id,
        )
    return [
        {
            "title": r["title"], "kind": r["kind"], "date_from": r["date_from"].isoformat(), "date_to": r["date_to"].isoformat(),
            "full_day": r["full_day"], "start_time": hm(r["start_time"]), "end_time": hm(r["end_time"]),
            "scope": "college" if r["school_id"] is None else "school", "school_name": r["school_name"],
        }
        for r in rows
    ]


SETUP_SQL = """
select
  (select count(*) from public.profiles where school_id = $1 and role = 'teacher' and is_active) as teachers,
  (select count(*) from public.semesters sm join public.courses c on c.id = sm.course_id
    where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')) as classes,
  (select count(*) from public.subjects s join public.semesters sm on sm.id = s.semester_id
    where s.school_id = $1 and (sm.number % 2 = 1) = ($2 = 'odd')) as subjects,
  (select count(*) from public.assignments a join public.subjects s on s.id = a.subject_id join public.semesters sm on sm.id = s.semester_id
    where a.school_id = $1 and a.term_id = $3 and (sm.number % 2 = 1) = ($2 = 'odd')) as assigned,
  (select count(distinct f.semester_id) from public.fixed_slots f where f.school_id = $1 and f.term_id = $3 and f.kind = 'PE') as classes_with_pe,
  (select count(distinct f.semester_id) from public.fixed_slots f where f.school_id = $1 and f.term_id = $3 and f.kind = 'SOL') as classes_with_sol,
  (select count(*) from public.term_semesters t where t.school_id = $1 and t.term_id = $3) as dates_set,
  (select count(*) from public.semesters sm join public.courses c on c.id = sm.course_id
    where sm.school_id = $1 and c.is_active and sm.no_pe_sol and (sm.number % 2 = 1) = ($2 = 'odd')) as no_pe_sol_classes
"""


async def _setup(school_id: uuid.UUID, term) -> dict[str, Any]:
    if term is None:
        return {"term_set": False}
    r = await db.fetchrow(SETUP_SQL, school_id, term["kind"], term["id"])
    return {
        "term_set": True,
        "teachers": r["teachers"], "classes": r["classes"], "subjects": r["subjects"], "assigned": r["assigned"],
        "classes_with_pe": r["classes_with_pe"], "classes_with_sol": r["classes_with_sol"], "dates_set": r["dates_set"],
        "no_pe_sol_classes": r["no_pe_sol_classes"],
    }


@router.get("/admin/overview")
async def admin_overview(user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    schools, counts, term, upcoming = await asyncio.gather(
        db.fetch("select id, name, is_active from public.schools order by name"),
        db.fetchrow(
            """
            select count(*) filter (where role = 'admin')   as admins,
                   count(*) filter (where role = 'hod')     as hods,
                   count(*) filter (where role = 'teacher') as teachers
            from public.profiles where is_active
            """
        ),
        term_svc.active_term(),
        _upcoming(None, all_schools=True),
    )
    return {
        "scope": "college",
        "schools": [{"id": str(s["id"]), "name": s["name"], "is_active": s["is_active"]} for s in schools],
        "counts": {"admins": counts["admins"], "hods": counts["hods"], "teachers": counts["teachers"]},
        "active_term": term_svc.term_out(term) if term else None,
        "upcoming": upcoming,
    }


@router.get("/hod/overview")
async def hod_overview(user: CurrentUser = Depends(require_roles("hod", "admin"))) -> dict:
    term = await term_svc.active_term()
    if user.role == "admin":  # admin can do everything an HOD can, in every school
        schools, upcoming = await asyncio.gather(
            db.fetch("select id, name from public.schools where is_active order by name"), _upcoming(None, all_schools=True)
        )
        return {
            "scope": "all-schools", "school": None, "schools": [{"id": str(s["id"]), "name": s["name"]} for s in schools],
            "active_term": term_svc.term_out(term) if term else None, "upcoming": upcoming, "setup": None,
        }
    school_id = uuid.UUID(user.school_id)  # type: ignore[arg-type]
    school, teachers, setup, upcoming = await asyncio.gather(
        db.fetchrow("select id, name from public.schools where id = $1", school_id),
        db.fetchval("select count(*) from public.profiles where school_id = $1 and role = 'teacher' and is_active", school_id),
        _setup(school_id, term),
        _upcoming(school_id),
    )
    return {
        "scope": "school",
        "school": {"id": str(school["id"]), "name": school["name"]} if school else None,
        "counts": {"teachers": teachers},
        "active_term": term_svc.term_out(term) if term else None,
        "setup": setup,
        "upcoming": upcoming,
    }


@router.get("/teacher/overview")
async def teacher_overview(user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    # HOD is also a teacher, so HOD gets Teacher Mode too.
    school_id = uuid.UUID(user.school_id) if user.school_id else None
    term, upcoming = await asyncio.gather(term_svc.active_term(), _upcoming(school_id))
    return {
        "scope": "me",
        "mode": "hod-teacher-mode" if user.role == "hod" else "teacher",
        "school": {"id": user.school_id, "name": user.school_name},
        "active_term": term_svc.term_out(term) if term else None,
        "upcoming": upcoming,
    }
