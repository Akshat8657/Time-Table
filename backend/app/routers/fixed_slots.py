"""Fixed (locked) slots: PE and SOL as DOUBLE blocks, plus any other locked slot (plan Sections 8, 10).

Every request is checked against the real period grid: working days, the class's day-wise schedule (end time / OFF day),
overlaps (class + teachers) and the teachers' visiting days. An impossible slot is refused with a clear reason.
A double block is 2 consecutive PERIODS - the short break or even the LUNCH between them is fine (plan V8).

COMBINED sessions (plan V9): one locked slot may cover SEVERAL classes together (e.g. SOL for M.Sc. IT Sem 1 + M.Sc. AI Sem 1
with one teacher). It is stored as one row per covered class; the rows of one session share `combined_group_id`. To the outside
it is ONE slot (one id, one session): the teacher is not double-booked, and every covered class is occupied at that time.
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
select f.id, f.combined_group_id, f.school_id, f.term_id, f.semester_id, f.day, f.start_period, f.length, f.kind, f.label, f.subject_id,
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


def _key(r) -> uuid.UUID:
    """One locked slot = one key: the group id of a combined session, or the row id of a normal single-class slot."""
    return r["combined_group_id"] or r["id"]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def evaluate(
    *,
    term: asyncpg.Record,
    sems: list[asyncpg.Record],
    labels: dict[uuid.UUID, str],
    day: int,
    start_period: int,
    length: int,
    kind: str,
    teacher_id: uuid.UUID | None,
    co_teacher_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    ignore_key: uuid.UUID | None = None,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Is this slot possible for ALL the classes it covers? Returns (problems, resolved, notes).

    `resolved` holds the teachers and the subject of every covered class after the automatic defaults.
    `ignore_key` = the slot being edited (its own rows are never a clash)."""
    multi = len(sems) > 1
    school_id = sems[0]["school_id"]
    sem_ids = [s["id"] for s in sems]

    def tag(sem: asyncpg.Record, text: str) -> str:
        return f"{labels[sem['id']]}: {text}" if multi else text

    (grid, college), rule_rows, subject_results = await asyncio.gather(
        timing_svc.school_grid(school_id),
        db.fetch("select semester_id, is_off, end_time from public.semester_days where semester_id = any($1::uuid[]) and day = $2", sem_ids, day),
        asyncio.gather(*[_subject(None if multi else subject_id, kind, s) for s in sems]),
    )
    rules = {r["semester_id"]: DayRule(r["is_off"], r["end_time"]) for r in rule_rows}

    problems: list[str] = []
    for sem in sems:
        for text in timing_svc.slot_problems(grid, college["days_per_week"], rules.get(sem["id"]), day, start_period, length):
            problems.append(tag(sem, text))
        if kind in ("PE", "SOL") and sem["no_pe_sol"]:
            problems.append(tag(sem, f"This class is marked 'no PE / SOL'. Remove that mark first if {kind} should be taught."))
    notes = timing_svc.slot_notes(grid, start_period, length) if not problems else []
    if multi and subject_id is not None:
        problems.append("A combined slot takes each class's own PE / SOL subject automatically - leave the subject empty.")
    subjects: dict[uuid.UUID, asyncpg.Record | None] = {}
    for sem, (subject, subject_problem) in zip(sems, subject_results):
        subjects[sem["id"]] = subject
        if subject_problem:
            problems.append(tag(sem, subject_problem))
    chosen = [s for s in subjects.values() if s is not None]

    # no teacher given -> take the assigned teacher(s) of the class subject(s); classes that disagree need a human decision
    assigned_pairs: list[tuple[Any, Any]] = []
    if teacher_id is None and chosen:
        found = await db.fetch(
            "select subject_id, teacher_id, co_teacher_id from public.assignments where term_id = $1 and subject_id = any($2::uuid[])",
            term["id"], [s["id"] for s in chosen],
        )
        assigned_pairs = list({(a["teacher_id"], a["co_teacher_id"]) for a in found})
        if len(assigned_pairs) == 1:
            teacher_id = assigned_pairs[0][0]
            if co_teacher_id is None:
                co_teacher_id = assigned_pairs[0][1]
    if co_teacher_id is not None and co_teacher_id == teacher_id:
        problems.append("The teacher and the co-teacher must be two different people.")
        co_teacher_id = None
    if co_teacher_id is not None and teacher_id is None:
        problems.append("Please choose the main teacher before the co-teacher.")

    wanted = [t for t in (teacher_id, co_teacher_id) if t is not None]
    asked = list({*wanted, *[t for pair in assigned_pairs for t in pair if t is not None]}) if len(assigned_pairs) > 1 else wanted
    people, can_teach, others = await asyncio.gather(
        _people(school_id, asked),
        db.fetch(
            "select teacher_id, subject_id from public.teacher_subjects where subject_id = any($1::uuid[]) and teacher_id = any($2::uuid[])",
            [s["id"] for s in chosen], wanted,
        ) if (chosen and wanted) else _nothing(),
        db.fetch(
            SLOT_SQL + " where f.term_id = $1 and f.day = $2 and (f.semester_id = any($3::uuid[]) or f.teacher_id = any($4::uuid[]) or f.co_teacher_id = any($4::uuid[]))",
            term["id"], day, sem_ids, wanted,
        ),
    )
    if len(assigned_pairs) > 1:
        names = _unique([people[t]["full_name"] if t in people else "someone" for pair in assigned_pairs for t in pair[:1] if t is not None])
        problems.append(
            f"These classes have different teachers for their {kind} subject ({', '.join(names)}). "
            "Choose the teacher of the combined session yourself."
        )
    able = {(r["teacher_id"], r["subject_id"]) for r in can_teach}
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
        for sem in sems:
            subject = subjects[sem["id"]]
            if subject is not None and (tid, subject["id"]) not in able:
                problems.append(tag(sem, f"{person['full_name']} cannot teach '{subject['name']}'."))

    mine = set(wanted)
    seen_busy: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for o in others:
        if (ignore_key is not None and _key(o) == ignore_key) or not _overlap(start_period, length, o["start_period"], o["length"]):
            continue
        if o["semester_id"] in sem_ids:
            where = labels[o["semester_id"]] if multi else "This class"
            problems.append(f"{where} already has '{o['label']}' at {_span(o['start_period'], o['length'])} on {DAY_NAMES[day]}.")
            continue
        for tid in {o["teacher_id"], o["co_teacher_id"]} & mine:
            if (_key(o), tid) in seen_busy:
                continue
            seen_busy.add((_key(o), tid))
            name = people[tid]["full_name"] if tid in people else "A teacher"
            problems.append(f"{name} is already in a locked slot ({o['course_name']} Sem {o['number']}, '{o['label']}') at that time.")
    resolved = {
        "teacher_id": teacher_id,
        "co_teacher_id": co_teacher_id,
        "subjects": {sid: (subj["id"] if subj else None) for sid, subj in subjects.items()},
    }
    return _unique(problems), resolved, notes


async def _nothing() -> list:
    return []


async def _have(value):
    """Lets a value we already hold sit next to database calls inside asyncio.gather()."""
    return value


def slot_out(entries: list[dict[str, Any]], grid) -> dict[str, Any]:
    """ONE locked slot for the screens. `entries` = its rows (one row per covered class) from problems_for_term()."""
    entries = sorted(entries, key=lambda e: e["class_label"])
    r = entries[0]["row"]
    multi = len(entries) > 1
    problems: list[str] = []
    for e in entries:
        problems += [f"{e['class_label']}: {p}" if multi else p for p in e["grid_problems"]]
    problems += entries[0]["teacher_problems"]
    classes = [
        {
            "semester_id": str(e["row"]["semester_id"]),
            "class_label": e["class_label"],
            "subject_id": str(e["row"]["subject_id"]) if e["row"]["subject_id"] else None,
            "subject_name": e["row"]["subject_name"],
        }
        for e in entries
    ]
    return {
        "id": str(_key(r)),
        "semester_id": str(r["semester_id"]),
        "class_label": " + ".join(e["class_label"] for e in entries),
        "semester_ids": [c["semester_id"] for c in classes],
        "classes": classes,
        "combined": multi,
        "day": r["day"],
        "day_name": DAY_NAMES[r["day"]],
        "start_period": r["start_period"],
        "length": r["length"],
        "kind": r["kind"],
        "label": r["label"],
        "subject_id": classes[0]["subject_id"] if not multi else None,
        "subject_name": " / ".join(_unique([c["subject_name"] for c in classes if c["subject_name"]])) or None,
        "teacher_id": str(r["teacher_id"]) if r["teacher_id"] else None,
        "teacher_name": r["teacher_name"],
        "co_teacher_id": str(r["co_teacher_id"]) if r["co_teacher_id"] else None,
        "co_teacher_name": r["co_teacher_name"],
        "note": r["note"],
        "time": timing_svc.time_range(grid, r["start_period"], r["length"]),
        "notes": timing_svc.slot_notes(grid, r["start_period"], r["length"]),
        "problems": _unique(problems),
    }


async def problems_for_term(
    school_id: uuid.UUID | None,
    *,
    term: asyncpg.Record | None = None,
    college: asyncpg.Record | None = None,
    rule_rows: list[asyncpg.Record] | None = None,
    lunch: tuple[Any, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every locked-slot ROW (one per covered class) of the active term with the reasons it is not possible today
    (also used by the timing screens). A caller that already loaded the term / college timing / day rules / lunch can
    pass them in (saves database trips). `group` tells which rows belong to the same combined session."""
    term = term or await term_svc.active_term()
    if term is None:
        return []
    sql = SLOT_SQL + " where f.term_id = $1" + (" and f.school_id = $2" if school_id else "")
    args: list[Any] = [term["id"]] + ([school_id] if school_id else [])
    rows, college, rule_rows = await asyncio.gather(
        db.fetch(sql, *args),
        _have(college) if college is not None else timing_svc.college_row(),
        _have(rule_rows) if rule_rows is not None else db.fetch(
            "select semester_id, day, is_off, end_time from public.semester_days" + (" where school_id = $1" if school_id else ""),
            *([school_id] if school_id else []),
        ),
    )
    rules = timing_svc.rules_from_rows(rule_rows)
    school_ids = {r["school_id"] for r in rows}
    lunches = {}
    if lunch is not None and school_id is not None:
        lunches[school_id] = lunch
    missing = [sid for sid in school_ids if sid not in lunches]
    if missing:
        for sid, found in zip(missing, await asyncio.gather(*[timing_svc.school_lunch(x) for x in missing])):
            lunches[sid] = found
    grids = {sid: timing_svc.make_grid(college, *lunches[sid]) for sid in school_ids}
    out = []
    for r in rows:
        grid = grids[r["school_id"]]
        grid_problems = timing_svc.slot_problems(grid, college["days_per_week"], rules.get((r["semester_id"], r["day"])), r["day"], r["start_period"], r["length"])
        teacher_problems = []
        for name, visiting, days in ((r["teacher_name"], r["is_visiting"], r["visiting_days"]), (r["co_teacher_name"], r["co_is_visiting"], r["co_visiting_days"])):
            if visiting and r["day"] not in (days or []):
                teacher_problems.append(f"{name} is visiting faculty and cannot come on {DAY_NAMES[r['day']]}.")
        out.append(
            {
                "semester_id": str(r["semester_id"]), "id": str(r["id"]), "group": str(_key(r)),
                "class_label": term_svc.semester_label(r["course_name"], r["number"]),
                "problems": grid_problems + teacher_problems, "grid_problems": grid_problems, "teacher_problems": teacher_problems,
                "row": r, "grid": grid,
            }
        )
    return out


def group_entries(found: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The rows of problems_for_term() grouped into locked slots (a combined session = several rows)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in found:
        groups.setdefault(e["group"], []).append(e)
    return list(groups.values())


# ---------------------------------------------------------------------- list
@router.get("")
async def list_slots(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term, college, lunch, rule_rows = await asyncio.gather(
        term_svc.active_term(),
        timing_svc.college_row(),
        timing_svc.school_lunch(sid),
        db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", sid),
    )
    grid = timing_svc.make_grid(college, *lunch)
    if term is None:
        return {"active_term": None, "grid": grid.as_dict(), "days_per_week": college["days_per_week"], "classes": [], "slots": []}

    sems, found = await asyncio.gather(
        db.fetch(
            """
            select sm.id, sm.number, sm.no_pe_sol, c.name as course_name
            from public.semesters sm join public.courses c on c.id = sm.course_id
            where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            order by c.name, sm.number
            """,
            sid, term["kind"],
        ),
        problems_for_term(sid, term=term, college=college, rule_rows=rule_rows, lunch=lunch),
    )
    rules = timing_svc.rules_from_rows(rule_rows)
    slots = [slot_out(entries, grid) for entries in group_entries(found)]
    slots.sort(key=lambda s: (s["class_label"], s["day"], s["start_period"]))
    classes = []
    for sm in sems:
        mine = [s for s in slots if str(sm["id"]) in s["semester_ids"]]
        classes.append(
            {
                "semester_id": str(sm["id"]),
                "label": term_svc.semester_label(sm["course_name"], sm["number"]),
                "days": timing_svc.class_days(grid, college["days_per_week"], timing_svc.rules_of(rules, sm["id"])),
                "has_pe": any(s["kind"] == "PE" for s in mine),
                "has_sol": any(s["kind"] == "SOL" for s in mine),
                "no_pe_sol": sm["no_pe_sol"],
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


class NoPeSolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_pe_sol: bool


@router.put("/classes/{semester_id}/no-pe-sol")
async def mark_class_without_pe_sol(semester_id: str, body: NoPeSolIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """Some classes have no PE and no SOL at all (e.g. B.Sc. Sem 5 and 7). Marking that keeps the checklist quiet."""
    sems, term, labels = await _contexts(user, [semester_id])
    sem = sems[0]
    label = labels[sem["id"]]
    if body.no_pe_sol:
        have = await db.fetchval(
            "select count(*) from public.fixed_slots where semester_id = $1 and term_id = $2 and kind in ('PE', 'SOL')", sem["id"], term["id"]
        )
        if have:
            raise ApiError(409, "IN_USE", f"{label} still has {have} PE / SOL slot(s). Remove them first, then mark the class as 'no PE / SOL'.")
    async with db.transaction() as conn:
        await conn.execute("update public.semesters set no_pe_sol = $2 where id = $1", sem["id"], body.no_pe_sol)
        await audit.record(
            conn, user, "update", "class_pe_sol", sem["id"],
            f"{label}: " + ("has no PE / SOL" if body.no_pe_sol else "PE / SOL are expected again"), school_id=sem["school_id"],
        )
    return {"semester_id": str(sem["id"]), "no_pe_sol": body.no_pe_sol}


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


MAX_COMBINED = 12


class SlotIn(SlotBase):
    """One class (`semester_id`) - or several classes together (`semester_ids`) = a COMBINED session."""

    semester_id: str | None = None
    semester_ids: list[str] | None = Field(default=None, max_length=MAX_COMBINED)

    @model_validator(mode="after")
    def _classes(self):
        if self.semester_id and self.semester_ids is not None:
            raise ValueError("Send either one class (semester_id) or several (semester_ids), not both.")
        ids = self.semester_ids if self.semester_ids is not None else ([self.semester_id] if self.semester_id else [])
        if not ids:
            raise ValueError("Please choose at least one class.")
        if len(set(ids)) != len(ids):
            raise ValueError("The same class was chosen twice.")
        return self

    @property
    def classes(self) -> list[str]:
        return list(self.semester_ids) if self.semester_ids is not None else [self.semester_id]  # type: ignore[list-item]


def _length(kind: str, length: int | None) -> int:
    return 2 if kind in ("PE", "SOL") else (length or 1)  # PE and SOL are ALWAYS a double block


def _label(kind: str, label: str | None) -> str:
    return label or KIND_LABEL.get(kind, "Locked slot")


async def _contexts(user: CurrentUser, semester_ids: list[str]):
    """The classes of a slot: found (HOD: own school only), all in ONE school and all running in the active term."""
    sems = list(await asyncio.gather(*[load_in_scope("semesters", sid, user, what="class") for sid in semester_ids]))
    if len({s["school_id"] for s in sems}) > 1:
        raise ApiError(422, "MIXED_SCHOOLS", "A combined slot can only cover classes of one school.")
    term, courses = await asyncio.gather(
        term_svc.require_active_term(),
        db.fetch("select id, name from public.courses where id = any($1::uuid[])", list({s["course_id"] for s in sems})),
    )
    names = {c["id"]: c["name"] for c in courses}
    labels = {s["id"]: term_svc.semester_label(names[s["course_id"]], s["number"]) for s in sems}
    for s in sems:
        term_svc.require_active_semester(term, s["number"], labels[s["id"]])
    return sems, term, labels


def _refuse(problems: list[str]) -> ApiError:
    return ApiError(422, "SLOT_NOT_POSSIBLE", "This slot is not possible: " + " ".join(problems), extra={"problems": problems})


def _opt(value: str | None, what: str):
    return as_uuid(value, what) if value else None


def _classes_text(sems: list[asyncpg.Record], labels: dict[uuid.UUID, str]) -> str:
    return " + ".join(labels[s["id"]] for s in sorted(sems, key=lambda s: labels[s["id"]]))


@router.post("/check")
async def check_slot(body: SlotIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sems, term, labels = await _contexts(user, body.classes)
    length = _length(body.kind, body.length)
    problems, _, notes = await evaluate(
        term=term, sems=sems, labels=labels, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), co_teacher_id=_opt(body.co_teacher_id, "co-teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    return {"ok": not problems, "problems": problems, "notes": notes, "length": length, "combined": len(sems) > 1}


_INSERT_ROW = """
insert into public.fixed_slots (id, combined_group_id, school_id, term_id, semester_id, day, start_period, length, kind, label,
                                subject_id, teacher_id, co_teacher_id, note, created_by)
values (coalesce($1::uuid, gen_random_uuid()), $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
"""


@router.post("", status_code=201)
async def create_slot(body: SlotIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sems, term, labels = await _contexts(user, body.classes)
    length = _length(body.kind, body.length)
    problems, resolved, notes = await evaluate(
        term=term, sems=sems, labels=labels, day=body.day, start_period=body.start_period, length=length, kind=body.kind,
        teacher_id=_opt(body.teacher_id, "teacher"), co_teacher_id=_opt(body.co_teacher_id, "co-teacher"), subject_id=_opt(body.subject_id, "subject"),
    )
    if problems:
        raise _refuse(problems)
    label = _label(body.kind, body.label)
    key = uuid.uuid4()
    combined = len(sems) > 1
    async with db.transaction() as conn:
        for i, sem in enumerate(sems):  # the first row carries the id of the whole session
            await conn.execute(
                _INSERT_ROW,
                key if i == 0 else None, key if combined else None, sem["school_id"], term["id"], sem["id"], body.day, body.start_period, length,
                body.kind, label, resolved["subjects"].get(sem["id"]), resolved["teacher_id"], resolved["co_teacher_id"], body.note, uuid.UUID(user.id),
            )
        await audit.record(
            conn, user, "create", "fixed_slot", key,
            f"Locked {label} for {_classes_text(sems, labels)}{' (combined session)' if combined else ''}: {DAY_NAMES[body.day]} {_span(body.start_period, length)}",
            school_id=sems[0]["school_id"], details={"classes": len(sems)} if combined else None,
        )
    return {"id": str(key), "length": length, "notes": notes, "combined": combined, "classes": len(sems)}


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
    semester_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_COMBINED)  # the NEW full set of covered classes


async def _load_slot(slot_id: str, user: CurrentUser) -> list[asyncpg.Record]:
    """All rows of one locked slot (a combined session has one per class). Another school's slot 'does not exist' for an HOD."""
    key = as_uuid(slot_id, "locked slot")
    rows = await db.fetch(SLOT_SQL + " where coalesce(f.combined_group_id, f.id) = $1 order by c.name, sm.number", key)
    if not rows or (user.role != "admin" and str(rows[0]["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That locked slot was not found.")
    return rows


@router.patch("/{slot_id}")
async def update_slot(slot_id: str, body: SlotPatch, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    rows = await _load_slot(slot_id, user)
    old = rows[0]
    key = _key(old)
    fields = body.model_fields_set
    wanted_ids = [str(x) for x in body.semester_ids] if body.semester_ids is not None else [str(r["semester_id"]) for r in rows]
    if len(set(wanted_ids)) != len(wanted_ids):
        raise ApiError(422, "VALIDATION_ERROR", "The same class was chosen twice.")
    sems, term, labels = await _contexts(user, wanted_ids)
    if old["term_id"] != term["id"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "Locked slots of an old term are kept as history and cannot be changed.")
    day = body.day or old["day"]
    start = body.start_period or old["start_period"]
    length = _length(old["kind"], body.length if body.length else old["length"])
    label = body.label if "label" in fields and body.label else old["label"]
    teacher = _opt(body.teacher_id, "teacher") if "teacher_id" in fields else old["teacher_id"]
    co_teacher = _opt(body.co_teacher_id, "co-teacher") if "co_teacher_id" in fields else old["co_teacher_id"]
    single = len(sems) == 1
    if "subject_id" in fields:
        subject = _opt(body.subject_id, "subject")
    else:
        subject = old["subject_id"] if (single and len(rows) == 1) else None
    note = body.note if "note" in fields else old["note"]
    problems, resolved, notes = await evaluate(
        term=term, sems=sems, labels=labels, day=day, start_period=start, length=length, kind=old["kind"],
        teacher_id=teacher, co_teacher_id=co_teacher, subject_id=subject, ignore_key=key,
    )
    if problems:
        raise _refuse(problems)

    keep = {r["semester_id"]: r for r in rows}
    new_ids = {s["id"] for s in sems}
    gid = key if len(sems) > 1 else None
    added = [labels[s["id"]] for s in sems if s["id"] not in keep]
    removed = [term_svc.semester_label(r["course_name"], r["number"]) for r in rows if r["semester_id"] not in new_ids]
    async with db.transaction() as conn:
        for r in rows:
            if r["semester_id"] not in new_ids:
                await conn.execute("delete from public.fixed_slots where id = $1", r["id"])
        for sem in sems:
            subj = resolved["subjects"].get(sem["id"])
            if sem["id"] in keep:
                await conn.execute(
                    """
                    update public.fixed_slots set combined_group_id = $2, day = $3, start_period = $4, length = $5, label = $6,
                           subject_id = $7, teacher_id = $8, co_teacher_id = $9, note = $10 where id = $1
                    """,
                    keep[sem["id"]]["id"], gid, day, start, length, label, subj, resolved["teacher_id"], resolved["co_teacher_id"], note,
                )
            else:
                await conn.execute(
                    _INSERT_ROW, None, gid, sem["school_id"], term["id"], sem["id"], day, start, length, old["kind"], label,
                    subj, resolved["teacher_id"], resolved["co_teacher_id"], note, uuid.UUID(user.id),
                )
        extra = ""
        if added:
            extra += f"; added {', '.join(added)}"
        if removed:
            extra += f"; removed {', '.join(removed)}"
        await audit.record(
            conn, user, "update", "fixed_slot", key,
            f"Changed locked slot '{label}' of {_classes_text(sems, labels)}: {DAY_NAMES[day]} {_span(start, length)}{extra}",
            school_id=old["school_id"],
        )
    return {"id": str(key), "notes": notes, "combined": len(sems) > 1, "classes": len(sems)}


@router.delete("/{slot_id}")
async def delete_slot(slot_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    rows = await _load_slot(slot_id, user)
    old = rows[0]
    classes = " + ".join(term_svc.semester_label(r["course_name"], r["number"]) for r in rows)
    async with db.transaction() as conn:
        await conn.execute("delete from public.fixed_slots where coalesce(combined_group_id, id) = $1", _key(old))
        await audit.record(
            conn, user, "delete", "fixed_slot", _key(old),
            f"Removed locked slot '{old['label']}' of {classes} ({DAY_NAMES[old['day']]} {_span(old['start_period'], old['length'])})",
            school_id=old["school_id"],
        )
    return {"deleted": True}
