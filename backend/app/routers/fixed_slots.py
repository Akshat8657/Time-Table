"""Fixed (locked) slots: PE and SOL as DOUBLE blocks, plus any other locked slot (plan Sections 8, 10).

Every request is checked against the real period grid: working days, lunch, the class's early-release time,
overlaps (class + teacher) and the teacher's visiting days. An impossible slot is refused with a clear reason.
"""
from __future__ import annotations

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
from ..validation import DAY_NAMES, DAY_SHORT, hm, optional_text, text

router = APIRouter(prefix="/api/fixed-slots", tags=["fixed-slots"])
ROLES = ("admin", "hod")
KIND_LABEL = {"PE": "Physical Education (PE)", "SOL": "Science of Living (SOL)"}

SLOT_SQL = """
select f.id, f.school_id, f.term_id, f.semester_id, f.day, f.start_period, f.length, f.kind, f.label, f.subject_id,
       f.teacher_id, f.note, sm.number, sm.early_release_time, c.name as course_name,
       p.full_name as teacher_name, p.is_visiting, p.visiting_days, s.name as subject_name
from public.fixed_slots f
join public.semesters sm on sm.id = f.semester_id
join public.courses c on c.id = sm.course_id
left join public.profiles p on p.id = f.teacher_id
left join public.subjects s on s.id = f.subject_id
"""


def _overlap(a_start: int, a_len: int, b_start: int, b_len: int) -> bool:
    return a_start < b_start + b_len and b_start < a_start + a_len


async def evaluate(
    *,
    term: asyncpg.Record,
    sem: asyncpg.Record,
    day: int,
    start_period: int,
    length: int,
    kind: str,
    teacher_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    ignore_id: uuid.UUID | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Returns (problems, resolved) - resolved holds the subject / teacher after the automatic defaults."""
    grid, college = await timing_svc.school_grid(sem["school_id"])
    problems = timing_svc.slot_problems(grid, college["days_per_week"], sem["early_release_time"], day, start_period, length)

    subject = None
    if subject_id is not None:
        subject = await db.fetchrow("select id, name, semester_id, special_kind from public.subjects where id = $1 and school_id = $2", subject_id, sem["school_id"])
        if subject is None or subject["semester_id"] != sem["id"]:
            problems.append("The chosen subject does not belong to this class.")
            subject = None
    elif kind in ("PE", "SOL"):  # link the class's own PE / SOL subject automatically (when there is exactly one)
        found = await db.fetch("select id, name, semester_id, special_kind from public.subjects where semester_id = $1 and special_kind = $2", sem["id"], kind)
        if len(found) == 1:
            subject = found[0]
    if subject is not None and teacher_id is None:  # default the teacher from the assignment
        teacher_id = await db.fetchval("select teacher_id from public.assignments where term_id = $1 and subject_id = $2", term["id"], subject["id"])

    teacher = None
    if teacher_id is not None:
        teacher = await db.fetchrow(
            "select id, full_name, is_active, is_visiting, visiting_days from public.profiles where id = $1 and school_id = $2 and role in ('teacher', 'hod')",
            teacher_id, sem["school_id"],
        )
        if teacher is None:
            problems.append("The chosen teacher was not found in this school.")
        else:
            if not teacher["is_active"]:
                problems.append(f"{teacher['full_name']} is deactivated.")
            if teacher["is_visiting"] and day not in (teacher["visiting_days"] or []):
                allowed = ", ".join(DAY_NAMES[d] for d in sorted(teacher["visiting_days"] or [])) or "no days"
                problems.append(f"{teacher['full_name']} is visiting faculty and can only come on: {allowed}.")
            if subject is not None:
                can = await db.fetchval(
                    "select exists(select 1 from public.teacher_subjects where teacher_id = $1 and subject_id = $2)", teacher["id"], subject["id"]
                )
                if not can:
                    problems.append(f"{teacher['full_name']} cannot teach '{subject['name']}'.")

    # overlaps with other locked slots (same class / same teacher), same day and term
    others = await db.fetch(
        SLOT_SQL + " where f.term_id = $1 and f.day = $2 and (f.semester_id = $3 or ($4::uuid is not null and f.teacher_id = $4))",
        term["id"], day, sem["id"], teacher_id,
    )
    for o in others:
        if ignore_id is not None and o["id"] == ignore_id:
            continue
        if not _overlap(start_period, length, o["start_period"], o["length"]):
            continue
        if o["semester_id"] == sem["id"]:
            problems.append(f"This class already has '{o['label']}' at period {o['start_period']}"
                            + (f"-{o['start_period'] + o['length'] - 1}" if o["length"] > 1 else "") + f" on {DAY_NAMES[day]}.")
        else:
            problems.append(f"{o['teacher_name']} is already in a locked slot ({o['course_name']} Sem {o['number']}, '{o['label']}') at that time.")
    return problems, {"teacher_id": teacher_id, "subject_id": subject["id"] if subject else None}


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
        "note": r["note"],
        "time": timing_svc.time_range(grid, r["start_period"], r["length"]),
        "problems": problems,
    }


async def problems_for_term(school_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Every locked slot of the active term with the reasons it is not possible today (used by the timing screens too)."""
    term = await term_svc.active_term()
    if term is None:
        return []
    sql = SLOT_SQL + " where f.term_id = $1" + (" and f.school_id = $2" if school_id else "")
    rows = await db.fetch(sql, term["id"], *([school_id] if school_id else []))
    college = await timing_svc.college_row()
    grids: dict[Any, Any] = {}
    out = []
    for r in rows:
        if r["school_id"] not in grids:
            ls, le = await timing_svc.school_lunch(r["school_id"])
            grids[r["school_id"]] = timing_svc.make_grid(college, ls, le)
        grid = grids[r["school_id"]]
        probs = timing_svc.slot_problems(grid, college["days_per_week"], r["early_release_time"], r["day"], r["start_period"], r["length"])
        if r["is_visiting"] and r["day"] not in (r["visiting_days"] or []):
            probs.append(f"{r['teacher_name']} is visiting faculty and cannot come on {DAY_NAMES[r['day']]}.")
        out.append({"semester_id": str(r["semester_id"]), "id": str(r["id"]), "problems": probs, "row": r, "grid": grid})
    return out


# ---------------------------------------------------------------------- list
@router.get("")
async def list_slots(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    college = await timing_svc.college_row()
    ls, le = await timing_svc.school_lunch(sid)
    grid = timing_svc.make_grid(college, ls, le)
    if term is None:
        return {"active_term": None, "grid": grid.as_dict(), "days_per_week": college["days_per_week"], "classes": [], "slots": []}

    sems = await db.fetch(
        """
        select sm.id, sm.number, sm.early_release_time, c.name as course_name
        from public.semesters sm join public.courses c on c.id = sm.course_id
        where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
        order by c.name, sm.number
        """,
        sid, term["kind"],
    )
    probs = {p["id"]: p for p in await problems_for_term(sid)}
    slots = [slot_out(p["row"], grid, p["problems"]) for p in probs.values()]
    slots.sort(key=lambda s: (s["class_label"], s["day"], s["start_period"]))
    classes = []
    for sm in sems:
        mine = [s for s in slots if s["semester_id"] == str(sm["id"])]
        classes.append(
            {
                "semester_id": str(sm["id"]),
                "label": term_svc.semester_label(sm["course_name"], sm["number"]),
                "early_release_time": hm(sm["early_release_time"]),
                "periods_available": grid.available_periods(sm["early_release_time"]),
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
    term = await term_svc.require_active_term()
    course = await db.fetchval("select name from public.courses where id = $1", sem["course_id"])
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
    problems, _ = await evaluate(
        term=term, sem=sem, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    return {"ok": not problems, "problems": problems, "length": length}


@router.post("", status_code=201)
async def create_slot(body: SlotIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem, term, course = await _context(user, body.semester_id)
    length = _length(body.kind, body.length)
    problems, resolved = await evaluate(
        term=term, sem=sem, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    if problems:
        raise _refuse(problems)
    label = _label(body.kind, body.label)
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            """
            insert into public.fixed_slots (school_id, term_id, semester_id, day, start_period, length, kind, label,
                                            subject_id, teacher_id, note, created_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) returning id
            """,
            sem["school_id"], term["id"], sem["id"], body.day, body.start_period, length, body.kind, label,
            resolved["subject_id"], resolved["teacher_id"], body.note, uuid.UUID(user.id),
        )
        await audit.record(
            conn, user, "create", "fixed_slot", row["id"],
            f"Locked {label} for {term_svc.semester_label(course, sem['number'])}: {DAY_NAMES[body.day]} periods "
            f"{body.start_period}-{body.start_period + length - 1}",
            school_id=sem["school_id"],
        )
    return {"id": str(row["id"]), "length": length}


class SlotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int | None = Field(default=None, ge=1, le=6)
    start_period: int | None = Field(default=None, ge=1, le=30)
    length: int | None = Field(default=None, ge=1, le=8)
    label: optional_text(80) = None  # type: ignore[valid-type]
    subject_id: str | None = None
    teacher_id: str | None = None
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
    subject = _opt(body.subject_id, "subject") if "subject_id" in fields else old["subject_id"]
    note = body.note if "note" in fields else old["note"]
    problems, resolved = await evaluate(
        term=term, sem=sem, day=day, start_period=start, length=length, kind=old["kind"],
        teacher_id=teacher, subject_id=subject, ignore_id=old["id"],
    )
    if problems:
        raise _refuse(problems)
    async with db.transaction() as conn:
        await conn.execute(
            """
            update public.fixed_slots set day = $2, start_period = $3, length = $4, label = $5,
                   subject_id = $6, teacher_id = $7, note = $8 where id = $1
            """,
            old["id"], day, start, length, label, resolved["subject_id"], resolved["teacher_id"], note,
        )
        await audit.record(
            conn, user, "update", "fixed_slot", old["id"],
            f"Changed locked slot '{label}' of {term_svc.semester_label(course, sem['number'])}: {DAY_NAMES[day]} periods {start}-{start + length - 1}",
            school_id=old["school_id"],
        )
    return {"id": str(old["id"])}


@router.delete("/{slot_id}")
async def delete_slot(slot_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    old = await load_in_scope("fixed_slots", slot_id, user, what="locked slot")
    async with db.transaction() as conn:
        await conn.execute("delete from public.fixed_slots where id = $1", old["id"])
        await audit.record(
            conn, user, "delete", "fixed_slot", old["id"],
            f"Removed locked slot '{old['label']}' ({DAY_NAMES[old['day']]} periods {old['start_period']}-{old['start_period'] + old['length'] - 1})",
            school_id=old["school_id"],
        )
    return {"deleted": True}
