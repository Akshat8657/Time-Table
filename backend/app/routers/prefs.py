"""Teacher preferences + the preference window (plan Section 14).

  GET  /api/prefs/me                  my weekly grid, my special choices, the school's limit, the window            (teacher / HOD-as-teacher)
  PUT  /api/prefs/me                  save them - only while the window is open                                     (teacher / HOD-as-teacher)
  GET  /api/prefs/overview            the window, the limit and EVERY teacher filled / not filled                   (HOD / admin)
  GET  /api/prefs/teacher/{id}        one teacher's preferences, read-only                                          (HOD / admin)
  PUT  /api/prefs/settings            how many "not available" marks a teacher may use + when the reminder goes out  (HOD / admin)
  POST /api/prefs/window/open|close|reopen|extend                                                                    (HOD / admin)

Not-available slots + the daily maximum become HARD rules in the generator and the manual grid; stars + first / last lecture are a
soft rule (see services/prefs.py). Visiting-faculty days and hours are separate and cannot be edited here.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import notify
from ..services import prefflow
from ..services import prefs as pf
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services.views import IST
from ..validation import optional_text

router = APIRouter(prefix="/api/prefs", tags=["preferences"])
TEACHING = ("teacher", "hod")
MANAGERS = ("admin", "hod")
MIN_AHEAD = timedelta(minutes=1)
MAX_AHEAD = timedelta(days=120)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline(value: datetime, now: datetime) -> datetime:
    """A deadline typed in the browser is plain local time = IST; it must be in the future (but not years away)."""
    d = value.replace(tzinfo=IST) if value.tzinfo is None else value
    if d <= now + MIN_AHEAD:
        raise ApiError(422, "DEADLINE_PAST", "Choose a deadline that is still in the future.", extra={"fields": {"deadline": "Pick a later date and time."}})
    if d > now + MAX_AHEAD:
        raise ApiError(422, "DEADLINE_FAR", "Choose a deadline within the next 4 months.", extra={"fields": {"deadline": "Too far ahead."}})
    return d.astimezone(timezone.utc)


# ---------------------------------------------------------------------------------------------------- one teacher's screen
async def _payload(teacher: Any, school_id: uuid.UUID, *, is_self: bool) -> dict[str, Any]:
    """Everything the preference screen shows for one teacher (the same shape for the teacher and for the HOD's read-only view)."""
    now = _now()
    (grid, college), pref, settings, win, term = await asyncio.gather(
        timing_svc.school_grid(school_id), pf.load_teacher_pref(teacher["id"]), pf.load_settings(school_id), prefflow.window_row(school_id),
        term_svc.active_term(),
    )
    fixed = await db.fetch(
        "select label, day, start_period, length from public.fixed_slots where school_id = $1 and term_id = $2 and (teacher_id = $3 or co_teacher_id = $3) order by day, start_period",
        school_id, term["id"], teacher["id"],
    ) if term is not None else []
    days = min(6, college["days_per_week"])
    visiting = frozenset(teacher["visiting_days"] or []) if teacher["is_visiting"] else None
    slots = {f"{d}:{p}": "unavailable" for d, p in pref.unavailable} | {f"{d}:{p}": "preferred" for d, p in pref.preferred}
    open_now = win is not None and pf.window_is_open(win["status"], win["deadline"], now)
    submitted = False
    if win is not None:
        submitted = bool(await db.fetchval("select exists(select 1 from public.preference_responses where window_id = $1 and teacher_id = $2)", win["id"], teacher["id"]))
    if not is_self:
        reason = "View only - preferences are filled in by the teacher."
    elif open_now:
        reason = None
    elif win is None:
        reason = "No preference window is open. Your HOD opens one when it is time to collect preferences - until then you can view, but not change, your choices."
    else:
        when = win["closed_at"] or win["deadline"]
        reason = f"The preference window closed on {prefflow.deadline_text(when)}. You can view your choices but not change them. Ask your HOD to reopen it if you need to."
    return {
        "teacher": {
            "id": str(teacher["id"]), "name": teacher["full_name"], "is_visiting": bool(teacher["is_visiting"]),
            "visiting_days": sorted(visiting) if visiting is not None else [], "specialization": teacher["specialization"],
        },
        "grid": grid.as_dict(), "days": days, "periods": len(grid.periods),
        "locked_days": sorted(set(range(1, days + 1)) - visiting) if visiting is not None else [],
        "slots": slots, "prefer_first": pref.prefer_first, "avoid_last": pref.avoid_last, "max_per_day": pref.max_per_day,
        "limit": settings["max_unavailable"], "unavailable_count": len(pref.unavailable), "preferred_count": len(pref.preferred),
        "summary": pf.summary_text(pref), "fixed_notes": pf.fixed_notes(pref, fixed),
        "window": prefflow.window_out(win, now), "can_edit": bool(is_self and open_now), "reason": reason, "submitted": submitted,
    }


async def _teacher_row(teacher_id: uuid.UUID):
    return await db.fetchrow(
        "select id, full_name, school_id, role, is_visiting, visiting_days, specialization, is_active from public.profiles where id = $1", teacher_id
    )


@router.get("/me")
async def my_preferences(user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    if user.school_id is None:
        raise ApiError(403, "NO_SCHOOL", "Your account is not linked to a school yet. Please contact the administrator.")
    row = await _teacher_row(uuid.UUID(user.id))
    return await _payload(row, row["school_id"], is_self=True)


@router.get("/status")
async def my_status(user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    """Small and quick, for the dashboard: is a window open for me, and have I filled it?"""
    if user.school_id is None:
        return {"window": None, "submitted": False, "can_edit": False}
    sid, now = uuid.UUID(user.school_id), _now()
    win = await prefflow.window_row(sid)
    submitted = bool(win is not None and await db.fetchval(
        "select exists(select 1 from public.preference_responses where window_id = $1 and teacher_id = $2)", win["id"], uuid.UUID(user.id)))
    return {"window": prefflow.window_out(win, now), "submitted": submitted, "can_edit": bool(win is not None and pf.window_is_open(win["status"], win["deadline"], now))}


@router.get("/progress")
async def progress(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    """Small and quick, for the HOD's dashboard: the window and how many teachers have filled."""
    sid = await resolve_school(user, school_id)
    win = await prefflow.window_row(sid)
    total, filled = await asyncio.gather(
        db.fetchval("select count(*) from public.profiles where school_id = $1 and is_active and role in ('teacher', 'hod')", sid),
        db.fetchval("select count(*) from public.preference_responses r join public.profiles p on p.id = r.teacher_id where r.window_id = $1 and p.is_active", win["id"]) if win else asyncio.sleep(0, 0),
    )
    return {"window": prefflow.window_out(win, _now()), "filled": filled, "total": total}


class SlotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=6)
    period: int = Field(ge=1, le=20)
    kind: Literal["preferred", "unavailable"]


class PrefsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slots: list[SlotIn] = Field(default_factory=list, max_length=150)
    prefer_first: bool = False
    avoid_last: bool = False
    max_per_day: int | None = Field(default=None, ge=0, le=99)   # empty / 0 = no limit


@router.put("/me")
async def save_my_preferences(body: PrefsIn, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    if user.school_id is None:
        raise ApiError(403, "NO_SCHOOL", "Your account is not linked to a school yet. Please contact the administrator.")
    me, school_id = uuid.UUID(user.id), uuid.UUID(user.school_id)
    teacher = await _teacher_row(me)
    (grid, college), settings = await asyncio.gather(timing_svc.school_grid(school_id), pf.load_settings(school_id))
    slots: dict[pf.Cell, str] = {}
    for s in body.slots:
        key = (s.day, s.period)
        if key in slots and slots[key] != s.kind:
            raise ApiError(422, "BAD_SLOT", f"{pf.day_name(s.day)} period {s.period} was marked twice with different choices.")
        slots[key] = s.kind
    max_day = body.max_per_day or None
    try:
        pf.check_save(
            slots, working_days=college["days_per_week"], periods=len(grid.periods),
            visiting_days=frozenset(teacher["visiting_days"] or []) if teacher["is_visiting"] else None,
            limit=settings["max_unavailable"], max_per_day=max_day,
        )
    except pf.PrefProblem as err:
        raise ApiError(422, err.code, err.message, extra=err.extra) from None

    new = pf.TeacherPref(
        me, frozenset(c for c, k in slots.items() if k == "unavailable"), frozenset(c for c, k in slots.items() if k == "preferred"),
        body.prefer_first, body.avoid_last, max_day,
    )
    async with db.transaction() as conn:
        win = await conn.fetchrow(   # locked: the window cannot be closed under our feet while we save
            "select * from public.preference_windows where school_id = $1 and status = 'open' for update", school_id
        )
        if win is None or not pf.window_is_open(win["status"], win["deadline"], _now()):
            raise ApiError(409, "WINDOW_CLOSED", "The preference window is closed, so your choices cannot be changed now. You can still view them.")
        await conn.execute(
            """
            insert into public.teacher_preferences (teacher_id, school_id, prefer_first, avoid_last, max_per_day, updated_at, updated_by)
            values ($1, $2, $3, $4, $5, now(), $1)
            on conflict (teacher_id) do update set school_id = excluded.school_id, prefer_first = excluded.prefer_first,
              avoid_last = excluded.avoid_last, max_per_day = excluded.max_per_day, updated_at = now(), updated_by = $1
            """,
            me, school_id, body.prefer_first, body.avoid_last, max_day,
        )
        await conn.execute("delete from public.teacher_pref_slots where teacher_id = $1", me)
        if slots:
            await conn.executemany(
                "insert into public.teacher_pref_slots (teacher_id, school_id, day, period, kind) values ($1, $2, $3, $4, $5)",
                [(me, school_id, d, p, k) for (d, p), k in sorted(slots.items())],
            )
        await conn.execute(
            "insert into public.preference_responses (window_id, teacher_id, school_id) values ($1, $2, $3) "
            "on conflict (window_id, teacher_id) do update set submitted_at = now()",
            win["id"], me, school_id,
        )
        await audit.record(conn, user, "update", "preference", me, f"{user.full_name} saved their preferences ({pf.summary_text(new)})", school_id=school_id,
                           details={"unavailable": len(new.unavailable), "preferred": len(new.preferred), "max_per_day": max_day,
                                    "prefer_first": body.prefer_first, "avoid_last": body.avoid_last})
    return await _payload(teacher, school_id, is_self=True)


# ---------------------------------------------------------------------------------------------------- the HOD / admin console
async def _history(school_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select w.id, w.status, w.opened_at, w.deadline, w.closed_at, w.closed_reason,
               (select count(*) from public.preference_responses r where r.window_id = w.id) as filled
        from public.preference_windows w where w.school_id = $1 order by w.opened_at desc limit 6
        """,
        school_id,
    )
    return [
        {"id": str(r["id"]), "status": r["status"], "opened_at": r["opened_at"].astimezone(IST).isoformat(), "deadline": r["deadline"].astimezone(IST).isoformat(),
         "closed_reason": r["closed_reason"], "filled": r["filled"]}
        for r in rows
    ]


@router.get("/overview")
async def overview(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    now = _now()
    win, teachers, prefs, settings, name, history = await asyncio.gather(
        prefflow.window_row(sid), prefflow.school_teachers(sid), pf.load_prefs(sid), pf.load_settings(sid),
        db.fetchval("select name from public.schools where id = $1", sid), _history(sid),
    )
    done = await prefflow.filled_ids(win["id"]) if win is not None else {}
    rows = []
    for t in teachers:
        p = prefs.get(t["id"])
        c = p.counts() if p else {"unavailable": 0, "preferred": 0}
        rows.append({
            "id": str(t["id"]), "name": t["full_name"], "email": t["email"], "is_visiting": bool(t["is_visiting"]),
            "filled": t["id"] in done, "submitted_at": done[t["id"]].astimezone(IST).isoformat() if t["id"] in done else None,
            "has_prefs": bool(p and not p.is_blank), "unavailable": c["unavailable"], "preferred": c["preferred"],
            "max_per_day": p.max_per_day if p else None, "prefer_first": bool(p and p.prefer_first), "avoid_last": bool(p and p.avoid_last),
            "summary": pf.summary_text(p), "over_limit": c["unavailable"] > settings["max_unavailable"],
        })
    return {
        "school": {"id": str(sid), "name": name}, "settings": settings, "window": prefflow.window_out(win, now),
        "filled": sum(1 for r in rows if r["filled"]), "total": len(rows), "teachers": rows, "history": history,
    }


@router.get("/teacher/{teacher_id}")
async def teacher_preferences(teacher_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row = await _teacher_row(as_uuid(teacher_id, "teacher"))
    if row is None or row["role"] not in ("teacher", "hod") or row["school_id"] is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That teacher was not found.")
    return await _payload(row, row["school_id"], is_self=False)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    max_unavailable: int | None = Field(default=None, ge=0, le=72)
    reminder_hours: int | None = Field(default=None, ge=1, le=168)


@router.put("/settings")
async def save_settings(body: SettingsIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, body.school_id)
    current = await pf.load_settings(sid)
    new_max = current["max_unavailable"] if body.max_unavailable is None else body.max_unavailable
    new_hours = current["reminder_hours"] if body.reminder_hours is None else body.reminder_hours
    async with db.transaction() as conn:
        await conn.execute(
            """
            insert into public.preference_settings (school_id, max_unavailable, reminder_hours, updated_at, updated_by)
            values ($1, $2, $3, now(), $4)
            on conflict (school_id) do update set max_unavailable = excluded.max_unavailable, reminder_hours = excluded.reminder_hours,
              updated_at = now(), updated_by = excluded.updated_by
            """,
            sid, new_max, new_hours, uuid.UUID(user.id),
        )
        await audit.record(conn, user, "update", "preference_settings", sid,
                           f"Preference limit: at most {new_max} 'not available' slot(s) per teacher; reminder {new_hours} h before the deadline",
                           school_id=sid, details={"max_unavailable": new_max, "reminder_hours": new_hours})
    over = await db.fetchval(
        "select count(*) from (select teacher_id from public.teacher_pref_slots where school_id = $1 and kind = 'unavailable' "
        "group by teacher_id having count(*) > $2) x", sid, new_max,
    )
    return {"settings": {"max_unavailable": new_max, "reminder_hours": new_hours}, "teachers_over_limit": over}


# ---------------------------------------------------------------------------------------------------- the window
class OpenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    deadline: datetime
    note: optional_text(300) = None  # type: ignore[valid-type]


class DeadlineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    deadline: datetime


class SchoolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None


async def _unfilled(school_id: uuid.UUID, window_id: uuid.UUID, exclude: uuid.UUID) -> list[Any]:
    done = await prefflow.filled_ids(window_id)
    return [t for t in await prefflow.school_teachers(school_id, exclude=exclude) if t["id"] not in done]


@router.post("/window/open", status_code=201)
async def open_window(body: OpenIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, body.school_id)
    now = _now()
    deadline = _deadline(body.deadline, now)
    school_name = await db.fetchval("select name from public.schools where id = $1", sid)
    async with db.transaction() as conn:
        if await conn.fetchval("select exists(select 1 from public.preference_windows where school_id = $1 and status = 'open')", sid):
            raise ApiError(409, "ALREADY_OPEN", "A preference window is already open for this school. Close it first, or extend its deadline.")
        row = await conn.fetchrow(
            "insert into public.preference_windows (school_id, deadline, note, opened_by) values ($1, $2, $3, $4) returning *",
            sid, deadline, body.note, uuid.UUID(user.id),
        )
        teachers = await prefflow.school_teachers(sid, exclude=uuid.UUID(user.id))
        sent = await notify.create(conn, prefflow.open_notices(row["id"], sid, school_name, deadline, teachers, body.note))
        await audit.record(conn, user, "open", "preference_window", row["id"],
                           f"Opened the preference window of {school_name} until {prefflow.deadline_text(deadline)} ({len(teachers)} teacher(s) told)", school_id=sid)
    await notify.deliver(sent)
    return {"window": prefflow.window_out(row, now), "notified": len(sent)}


@router.post("/window/close")
async def close_window(body: SchoolIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, body.school_id)
    now = _now()
    async with db.transaction() as conn:
        row = await conn.fetchrow("select * from public.preference_windows where school_id = $1 and status = 'open' for update", sid)
        if row is None:
            raise ApiError(409, "NOT_OPEN", "There is no open preference window to close.")
        row = await conn.fetchrow(
            "update public.preference_windows set status = 'closed', closed_at = $2, closed_reason = 'manual', closed_by = $3 where id = $1 returning *",
            row["id"], now, uuid.UUID(user.id),
        )
        filled = await conn.fetchval("select count(*) from public.preference_responses where window_id = $1", row["id"])
        await audit.record(conn, user, "close", "preference_window", row["id"], f"Closed the preference window ({filled} teacher(s) had filled)", school_id=sid)
    return {"window": prefflow.window_out(row, now)}


@router.post("/window/reopen")
async def reopen_window(body: DeadlineIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, body.school_id)
    now = _now()
    deadline = _deadline(body.deadline, now)
    async with db.transaction() as conn:
        if await conn.fetchval("select exists(select 1 from public.preference_windows where school_id = $1 and status = 'open')", sid):
            raise ApiError(409, "ALREADY_OPEN", "The preference window is already open.")
        last = await conn.fetchrow("select * from public.preference_windows where school_id = $1 order by opened_at desc limit 1 for update", sid)
        if last is None:
            raise ApiError(409, "NOTHING_TO_REOPEN", "There is no earlier window to reopen. Open a new one instead.")
        row = await conn.fetchrow(
            "update public.preference_windows set status = 'open', deadline = $2, opened_at = $3, closed_at = null, closed_reason = null, closed_by = null, "
            "reminder_sent_at = null, reopened_count = reopened_count + 1 where id = $1 returning *",
            last["id"], deadline, now,
        )
        todo = await _unfilled(sid, row["id"], uuid.UUID(user.id))
        sent = await notify.create(conn, prefflow.reopen_notices(row["id"], row["reopened_count"], sid, deadline, todo))
        await audit.record(conn, user, "reopen", "preference_window", row["id"],
                           f"Reopened the preference window until {prefflow.deadline_text(deadline)} ({len(todo)} teacher(s) who had not filled were told)", school_id=sid)
    await notify.deliver(sent)
    return {"window": prefflow.window_out(row, now), "notified": len(sent)}


@router.post("/window/extend")
async def extend_window(body: DeadlineIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, body.school_id)
    now = _now()
    deadline = _deadline(body.deadline, now)
    async with db.transaction() as conn:
        row = await conn.fetchrow("select * from public.preference_windows where school_id = $1 and status = 'open' for update", sid)
        if row is None:
            raise ApiError(409, "NOT_OPEN", "There is no open preference window. Use Reopen instead.")
        if deadline <= row["deadline"]:
            raise ApiError(422, "DEADLINE_NOT_LATER", "Choose a time after the current deadline.", extra={"fields": {"deadline": "Pick a later time."}})
        settings = await pf.load_settings(sid)
        due = pf.reminder_at(row["opened_at"], deadline, settings["reminder_hours"])
        row = await conn.fetchrow(   # the reminder is re-armed when it has not fired yet for the new, later deadline
            "update public.preference_windows set deadline = $2, reminder_sent_at = case when $3 > now() then null else reminder_sent_at end where id = $1 returning *",
            row["id"], deadline, due,
        )
        todo = await _unfilled(sid, row["id"], uuid.UUID(user.id))
        sent = await notify.create(conn, prefflow.extend_notices(row["id"], sid, deadline, todo))
        await audit.record(conn, user, "extend", "preference_window", row["id"],
                           f"Extended the preference deadline to {prefflow.deadline_text(deadline)} ({len(todo)} teacher(s) who had not filled were told)", school_id=sid)
    await notify.deliver(sent)
    return {"window": prefflow.window_out(row, now), "notified": len(sent)}
