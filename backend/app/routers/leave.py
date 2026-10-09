"""Leave requests (plan Section 13): a teacher asks for leave (date or range) -> the HOD approves or rejects.

A HOD's own leave goes to the administrators.  Approving a leave marks the teacher absent on each working day of the range
and prepares the substitution needs for those days.  Everyone is notified through the Phase 4 notification service.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import notify
from ..services import subflow
from ..services import terms as term_svc
from ..services import views as vw
from ..services.changes import load_changes
from ..services.viewdata import load_view_data
from ..validation import ISODate, optional_text

router = APIRouter(prefix="/api/leave", tags=["leave"])
TEACHING = ("teacher", "hod")
MANAGERS = ("admin", "hod")


def leave_out(r: Any, *, name: str | None = None, decided_by_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "teacher_id": str(r["teacher_id"]), "teacher_name": name,
        "date_from": r["date_from"].isoformat(), "date_to": r["date_to"].isoformat(),
        "days": (r["date_to"] - r["date_from"]).days + 1, "reason": r["reason"], "status": r["status"],
        "decided_by": str(r["decided_by"]) if r["decided_by"] else None, "decided_by_name": decided_by_name,
        "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None, "decision_note": r["decision_note"],
        "created_at": r["created_at"].isoformat(),
    }


class LeaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: ISODate
    date_to: ISODate | None = None
    reason: optional_text(300) = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _ok(self):
        last = self.date_to or self.date_from
        if last < self.date_from:
            raise ValueError("The end date cannot be before the start date.")
        if (last - self.date_from).days > 120:
            raise ValueError("One leave request can cover at most 121 days.")
        return self


@router.post("", status_code=201)
async def request_leave(body: LeaveIn, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    if user.school_id is None:
        raise ApiError(403, "NO_SCHOOL", "Your account is not linked to a school yet.")
    school_id = uuid.UUID(user.school_id)
    term = await term_svc.require_active_term()
    date_to = body.date_to or body.date_from
    me = uuid.UUID(user.id)
    async with db.transaction() as conn:
        clash = await conn.fetchval(
            "select 1 from public.leave_requests where teacher_id = $1 and status = 'pending' and date_from <= $2 and date_to >= $3",
            me, date_to, body.date_from,
        )
        if clash:
            raise ApiError(409, "LEAVE_PENDING", "You already have a pending leave request that overlaps these dates.")
        row = await conn.fetchrow(
            "insert into public.leave_requests (school_id, term_id, teacher_id, date_from, date_to, reason) "
            "values ($1, $2, $3, $4, $5, $6) returning *",
            school_id, term["id"], me, body.date_from, date_to, body.reason,
        )
        await audit.record(conn, user, "create", "leave_request", row["id"],
                           f"Requested leave {body.date_from.isoformat()} to {date_to.isoformat()}", school_id=school_id)
        managers = await subflow.managers_of(school_id, exclude=me)
        span = body.date_from.strftime("%d %b") + ("" if date_to == body.date_from else f" to {date_to.strftime('%d %b %Y')}")
        notices = [
            notify.Notice(m, "leave_requested", f"Leave request from {user.full_name}", f"{span}." + (f" Reason: {body.reason}" if body.reason else ""),
                          subflow.CONSOLE_LINK, {"leave_id": str(row["id"])}, school_id, f"leave-req:{row['id']}:{m}")
            for m in managers
        ]
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"id": str(row["id"]), "status": "pending"}


@router.get("")
async def list_leave(
    scope: Literal["mine", "school"] = "mine", status: str | None = None, school_id: str | None = None,
    user: CurrentUser = Depends(require_roles("admin", "hod", "teacher")),
) -> dict:
    args: list[Any] = []
    where = []
    if user.role == "teacher" or scope == "mine":
        args.append(uuid.UUID(user.id))
        where.append(f"l.teacher_id = ${len(args)}")
    elif user.role == "hod":
        args.append(uuid.UUID(user.school_id))  # type: ignore[arg-type]
        where.append(f"l.school_id = ${len(args)}")
    elif school_id:
        args.append(await resolve_school(user, school_id))
        where.append(f"l.school_id = ${len(args)}")
    if status in ("pending", "approved", "rejected", "cancelled"):
        args.append(status)
        where.append(f"l.status = ${len(args)}")
    sql = (
        "select l.*, p.full_name as teacher_name, d.full_name as decided_by_name "
        "from public.leave_requests l join public.profiles p on p.id = l.teacher_id "
        "left join public.profiles d on d.id = l.decided_by"
    )
    if where:
        sql += " where " + " and ".join(where)
    rows = await db.fetch(sql + " order by l.created_at desc limit 300", *args)
    return {"leave": [leave_out(r, name=r["teacher_name"], decided_by_name=r["decided_by_name"]) for r in rows]}


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    note: optional_text(300) = None  # type: ignore[valid-type]


@router.post("/{leave_id}/decide")
async def decide_leave(leave_id: str, body: DecideIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row = await db.fetchrow("select * from public.leave_requests where id = $1", as_uuid(leave_id, "leave request"))
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That leave request was not found.")
    if str(row["teacher_id"]) == user.id:
        raise ApiError(403, "FORBIDDEN", "You cannot decide on your own leave request.")
    if row["status"] != "pending":
        raise ApiError(409, "ALREADY_DECIDED", f"This request is already {row['status']}.")
    school_id = row["school_id"]
    term = await term_svc.require_active_term()
    status = "approved" if body.decision == "approve" else "rejected"
    created_subs = 0
    async with db.transaction() as conn:
        await conn.execute(
            "update public.leave_requests set status = $2, decided_by = $3, decided_at = now(), decision_note = $4 where id = $1",
            row["id"], status, uuid.UUID(user.id), body.note,
        )
        if status == "approved" and row["term_id"] == term["id"]:
            data = await load_view_data(school_id, term, (row["date_from"], row["date_to"]))
            change = await load_changes(school_id, term["id"], (row["date_from"], row["date_to"]))
            days_per_week = data.days_per_week
            d = row["date_from"]
            while d <= row["date_to"]:
                if d.isoweekday() <= min(6, days_per_week):
                    absence = await conn.fetchrow(
                        "insert into public.teacher_absences (school_id, term_id, teacher_id, absence_date, reason, source, leave_id, created_by) "
                        "values ($1, $2, $3, $4, $5, 'leave', $6, $7) on conflict (teacher_id, absence_date) do nothing returning id",
                        school_id, term["id"], row["teacher_id"], d, row["reason"], row["id"], uuid.UUID(user.id),
                    )
                    if absence is not None:
                        subs = await subflow.make_subs(
                            conn, data, change, absence_id=absence["id"], teacher_id=row["teacher_id"], d=d,
                            school_id=school_id, term_id=term["id"], created_by=uuid.UUID(user.id),
                        )
                        created_subs += sum(1 for s in subs if s["status"] == "open")
                d += timedelta(days=1)
        await audit.record(conn, user, status, "leave_request", row["id"],
                           f"{status.capitalize()} leave of {row['teacher_id']} ({row['date_from']} to {row['date_to']})", school_id=school_id)
        name = await db.fetchval("select full_name from public.profiles where id = $1", row["teacher_id"])
        body_text = (
            f"Your leave {row['date_from'].strftime('%d %b')}-{row['date_to'].strftime('%d %b %Y')} was {status}."
            + (f" Note: {body.note}" if body.note else "")
        )
        notices = [notify.Notice(row["teacher_id"], f"leave_{status}", f"Leave {status}", body_text, subflow.TEACHER_ABSENCE_LINK, {"leave_id": str(row["id"])}, school_id, f"leave-{status}:{row['id']}")]
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"status": status, "substitutions_prepared": created_subs}


@router.post("/{leave_id}/cancel")
async def cancel_leave(leave_id: str, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    row = await db.fetchrow("select * from public.leave_requests where id = $1", as_uuid(leave_id, "leave request"))
    if row is None or str(row["teacher_id"]) != user.id:
        raise ApiError(404, "NOT_FOUND", "That leave request was not found.")
    if row["status"] != "pending":
        raise ApiError(409, "ALREADY_DECIDED", f"This request is already {row['status']} and cannot be withdrawn.")
    async with db.transaction() as conn:
        await conn.execute("update public.leave_requests set status = 'cancelled' where id = $1", row["id"])
        await audit.record(conn, user, "cancel", "leave_request", row["id"], "Withdrew leave request", school_id=row["school_id"])
    return {"status": "cancelled"}
