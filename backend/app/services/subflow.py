"""Shared substitution flow: turn an absence into substitution needs, build the notices, and the timeout sweep.

Used by the absence, leave and substitution routers so the same rules live in one place.  The notification SENDER is the
Phase 4 service (`notify`); here we only decide WHO is told WHAT, and we never spam (only affected people).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Iterable

from .. import audit, db
from . import notify
from . import substitution as subsvc
from . import terms as term_svc
from . import timing as timing_svc
from . import views as vw
from .changes import DayChanges, load_changes
from .substitution import AffectedSlot
from .viewdata import ViewData, load_view_data

TEACHER_OFFER_LINK = "/my/offers"
TEACHER_ABSENCE_LINK = "/my/absent-leave"
CONSOLE_LINK = "/substitutions"


# ---------------------------------------------------------------------------------------------------- recipients
async def managers_of(school_id: uuid.UUID, exclude: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Who manages absences for this school: every admin + the school's HOD(s), minus `exclude` (so a HOD's own
    absence goes to the admins, not back to themselves)."""
    rows = await db.fetch(
        "select id from public.profiles where is_active and (role = 'admin' or (role = 'hod' and school_id = $1))",
        school_id,
    )
    return [r["id"] for r in rows if r["id"] != exclude]


# ---------------------------------------------------------------------------------------------------- create the needs
def _when(data: ViewData, slot: AffectedSlot) -> str:
    classes = ", ".join(sorted(data.sems[s].label for s in slot.semester_ids if s in data.sems))
    span = f"period {slot.start}" + (f"-{slot.start + slot.length - 1}" if slot.length > 1 else "")
    return f"{slot.label} ({classes}, {span} {timing_svc.time_range(data.grid, slot.start, slot.length)})"


async def make_subs(
    conn, data: ViewData, change: DayChanges, *, absence_id: uuid.UUID, teacher_id: uuid.UUID, d: date,
    school_id: uuid.UUID, term_id: uuid.UUID, created_by: uuid.UUID | None,
) -> list[dict]:
    """Insert an OPEN substitution for every lecture the absent teacher had that day that nobody present already covers.
    Returns a small summary per created / skipped slot (for the UI). Idempotent: a slot that already has a sub is skipped."""
    existing = {
        r["start_period"] for r in await conn.fetch(
            "select start_period from public.substitutions where absence_id = $1", absence_id
        )
    }
    out: list[dict] = []
    for slot in subsvc.affected_lectures(data, teacher_id, d, change):
        item = {
            "start_period": slot.start, "length": slot.length, "label": slot.label,
            "classes": [data.sems[s].label for s in slot.semester_ids if s in data.sems],
            "time": timing_svc.time_range(data.grid, slot.start, slot.length),
        }
        if slot.covered_by_colleague:
            item["status"] = "covered"
            item["note"] = "A co-teacher is present - no substitute needed."
            out.append(item)
            continue
        if slot.start in existing:
            item["status"] = "exists"
            out.append(item)
            continue
        row = await conn.fetchrow(
            """
            insert into public.substitutions
              (school_id, term_id, absence_id, lecture_date, weekday, start_period, length, semester_ids, subject_ids,
               parallel_key, label, absent_teacher_id, created_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8::uuid[], $9::uuid[], $10, $11, $12, $13)
            returning id
            """,
            school_id, term_id, absence_id, d, d.isoweekday(), slot.start, slot.length,
            list(slot.semester_ids), list(slot.subject_ids), slot.parallel_key, slot.label[:200], teacher_id, created_by,
        )
        item["status"], item["id"] = "open", str(row["id"])
        out.append(item)
    return out


# ---------------------------------------------------------------------------------------------------- notices
def absence_notices(
    data: ViewData, *, teacher_id: uuid.UUID, d: date, slots: list[dict], managers: Iterable[uuid.UUID],
    school_id: uuid.UUID, source: str, by_self: bool,
) -> list[notify.Notice]:
    name = data.person_name(teacher_id) or "A teacher"
    nice = d.strftime("%a %d %b %Y")
    need = [s for s in slots if s["status"] == "open"]
    covered = [s for s in slots if s["status"] == "covered"]
    body = (
        f"{name} is absent on {nice}. "
        + (f"{len(need)} lecture(s) need a substitute." if need else "No lecture needs a substitute.")
        + (f" {len(covered)} already covered by a co-teacher." if covered else "")
    )
    out = [
        notify.Notice(m, "absence_marked", f"{name} absent - {nice}", body, CONSOLE_LINK, {"teacher_id": str(teacher_id), "date": d.isoformat(), "need": len(need)}, school_id, f"absence:{teacher_id}:{d}:{m}")
        for m in managers
    ]
    if by_self:
        out.append(notify.Notice(
            teacher_id, "absence_confirmed", f"You are marked absent - {nice}",
            f"{len(need)} of your lecture(s) need a substitute." if need else "None of your lectures need a substitute that day.",
            TEACHER_ABSENCE_LINK, {"date": d.isoformat()}, school_id, f"absence-self:{teacher_id}:{d}",
        ))
    return out


def filled_notices(data: ViewData, sub: dict, *, winner: uuid.UUID, losers: Iterable[uuid.UUID], managers: Iterable[uuid.UUID], school_id: uuid.UUID, via: str) -> list[notify.Notice]:
    """When a substitution is filled: tell the substitute, the absent teacher, the HOD, and (for an auto round) the teachers who lost the race."""
    line = _sub_line(data, sub)
    sub_name = data.person_name(winner) or "A teacher"
    out = [
        notify.Notice(winner, "sub_assigned", "You are covering a lecture", f"Please take: {line}.", TEACHER_OFFER_LINK, {"substitution_id": str(sub["id"])}, school_id, f"sub-win:{sub['id']}:{winner}"),
        notify.Notice(sub["absent_teacher_id"], "sub_filled", "Your lecture is covered", f"{sub_name} will take {line}.", TEACHER_ABSENCE_LINK, {"substitution_id": str(sub["id"])}, school_id, f"sub-filled:{sub['id']}:{sub['absent_teacher_id']}"),
    ]
    for m in managers:
        out.append(notify.Notice(m, "sub_filled", "Substitution filled", f"{sub_name} will take {line}.", CONSOLE_LINK, {"substitution_id": str(sub["id"])}, school_id, f"sub-filled:{sub['id']}:{m}"))
    for t in losers:
        out.append(notify.Notice(t, "offer_closed", "Offer already filled", f"The lecture {line} was taken by someone else.", TEACHER_OFFER_LINK, {"substitution_id": str(sub["id"])}, school_id, f"offer-closed:{sub['id']}:{t}"))
    return out


def offer_notices(data: ViewData, sub: dict, candidates: Iterable[uuid.UUID], *, school_id: uuid.UUID, deadline: datetime) -> list[notify.Notice]:
    line = _sub_line(data, sub)
    absent = data.person_name(sub["absent_teacher_id"]) or "a colleague"
    return [
        notify.Notice(
            t, "sub_offer", "Can you cover a lecture?", f"{line} - for {absent}. First to accept gets it.",
            TEACHER_OFFER_LINK,
            {"substitution_id": str(sub["id"]), "offer": True, "deadline": deadline.isoformat()},
            school_id, f"offer:{sub['id']}:{t}",
        )
        for t in candidates
    ]


def _sub_line(data: ViewData, sub: dict) -> str:
    classes = ", ".join(sorted(data.sems[s].label for s in sub.get("semester_ids", ()) if s in data.sems)) or "a class"
    span = f"period {sub['start_period']}" + (f"-{sub['start_period'] + sub['length'] - 1}" if sub["length"] > 1 else "")
    when = f"{sub['lecture_date'].strftime('%a %d %b')}, {span} ({timing_svc.time_range(data.grid, sub['start_period'], sub['length'])})"
    return f"{sub['label']} - {classes}, {when}"


# ---------------------------------------------------------------------------------------------------- timeout sweep
async def sweep(now_utc: datetime) -> int:
    """Expire auto-offer rounds whose deadline passed (or whose lecture already started), mark those substitutions
    'unfilled', and alert the HOD to assign manually. Runs from the server's background loop. Returns subs closed."""
    rounds = await db.fetch(
        """
        select distinct s.id, s.school_id, s.term_id, s.lecture_date, s.start_period, s.length, s.label,
               s.semester_ids, s.absent_teacher_id
        from public.substitutions s
        where s.status = 'offering' and s.offer_deadline is not null and s.offer_deadline <= $1
        """,
        now_utc,
    )
    if not rounds:
        return 0
    closed = 0
    by_school: dict[uuid.UUID, list[dict]] = {}
    async with db.transaction() as conn:
        for r in rounds:
            locked = await conn.fetchrow("select status from public.substitutions where id = $1 for update", r["id"])
            if locked is None or locked["status"] != "offering":
                continue
            await conn.execute(
                "update public.substitution_offers set status = 'expired', responded_at = now() "
                "where substitution_id = $1 and status = 'pending'",
                r["id"],
            )
            await conn.execute("update public.substitutions set status = 'unfilled', offer_deadline = null where id = $1", r["id"])
            by_school.setdefault(r["school_id"], []).append(dict(r))
            closed += 1
    sent: list[notify.Sent] = []
    for school_id, subs in by_school.items():
        managers = await managers_of(school_id)
        if not managers:
            continue
        # a light ViewData just for labels
        term = await db.fetchrow("select * from public.academic_terms where id = $1", subs[0]["term_id"])
        data = await load_view_data(school_id, term)
        notices: list[notify.Notice] = []
        for s in subs:
            s["semester_ids"] = set(s["semester_ids"] or [])
            line = _sub_line(data, s)
            for m in managers:
                notices.append(notify.Notice(
                    m, "sub_unfilled", "Nobody accepted - assign a substitute", f"No teacher accepted {line}. Please assign one.",
                    CONSOLE_LINK, {"substitution_id": str(s["id"])}, school_id, f"sub-unfilled:{s['id']}:{m}",
                ))
        async with db.transaction() as conn:
            sent += await notify.create(conn, notices)
    await notify.deliver(sent)
    return closed
