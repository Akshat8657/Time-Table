"""College setup: schools, courses and semesters (plan Sections 4, 5.1, 6).  Admin edits; HOD only reads his own school."""
from __future__ import annotations

import asyncio
import uuid
from datetime import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..validation import optional_text, text

router = APIRouter(prefix="/api/structure", tags=["college-setup"])


# ---------------------------------------------------------------------- read (admin: all schools, HOD: own school)
@router.get("/tree")
async def tree(school_id: str | None = None, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    if user.role == "hod":
        school_ids = [await resolve_school(user, school_id)]
    elif school_id is not None:
        school_ids = [await resolve_school(user, school_id)]
    else:
        school_ids = [r["id"] for r in await db.fetch("select id from public.schools order by name")]

    schools, courses, sems, term = await asyncio.gather(
        db.fetch(
            """
            select s.id, s.name, s.is_active,
                   array(select p.full_name from public.profiles p where p.school_id = s.id and p.role = 'hod' and p.is_active order by p.full_name) as hod_names,
                   (select count(*) from public.profiles p where p.school_id = s.id and p.role = 'hod' and p.is_active)     as hods,
                   (select count(*) from public.profiles p where p.school_id = s.id and p.role = 'teacher' and p.is_active) as teachers
            from public.schools s where s.id = any($1::uuid[]) order by s.name
            """,
            school_ids,
        ),
        db.fetch(
            "select id, school_id, name, code, num_semesters, is_active from public.courses "
            "where school_id = any($1::uuid[]) order by name",
            school_ids,
        ),
        db.fetch(
            "select id, course_id, number from public.semesters where school_id = any($1::uuid[]) order by number",
            school_ids,
        ),
        term_svc.active_term(),
    )
    sems_by_course: dict[Any, list[dict]] = {}
    for sm in sems:
        sems_by_course.setdefault(sm["course_id"], []).append(
            {
                "id": str(sm["id"]),
                "number": sm["number"],
                "running": bool(term) and term_svc.parity_matches(term["kind"], sm["number"]),
            }
        )
    courses_by_school: dict[Any, list[dict]] = {}
    for c in courses:
        courses_by_school.setdefault(c["school_id"], []).append(
            {
                "id": str(c["id"]),
                "name": c["name"],
                "code": c["code"],
                "num_semesters": c["num_semesters"],
                "is_active": c["is_active"],
                "semesters": sems_by_course.get(c["id"], []),
            }
        )
    return {
        "active_term": term_svc.term_out(term) if term else None,
        "schools": [
            {
                "id": str(s["id"]),
                "name": s["name"],
                "is_active": s["is_active"],
                "hods": s["hods"],
                "hod_names": list(s["hod_names"]),
                "teachers": s["teachers"],
                "courses": courses_by_school.get(s["id"], []),
            }
            for s in schools
        ],
    }


@router.get("/schools")
async def list_schools(user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    """Light list for dropdowns (an HOD only ever gets his own school)."""
    if user.role == "hod":
        rows = await db.fetch("select id, name, is_active from public.schools where id = $1", uuid.UUID(user.school_id))  # type: ignore[arg-type]
    else:
        rows = await db.fetch("select id, name, is_active from public.schools order by name")
    return {"schools": [{"id": str(r["id"]), "name": r["name"], "is_active": r["is_active"]} for r in rows]}


# ---------------------------------------------------------------------- schools (admin only)
class SchoolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 120)  # type: ignore[valid-type]


class SchoolPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 120) | None = None  # type: ignore[valid-type]
    is_active: bool | None = None


@router.post("/schools", status_code=201)
async def create_school(body: SchoolIn, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow("insert into public.schools (name) values ($1) returning id, name, is_active", body.name)
        # a new school starts with the real college lunch (13:10-13:40); its HOD can change it on the School Timing screen
        await conn.execute("insert into public.school_timing (school_id, lunch_start, lunch_end) values ($1, $2, $3)", row["id"], time(13, 10), time(13, 40))
        await audit.record(conn, user, "create", "school", row["id"], f"Created school '{row['name']}'", school_id=row["id"])
    return {"id": str(row["id"]), "name": row["name"], "is_active": row["is_active"]}


@router.patch("/schools/{school_id}")
async def update_school(school_id: str, body: SchoolPatch, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    sid = await resolve_school(user, school_id)
    async with db.transaction() as conn:
        old = await conn.fetchrow("select * from public.schools where id = $1 for update", sid)
        name = body.name if body.name is not None else old["name"]
        active = body.is_active if body.is_active is not None else old["is_active"]
        await conn.execute("update public.schools set name = $2, is_active = $3 where id = $1", sid, name, active)
        changes = []
        if name != old["name"]:
            changes.append(f"renamed '{old['name']}' to '{name}'")
        if active != old["is_active"]:
            changes.append("activated" if active else "deactivated")
        if changes:
            await audit.record(conn, user, "update", "school", sid, f"School {', '.join(changes)}", school_id=sid)
    return {"id": str(sid), "name": name, "is_active": active}


@router.delete("/schools/{school_id}")
async def delete_school(school_id: str, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    sid = await resolve_school(user, school_id)
    people = await db.fetchval("select count(*) from public.profiles where school_id = $1", sid)
    courses = await db.fetchval("select count(*) from public.courses where school_id = $1", sid)
    if people or courses:
        raise ApiError(
            409, "IN_USE",
            f"This school still has {courses} course(s) and {people} account(s). Deactivate it instead of deleting it.",
        )
    async with db.transaction() as conn:
        name = await conn.fetchval("delete from public.schools where id = $1 returning name", sid)
        await audit.record(conn, user, "delete", "school", sid, f"Deleted school '{name}'", school_id=sid)
    return {"deleted": True}


# ---------------------------------------------------------------------- courses (admin only)
class CourseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str
    name: text(1, 120)  # type: ignore[valid-type]
    code: optional_text(20) = None  # type: ignore[valid-type]
    num_semesters: int = Field(ge=1, le=12)


class CoursePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 120) | None = None  # type: ignore[valid-type]
    code: optional_text(20) = None  # type: ignore[valid-type]
    num_semesters: int | None = Field(default=None, ge=1, le=12)
    is_active: bool | None = None


@router.post("/courses", status_code=201)
async def create_course(body: CourseIn, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    sid = await resolve_school(user, body.school_id)
    async with db.transaction() as conn:
        c = await conn.fetchrow(
            "insert into public.courses (school_id, name, code, num_semesters) values ($1, $2, $3, $4) returning id",
            sid, body.name, body.code, body.num_semesters,
        )
        await conn.execute(
            "insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, $3::int) g",
            sid, c["id"], body.num_semesters,
        )
        await audit.record(
            conn, user, "create", "course", c["id"],
            f"Created course '{body.name}' with {body.num_semesters} semester(s)", school_id=sid,
        )
    return {"id": str(c["id"])}


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, body: CoursePatch, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    cid = as_uuid(course_id, "course")
    async with db.transaction() as conn:
        old = await conn.fetchrow("select * from public.courses where id = $1 for update", cid)
        if old is None:
            raise ApiError(404, "NOT_FOUND", "That course was not found.")
        fields = body.model_fields_set
        name = body.name if "name" in fields and body.name is not None else old["name"]
        code = body.code if "code" in fields else old["code"]
        active = body.is_active if body.is_active is not None else old["is_active"]
        n = body.num_semesters if body.num_semesters is not None else old["num_semesters"]

        if n < old["num_semesters"]:  # removing the highest semesters - only when they are completely empty
            busy = await conn.fetchval(
                """
                select count(*) from public.semesters sm
                where sm.course_id = $1 and sm.number > $2
                  and (exists (select 1 from public.subjects s where s.semester_id = sm.id)
                       or exists (select 1 from public.fixed_slots f where f.semester_id = sm.id))
                """,
                cid, n,
            )
            if busy:
                raise ApiError(
                    409, "IN_USE",
                    "Some of the semesters you are removing still have subjects or locked slots. Remove those first.",
                )
            await conn.execute("delete from public.semesters where course_id = $1 and number > $2", cid, n)
        elif n > old["num_semesters"]:
            await conn.execute(
                "insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series($3::int, $4::int) g",
                old["school_id"], cid, old["num_semesters"] + 1, n,
            )
        await conn.execute(
            "update public.courses set name = $2, code = $3, is_active = $4, num_semesters = $5 where id = $1",
            cid, name, code, active, n,
        )
        await audit.record(
            conn, user, "update", "course", cid, f"Updated course '{name}'", school_id=old["school_id"],
            details={"name": name, "code": code, "is_active": active, "num_semesters": n},
        )
    return {"id": str(cid)}


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str, user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    cid = as_uuid(course_id, "course")
    old = await db.fetchrow("select * from public.courses where id = $1", cid)
    if old is None:
        raise ApiError(404, "NOT_FOUND", "That course was not found.")
    busy = await db.fetchval(
        "select count(*) from public.subjects s join public.semesters sm on sm.id = s.semester_id where sm.course_id = $1", cid
    )
    if busy:
        raise ApiError(409, "IN_USE", f"This course still has {busy} subject(s). Deactivate it instead of deleting it.")
    async with db.transaction() as conn:
        await conn.execute("delete from public.courses where id = $1", cid)
        await audit.record(conn, user, "delete", "course", cid, f"Deleted course '{old['name']}'", school_id=old["school_id"])
    return {"deleted": True}
