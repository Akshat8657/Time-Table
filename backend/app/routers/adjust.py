"""One-day adjustments (plan Section 13 items 8-9): move / cancel / swap ONE class's lecture for ONE date.

The weekly published timetable never changes.  Every change is clash-checked against that date's effective timetable, the
affected teachers are notified, and a cancelled lecture is counted as lost in the Phase 4 hours tracking.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import adjust as adj_svc
from ..services import notify
from ..services import subflow
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services import views as vw
from ..services.changes import load_changes
from ..services.viewdata import ViewData, load_view_data
from ..validation import ISODate, optional_text

router = APIRouter(prefix="/api/adjust", tags=["adjustments"])
MANAGERS = ("admin", "hod")


async def _ctx(user: CurrentUser, semester_id: str, d: date) -> tuple[uuid.UUID, Any, ViewData, Any, Any]:
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    term = await term_svc.require_active_term()
    data = await load_view_data(sem["school_id"], term, (d, d))
    change = await load_changes(sem["school_id"], term["id"], (d, d))
    if sem["id"] not in data.sems:
        raise ApiError(409, "SEMESTER_NOT_ACTIVE", "That class is not running in the active term.")
    return sem["id"], sem, data, change, term


def _snapshot(data: ViewData, change, sem_id: uuid.UUID, d: date, start: int) -> dict[str, Any]:
    wd = d.isoweekday()
    group = [r for r in data.rows if r.semester_id == sem_id and r.day == wd and r.start == start]
    if not group:
        raise ApiError(404, "NO_LECTURE", "There is no lecture of this class at that period on that day.")
    key = group[0].key
    across = {r.semester_id for r in data.rows if r.key == key}
    if len(across) > 1:
        raise ApiError(409, "COMBINED", "This is a combined session shared by several classes - it cannot be adjusted for one class.")
    if change.adjustment_for(d, {sem_id}, start) is not None:
        raise ApiError(409, "ALREADY_ADJUSTED", "This lecture is already moved or cancelled for that date.")
    a, b = data.period_minutes(start, group[0].length)
    if data.calendar.blocking(data.school_id, sem_id, d, a, b) is not None:
        raise ApiError(409, "NOT_RUNNING", "That lecture does not run on that date (holiday / event).")
    subject_ids = {r.subject_id for r in group if r.subject_id}
    teachers = {t for r in group for t in r.teachers}
    label = " / ".join(sorted(data.subjects[s].name for s in subject_ids if s in data.subjects)) or group[0].label
    return {
        "start": start, "length": group[0].length, "subject_ids": subject_ids, "teachers": teachers, "label": label,
        "parallel_key": group[0].group_key,
        "teacher_id": group[0].teacher_id if len(group) == 1 else None,
        "co_teacher_id": group[0].co_teacher_id if len(group) == 1 else None,
    }


async def _insert(conn, *, school_id, term_id, d, kind, sem_id, snap, new_start, swap_group, reason, created_by) -> uuid.UUID:
    row = await conn.fetchrow(
        """
        insert into public.day_adjustments
          (school_id, term_id, lecture_date, kind, semester_ids, orig_start, length, new_start, subject_ids, parallel_key,
           label, teacher_id, co_teacher_id, swap_group, reason, created_by)
        values ($1, $2, $3, $4, $5::uuid[], $6, $7, $8, $9::uuid[], $10, $11, $12, $13, $14, $15, $16) returning id
        """,
        school_id, term_id, d, kind, [sem_id], snap["start"], snap["length"], new_start, list(snap["subject_ids"]),
        snap["parallel_key"], snap["label"][:200], snap["teacher_id"], snap["co_teacher_id"], swap_group, reason, created_by,
    )
    return row["id"]


def _notices(data: ViewData, snap: dict, sem_id: uuid.UUID, d: date, text: str, school_id: uuid.UUID, tag: str) -> list[notify.Notice]:
    cls = data.sems[sem_id].label if sem_id in data.sems else "a class"
    return [
        notify.Notice(t, "lecture_adjusted", "A lecture of yours changed", f"{snap['label']} ({cls}, {d.strftime('%a %d %b')}): {text}",
                      subflow.TEACHER_ABSENCE_LINK, {"date": d.isoformat()}, school_id, f"adjust:{tag}:{t}")
        for t in snap["teachers"]
    ]


# ---------------------------------------------------------------------------------------------------- read
@router.get("")
async def day_lectures(semester_id: str, date: ISODate, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sem_id, sem, data, change, _term = await _ctx(user, semester_id, date)
    cells = vw.build_cells(data, data.rows_of_class(sem_id), across_classes=False, sem_ids={sem_id},
                           day=date.isoweekday(), dates={date.isoweekday(): date}, with_extras=True, change=change)
    adjustable = []
    wd = date.isoweekday()
    for r in data.rows:
        if r.semester_id == sem_id and r.day == wd:
            across = {x.semester_id for x in data.rows if x.key == r.key}
            adjustable.append({"start_period": r.start, "combined": len(across) > 1})
    raw = await db.fetch(
        "select id, kind, orig_start, new_start, length, label, swap_group from public.day_adjustments "
        "where school_id = $1 and lecture_date = $2 and $3 = any(semester_ids) order by orig_start",
        sem["school_id"], date, sem_id,
    )
    return {
        "date": date.isoformat(), "class_label": data.sems[sem_id].label, "grid": data.grid.as_dict(),
        "cells": cells, "stats": {**vw.stats_of(cells), **vw.day_extra_stats(cells)},
        "adjustable_starts": sorted({a["start_period"] for a in adjustable if not a["combined"]}),
        "adjustments": [
            {"id": str(a["id"]), "kind": a["kind"], "orig_start": a["orig_start"], "new_start": a["new_start"],
             "label": a["label"], "swap_group": str(a["swap_group"]) if a["swap_group"] else None}
            for a in raw
        ],
    }


@router.get("/suggest")
async def suggest(semester_id: str, date: ISODate, start_period: int, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sem_id, sem, data, change, _term = await _ctx(user, semester_id, date)
    snap = _snapshot(data, change, sem_id, date, start_period)
    moves = adj_svc.suggest_moves(data, change, sem_ids=[sem_id], d=date, orig_start=start_period, length=snap["length"], teachers=snap["teachers"])
    swaps = adj_svc.suggest_swaps(data, change, sem_id=sem_id, d=date, orig_start=start_period, length=snap["length"], teachers=snap["teachers"])
    return {
        "lecture": {"label": snap["label"], "start_period": start_period, "length": snap["length"],
                    "time": timing_svc.time_range(data.grid, start_period, snap["length"])},
        "moves": moves, "swaps": swaps,
        "message": (f"{len(moves)} move option(s) and {len(swaps)} swap option(s) are clash-free today."
                    if moves or swaps else "No clash-free move or swap is possible for this lecture today."),
    }


# ---------------------------------------------------------------------------------------------------- move
class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    date: ISODate
    start_period: int
    new_start: int
    reason: optional_text(300) = None  # type: ignore[valid-type]


@router.post("/move", status_code=201)
async def move(body: MoveIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sem_id, sem, data, change, term = await _ctx(user, body.semester_id, body.date)
    snap = _snapshot(data, change, sem_id, body.date, body.start_period)
    ok, why = adj_svc.can_move(data, change, sem_ids=[sem_id], d=body.date, orig_start=body.start_period, length=snap["length"], new_start=body.new_start, teachers=snap["teachers"])
    if not ok:
        raise ApiError(409, "CLASH", f"Cannot move there: {why}.")
    async with db.transaction() as conn:
        aid = await _insert(conn, school_id=sem["school_id"], term_id=term["id"], d=body.date, kind="move", sem_id=sem_id, snap=snap, new_start=body.new_start, swap_group=None, reason=body.reason, created_by=uuid.UUID(user.id))
        await audit.record(conn, user, "move", "day_adjustment", aid, f"Moved {snap['label']} ({data.sems[sem_id].label}) from P{body.start_period} to P{body.new_start} on {body.date.isoformat()}", school_id=sem["school_id"])
        text = f"moved from period {body.start_period} to period {body.new_start} ({timing_svc.time_range(data.grid, body.new_start, snap['length'])})"
        sent = await notify.create(conn, _notices(data, snap, sem_id, body.date, text, sem["school_id"], f"move:{aid}"))
    await notify.deliver(sent)
    return {"id": str(aid), "kind": "move", "new_start": body.new_start}


# ---------------------------------------------------------------------------------------------------- cancel
class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    date: ISODate
    start_period: int
    reason: optional_text(300) = None  # type: ignore[valid-type]


@router.post("/cancel", status_code=201)
async def cancel(body: CancelIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sem_id, sem, data, change, term = await _ctx(user, body.semester_id, body.date)
    snap = _snapshot(data, change, sem_id, body.date, body.start_period)
    async with db.transaction() as conn:
        aid = await _insert(conn, school_id=sem["school_id"], term_id=term["id"], d=body.date, kind="cancel", sem_id=sem_id, snap=snap, new_start=None, swap_group=None, reason=body.reason, created_by=uuid.UUID(user.id))
        # cancel any substitution for that exact slot (no point covering a cancelled lecture)
        subs = await conn.fetch(
            "update public.substitutions set status = 'cancelled', offer_deadline = null "
            "where school_id = $1 and lecture_date = $2 and start_period = $3 and status <> 'cancelled' and semester_ids && $4::uuid[] "
            "returning id, substitute_id",
            sem["school_id"], body.date, body.start_period, [sem_id],
        )
        for s in subs:
            await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where substitution_id = $1 and status = 'pending'", s["id"])
        await audit.record(conn, user, "cancel", "day_adjustment", aid, f"Cancelled {snap['label']} ({data.sems[sem_id].label}) on {body.date.isoformat()}", school_id=sem["school_id"])
        notices = _notices(data, snap, sem_id, body.date, "cancelled for that day", sem["school_id"], f"cancel:{aid}")
        for s in subs:
            if s["substitute_id"]:
                notices.append(notify.Notice(s["substitute_id"], "sub_cancelled", "Substitution cancelled", f"{snap['label']} on {body.date.strftime('%a %d %b')} was cancelled - no need to cover it.", subflow.TEACHER_OFFER_LINK, {}, sem["school_id"], f"sub-cxl:{s['id']}:{s['substitute_id']}"))
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"id": str(aid), "kind": "cancel"}


# ---------------------------------------------------------------------------------------------------- swap
class SwapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    date: ISODate
    start_period: int
    with_start: int
    reason: optional_text(300) = None  # type: ignore[valid-type]


@router.post("/swap", status_code=201)
async def swap(body: SwapIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    if body.start_period == body.with_start:
        raise ApiError(422, "SAME_PERIOD", "Choose two different periods to swap.")
    sem_id, sem, data, change, term = await _ctx(user, body.semester_id, body.date)
    a = _snapshot(data, change, sem_id, body.date, body.start_period)
    b = _snapshot(data, change, sem_id, body.date, body.with_start)
    if a["length"] != b["length"]:
        raise ApiError(409, "SIZE", "Only lectures of the same length (e.g. two single periods) can be swapped.")
    exclude = {body.start_period, body.with_start}
    ok_a, why_a = adj_svc.can_move(data, change, sem_ids=[sem_id], d=body.date, orig_start=body.start_period, length=a["length"], new_start=body.with_start, teachers=a["teachers"], exclude_base=exclude)
    ok_b, why_b = adj_svc.can_move(data, change, sem_ids=[sem_id], d=body.date, orig_start=body.with_start, length=b["length"], new_start=body.start_period, teachers=b["teachers"], exclude_base=exclude)
    if not (ok_a and ok_b):
        raise ApiError(409, "CLASH", f"Cannot swap: {why_a if not ok_a else why_b}.")
    group = uuid.uuid4()
    async with db.transaction() as conn:
        id_a = await _insert(conn, school_id=sem["school_id"], term_id=term["id"], d=body.date, kind="move", sem_id=sem_id, snap=a, new_start=body.with_start, swap_group=group, reason=body.reason, created_by=uuid.UUID(user.id))
        await _insert(conn, school_id=sem["school_id"], term_id=term["id"], d=body.date, kind="move", sem_id=sem_id, snap=b, new_start=body.start_period, swap_group=group, reason=body.reason, created_by=uuid.UUID(user.id))
        await audit.record(conn, user, "swap", "day_adjustment", id_a, f"Swapped {a['label']} (P{body.start_period}) and {b['label']} (P{body.with_start}) of {data.sems[sem_id].label} on {body.date.isoformat()}", school_id=sem["school_id"])
        notices = (
            _notices(data, a, sem_id, body.date, f"swapped to period {body.with_start}", sem["school_id"], f"swap:{group}:a")
            + _notices(data, b, sem_id, body.date, f"swapped to period {body.start_period}", sem["school_id"], f"swap:{group}:b")
        )
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"swap_group": str(group), "kind": "swap"}


@router.delete("/{adjust_id}")
async def undo(adjust_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row = await db.fetchrow("select * from public.day_adjustments where id = $1", as_uuid(adjust_id, "adjustment"))
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That adjustment was not found.")
    async with db.transaction() as conn:
        if row["swap_group"] is not None:
            await conn.execute("delete from public.day_adjustments where swap_group = $1", row["swap_group"])
        else:
            await conn.execute("delete from public.day_adjustments where id = $1", row["id"])
        await audit.record(conn, user, "undo", "day_adjustment", row["id"], f"Undid a {row['kind']} on {row['lecture_date'].isoformat()}", school_id=row["school_id"])
    return {"undone": True}
