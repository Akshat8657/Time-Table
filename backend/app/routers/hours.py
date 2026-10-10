"""Semester-hours tracking and holiday compensation (plan Section 12).

  GET    /api/hours/subjects     HOD / admin: every subject of the school - required vs deliverable vs delivered hours, "short by X"
  GET    /api/hours/my           teacher / HOD-as-teacher: the same for the subjects I teach
  POST   /api/hours/suggest      clash-free EXTRA slots (dates + periods) that would cover a subject's shortfall
  POST   /api/hours/extra        the HOD approves chosen slots -> one-off extra lectures + a notification to the teacher(s)
  GET    /api/hours/extra        the extra lectures of the school
  DELETE /api/hours/extra/{id}   cancel a future extra lecture (the teacher is told)

Everything is computed live from the PUBLISHED timetable, the holidays / events and the semester dates (services/hours.py).
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db, readcache
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import hours as hr
from ..services import notify
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services import views as vw
from ..services.viewdata import ViewData, load_view_data
from ..validation import DAY_SHORT, ISODate, optional_text

router = APIRouter(prefix="/api/hours", tags=["hours"])
MANAGERS = ("admin", "hod")
TEACHING = ("teacher", "hod")
MAX_SLOTS = 30


async def _assignments(school_id: uuid.UUID, term_id: uuid.UUID) -> dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]]:
    async def load() -> dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]]:
        rows = await db.fetch("select subject_id, teacher_id, co_teacher_id from public.assignments where school_id = $1 and term_id = $2", school_id, term_id)
        return {r["subject_id"]: (r["teacher_id"], r["co_teacher_id"]) for r in rows}

    return await readcache.cached(("hours.assignments", school_id, term_id), load)


async def _data(school_id: uuid.UUID, *, with_prefs: bool = False) -> ViewData | None:
    term = await term_svc.active_term()
    return None if term is None else await load_view_data(school_id, term, with_prefs=with_prefs)


async def _cancelled(school_id: uuid.UUID, term_id: uuid.UUID) -> set:
    """(semester_id, date, start_period) slots cancelled by a one-day adjustment - counted as lost hours (Phase 5)."""
    rows = await db.fetch(
        "select semester_ids, lecture_date, orig_start from public.day_adjustments where school_id = $1 and term_id = $2 and kind = 'cancel'",
        school_id, term_id,
    )
    return {(sem, r["lecture_date"], r["orig_start"]) for r in rows for sem in (r["semester_ids"] or [])}


async def _all_hours(school_id: uuid.UUID, data: ViewData, today: date) -> dict[uuid.UUID, list[hr.SubjectHours]]:
    """The hours of EVERY subject of the school (read-only, shared): the dashboards and every teacher's "my hours" use the
    same numbers, so they are worked out once until anything changes (see readcache.py)."""
    async def load() -> dict[uuid.UUID, list[hr.SubjectHours]]:
        return hr.compute_hours(data, today, cancelled=await _cancelled(school_id, data.term["id"]))

    return await readcache.cached(("hours.all", school_id, data.term["id"], today), load)


def _dates_out(data: ViewData, sem_id: uuid.UUID) -> dict[str, str] | None:
    d = data.term_dates.get(sem_id)
    return None if d is None else {"start": d[0].isoformat(), "end": d[1].isoformat()}


async def school_summary(school_id: uuid.UUID) -> dict[str, Any] | None:
    """For the dashboards: how many subjects are short and by how many hours."""
    data = await _data(school_id)
    if data is None:
        return None
    return hr.summarize(await _all_hours(school_id, data, vw.today_ist()))


# ---------------------------------------------------------------------------------------------------- tracking
@router.get("/summary")
async def summary(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    """Small and quick: the numbers for the dashboard alert. HOD = own school; admin = one school (school_id) or every active school."""
    if user.role == "admin" and school_id is None:
        schools = await db.fetch("select id, name from public.schools where is_active order by name")
        return {"schools": [{"school_id": str(r["id"]), "school_name": r["name"], "summary": await school_summary(r["id"])} for r in schools]}
    sid = await resolve_school(user, school_id)
    name = await db.fetchval("select name from public.schools where id = $1", sid)
    return {"schools": [{"school_id": str(sid), "school_name": name, "summary": await school_summary(sid)}]}


@router.get("/subjects")
async def subjects(school_id: str | None = None, semester_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    data = await _data(sid)
    if data is None:
        return {"term": None, "classes": [], "summary": {}}
    only = {as_uuid(semester_id, "class")} if semester_id else None
    if only and not only <= set(data.sems):
        raise ApiError(404, "NOT_FOUND", "That class is not running in the active term.")
    today = vw.today_ist()
    per_class = hr.compute_hours(data, today, only, await _assignments(sid, data.term["id"]), cancelled=await _cancelled(sid, data.term["id"]))
    classes = []
    for sem_id, rows in per_class.items():
        classes.append({
            "semester_id": str(sem_id), "class_label": data.sems[sem_id].label, "published": sem_id in data.pubs,
            "dates": _dates_out(data, sem_id), "short_hours": sum(h.shortfall for h in rows),
            "short_subjects": sum(1 for h in rows if h.shortfall > 0), "subjects": [h.as_dict() for h in rows],
        })
    return {
        "term": term_svc.term_out(data.term), "today": today.isoformat(), "classes": classes, "summary": hr.summarize(per_class),
    }


@router.get("/my")
async def my_hours(user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    sid = await resolve_school(user, None)
    data = await _data(sid)
    if data is None:
        return {"term": None, "subjects": [], "summary": {}}
    me = uuid.UUID(user.id)
    assigned = await _assignments(sid, data.term["id"])
    mine_assigned = {s for s, (t, co) in assigned.items() if me in (t, co)}
    today = vw.today_ist()
    per_class = await _all_hours(sid, data, today)
    rows = [h for hs in per_class.values() for h in hs if any(t == me for t, _n in h.teachers) or h.subject.id in mine_assigned]
    return {
        "term": term_svc.term_out(data.term), "today": today.isoformat(),
        "subjects": [{**h.as_dict(), "dates": _dates_out(data, h.subject.semester_id)} for h in sorted(rows, key=lambda h: (h.class_label, h.subject.name.lower()))],
        "summary": hr.summarize({h.subject.semester_id: [x for x in rows if x.subject.semester_id == h.subject.semester_id] for h in rows}),
    }


# ---------------------------------------------------------------------------------------------------- compensation
class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    count: int | None = Field(default=None, ge=1, le=MAX_SLOTS)


async def _plan(user: CurrentUser, subject_id: str):
    subj = await load_in_scope("subjects", subject_id, user, what="subject")
    data = await _data(subj["school_id"], with_prefs=True)   # Phase 6: a teacher's not-available slots rule a compensation slot out
    if data is None:
        raise ApiError(409, "NO_ACTIVE_TERM", "No active term is selected yet.")
    if subj["id"] not in data.subjects or subj["semester_id"] not in data.sems:
        raise ApiError(409, "SEMESTER_NOT_ACTIVE", "That subject's class is not running in the active term.")
    if subj["semester_id"] not in data.pubs:
        raise ApiError(409, "NOT_PUBLISHED", "Publish this class's timetable first - extra lectures are planned around the published timetable.")
    if subj["semester_id"] not in data.term_dates:
        raise ApiError(409, "NO_DATES", "Set the semester start and end dates on the Active Term screen first.")
    assigned = await _assignments(subj["school_id"], data.term["id"])
    teacher_map = hr.teachers_of_subjects(data, assigned)
    planner = hr.Planner(data, hr.unit_of(data, subj["id"]), teacher_map)
    return subj, data, planner, teacher_map


@router.post("/suggest")
async def suggest(body: SuggestIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    subj, data, planner, _ = await _plan(user, body.subject_id)
    today = vw.today_ist()
    per_class = hr.compute_hours(data, today, {subj["semester_id"]}, cancelled=await _cancelled(subj["school_id"], data.term['id']))
    mine = next(h for h in per_class[subj["semester_id"]] if h.subject.id == subj["id"])
    needed = body.count or mine.as_dict()["extra_lectures_needed"]
    if needed == 0:
        return {"subject": mine.as_dict(), "needed": 0, "suggestions": [], "unfilled": 0, "message": "This subject is not short - no extra lectures are needed."}
    chosen = planner.suggest(min(needed, MAX_SLOTS), today)
    unfilled = max(0, min(needed, MAX_SLOTS) - len(chosen))
    why = hr.why_not_more(planner) if unfilled else ""
    msg = (
        f"Found {len(chosen)} clash-free extra slot(s)." + (f" {unfilled} more could not be placed without a clash. {why}" if unfilled else "")
        if chosen else f"No clash-free extra slot exists before the semester ends. {why}"
    ).strip()
    return {
        "subject": mine.as_dict(), "needed": needed, "suggestions": [hr.slot_out(data, planner, s) for s in chosen],
        "unfilled": unfilled, "message": msg,
        "group": [{"id": str(s.id), "name": s.name} for s in planner.subs] if len(planner.subs) > 1 else None,
    }


class SlotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: ISODate
    start_period: int = Field(ge=1, le=30)


class ExtraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    slots: list[SlotIn] = Field(min_length=1, max_length=MAX_SLOTS)
    reason: optional_text(300) = None  # type: ignore[valid-type]


def _when(data: ViewData, d: date, start: int, length: int) -> str:
    return f"{DAY_SHORT[d.isoweekday()]} {d.strftime('%d %b')}, period {start}{'' if length == 1 else f'-{start + length - 1}'} ({timing_svc.time_range(data.grid, start, length)})"


@router.post("/extra", status_code=201)
async def add_extra(body: ExtraIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    subj = await load_in_scope("subjects", body.subject_id, user, what="subject")
    batch = uuid.uuid4()
    async with db.transaction() as conn:
        # approvals of one school are served one after the other, so two people can never take the same teacher slot
        await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"extra:{subj['school_id']}")
        _s, data, planner, teacher_map = await _plan(user, body.subject_id)
        problems: list[str] = []
        accepted: list[hr.Slot] = []
        for slot in sorted(body.slots, key=lambda s: (s.date, s.start_period)):
            found = planner.problems(slot.date, slot.start_period)
            if found:
                problems.append(f"{slot.date.strftime('%a %d %b')} period {slot.start_period}: " + " ".join(found))
                continue
            s = hr.Slot(slot.date, slot.start_period, planner.length)
            planner.add(s)
            accepted.append(s)
        if problems:
            raise ApiError(
                422, "EXTRA_BLOCKED", "Some slots are not possible any more: " + " | ".join(problems)[:700],
                extra={"problems": problems},
            )
        unit = hr.unit_of(data, subj["id"])
        created: list[dict[str, Any]] = []
        for s in accepted:
            pk = uuid.uuid4() if len(unit) > 1 else None
            for sub_id in unit:
                teachers = teacher_map.get(sub_id, ())
                t, co = (teachers[0] if teachers else None), (teachers[1] if len(teachers) > 1 else None)
                row = await conn.fetchrow(
                    """
                    insert into public.extra_lectures (school_id, term_id, semester_id, subject_id, lecture_date, start_period, length,
                                                       parallel_key, teacher_id, co_teacher_id, reason, created_by)
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) returning id
                    """,
                    subj["school_id"], data.term["id"], subj["semester_id"], sub_id, s.day_date, s.start, s.length, pk, t, co,
                    body.reason, uuid.UUID(user.id),
                )
                created.append({"id": str(row["id"]), "subject_id": str(sub_id), "date": s.day_date.isoformat(), "start_period": s.start})
        label = data.sems[subj["semester_id"]].label
        await audit.record(
            conn, user, "create", "extra_lecture", subj["id"],
            f"Approved {len(accepted)} extra lecture(s) of {subj['name']} for {label}", school_id=subj["school_id"],
            details={"slots": [f"{s.day_date.isoformat()} P{s.start}" for s in accepted], "reason": body.reason},
        )
        # one notification per teacher for the whole approval
        per_teacher: dict[uuid.UUID, list[hr.Slot]] = {}
        for s in accepted:
            for sub_id in unit:
                for t in teacher_map.get(sub_id, ()):
                    per_teacher.setdefault(t, [])
                    if s not in per_teacher[t]:
                        per_teacher[t].append(s)
        names = " / ".join(data.subjects[x].name for x in unit)
        notices = []
        for t, slots in per_teacher.items():
            listing = "; ".join(_when(data, s.day_date, s.start, s.length) for s in slots[:3]) + (f" and {len(slots) - 3} more" if len(slots) > 3 else "")
            why = f" Reason: {body.reason}" if body.reason else ""
            notices.append(notify.Notice(
                t, "extra_lecture", "Extra lecture added" if len(slots) == 1 else f"{len(slots)} extra lectures added",
                f"{names} ({label}): {listing}.{why}", "/my/timetable",
                {"batch": str(batch), "subject": names, "class": label, "slots": [_when(data, s.day_date, s.start, s.length) for s in slots]},
                subj["school_id"], f"extra:{batch}",
            ))
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"created": created, "lectures": len(accepted), "notified": len(sent), "message": f"{len(accepted)} extra lecture(s) added. {len(sent)} teacher(s) notified."}


@router.get("/extra")
async def list_extra(
    school_id: str | None = None, semester_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))
) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"extra": []}
    args: list[Any] = [sid, term["id"]]
    sql = (
        "select x.id, x.semester_id, x.subject_id, x.lecture_date, x.start_period, x.length, x.parallel_key, x.reason, x.teacher_id, "
        "s.name as subject_name, s.code, c.name as course_name, sm.number, t.full_name as teacher_name "
        "from public.extra_lectures x join public.subjects s on s.id = x.subject_id join public.semesters sm on sm.id = x.semester_id "
        "join public.courses c on c.id = sm.course_id left join public.profiles t on t.id = x.teacher_id "
        "where x.school_id = $1 and x.term_id = $2"
    )
    if semester_id:
        args.append(as_uuid(semester_id, "class"))
        sql += f" and x.semester_id = ${len(args)}"
    rows = await db.fetch(sql + " order by x.lecture_date desc, x.start_period limit 300", *args)
    grid, _college = await timing_svc.school_grid(sid)
    today = vw.today_ist()
    merged: dict[Any, dict[str, Any]] = {}
    for r in rows:
        key = r["parallel_key"] or r["id"]
        if key in merged:
            merged[key]["subjects"].append(r["subject_name"])
            continue
        merged[key] = {
            "id": str(r["id"]), "semester_id": str(r["semester_id"]), "class_label": term_svc.semester_label(r["course_name"], r["number"]),
            "subjects": [r["subject_name"]], "date": r["lecture_date"].isoformat(), "start_period": r["start_period"], "length": r["length"],
            "time": timing_svc.time_range(grid, r["start_period"], r["length"]), "reason": r["reason"], "teacher_name": r["teacher_name"],
            "can_cancel": r["lecture_date"] > today,
        }
    return {"extra": list(merged.values())}


@router.delete("/extra/{extra_id}")
async def cancel_extra(extra_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row = await db.fetchrow("select * from public.extra_lectures where id = $1", as_uuid(extra_id, "extra lecture"))
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That extra lecture was not found.")
    if row["lecture_date"] <= vw.today_ist():
        raise ApiError(409, "ALREADY_HAPPENED", "Only a future extra lecture can be cancelled.")
    key = row["parallel_key"] or row["id"]
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "select e.*, s.name as subject_name from public.extra_lectures e join public.subjects s on s.id = e.subject_id "
            "where e.school_id = $1 and (e.id = $2 or e.parallel_key = $3)",
            row["school_id"], row["id"], key,
        )
        await conn.execute("delete from public.extra_lectures where id = any($1::uuid[])", [r["id"] for r in rows])
        grid, _c = await timing_svc.school_grid(row["school_id"])
        names = " / ".join(r["subject_name"] for r in rows)
        when = f"{DAY_SHORT[row['lecture_date'].isoweekday()]} {row['lecture_date'].strftime('%d %b')}, period {row['start_period']} ({timing_svc.time_range(grid, row['start_period'], row['length'])})"
        await audit.record(
            conn, user, "delete", "extra_lecture", row["id"], f"Cancelled the extra lecture of {names} on {row['lecture_date'].isoformat()}",
            school_id=row["school_id"],
        )
        people = {t for r in rows for t in (r["teacher_id"], r["co_teacher_id"]) if t is not None}
        sent = await notify.create(conn, [
            notify.Notice(t, "extra_lecture_cancelled", "Extra lecture cancelled", f"{names}: {when} was cancelled.", "/my/timetable",
                          {"extra": str(row["id"])}, row["school_id"], f"extra-cancel:{row['id']}")
            for t in people
        ])
    await notify.deliver(sent)
    return {"cancelled": True, "notified": len(sent)}
