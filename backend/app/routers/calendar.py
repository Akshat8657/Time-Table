"""Holidays & events (plan Section 12).  Admin: college-wide holidays.  HOD: school holidays / events (own school only)."""
from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator

from .. import audit, db
from ..data.india_holidays import INDIA_HOLIDAYS, note_for
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..validation import HHMM, ISODate, hm, optional_text, text

router = APIRouter(prefix="/api/calendar", tags=["holidays-events"])

ENTRY_SQL = """
select e.id, e.school_id, s.name as school_name, e.kind, e.title, e.date_from, e.date_to, e.full_day, e.start_time,
       e.end_time, e.note, e.is_preloaded
from public.calendar_entries e
left join public.schools s on s.id = e.school_id
"""


async def _classes_of(entry_ids: list[uuid.UUID]) -> dict[Any, list[dict]]:
    if not entry_ids:
        return {}
    rows = await db.fetch(
        """
        select cc.entry_id, sm.id, sm.number, c.name as course_name
        from public.calendar_entry_classes cc
        join public.semesters sm on sm.id = cc.semester_id
        join public.courses c on c.id = sm.course_id
        where cc.entry_id = any($1::uuid[]) order by c.name, sm.number
        """,
        entry_ids,
    )
    out: dict[Any, list[dict]] = {}
    for r in rows:
        out.setdefault(r["entry_id"], []).append(
            {"semester_id": str(r["id"]), "label": term_svc.semester_label(r["course_name"], r["number"])}
        )
    return out


def _can_edit(user: CurrentUser, school_id: Any) -> bool:
    if user.role == "admin":
        return True
    return user.role == "hod" and school_id is not None and str(school_id) == user.school_id


def entry_out(r: asyncpg.Record, classes: list[dict], user: CurrentUser) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "scope": "college" if r["school_id"] is None else "school",
        "school_id": str(r["school_id"]) if r["school_id"] else None,
        "school_name": r["school_name"],
        "kind": r["kind"],
        "title": r["title"],
        "date_from": r["date_from"].isoformat(),
        "date_to": r["date_to"].isoformat(),
        "full_day": r["full_day"],
        "start_time": hm(r["start_time"]),
        "end_time": hm(r["end_time"]),
        "note": r["note"],
        "is_preloaded": r["is_preloaded"],
        "classes": classes,
        "can_edit": _can_edit(user, r["school_id"]),
    }


# ---------------------------------------------------------------------- read
@router.get("")
async def list_entries(
    school_id: str | None = None,
    year: int | None = None,
    kind: Literal["holiday", "event", "all"] = "all",
    q: str | None = None,
    user: CurrentUser = Depends(require_roles("admin", "hod", "teacher")),
) -> dict:
    where = ["(e.school_id is null"]
    args: list[Any] = []
    if user.role in ("hod", "teacher"):
        if school_id is not None:
            await resolve_school(user, school_id)
        args.append(uuid.UUID(user.school_id))  # type: ignore[arg-type]
        where[0] += f" or e.school_id = ${len(args)}"
    elif school_id is not None:
        args.append(await resolve_school(user, school_id))
        where[0] += f" or e.school_id = ${len(args)}"
    where[0] += ")"
    if year is not None:
        args.append(date(year, 1, 1))
        args.append(date(year, 12, 31))
        where.append(f"e.date_to >= ${len(args) - 1} and e.date_from <= ${len(args)}")
    if kind != "all":
        args.append(kind)
        where.append(f"e.kind = ${len(args)}")
    if q and q.strip():
        args.append(f"%{q.strip()[:80].replace('%', ' ').replace('_', ' ')}%")
        where.append(f"e.title ilike ${len(args)}")
    rows = await db.fetch(ENTRY_SQL + " where " + " and ".join(where) + " order by e.date_from, e.title limit 1000", *args)
    classes = await _classes_of([r["id"] for r in rows])
    return {"entries": [entry_out(r, classes.get(r["id"], []), user) for r in rows]}


# ---------------------------------------------------------------------- create / edit / delete
class EntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["holiday", "event"] = "holiday"
    title: text(1, 150)  # type: ignore[valid-type]
    date_from: ISODate
    date_to: ISODate | None = None
    full_day: bool = True
    start_time: HHMM | None = None
    end_time: HHMM | None = None
    note: optional_text(300) = None  # type: ignore[valid-type]
    semester_ids: list[str] = []

    @model_validator(mode="after")
    def _ok(self):
        last = self.date_to or self.date_from
        if last < self.date_from:
            raise ValueError("The end date cannot be before the start date.")
        if (last - self.date_from).days > 60:
            raise ValueError("One entry can cover at most 61 days. Add a second entry for longer breaks.")
        if not self.full_day:
            if self.start_time is None or self.end_time is None:
                raise ValueError("Please give the start and end time, or mark the entry as 'full day'.")
            if self.end_time <= self.start_time:
                raise ValueError("The end time must be after the start time.")
        return self


class EntryIn(EntryBase):
    scope: Literal["college", "school"] = "school"
    school_id: str | None = None


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["holiday", "event"] | None = None
    title: text(1, 150) | None = None  # type: ignore[valid-type]
    date_from: ISODate | None = None
    date_to: ISODate | None = None
    full_day: bool | None = None
    start_time: HHMM | None = None
    end_time: HHMM | None = None
    note: optional_text(300) = None  # type: ignore[valid-type]
    semester_ids: list[str] | None = None


async def _check_times(full_day: bool, start: time | None, end: time | None) -> None:
    if full_day:
        return
    college = await timing_svc.college_row()
    if start < college["college_start"] or end > college["college_end"]:  # type: ignore[operator]
        raise ApiError(
            422, "VALIDATION_ERROR",
            f"The time must be inside the college hours ({hm(college['college_start'])}-{hm(college['college_end'])}).",
            extra={"fields": {"start_time": "Outside the college hours."}},
        )


async def _class_ids(conn, ids: list[str], school_id: uuid.UUID) -> list[uuid.UUID]:
    wanted = list({as_uuid(i, "class") for i in ids})
    if wanted:
        found = await conn.fetchval("select count(*) from public.semesters where school_id = $1 and id = any($2::uuid[])", school_id, wanted)
        if found != len(wanted):
            raise ApiError(422, "VALIDATION_ERROR", "One of the chosen classes does not belong to this school.",
                           extra={"fields": {"semester_ids": "Choose classes of this school only."}})
    return wanted


async def _load_editable(user: CurrentUser, entry_id: str) -> asyncpg.Record:
    row = await db.fetchrow("select * from public.calendar_entries where id = $1", as_uuid(entry_id, "entry"))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "That entry was not found.")
    if row["school_id"] is None:
        if user.role != "admin":
            raise ApiError(403, "FORBIDDEN", "Only the administrator can change college-wide holidays.")
    elif user.role != "admin" and str(row["school_id"]) != user.school_id:
        raise ApiError(404, "NOT_FOUND", "That entry was not found.")
    return row


@router.post("", status_code=201)
async def create_entry(body: EntryIn, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    date_to = body.date_to or body.date_from
    if body.scope == "college":
        if user.role != "admin":
            raise ApiError(403, "FORBIDDEN", "Only the administrator can add college-wide holidays.")
        if body.kind != "holiday" or not body.full_day or body.semester_ids:
            raise ApiError(422, "VALIDATION_ERROR", "A college-wide entry must be a full-day holiday for all classes.")
        sid = None
    else:
        sid = await resolve_school(user, body.school_id)
    await _check_times(body.full_day, body.start_time, body.end_time)
    async with db.transaction() as conn:
        class_ids = await _class_ids(conn, body.semester_ids, sid) if sid else []
        row = await conn.fetchrow(
            """
            insert into public.calendar_entries (school_id, kind, title, date_from, date_to, full_day, start_time, end_time, note, created_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning id
            """,
            sid, body.kind, body.title, body.date_from, date_to, body.full_day,
            None if body.full_day else body.start_time, None if body.full_day else body.end_time, body.note, uuid.UUID(user.id),
        )
        if class_ids:
            await conn.execute(
                "insert into public.calendar_entry_classes (entry_id, semester_id, school_id) select $1, x, $3 from unnest($2::uuid[]) x",
                row["id"], class_ids, sid,
            )
        await audit.record(
            conn, user, "create", "holiday" if body.kind == "holiday" else "event", row["id"],
            f"Added {'college-wide ' if sid is None else ''}{body.kind} '{body.title}' on {body.date_from.isoformat()}"
            + (f" to {date_to.isoformat()}" if date_to != body.date_from else ""),
            school_id=sid,
        )
    return {"id": str(row["id"])}


@router.patch("/{entry_id}")
async def update_entry(entry_id: str, body: EntryPatch, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    old = await _load_editable(user, entry_id)
    f = body.model_fields_set
    kind = body.kind or old["kind"]
    title = body.title if "title" in f and body.title else old["title"]
    date_from = body.date_from or old["date_from"]
    date_to = body.date_to or (old["date_to"] if body.date_from is None else max(old["date_to"], date_from))
    full_day = body.full_day if body.full_day is not None else old["full_day"]
    start_time = body.start_time if "start_time" in f else old["start_time"]
    end_time = body.end_time if "end_time" in f else old["end_time"]
    note = body.note if "note" in f else old["note"]
    if date_to < date_from or (date_to - date_from).days > 60:
        raise ApiError(422, "VALIDATION_ERROR", "Please check the dates (at most 61 days, end not before start).",
                       extra={"fields": {"date_to": "Check the dates."}})
    if not full_day and (start_time is None or end_time is None or end_time <= start_time):
        raise ApiError(422, "VALIDATION_ERROR", "Please give a valid start and end time.", extra={"fields": {"start_time": "Check the times."}})
    if old["school_id"] is None and (kind != "holiday" or not full_day):
        raise ApiError(422, "VALIDATION_ERROR", "A college-wide entry must be a full-day holiday.")
    await _check_times(full_day, start_time, end_time)
    async with db.transaction() as conn:
        await conn.execute(
            """
            update public.calendar_entries set kind = $2, title = $3, date_from = $4, date_to = $5, full_day = $6,
                   start_time = $7, end_time = $8, note = $9 where id = $1
            """,
            old["id"], kind, title, date_from, date_to, full_day,
            None if full_day else start_time, None if full_day else end_time, note,
        )
        if body.semester_ids is not None and old["school_id"] is not None:
            class_ids = await _class_ids(conn, body.semester_ids, old["school_id"])
            await conn.execute("delete from public.calendar_entry_classes where entry_id = $1", old["id"])
            if class_ids:
                await conn.execute(
                    "insert into public.calendar_entry_classes (entry_id, semester_id, school_id) select $1, x, $3 from unnest($2::uuid[]) x",
                    old["id"], class_ids, old["school_id"],
                )
        await audit.record(
            conn, user, "update", "holiday" if kind == "holiday" else "event", old["id"],
            f"Changed {kind} '{title}' ({date_from.isoformat()})", school_id=old["school_id"],
        )
    return {"id": str(old["id"])}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: str, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    old = await _load_editable(user, entry_id)
    async with db.transaction() as conn:
        await conn.execute("delete from public.calendar_entries where id = $1", old["id"])
        await audit.record(
            conn, user, "delete", "holiday" if old["kind"] == "holiday" else "event", old["id"],
            f"Deleted {old['kind']} '{old['title']}' ({old['date_from'].isoformat()})", school_id=old["school_id"],
        )
    return {"deleted": True}


@router.post("/restore-india-list")
async def restore_india_list(user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    """Adds back any official 2026 / 2027 holiday that is missing from the college-wide list (nothing is overwritten)."""
    added = 0
    async with db.transaction() as conn:
        for title, d in INDIA_HOLIDAYS:
            result = await conn.execute(
                """
                insert into public.calendar_entries (school_id, kind, title, date_from, date_to, full_day, note, is_preloaded, created_by)
                select null, 'holiday', $1, $2, $2, true, $3, true, $4
                where not exists (select 1 from public.calendar_entries where school_id is null and lower(title) = lower($1) and date_from = $2)
                on conflict do nothing
                """,
                title, d, note_for(d), uuid.UUID(user.id),
            )
            added += int(result.split()[-1])
        await audit.record(conn, user, "create", "holiday", None, f"Restored the standard India holiday list ({added} added)", details={"added": added})
    return {"added": added}
