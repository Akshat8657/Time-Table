"""Active academic term (odd / even + year) and the semester start / end dates (plan Sections 4, 12, 18)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..validation import ISODate

router = APIRouter(prefix="/api/terms", tags=["terms"])


@router.get("")
async def list_terms(user: CurrentUser = Depends(require_roles("admin", "hod", "teacher"))) -> dict:
    rows = await db.fetch("select * from public.academic_terms order by year desc, kind desc")
    terms = [term_svc.term_out(r) for r in rows]
    return {"active": next((t for t in terms if t["is_active"]), None), "terms": terms}


class ActivateTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["odd", "even"]
    year: int = Field(ge=2000, le=2100)


@router.put("/active")
async def set_active_term(body: ActivateTerm, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    """Makes 'Odd 2026' / 'Even 2027' ... the running term. The previous one stays saved as history."""
    async with db.transaction() as conn:
        current = await conn.fetchrow("select * from public.academic_terms where is_active for update")
        existing = await conn.fetchrow(
            "select * from public.academic_terms where kind = $1 and year = $2", body.kind, body.year
        )
        if existing is not None and existing["is_active"]:
            return {"active": term_svc.term_out(existing), "changed": False}
        if current is not None:
            await conn.execute("update public.academic_terms set is_active = false where id = $1", current["id"])
        uid = uuid.UUID(user.id)
        if existing is None:
            target = await conn.fetchrow(
                "insert into public.academic_terms (kind, year, is_active, activated_at, activated_by, created_by) "
                "values ($1, $2, true, now(), $3, $3) returning *",
                body.kind, body.year, uid,
            )
        else:
            target = await conn.fetchrow(
                "update public.academic_terms set is_active = true, activated_at = now(), activated_by = $2 "
                "where id = $1 returning *",
                existing["id"], uid,
            )
        name = term_svc.term_name(target["kind"], target["year"])
        previous = term_svc.term_name(current["kind"], current["year"]) if current else None
        await audit.record(
            conn, user, "activate", "term", target["id"],
            f"Active term set to {name}" + (f" (was {previous})" if previous else ""),
            details={"kind": body.kind, "year": body.year, "previous": previous},
        )
    return {"active": term_svc.term_out(target), "changed": True}


# ---------------------------------------------------------------------- semester start / end dates
@router.get("/{term_id}/dates")
async def get_dates(
    term_id: str, school_id: str | None = None, user: CurrentUser = Depends(require_roles("admin", "hod"))
) -> dict:
    term = await _term(term_id)
    sid = await resolve_school(user, school_id)
    rows = await db.fetch(
        """
        select sm.id, sm.number, c.name as course_name, ts.start_date, ts.end_date
        from public.semesters sm
        join public.courses c on c.id = sm.course_id
        left join public.term_semesters ts on ts.semester_id = sm.id and ts.term_id = $2
        where sm.school_id = $1 and c.is_active
        order by c.name, sm.number
        """,
        sid, term["id"],
    )
    classes = [
        {
            "semester_id": str(r["id"]),
            "course": r["course_name"],
            "number": r["number"],
            "label": term_svc.semester_label(r["course_name"], r["number"]),
            "start_date": r["start_date"].isoformat() if r["start_date"] else None,
            "end_date": r["end_date"].isoformat() if r["end_date"] else None,
        }
        for r in rows
        if term_svc.parity_matches(term["kind"], r["number"])
    ]
    return {"term": term_svc.term_out(term), "school_id": str(sid), "classes": classes}


class DateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    start_date: ISODate
    end_date: ISODate


class DatesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    items: list[DateItem] = Field(min_length=1, max_length=60)


@router.put("/{term_id}/dates")
async def save_dates(
    term_id: str, body: DatesBody, user: CurrentUser = Depends(require_roles("admin", "hod"))
) -> dict:
    term = await _term(term_id)
    if not term["is_active"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "Dates can only be changed for the active term.")
    sid = await resolve_school(user, body.school_id)

    wanted = {as_uuid(i.semester_id, "class"): i for i in body.items}
    sems = await db.fetch(
        term_svc.SEMESTER_SELECT + " where sm.school_id = $1 and sm.id = any($2::uuid[])", sid, list(wanted)
    )
    if len(sems) != len(wanted):
        raise ApiError(404, "NOT_FOUND", "One of the classes was not found in this school.")
    fields: dict[str, str] = {}
    for s in sems:
        item = wanted[s["id"]]
        label = term_svc.semester_label(s["course_name"], s["number"])
        if not term_svc.parity_matches(term["kind"], s["number"]):
            raise ApiError(
                409, "SEMESTER_NOT_ACTIVE", f"{label} does not run in {term_svc.term_name(term['kind'], term['year'])}."
            )
        if item.end_date <= item.start_date:
            fields[str(s["id"])] = "The end date must be after the start date."
        elif (item.end_date - item.start_date).days > 300:
            fields[str(s["id"])] = "A semester cannot be longer than about 10 months."
        elif not (term["year"] - 1 <= item.start_date.year <= term["year"] + 1):
            fields[str(s["id"])] = f"The start date should be around the year {term['year']}."
    if fields:
        raise ApiError(422, "VALIDATION_ERROR", "Please check the dates you entered.", extra={"fields": fields})

    async with db.transaction() as conn:
        for sem_id, item in wanted.items():
            await conn.execute(
                """
                insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date, updated_by)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (term_id, semester_id)
                do update set start_date = excluded.start_date, end_date = excluded.end_date, updated_by = excluded.updated_by
                """,
                term["id"], sem_id, sid, item.start_date, item.end_date, uuid.UUID(user.id),
            )
        await audit.record(
            conn, user, "update", "semester_dates", term["id"],
            f"Semester dates saved for {len(wanted)} class(es) in {term_svc.term_name(term['kind'], term['year'])}",
            school_id=sid,
            details={str(k): [v.start_date.isoformat(), v.end_date.isoformat()] for k, v in wanted.items()},
        )
    return {"saved": len(wanted)}


async def _term(term_id: str):
    row = await db.fetchrow("select * from public.academic_terms where id = $1", as_uuid(term_id, "term"))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "That term was not found.")
    return row
