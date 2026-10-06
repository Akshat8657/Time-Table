"""Subjects & credits, labs, electives and elective groups (plan Sections 8, 9).  HOD: own school only."""
from __future__ import annotations

import uuid
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..validation import text

router = APIRouter(prefix="/api/subjects", tags=["subjects"])
ROLES = ("admin", "hod")

LIST_SQL = """
select s.id, s.school_id, s.semester_id, s.name, s.code, s.credits, s.semester_hours, s.is_lab, s.is_elective,
       s.special_kind, s.elective_group_id, g.name as group_name,
       sm.number, c.id as course_id, c.name as course_name,
       a.id as assignment_id, a.teacher_id, p.full_name as teacher_name
from public.subjects s
join public.semesters sm on sm.id = s.semester_id
join public.courses c on c.id = sm.course_id
left join public.elective_groups g on g.id = s.elective_group_id
left join public.assignments a on a.subject_id = s.id and a.term_id = $2
left join public.profiles p on p.id = a.teacher_id
where s.school_id = $1
"""


def subject_out(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "semester_id": str(r["semester_id"]),
        "course_id": str(r["course_id"]),
        "course_name": r["course_name"],
        "semester_number": r["number"],
        "class_label": term_svc.semester_label(r["course_name"], r["number"]),
        "name": r["name"],
        "code": r["code"],
        "credits": r["credits"],
        "semester_hours": r["semester_hours"],
        "is_lab": r["is_lab"],
        "is_elective": r["is_elective"],
        "special_kind": r["special_kind"],
        "elective_group_id": str(r["elective_group_id"]) if r["elective_group_id"] else None,
        "elective_group_name": r["group_name"],
        "assignment_id": str(r["assignment_id"]) if r["assignment_id"] else None,
        "teacher_id": str(r["teacher_id"]) if r["teacher_id"] else None,
        "teacher_name": r["teacher_name"],
    }


# ---------------------------------------------------------------------- elective-group warnings
async def group_warnings(conn_or_none: Any, group_id: uuid.UUID | None) -> list[str]:
    """Plan Section 9: all electives of one group should have the same credits."""
    if group_id is None:
        return []
    fetch = conn_or_none.fetch if conn_or_none is not None else db.fetch
    rows = await fetch(
        "select g.name as group_name, s.name, s.credits from public.subjects s "
        "join public.elective_groups g on g.id = s.elective_group_id where s.elective_group_id = $1 order by s.name",
        group_id,
    )
    if not rows:
        return []
    warnings: list[str] = []
    if len({r["credits"] for r in rows}) > 1:
        detail = ", ".join(f"{r['name']}: {r['credits']}" for r in rows)
        warnings.append(
            f"Elective Group '{rows[0]['group_name']}' has different credits ({detail}). "
            "All electives in one group run in the same time slots, so they should have the same credits."
        )
    return warnings


def subject_warnings(is_lab: bool, credits: int, special_kind: str | None) -> list[str]:
    out = []
    if is_lab and credits % 2 == 1:
        out.append("Lab sessions are always 2 lectures back-to-back, so an even number of credits fits best.")
    if special_kind:
        out.append(
            f"{special_kind} lectures come only from the fixed double blocks (Fixed Slots screen) - credits do not decide them."
        )
    return out


# ---------------------------------------------------------------------- list
@router.get("")
async def list_subjects(
    school_id: str | None = None,
    course_id: str | None = None,
    semester_id: str | None = None,
    q: str | None = None,
    user: CurrentUser = Depends(require_roles(*ROLES)),
) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"subjects": [], "active_term": None}
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
        sql += f" and (s.name ilike ${len(args)} or s.code ilike ${len(args)})"
    rows = await db.fetch(sql + " order by c.name, sm.number, s.is_elective, s.name limit 1000", *args)
    return {"subjects": [subject_out(r) for r in rows], "active_term": term_svc.term_out(term)}


# ---------------------------------------------------------------------- create / edit / delete
class SubjectBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 150)  # type: ignore[valid-type]
    code: text(1, 30)  # type: ignore[valid-type]
    credits: int = Field(ge=1, le=30)
    semester_hours: int = Field(ge=1, le=400)
    is_lab: bool = False
    is_elective: bool = False
    special_kind: Literal["PE", "SOL"] | None = None
    elective_group_id: str | None = None

    @model_validator(mode="after")
    def _combos(self):
        if self.special_kind and (self.is_lab or self.is_elective):
            raise ValueError("PE and SOL subjects cannot also be a lab or an elective.")
        if self.elective_group_id and not self.is_elective:
            raise ValueError("Only an elective subject can belong to an elective group.")
        return self


class SubjectIn(SubjectBase):
    semester_id: str


class SubjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 150) | None = None  # type: ignore[valid-type]
    code: text(1, 30) | None = None  # type: ignore[valid-type]
    credits: int | None = Field(default=None, ge=1, le=30)
    semester_hours: int | None = Field(default=None, ge=1, le=400)
    is_lab: bool | None = None
    is_elective: bool | None = None
    special_kind: Literal["PE", "SOL"] | None = None
    elective_group_id: str | None = None


async def _ensure_running(semester_id: uuid.UUID) -> None:
    """Edits only make sense for classes that run in the active term."""
    term = await term_svc.require_active_term()
    sem = await db.fetchrow(
        "select sm.number, c.name from public.semesters sm join public.courses c on c.id = sm.course_id where sm.id = $1", semester_id
    )
    term_svc.require_active_semester(term, sem["number"], term_svc.semester_label(sem["name"], sem["number"]))


async def _group_for(conn, group_id: str | None, semester_id: uuid.UUID, school_id: uuid.UUID) -> uuid.UUID | None:
    if not group_id:
        return None
    gid = as_uuid(group_id, "elective group")
    ok = await conn.fetchval(
        "select exists(select 1 from public.elective_groups where id = $1 and semester_id = $2 and school_id = $3)",
        gid, semester_id, school_id,
    )
    if not ok:
        raise ApiError(
            422, "VALIDATION_ERROR", "That elective group belongs to a different class.",
            extra={"fields": {"elective_group_id": "Choose a group of the same class."}},
        )
    return gid


@router.post("", status_code=201)
async def create_subject(body: SubjectIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem = await load_in_scope("semesters", body.semester_id, user, what="class")
    term = await term_svc.require_active_term()
    course = await db.fetchrow("select name, is_active from public.courses where id = $1", sem["course_id"])
    term_svc.require_active_semester(term, sem["number"], term_svc.semester_label(course["name"], sem["number"]))
    if not course["is_active"]:
        raise ApiError(409, "COURSE_INACTIVE", "This course is inactive.")
    async with db.transaction() as conn:
        gid = await _group_for(conn, body.elective_group_id, sem["id"], sem["school_id"])
        row = await conn.fetchrow(
            """
            insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours, is_lab, is_elective,
                                         special_kind, elective_group_id, created_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) returning id
            """,
            sem["school_id"], sem["id"], body.name, body.code, body.credits, body.semester_hours,
            body.is_lab, body.is_elective, body.special_kind, gid, uuid.UUID(user.id),
        )
        await audit.record(
            conn, user, "create", "subject", row["id"],
            f"Created subject {body.name} ({body.code}) for {term_svc.semester_label(course['name'], sem['number'])}",
            school_id=sem["school_id"], details={"credits": body.credits, "hours": body.semester_hours},
        )
        warnings = await group_warnings(conn, gid) + subject_warnings(body.is_lab, body.credits, body.special_kind)
    return {"id": str(row["id"]), "warnings": warnings}


@router.patch("/{subject_id}")
async def update_subject(subject_id: str, body: SubjectPatch, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("subjects", subject_id, user, what="subject")
    await _ensure_running(old["semester_id"])
    fields = body.model_fields_set
    name = body.name if "name" in fields and body.name else old["name"]
    code = body.code if "code" in fields and body.code else old["code"]
    credits = body.credits if body.credits is not None else old["credits"]
    hours = body.semester_hours if body.semester_hours is not None else old["semester_hours"]
    is_lab = body.is_lab if body.is_lab is not None else old["is_lab"]
    is_elective = body.is_elective if body.is_elective is not None else old["is_elective"]
    special = body.special_kind if "special_kind" in fields else old["special_kind"]
    group = body.elective_group_id if "elective_group_id" in fields else (str(old["elective_group_id"]) if old["elective_group_id"] else None)
    if not is_elective:
        group = None
    if special and (is_lab or is_elective):
        raise ApiError(422, "VALIDATION_ERROR", "PE and SOL subjects cannot also be a lab or an elective.")

    async with db.transaction() as conn:
        gid = await _group_for(conn, group, old["semester_id"], old["school_id"])
        await conn.execute(
            """
            update public.subjects
               set name = $2, code = $3, credits = $4, semester_hours = $5, is_lab = $6, is_elective = $7,
                   special_kind = $8, elective_group_id = $9
             where id = $1
            """,
            old["id"], name, code, credits, hours, is_lab, is_elective, special, gid,
        )
        await audit.record(
            conn, user, "update", "subject", old["id"], f"Updated subject {name} ({code})",
            school_id=old["school_id"],
            details={"credits": credits, "hours": hours, "lab": is_lab, "elective": is_elective, "special": special},
        )
        warnings = await group_warnings(conn, gid) + subject_warnings(is_lab, credits, special)
    return {"id": str(old["id"]), "warnings": warnings}


@router.delete("/{subject_id}")
async def delete_subject(subject_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("subjects", subject_id, user, what="subject")
    await _ensure_running(old["semester_id"])
    used = await db.fetchrow(
        """
        select (select count(*) from public.assignments where subject_id = $1) as assigned,
               (select count(*) from public.fixed_slots where subject_id = $1) as locked
        """,
        old["id"],
    )
    if used["assigned"] or used["locked"]:
        raise ApiError(
            409, "IN_USE",
            f"'{old['name']}' is still used in {used['assigned']} assignment(s) and {used['locked']} locked slot(s). "
            "Remove those first.",
        )
    async with db.transaction() as conn:
        await conn.execute("delete from public.subjects where id = $1", old["id"])
        await audit.record(conn, user, "delete", "subject", old["id"], f"Deleted subject {old['name']} ({old['code']})", school_id=old["school_id"])
    return {"deleted": True}


# ---------------------------------------------------------------------- elective groups
class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    name: text(1, 60)  # type: ignore[valid-type]


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 60)  # type: ignore[valid-type]


@router.get("/groups")
async def list_groups(
    school_id: str | None = None, semester_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))
) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"groups": []}
    args: list[Any] = [sid, term["kind"]]
    extra = ""
    if semester_id:
        args.append(as_uuid(semester_id, "class"))
        extra = f" and g.semester_id = ${len(args)}"
    groups = await db.fetch(
        f"""
        select g.id, g.name, g.semester_id, sm.number, c.name as course_name
        from public.elective_groups g
        join public.semesters sm on sm.id = g.semester_id
        join public.courses c on c.id = sm.course_id
        where g.school_id = $1 and (sm.number % 2 = 1) = ($2 = 'odd') {extra}
        order by c.name, sm.number, g.name
        """,
        *args,
    )
    members = await db.fetch(
        "select id, elective_group_id, name, code, credits from public.subjects where elective_group_id = any($1::uuid[]) order by name",
        [g["id"] for g in groups],
    )
    by_group: dict[Any, list[asyncpg.Record]] = {}
    for m in members:
        by_group.setdefault(m["elective_group_id"], []).append(m)
    out = []
    for g in groups:
        ms = by_group.get(g["id"], [])
        warnings = []
        if len({m["credits"] for m in ms}) > 1:
            warnings.append(
                "Credits differ: " + ", ".join(f"{m['name']} = {m['credits']}" for m in ms)
                + ". All electives in a group should have the same credits."
            )
        if len(ms) == 1:
            warnings.append("A group needs at least 2 electives to run in parallel.")
        out.append(
            {
                "id": str(g["id"]),
                "name": g["name"],
                "semester_id": str(g["semester_id"]),
                "class_label": term_svc.semester_label(g["course_name"], g["number"]),
                "members": [{"id": str(m["id"]), "name": m["name"], "code": m["code"], "credits": m["credits"]} for m in ms],
                "warnings": warnings,
            }
        )
    return {"groups": out}


@router.post("/groups", status_code=201)
async def create_group(body: GroupIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem = await load_in_scope("semesters", body.semester_id, user, what="class")
    term = await term_svc.require_active_term()
    term_svc.require_active_semester(term, sem["number"])
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "insert into public.elective_groups (school_id, semester_id, name) values ($1, $2, $3) returning id",
            sem["school_id"], sem["id"], body.name,
        )
        await audit.record(conn, user, "create", "elective_group", row["id"], f"Created elective group '{body.name}'", school_id=sem["school_id"])
    return {"id": str(row["id"])}


@router.patch("/groups/{group_id}")
async def rename_group(group_id: str, body: GroupPatch, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("elective_groups", group_id, user, what="elective group")
    async with db.transaction() as conn:
        await conn.execute("update public.elective_groups set name = $2 where id = $1", old["id"], body.name)
        await audit.record(conn, user, "update", "elective_group", old["id"], f"Renamed elective group '{old['name']}' to '{body.name}'", school_id=old["school_id"])
    return {"id": str(old["id"])}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("elective_groups", group_id, user, what="elective group")
    async with db.transaction() as conn:
        count = await conn.fetchval("select count(*) from public.subjects where elective_group_id = $1", old["id"])
        await conn.execute("delete from public.elective_groups where id = $1", old["id"])  # members just become 'ungrouped'
        await audit.record(
            conn, user, "delete", "elective_group", old["id"],
            f"Deleted elective group '{old['name']}' ({count} subject(s) ungrouped)", school_id=old["school_id"],
        )
    return {"deleted": True}
