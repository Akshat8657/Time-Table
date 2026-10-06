"""Fixed (locked) slots: PE and SOL as DOUBLE blocks, plus any other locked slot (plan Sections 8, 10).

Every request is checked against the real period grid: working days, the class's day-wise schedule (end time / OFF day),
overlaps (class + teachers) and the teachers' visiting days. An impossible slot is refused with a clear reason.
A double block is 2 consecutive PERIODS - the short break or even the LUNCH between them is fine (plan V8).
"""
from __future__ import annotations

import asyncio
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
from ..services import timing as timing_svc
from ..services.timing import DayRule
from ..validation import DAY_NAMES, DAY_SHORT, optional_text

router = APIRouter(prefix="/api/fixed-slots", tags=["fixed-slots"])
ROLES = ("admin", "hod")
KIND_LABEL = {"PE": "Physical Education (PE)", "SOL": "Science of Living (SOL)"}

SLOT_SQL = """
select f.id, f.school_id, f.term_id, f.semester_id, f.day, f.start_period, f.length, f.kind, f.label, f.subject_id,
       f.teacher_id, f.co_teacher_id, f.note, sm.number, c.name as course_name,
       p.full_name as teacher_name, p.is_visiting, p.visiting_days,
       cp.full_name as co_teacher_name, cp.is_visiting as co_is_visiting, cp.visiting_days as co_visiting_days,
       s.name as subject_name
from public.fixed_slots f
join public.semesters sm on sm.id = f.semester_id
join public.courses c on c.id = sm.course_id
left join public.profiles p on p.id = f.teacher_id
left join public.profiles cp on cp.id = f.co_teacher_id
left join public.subjects s on s.id = f.subject_id
"""


def _overlap(a_start: int, a_len: int, b_start: int, b_len: int) -> bool:
    return a_start < b_start + b_len and b_start < a_start + a_len


def _span(start: int, length: int) -> str:
    return f"period {start}" if length == 1 else f"periods {start}-{start + length - 1}"


async def _people(school_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, asyncpg.Record]:
    if not ids:
        return {}
    rows = await db.fetch(
        "select id, full_name, is_active, is_visiting, visiting_days from public.profiles "
        "where id = any($1::uuid[]) and school_id = $2 and role in ('teacher', 'hod')",
        ids, school_id,
    )
    return {r["id"]: r for r in rows}


async def _subject(subject_id: uuid.UUID | None, kind: str, sem: asyncpg.Record):
    """(subject, problem). A PE / SOL slot links the class's own PE / SOL subject automatically (when there is exactly one)."""
    if subject_id is not None:
        row = await db.fetchrow(
            "select id, name, semester_id, special_kind from public.subjects where id = $1 and school_id = $2", subject_id, sem["school_id"]
        )
        if row is None or row["semester_id"] != sem["id"]:
            return None, "The chosen subject does not belong to this class."
        return row, None
    if kind in ("PE", "SOL"):
        found = await db.fetch(
            "select id, name, semester_id, special_kind from public.subjects where semester_id = $1 and special_kind = $2", sem["id"], kind
        )
        if len(found) == 1:
            return found[0], None
    return None, None


async def evaluate(
    *,
    term: asyncpg.Record,
    sem: asyncpg.Record,
    day: int,
    start_period: int,
    length: int,
    kind: str,
    teacher_id: uuid.UUID | None,
    co_teacher_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    ignore_id: uuid.UUID | None = None,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Returns (problems, resolved, notes). `resolved` holds the subject / teachers after the automatic defaults."""
    (grid, college), rule_row, (subject, subject_problem) = await asyncio.gather(
        timing_svc.school_grid(sem["school_id"]),
        db.fetchrow("select is_off, end_time from public.semester_days where semester_id = $1 and day = $2", sem["id"], day),
        _subject(subject_id, kind, sem),
    )
    rule = DayRule(rule_row["is_off"], rule_row["end_time"]) if rule_row else None
    problems = timing_svc.slot_problems(grid, college["days_per_week"], rule, day, start_period, length)
    notes = timing_svc.slot_notes(grid, start_period, length) if not problems else []
    if subject_problem:
        problems.append(subject_problem)

    # no teacher given -> take the assigned teacher(s) of the subject
    if subject is not None and teacher_id is None:
        assigned = await db.fetchrow(
            "select teacher_id, co_teacher_id from public.assignments where term_id = $1 and subject_id = $2", term["id"], subject["id"]
        )
        if assigned is not None:
            teacher_id = assigned["teacher_id"]
            if co_teacher_id is None:
                co_teacher_id = assigned["co_teacher_id"]
    if co_teacher_id is not None and co_teacher_id == teacher_id:
        problems.append("The teacher and the co-teacher must be two different people.")
        co_teacher_id = None
    if co_teacher_id is not None and teacher_id is None:
        problems.append("Please choose the main teacher before the co-teacher.")

    wanted = [t for t in (teacher_id, co_teacher_id) if t is not None]
    people, can_teach, others = await asyncio.gather(
        _people(sem["school_id"], wanted),
        db.fetch(
            "select teacher_id from public.teacher_subjects where subject_id = $1 and teacher_id = any($2::uuid[])",
            subject["id"] if subject else None, wanted,
        ) if (subject and wanted) else _nothing(),
        db.fetch(
            SLOT_SQL + " where f.term_id = $1 and f.day = $2 and (f.semester_id = $3 or f.teacher_id = any($4::uuid[]) or f.co_teacher_id = any($4::uuid[]))",
            term["id"], day, sem["id"], wanted,
        ),
    )
    able = {r["teacher_id"] for r in can_teach}
    for tid in wanted:
        person = people.get(tid)
        if person is None:
            problems.append("The chosen teacher was not found in this school.")
            continue
        if not person["is_active"]:
            problems.append(f"{person['full_name']} is deactivated.")
        if person["is_visiting"] and day not in (person["visiting_days"] or []):
            allowed = ", ".join(DAY_NAMES[d] for d in sorted(person["visiting_days"] or [])) or "no days"
            problems.append(f"{person['full_name']} is visiting faculty and can only come on: {allowed}.")
        if subject is not None and tid not in able:
            problems.append(f"{person['full_name']} cannot teach '{subject['name']}'.")

    mine = set(wanted)
    for o in others:
        if (ignore_id is not None and o["id"] == ignore_id) or not _overlap(start_period, length, o["start_period"], o["length"]):
            continue
        if o["semester_id"] == sem["id"]:
            problems.append(f"This class already has '{o['label']}' at {_span(o['start_period'], o['length'])} on {DAY_NAMES[day]}.")
            continue
        busy = {o["teacher_id"], o["co_teacher_id"]} & mine
        for tid in busy:
            name = people[tid]["full_name"] if tid in people else "A teacher"
            problems.append(f"{name} is already in a locked slot ({o['course_name']} Sem {o['number']}, '{o['label']}') at that time.")
    return problems, {"teacher_id": teacher_id, "co_teacher_id": co_teacher_id, "subject_id": subject["id"] if subject else None}, notes


async def _nothing() -> list:
    return []


def slot_out(r: asyncpg.Record, grid, problems: list[str]) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "semester_id": str(r["semester_id"]),
        "class_label": term_svc.semester_label(r["course_name"], r["number"]),
        "day": r["day"],
        "day_name": DAY_NAMES[r["day"]],
        "start_period": r["start_period"],
        "length": r["length"],
        "kind": r["kind"],
        "label": r["label"],
        "subject_id": str(r["subject_id"]) if r["subject_id"] else None,
        "subject_name": r["subject_name"],
        "teacher_id": str(r["teacher_id"]) if r["teacher_id"] else None,
        "teacher_name": r["teacher_name"],
        "co_teacher_id": str(r["co_teacher_id"]) if r["co_teacher_id"] else None,
        "co_teacher_name": r["co_teacher_name"],
        "note": r["note"],
        "time": timing_svc.time_range(grid, r["start_period"], r["length"]),
        "notes": timing_svc.slot_notes(grid, r["start_period"], r["length"]),
        "problems": problems,
    }


async def problems_for_term(school_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Every locked slot of the active term with the reasons it is not possible today (also used by the timing screens)."""
    term = await term_svc.active_term()
    if term is None:
        return []
    sql = SLOT_SQL + " where f.term_id = $1" + (" and f.school_id = $2" if school_id else "")
    args: list[Any] = [term["id"]] + ([school_id] if school_id else [])
    rows, college, rule_rows = await asyncio.gather(
        db.fetch(sql, *args),
        timing_svc.college_row(),
        db.fetch("select semester_id, day, is_off, end_time from public.semester_days" + (" where school_id = $1" if school_id else ""),
                 *([school_id] if school_id else [])),
    )
    rules = timing_svc.rules_from_rows(rule_rows)
    grids: dict[Any, Any] = {}
    for sid in {r["school_id"] for r in rows}:
        ls, le = await timing_svc.school_lunch(sid)
        grids[sid] = timing_svc.make_grid(college, ls, le)
    out = []
    for r in rows:
        grid = grids[r["school_id"]]
        probs = timing_svc.slot_problems(grid, college["days_per_week"], rules.get((r["semester_id"], r["day"])), r["day"], r["start_period"], r["length"])
        for name, visiting, days in ((r["teacher_name"], r["is_visiting"], r["visiting_days"]), (r["co_teacher_name"], r["co_is_visiting"], r["co_visiting_days"])):
            if visiting and r["day"] not in (days or []):
                probs.append(f"{name} is visiting faculty and cannot come on {DAY_NAMES[r['day']]}.")
        out.append({"semester_id": str(r["semester_id"]), "id": str(r["id"]), "problems": probs, "row": r, "grid": grid})
    return out


# ---------------------------------------------------------------------- list
@router.get("")
async def list_slots(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term, college, lunch = await asyncio.gather(term_svc.active_term(), timing_svc.college_row(), timing_svc.school_lunch(sid))
    grid = timing_svc.make_grid(college, *lunch)
    if term is None:
        return {"active_term": None, "grid": grid.as_dict(), "days_per_week": college["days_per_week"], "classes": [], "slots": []}

    sems, found = await asyncio.gather(
        db.fetch(
            """
            select sm.id, sm.number, c.name as course_name
            from public.semesters sm join public.courses c on c.id = sm.course_id
            where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            order by c.name, sm.number
            """,
            sid, term["kind"],
        ),
        problems_for_term(sid),
    )
    rule_rows = await db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", sid)
    rules = timing_svc.rules_from_rows(rule_rows)
    slots = [slot_out(p["row"], grid, p["problems"]) for p in found]
    slots.sort(key=lambda s: (s["class_label"], s["day"], s["start_period"]))
    classes = []
    for sm in sems:
        mine = [s for s in slots if s["semester_id"] == str(sm["id"])]
        classes.append(
            {
                "semester_id": str(sm["id"]),
                "label": term_svc.semester_label(sm["course_name"], sm["number"]),
                "days": timing_svc.class_days(grid, college["days_per_week"], timing_svc.rules_of(rules, sm["id"])),
                "has_pe": any(s["kind"] == "PE" for s in mine),
                "has_sol": any(s["kind"] == "SOL" for s in mine),
            }
        )
    return {
        "active_term": term_svc.term_out(term),
        "grid": grid.as_dict(),
        "days_per_week": college["days_per_week"],
        "day_names": {str(k): v for k, v in DAY_SHORT.items()},
        "classes": classes,
        "slots": slots,
    }


# ---------------------------------------------------------------------- check / create / edit / delete
class SlotBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=6)
    start_period: int = Field(ge=1, le=30)
    length: int | None = Field(default=None, ge=1, le=8)
    kind: Literal["PE", "SOL", "OTHER"]
    label: optional_text(80) = None  # type: ignore[valid-type]
    subject_id: str | None = None
    teacher_id: str | None = None
    co_teacher_id: str | None = None
    note: optional_text(300) = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _ok(self):
        if self.kind == "OTHER" and not self.label:
            raise ValueError("Please give this locked slot a name (for example 'Seminar').")
        return self


class SlotIn(SlotBase):
    semester_id: str


def _length(kind: str, length: int | None) -> int:
    return 2 if kind in ("PE", "SOL") else (length or 1)  # PE and SOL are ALWAYS a double block


def _label(kind: str, label: str | None) -> str:
    return label or KIND_LABEL.get(kind, "Locked slot")


async def _context(user: CurrentUser, semester_id: str):
    sem = await load_in_scope("semesters", semester_id, user, what="class")
    term, course = await asyncio.gather(term_svc.require_active_term(), db.fetchval("select name from public.courses where id = $1", sem["course_id"]))
    term_svc.require_active_semester(term, sem["number"], term_svc.semester_label(course, sem["number"]))
    return sem, term, course


def _refuse(problems: list[str]) -> ApiError:
    return ApiError(422, "SLOT_NOT_POSSIBLE", "This slot is not possible: " + " ".join(problems), extra={"problems": problems})


def _opt(value: str | None, what: str):
    return as_uuid(value, what) if value else None


@router.post("/check")
async def check_slot(body: SlotIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem, term, _ = await _context(user, body.semester_id)
    length = _length(body.kind, body.length)
    problems, _, notes = await evaluate(
        term=term, sem=sem, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), co_teacher_id=_opt(body.co_teacher_id, "co-teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    return {"ok": not problems, "problems": problems, "notes": notes, "length": length}


@router.post("", status_code=201)
async def create_slot(body: SlotIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem, term, course = await _context(user, body.semester_id)
    length = _length(body.kind, body.length)
    problems, resolved, notes = await evaluate(
        term=term, sem=sem, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), co_teacher_id=_opt(body.co_teacher_id, "co-teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    if problems:
        raise _refuse(problems)
    label = _label(body.kind, body.label)
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            """
            insert into public.fixed_slots (school_id, term_id, semester_id, day, start_period, length, kind, label,
                                            subject_id, teacher_id, co_teacher_id, note, created_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) returning id
            """,
            sem["school_id"], term["id"], sem["id"], body.day, body.start_period, length, body.kind, label,
            resolved["subject_id"], resolved["teacher_id"], resolved["co_teacher_id"], body.note, uuid.UUID(user.id),
        )
        await audit.record(
            conn, user, "create", "fixed_slot", row["id"],
            f"Locked {label} for {term_svc.semester_label(course, sem['number'])}: {DAY_NAMES[body.day]} {_span(body.start_period, length)}",
            school_id=sem["school_id"],
        )
    return {"id": str(row["id"]), "length": length, "notes": notes}


class SlotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int | None = Field(default=None, ge=1, le=6)
    start_period: int | None = Field(default=None, ge=1, le=30)
    length: int | None = Field(default=None, ge=1, le=8)
    label: optional_text(80) = None  # type: ignore[valid-type]
    subject_id: str | None = None
    teacher_id: str | None = None
    co_teacher_id: str | None = None
    note: optional_text(300) = None  # type: ignore[valid-type]


@router.patch("/{slot_id}")
async def update_slot(slot_id: str, body: SlotPatch, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("fixed_slots", slot_id, user, what="locked slot")
    sem, term, course = await _context(user, str(old["semester_id"]))
    if old["term_id"] != term["id"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "Locked slots of an old term are kept as history and cannot be changed.")
    fields = body.model_fields_set
    day = body.day or old["day"]
    start = body.start_period or old["start_period"]
    length = _length(old["kind"], body.length if body.length else old["length"])
    label = body.label if "label" in fields and body.label else old["label"]
    teacher = _opt(body.teacher_id, "teacher") if "teacher_id" in fields else old["teacher_id"]
    co_teacher = _opt(body.co_teacher_id, "co-teacher") if "co_teacher_id" in fields else old["co_teacher_id"]
    subject = _opt(body.subject_id, "subject") if "subject_id" in fields else old["subject_id"]
    note = body.note if "note" in fields else old["note"]
    problems, resolved, notes = await evaluate(
        term=term, sem=sem, day=day, start_period=start, length=length, kind=old["kind"],
        teacher_id=teacher, co_teacher_id=co_teacher, subject_id=subject, ignore_id=old["id"],
    )
    if problems:
        raise _refuse(problems)
    async with db.transaction() as conn:
        await conn.execute(
            """
            update public.fixed_slots set day = $2, start_period = $3, length = $4, label = $5,
                   subject_id = $6, teacher_id = $7, co_teacher_id = $8, note = $9 where id = $1
            """,
            old["id"], day, start, length, label, resolved["subject_id"], resolved["teacher_id"], resolved["co_teacher_id"], note,
        )
        await audit.record(
            conn, user, "update", "fixed_slot", old["id"],
            f"Changed locked slot '{label}' of {term_svc.semester_label(course, sem['number'])}: {DAY_NAMES[day]} {_span(start, length)}",
            school_id=old["school_id"],
        )
    return {"id": str(old["id"]), "notes": notes}


@router.delete("/{slot_id}")
async def delete_slot(slot_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("fixed_slots", slot_id, user, what="locked slot")
    async with db.transaction() as conn:
        await conn.execute("delete from public.fixed_slots where id = $1", old["id"])
        await audit.record(
            conn, user, "delete", "fixed_slot", old["id"],
            f"Removed locked slot '{old['label']}' ({DAY_NAMES[old['day']]} {_span(old['start_period'], old['length'])})",
            school_id=old["school_id"],
        )
    return {"deleted": True}
