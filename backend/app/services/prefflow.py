"""The preference window (plan Section 14): who is told what when it opens / reopens / is extended / nears its deadline / ends.

The window is the HOD's tool: while it is OPEN every teacher of the school can edit their preferences, outside it they can only
view. It has a deadline; a reminder goes to the teachers who have NOT filled yet; at the deadline it closes by itself and the
HOD hears how many filled. All messages go through the Phase 4 notification service and only to the people they concern (no spam).

  window_row()        the window to show for a school: the open one, else the most recent one
  window_out()        its JSON shape
  filled_ids()        who has submitted in a window
  open_notices() ...  the messages
  sweep()             the 60-second background job: reminders + closing at the deadline
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from .. import db
from . import notify
from . import prefs as pf
from .views import IST

LINK_MINE = "/my/preferences"          # a teacher's own screen (each role lands on its own copy)
LINK_CONSOLE = "/preference-window"    # the HOD / admin console


def deadline_text(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


# ---------------------------------------------------------------------------------------------------- reading
async def window_row(school_id: uuid.UUID, conn=None):
    """The OPEN window of a school, else its most recent one (None if there never was one)."""
    q = "select * from public.preference_windows where school_id = $1 order by (status = 'open') desc, opened_at desc limit 1"
    return await (conn.fetchrow(q, school_id) if conn is not None else db.fetchrow(q, school_id))


def window_out(row, now: datetime) -> dict[str, Any] | None:
    if row is None:
        return None
    is_open = pf.window_is_open(row["status"], row["deadline"], now)
    return {
        "id": str(row["id"]), "status": row["status"], "is_open": is_open,
        "opened_at": row["opened_at"].astimezone(IST).isoformat(), "deadline": row["deadline"].astimezone(IST).isoformat(),
        "deadline_text": deadline_text(row["deadline"]),
        "closed_at": row["closed_at"].astimezone(IST).isoformat() if row["closed_at"] else None,
        "closed_reason": row["closed_reason"], "reopened_count": row["reopened_count"], "note": row["note"],
        "seconds_left": max(0, int((row["deadline"] - now).total_seconds())) if is_open else 0,
    }


async def school_teachers(school_id: uuid.UUID, exclude: uuid.UUID | None = None) -> list[Any]:
    """Everyone who teaches in the school (the HOD is a teacher too): active teacher + HOD profiles."""
    rows = await db.fetch(
        "select id, full_name, email, is_visiting, visiting_days, specialization from public.profiles "
        "where school_id = $1 and is_active and role in ('teacher', 'hod') order by full_name",
        school_id,
    )
    return [r for r in rows if r["id"] != exclude]


async def filled_ids(window_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
    rows = await db.fetch("select teacher_id, submitted_at from public.preference_responses where window_id = $1", window_id)
    return {r["teacher_id"]: r["submitted_at"] for r in rows}


# ---------------------------------------------------------------------------------------------------- the messages
def open_notices(window_id: uuid.UUID, school_id: uuid.UUID, school_name: str, deadline: datetime, teachers: Iterable[Any], note: str | None) -> list[notify.Notice]:
    body = f"Please fill your teaching preferences before {deadline_text(deadline)}." + (f" Note from your HOD: {note}" if note else "")
    return [
        notify.Notice(t["id"], "pref_window_open", "Preference window is open", body, LINK_MINE,
                      {"window_id": str(window_id), "deadline": deadline.isoformat()}, school_id, f"pref-open:{window_id}:{t['id']}")
        for t in teachers
    ]


def reopen_notices(window_id: uuid.UUID, count: int, school_id: uuid.UUID, deadline: datetime, teachers: Iterable[Any]) -> list[notify.Notice]:
    return [
        notify.Notice(t["id"], "pref_window_open", "Preference window reopened", f"You have not filled your preferences yet. New deadline: {deadline_text(deadline)}.",
                      LINK_MINE, {"window_id": str(window_id), "deadline": deadline.isoformat()}, school_id, f"pref-reopen:{window_id}:{count}:{t['id']}")
        for t in teachers
    ]


def extend_notices(window_id: uuid.UUID, school_id: uuid.UUID, deadline: datetime, teachers: Iterable[Any]) -> list[notify.Notice]:
    return [
        notify.Notice(t["id"], "pref_window_open", "Preference deadline extended", f"You have not filled your preferences yet. New deadline: {deadline_text(deadline)}.",
                      LINK_MINE, {"window_id": str(window_id), "deadline": deadline.isoformat()}, school_id, f"pref-extend:{window_id}:{int(deadline.timestamp())}:{t['id']}")
        for t in teachers
    ]


def reminder_notices(window_id: uuid.UUID, count: int, school_id: uuid.UUID, deadline: datetime, teachers: Iterable[Any]) -> list[notify.Notice]:
    return [
        notify.Notice(t["id"], "pref_reminder", "Reminder: fill your preferences", f"The preference window closes {deadline_text(deadline)} and you have not filled yours yet.",
                      LINK_MINE, {"window_id": str(window_id), "deadline": deadline.isoformat()}, school_id, f"pref-remind:{window_id}:{count}:{t['id']}")
        for t in teachers
    ]


def closed_notices(window_id: uuid.UUID, count: int, school_id: uuid.UUID, managers: Iterable[uuid.UUID], filled: int, total: int, missing: list[str]) -> list[notify.Notice]:
    who = ""
    if missing:
        who = " Not filled: " + ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "") + "."
    return [
        notify.Notice(m, "pref_window_closed", "Preference window closed", f"{filled} of {total} teachers filled their preferences.{who}",
                      LINK_CONSOLE, {"window_id": str(window_id), "filled": filled, "total": total}, school_id, f"pref-closed:{window_id}:{count}:{m}")
        for m in managers
    ]


async def managers_of(school_id: uuid.UUID, exclude: uuid.UUID | None = None) -> list[uuid.UUID]:
    """The school's HOD(s) + every admin (the people who run the window)."""
    rows = await db.fetch(
        "select id from public.profiles where is_active and (role = 'admin' or (role = 'hod' and school_id = $1))", school_id
    )
    return [r["id"] for r in rows if r["id"] != exclude]


# ---------------------------------------------------------------------------------------------------- the background job
async def sweep(now_utc: datetime) -> dict[str, int]:
    """Runs every ~60 s from the server: (1) sends the reminder to teachers who have not filled yet, once per window;
    (2) closes a window whose deadline has passed and tells the HOD how many filled. Safe to run anywhere, any time (idempotent)."""
    out = {"reminded": 0, "closed": 0}
    rows = await db.fetch(
        """
        select w.*, coalesce(s.reminder_hours, $1::smallint) as reminder_hours, sc.name as school_name
        from public.preference_windows w
        left join public.preference_settings s on s.school_id = w.school_id
        join public.schools sc on sc.id = w.school_id
        where w.status = 'open'
        """,
        pf.DEFAULT_REMINDER_HOURS,
    )
    for w in rows:
        school_id, wid = w["school_id"], w["id"]
        if w["deadline"] <= now_utc:   # (2) the deadline has passed
            sent: list[notify.Sent] = []
            async with db.transaction() as conn:
                locked = await conn.fetchrow("select status from public.preference_windows where id = $1 for update", wid)
                if locked is None or locked["status"] != "open":
                    continue
                await conn.execute("update public.preference_windows set status = 'closed', closed_at = $2, closed_reason = 'deadline' where id = $1", wid, now_utc)
                everyone = await school_teachers(school_id)
                done = {r["teacher_id"] for r in await conn.fetch("select teacher_id from public.preference_responses where window_id = $1", wid)}
                missing = [t["full_name"] for t in everyone if t["id"] not in done]
                notices = closed_notices(wid, w["reopened_count"], school_id, await managers_of(school_id), len(everyone) - len(missing), len(everyone), missing)
                sent = await notify.create(conn, notices)
            await notify.deliver(sent)
            out["closed"] += 1
            continue
        if w["reminder_sent_at"] is None and now_utc >= pf.reminder_at(w["opened_at"], w["deadline"], w["reminder_hours"]):   # (1) reminder
            sent = []
            async with db.transaction() as conn:
                locked = await conn.fetchrow("select status, reminder_sent_at from public.preference_windows where id = $1 for update", wid)
                if locked is None or locked["status"] != "open" or locked["reminder_sent_at"] is not None:
                    continue
                await conn.execute("update public.preference_windows set reminder_sent_at = $2 where id = $1", wid, now_utc)
                everyone = await school_teachers(school_id)
                done = {r["teacher_id"] for r in await conn.fetch("select teacher_id from public.preference_responses where window_id = $1", wid)}
                todo = [t for t in everyone if t["id"] not in done]
                sent = await notify.create(conn, reminder_notices(wid, w["reopened_count"], school_id, w["deadline"], todo))
            await notify.deliver(sent)
            out["reminded"] += len(sent)
    return out
