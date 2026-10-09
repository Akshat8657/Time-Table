"""Absence (plan Section 13): a teacher marks themselves absent, or a HOD/Admin marks anyone absent.

Marking an absence immediately: notifies the HOD/Admin (a HOD's own absence goes to the admins), lists the day's affected
lectures, and creates an OPEN substitution need for each lecture no present co-teacher already covers.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import notify
from ..services import subflow
from ..services import substitution as subsvc
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services import views as vw
from ..services.changes import load_changes
from ..services.viewdata import load_view_data
from ..validation import ISODate, optional_text

router = APIRouter(prefix="/api/absence", tags=["absence"])
ALL = ("admin", "hod", "teacher")
MANAGERS = ("admin", "hod")


class AbsenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_id: str | None = None                 # a manager may name someone; a teacher marks themselves
    date: ISODate | None = None                   # default = today
    reason: optional_text(300) = None  # type: ignore[valid-type]


async def _target(user: CurrentUser, teacher_id: str | None) -> tuple[uuid.UUID, uuid.UUID, bool]:
    """(teacher id, their school id, by-self?). A teacher can only mark themselves; a HOD only their school; admin anyone."""
    if teacher_id is None or (user.role == "teacher") or teacher_id == user.id:
        if user.school_id is None:
            raise ApiError(403, "NO_SCHOOL", "Your account is not linked to a school yet.")
        return uuid.UUID(user.id), uuid.UUID(user.school_id), True
    tid = as_uuid(teacher_id, "teacher")
    row = await db.fetchrow("select school_id, role from public.profiles where id = $1", tid)
    if row is None or row["role"] not in ("teacher", "hod") or row["school_id"] is None:
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    if user.role != "admin" and str(row["school_id"]) != user.school_id:
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    return tid, row["school_id"], False


@router.post("", status_code=201)
async def mark_absent(body: AbsenceIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    teacher_id, school_id, by_self = await _target(user, body.teacher_id)
    term = await term_svc.require_active_term()
    d = body.date or vw.today_ist()
    source = "self" if by_self else user.role
    data = await load_view_data(school_id, term, (d, d))
    change = await load_changes(school_id, term["id"], (d, d))
    async with db.transaction() as conn:
        absence = await conn.fetchrow(
            "insert into public.teacher_absences (school_id, term_id, teacher_id, absence_date, reason, source, created_by) "
            "values ($1, $2, $3, $4, $5, $6, $7) on conflict (teacher_id, absence_date) do nothing returning id",
            school_id, term["id"], teacher_id, d, body.reason, source, uuid.UUID(user.id),
        )
        if absence is None:
            raise ApiError(409, "ALREADY_ABSENT", f"{data.person_name(teacher_id)} is already marked absent on {d.strftime('%d %b %Y')}.")
        slots = await subflow.make_subs(
            conn, data, change, absence_id=absence["id"], teacher_id=teacher_id, d=d,
            school_id=school_id, term_id=term["id"], created_by=uuid.UUID(user.id),
        )
        await audit.record(conn, user, "create", "absence", absence["id"],
                           f"Marked {data.person_name(teacher_id)} absent on {d.isoformat()} ({sum(1 for s in slots if s['status'] == 'open')} to cover)", school_id=school_id)
        managers = await subflow.managers_of(school_id, exclude=uuid.UUID(user.id))
        notices = subflow.absence_notices(data, teacher_id=teacher_id, d=d, slots=slots, managers=managers, school_id=school_id, source=source, by_self=by_self)
        if not by_self:  # someone else marked them: tell the teacher too
            nice = d.strftime("%a %d %b %Y")
            notices.append(notify.Notice(teacher_id, "absence_marked_you", f"You are marked absent - {nice}",
                                         f"Marked by {user.full_name}." + (f" Reason: {body.reason}" if body.reason else ""),
                                         subflow.TEACHER_ABSENCE_LINK, {"date": d.isoformat()}, school_id, f"absence-you:{teacher_id}:{d}"))
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    need = [s for s in slots if s["status"] == "open"]
    return {
        "absence_id": str(absence["id"]), "teacher_id": str(teacher_id), "teacher_name": data.person_name(teacher_id),
        "date": d.isoformat(), "affected": slots, "to_cover": len(need),
        "message": f"{data.person_name(teacher_id)} marked absent. {len(need)} lecture(s) need a substitute." if need else f"{data.person_name(teacher_id)} marked absent. No lecture needs a substitute.",
    }


def _absence_out(r: Any) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "teacher_id": str(r["teacher_id"]), "teacher_name": r.get("teacher_name"),
        "date": r["absence_date"].isoformat(), "reason": r["reason"], "source": r["source"],
        "open": r.get("open", 0), "offering": r.get("offering", 0), "filled": r.get("filled", 0), "unfilled": r.get("unfilled", 0),
    }


@router.get("")
async def list_absences(date: ISODate | None = None, school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    d = date or vw.today_ist()
    rows = await db.fetch(
        """
        select a.*, p.full_name as teacher_name,
               count(s.id) filter (where s.status = 'open') as open,
               count(s.id) filter (where s.status = 'offering') as offering,
               count(s.id) filter (where s.status = 'filled') as filled,
               count(s.id) filter (where s.status = 'unfilled') as unfilled
        from public.teacher_absences a join public.profiles p on p.id = a.teacher_id
        left join public.substitutions s on s.absence_id = a.id
        where a.school_id = $1 and a.absence_date = $2
        group by a.id, p.full_name order by p.full_name
        """,
        sid, d,
    )
    return {"date": d.isoformat(), "absences": [_absence_out({**r, "teacher_name": r["teacher_name"]}) for r in rows]}


@router.get("/mine")
async def my_absences(user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    today = vw.today_ist()
    rows = await db.fetch(
        "select id, absence_date, reason, source from public.teacher_absences where teacher_id = $1 and absence_date >= $2 order by absence_date limit 60",
        uuid.UUID(user.id), today - timedelta(days=7),
    )
    return {"absences": [{"id": str(r["id"]), "date": r["absence_date"].isoformat(), "reason": r["reason"], "source": r["source"], "past": r["absence_date"] < today} for r in rows]}


@router.get("/{absence_id}/affected")
async def affected(absence_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row = await db.fetchrow("select * from public.teacher_absences where id = $1", as_uuid(absence_id, "absence"))
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That absence was not found.")
    term = await term_svc.require_active_term()
    data = await load_view_data(row["school_id"], term, (row["absence_date"], row["absence_date"]))
    subs = await db.fetch("select * from public.substitutions where absence_id = $1 order by start_period", row["id"])
    out = []
    for s in subs:
        out.append({
            "id": str(s["id"]), "start_period": s["start_period"], "length": s["length"], "label": s["label"],
            "time": timing_svc.time_range(data.grid, s["start_period"], s["length"]),
            "classes": [data.sems[x].label for x in (s["semester_ids"] or []) if x in data.sems],
            "status": s["status"], "mode": s["mode"], "substitute_name": data.person_name(s["substitute_id"]) if s["substitute_id"] else None,
        })
    return {
        "absence": {"id": str(row["id"]), "teacher_id": str(row["teacher_id"]), "teacher_name": data.person_name(row["teacher_id"]),
                    "date": row["absence_date"].isoformat(), "reason": row["reason"], "source": row["source"]},
        "substitutions": out,
    }


@router.delete("/{absence_id}")
async def remove_absence(absence_id: str, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    row = await db.fetchrow("select * from public.teacher_absences where id = $1", as_uuid(absence_id, "absence"))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "That absence was not found.")
    is_self = str(row["teacher_id"]) == user.id
    if user.role == "teacher" and not is_self:
        raise ApiError(403, "FORBIDDEN", "You can only undo your own absence.")
    if user.role == "hod" and not is_self and str(row["school_id"]) != user.school_id:
        raise ApiError(404, "NOT_FOUND", "That absence was not found.")
    if row["absence_date"] < vw.today_ist():
        raise ApiError(409, "PAST", "A past absence cannot be undone.")
    term = await term_svc.require_active_term()
    data = await load_view_data(row["school_id"], term, (row["absence_date"], row["absence_date"]))
    async with db.transaction() as conn:
        filled = await conn.fetch("select id, substitute_id, start_period, length, label, semester_ids, lecture_date from public.substitutions where absence_id = $1 and status = 'filled'", row["id"])
        pending_offers = await conn.fetch(
            "select distinct teacher_id from public.substitution_offers o join public.substitutions s on s.id = o.substitution_id "
            "where s.absence_id = $1 and o.status = 'pending'", row["id"],
        )
        await conn.execute("delete from public.teacher_absences where id = $1", row["id"])  # cascades its substitutions + offers
        await audit.record(conn, user, "delete", "absence", row["id"], f"Removed absence of {data.person_name(row['teacher_id'])} on {row['absence_date'].isoformat()}", school_id=row["school_id"])
        notices = []
        for f in filled:
            if f["substitute_id"]:
                line = subflow._sub_line(data, {**dict(f), "semester_ids": set(f["semester_ids"] or [])})
                notices.append(notify.Notice(f["substitute_id"], "sub_cancelled", "Substitution cancelled", f"You no longer need to cover {line}.", subflow.TEACHER_OFFER_LINK, {}, row["school_id"], f"sub-cxl:{f['id']}:{f['substitute_id']}"))
        for o in pending_offers:
            notices.append(notify.Notice(o["teacher_id"], "offer_closed", "Offer withdrawn", "A substitution offer was withdrawn (the absence was undone).", subflow.TEACHER_OFFER_LINK, {}, row["school_id"], f"offer-wd:{row['id']}:{o['teacher_id']}"))
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"removed": True}
