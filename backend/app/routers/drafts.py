"""Phase 2: manual timetable DRAFTS, the weekly grid editor and the conflict report (plan Section 19, Sections 10-11).

  GET    /api/drafts                       drafts of every class of the active term (+ how complete / how clean each one is)
  POST   /api/drafts                       new draft (optionally copied from another draft of the same class)
  GET    /api/drafts/{id}                  everything the grid editor shows
  PATCH  /api/drafts/{id}                  rename / note / make primary
  DELETE /api/drafts/{id}
  POST   /api/drafts/{id}/options          for one subject (or an existing placement) which cell is ok / warns / is blocked
  POST   /api/drafts/{id}/placements       place a subject, or all options of an elective group together (parallel)
  PATCH  /api/drafts/{id}/placements/{key} move
  POST   /api/drafts/{id}/swap             swap two placements
  DELETE /api/drafts/{id}/placements/{key} remove
  POST   /api/drafts/{id}/clear            remove every placement
  GET    /api/conflicts                    every clash / missing lecture / warning of the active term

All rules live in services/schedule.py. Teachers are not stored on a placement: they come live from the Assignment screen.
Drafts are private to the admin and the HOD of the school (teachers get nothing until publish, Phase 4).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, load_in_scope, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import prefs as prefs_svc
from ..services import schedule as sch
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services.timing import DayRule
from ..validation import DAY_NAMES, optional_text, text

router = APIRouter(prefix="/api/drafts", tags=["drafts"])
conflicts_router = APIRouter(prefix="/api/conflicts", tags=["drafts"])
ROLES = ("admin", "hod")
MAX_DRAFTS_PER_CLASS = 12


# ---------------------------------------------------------------------------------------------------- loading
@dataclass
class Loaded:
    ctx: sch.Ctx
    term: asyncpg.Record
    school_id: uuid.UUID
    sems: list[asyncpg.Record]
    rows: dict[uuid.UUID, dict[str, Any]]  # the draft rows (name, note, ...)

    def with_entries(self, entries: list[sch.Entry]) -> "Loaded":
        return replace(self, ctx=replace(self.ctx, entries=entries))

    def with_drafts(self, drafts: dict[uuid.UUID, sch.DraftInfo], rows: dict[uuid.UUID, dict[str, Any]]) -> "Loaded":
        return replace(self, ctx=replace(self.ctx, drafts=drafts), rows=rows)


async def load(school_id: uuid.UUID, term: asyncpg.Record | None = None) -> Loaded:
    """Everything one request needs, in one parallel round of queries."""
    term = term or await term_svc.require_active_term()
    (grid, college), sems, rule_rows, subs, assigns, people, fixed, drafts, entries, prefs = await asyncio.gather(
        timing_svc.school_grid(school_id),
        db.fetch(
            """
            select sm.id, sm.number, sm.no_pe_sol, c.name as course_name
            from public.semesters sm join public.courses c on c.id = sm.course_id
            where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            order by c.name, sm.number
            """,
            school_id, term["kind"],
        ),
        db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", school_id),
        db.fetch(
            """
            select s.id, s.semester_id, s.name, s.code, s.credits, s.weekly_lectures, s.is_lab, s.special_kind, s.elective_group_id,
                   g.name as group_name
            from public.subjects s
            join public.semesters sm on sm.id = s.semester_id join public.courses c on c.id = sm.course_id
            left join public.elective_groups g on g.id = s.elective_group_id
            where s.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            """,
            school_id, term["kind"],
        ),
        db.fetch("select subject_id, teacher_id, co_teacher_id from public.assignments where school_id = $1 and term_id = $2", school_id, term["id"]),
        db.fetch(
            "select id, full_name, is_active, is_visiting, visiting_days, max_weekly_hours from public.profiles "
            "where school_id = $1 and role in ('teacher', 'hod')",
            school_id,
        ),
        db.fetch(
            "select id, combined_group_id, semester_id, day, start_period, length, kind, label, teacher_id, co_teacher_id, subject_id "
            "from public.fixed_slots where school_id = $1 and term_id = $2",
            school_id, term["id"],
        ),
        db.fetch("select * from public.timetable_drafts where school_id = $1 and term_id = $2", school_id, term["id"]),
        db.fetch(
            """
            select e.id, e.draft_id, e.semester_id, e.subject_id, e.day, e.start_period, e.length, e.parallel_key, e.is_locked
            from public.draft_entries e join public.timetable_drafts d on d.id = e.draft_id
            where d.school_id = $1 and d.term_id = $2
            """,
            school_id, term["id"],
        ),
        prefs_svc.load_prefs(school_id),
    )
    ctx = sch.Ctx(
        grid=grid,
        days_per_week=college["days_per_week"],
        labels={s["id"]: term_svc.semester_label(s["course_name"], s["number"]) for s in sems},
        rules={(r["semester_id"], r["day"]): DayRule(r["is_off"], r["end_time"]) for r in rule_rows},
        subjects={
            s["id"]: sch.Subj(s["id"], s["semester_id"], s["name"], s["code"], s["credits"], s["is_lab"], s["special_kind"], s["elective_group_id"], s["group_name"], s["weekly_lectures"])
            for s in subs
        },
        assign={a["subject_id"]: (a["teacher_id"], a["co_teacher_id"]) for a in assigns},
        people={
            p["id"]: sch.Person(p["id"], p["full_name"], p["is_active"], p["is_visiting"], frozenset(p["visiting_days"] or []), p["max_weekly_hours"])
            for p in people
        },
        fixed=[
            sch.Fixed(f["id"], f["combined_group_id"] or f["id"], f["semester_id"], f["day"], f["start_period"], f["length"], f["kind"], f["label"], f["teacher_id"], f["co_teacher_id"], f["subject_id"])
            for f in fixed
        ],
        drafts={d["id"]: sch.DraftInfo(d["id"], d["semester_id"], d["name"], d["is_primary"]) for d in drafts},
        entries=[sch.Entry(e["id"], e["draft_id"], e["semester_id"], e["subject_id"], e["day"], e["start_period"], e["length"], e["parallel_key"], e["is_locked"]) for e in entries],
        prefs=prefs,
    )
    return Loaded(ctx, term, school_id, list(sems), {d["id"]: dict(d) for d in drafts})


async def _draft(user: CurrentUser, draft_id: str) -> tuple[asyncpg.Record, asyncpg.Record]:
    """The draft row (HOD: own school only) and the active term it must belong to."""
    row = await load_in_scope("timetable_drafts", draft_id, user, what="draft")
    term = await term_svc.require_active_term()
    if row["term_id"] != term["id"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "Drafts of an old term are kept as history and cannot be opened.")
    return row, term


# ---------------------------------------------------------------------------------------------------- output shapes
def _msgs(issues) -> list[str]:
    return [i.message for i in issues]


def _blocked(res: sch.Result) -> ApiError:
    problems = _msgs(res.hard)
    return ApiError(422, "PLACEMENT_BLOCKED", "This cannot be placed there: " + " ".join(problems), extra={"problems": problems})


def placement_out(L: Loaded, draft_id: uuid.UUID, rows: list[sch.Entry]) -> dict[str, Any]:
    ctx = L.ctx
    first = rows[0]
    res = sch.check_saved_unit(ctx, draft_id, rows)
    subs = [ctx.subjects[e.subject_id] for e in sorted(rows, key=lambda e: ctx.subjects[e.subject_id].name)]
    entries = []
    for s in subs:
        e = next(x for x in rows if x.subject_id == s.id)
        t, co = ctx.assign.get(s.id, (None, None))
        entries.append({
            "id": str(e.id), "subject_id": str(s.id), "subject_name": s.name, "subject_code": s.code, "is_lab": s.is_lab,
            "teacher_id": str(t) if t else None, "teacher_name": ctx.person_name(t) if t else None,
            "co_teacher_id": str(co) if co else None, "co_teacher_name": ctx.person_name(co) if co else None,
        })
    return {
        "key": str(first.key), "day": first.day, "start_period": first.start, "length": first.length,
        "time": timing_svc.time_range(ctx.grid, first.start, first.length),
        "parallel": len(rows) > 1, "group_name": subs[0].group_name if len(rows) > 1 else None, "is_lab": subs[0].is_lab,
        "is_locked": first.is_locked,
        "entries": entries, "problems": _msgs(res.hard), "warnings": _msgs(res.soft),
    }


def locked_out(L: Loaded, sem_id: uuid.UUID) -> list[dict[str, Any]]:
    ctx = L.ctx
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for (sid, _day), fixed_rows in ctx.fixed_by_class.items():
        if sid != sem_id:
            continue
        for f in fixed_rows:
            t, co = f.teacher_id, f.co_teacher_id
            classes = sorted(ctx.fixed_classes[f.key])
            out[f.key] = {
                "key": str(f.key), "day": f.day, "start_period": f.start, "length": f.length, "kind": f.kind, "label": f.label,
                "time": timing_svc.time_range(ctx.grid, f.start, f.length),
                "teacher_name": ctx.person_name(t) if t else None, "co_teacher_name": ctx.person_name(co) if co else None,
                "combined": len(classes) > 1, "classes": classes,
            }
    return sorted(out.values(), key=lambda x: (x["day"], x["start_period"]))


def draft_detail(L: Loaded, draft_id: uuid.UUID) -> dict[str, Any]:
    ctx = L.ctx
    info = ctx.drafts[draft_id]
    row = L.rows[draft_id]
    sem_id = info.semester_id
    units = sch.units_of(ctx.entries_by_draft.get(draft_id, []))
    placements = sorted((placement_out(L, draft_id, rows) for rows in units.values()), key=lambda p: (p["day"], p["start_period"]))
    comp = sch.completeness(ctx, sem_id, draft_id)
    by_subject = {r["subject_id"]: r for r in comp["subjects"]}
    subjects = []
    for s in sorted(ctx.subjects_by_class.get(sem_id, []), key=lambda x: (x.group_name or "~", x.name)):
        t, co = ctx.assign.get(s.id, (None, None))
        r = by_subject[str(s.id)]
        subjects.append({
            "id": str(s.id), "name": s.name, "code": s.code, "credits": s.credits, "weekly_lectures": s.weekly, "is_lab": s.is_lab, "special_kind": s.special_kind,
            "group_id": str(s.group_id) if s.group_id else None, "group_name": s.group_name,
            "teacher_name": ctx.person_name(t) if t else None, "co_teacher_name": ctx.person_name(co) if co else None,
            "required": r["required"], "placed": r["placed"], "status": r["status"],
        })
    sem = next(s for s in L.sems if s["id"] == sem_id)
    rules = timing_svc.rules_of(ctx.rules, sem_id)
    siblings = sorted(
        ({"id": str(d.id), "name": d.name, "is_primary": d.is_primary} for d in ctx.drafts.values() if d.semester_id == sem_id),
        key=lambda d: (not d["is_primary"], d["name"].lower()),
    )
    clashes = sum(len(p["problems"]) for p in placements)
    warnings = sum(len(p["warnings"]) for p in placements)
    return {
        "draft": {
            "id": str(draft_id), "name": info.name, "note": row.get("note"), "is_primary": info.is_primary, "semester_id": str(sem_id),
            "class_label": ctx.labels[sem_id], "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        },
        "siblings": siblings,
        "active_term": term_svc.term_out(L.term),
        "grid": ctx.grid.as_dict(),
        "days": timing_svc.class_days(ctx.grid, ctx.days_per_week, rules),
        "locked": locked_out(L, sem_id),
        "placements": placements,
        "subjects": subjects,
        "groups": [
            {"id": g["group_id"], "name": g["name"], "member_ids": g["members"], "required": g["required"], "placed": g["placed"],
             "status": g["status"], "kind": g["kind"], "is_lab": g["kind"] == "lab"}
            for g in comp["groups"]
        ],
        "completeness": {k: comp[k] for k in ("required", "placed", "percent", "missing")},
        "problems": {"clashes": clashes, "warnings": warnings},
        "no_pe_sol": bool(sem["no_pe_sol"]),
    }


def _detail_after(L: Loaded, entries: list[sch.Entry], draft_id: uuid.UUID) -> dict[str, Any]:
    """The detail of a draft after a change, built from what we already loaded (no second round of queries)."""
    L2 = L.with_entries(entries)
    L2.rows[draft_id] = {**L2.rows[draft_id], "updated_at": datetime.now(timezone.utc)}
    return draft_detail(L2, draft_id)


def _entry_row(r: dict[str, Any]) -> sch.Entry:
    return sch.Entry(r["id"], r["draft_id"], r["semester_id"], r["subject_id"], r["day"], r["start"], r["length"], r.get("parallel_key"))


_INSERT = """
insert into public.draft_entries (id, draft_id, school_id, semester_id, subject_id, day, start_period, length, parallel_key, created_by, is_locked)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""


# ---------------------------------------------------------------------------------------------------- list
@router.get("")
async def list_drafts(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"active_term": None, "classes": []}
    L = await load(sid, term)
    ctx = L.ctx
    classes = []
    for sm in L.sems:
        sem_id = sm["id"]
        items = []
        for d in sorted((d for d in ctx.drafts.values() if d.semester_id == sem_id), key=lambda d: (not d.is_primary, d.name.lower())):
            a = sch.analyze_draft(ctx, d.id)
            comp = a["completeness"]
            row = L.rows[d.id]
            items.append({
                "id": str(d.id), "name": d.name, "note": row.get("note"), "is_primary": d.is_primary,
                "updated_at": row["updated_at"].isoformat(), "placements": len(sch.units_of(ctx.entries_by_draft.get(d.id, []))),
                "percent": comp["percent"], "required": comp["required"], "placed": comp["placed"], "missing": comp["missing"],
                "clashes": len(a["clashes"]), "warnings": len(a["warnings"]),
            })
        locked = {f.key for (s, _d), rows in ctx.fixed_by_class.items() if s == sem_id for f in rows}
        classes.append({
            "semester_id": str(sem_id), "label": ctx.labels[sem_id], "subjects": len(ctx.subjects_by_class.get(sem_id, [])),
            "locked_sessions": len(locked), "drafts": items,
        })
    return {"active_term": term_svc.term_out(term), "classes": classes, "max_drafts_per_class": MAX_DRAFTS_PER_CLASS}


# ---------------------------------------------------------------------------------------------------- create / edit / delete a draft
class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: str
    name: text(1, 60)  # type: ignore[valid-type]
    note: optional_text(300) = None  # type: ignore[valid-type]
    copy_from: str | None = None


@router.post("", status_code=201)
async def create_draft(body: DraftIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sem = await load_in_scope("semesters", body.semester_id, user, what="class")
    L = await load(sem["school_id"])
    ctx = L.ctx
    if sem["id"] not in ctx.labels:
        label = await db.fetchval("select name from public.courses where id = $1", sem["course_id"])
        term_svc.require_active_semester(L.term, sem["number"], term_svc.semester_label(label, sem["number"]))
        raise ApiError(409, "COURSE_INACTIVE", "This course is inactive.")
    label = ctx.labels[sem["id"]]
    mine = [d for d in ctx.drafts.values() if d.semester_id == sem["id"]]
    if len(mine) >= MAX_DRAFTS_PER_CLASS:
        raise ApiError(409, "TOO_MANY_DRAFTS", f"{label} already has {len(mine)} drafts. Delete one you no longer need first.")
    if any(d.name.strip().lower() == body.name.strip().lower() for d in mine):
        raise ApiError(409, "ALREADY_EXISTS", "This class already has a draft with that name.", extra={"fields": {"name": "Choose another name."}})
    source: list[sch.Entry] = []
    if body.copy_from:
        src = as_uuid(body.copy_from, "draft")
        if src not in ctx.drafts or ctx.drafts[src].semester_id != sem["id"]:
            raise ApiError(404, "NOT_FOUND", "The draft to copy was not found in this class.")
        source = list(ctx.entries_by_draft.get(src, []))
    new_id = uuid.uuid4()
    async with db.transaction() as conn:
        await conn.execute(
            "insert into public.timetable_drafts (id, school_id, term_id, semester_id, name, note, is_primary, created_by) values ($1,$2,$3,$4,$5,$6,$7,$8)",
            new_id, sem["school_id"], L.term["id"], sem["id"], body.name, body.note, not mine, uuid.UUID(user.id),
        )
        remap: dict[uuid.UUID, uuid.UUID] = {}
        for e in source:
            pk = None
            if e.parallel_key is not None:
                pk = remap.setdefault(e.parallel_key, uuid.uuid4())
            await conn.execute(_INSERT, uuid.uuid4(), new_id, sem["school_id"], sem["id"], e.subject_id, e.day, e.start, e.length, pk, uuid.UUID(user.id), e.is_locked)
        await audit.record(
            conn, user, "create", "timetable_draft", new_id,
            f"Created draft '{body.name}' for {label}" + (f" (copy of '{ctx.drafts[as_uuid(body.copy_from, 'draft')].name}')" if body.copy_from else ""),
            school_id=sem["school_id"],
        )
    return {"id": str(new_id), "is_primary": not mine, "copied": len(source)}


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: text(1, 60) | None = None  # type: ignore[valid-type]
    note: optional_text(300) = None  # type: ignore[valid-type]
    is_primary: bool | None = None


@router.patch("/{draft_id}")
async def update_draft(draft_id: str, body: DraftPatch, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    fields = body.model_fields_set
    siblings = await db.fetch("select id, name, is_primary from public.timetable_drafts where term_id = $1 and semester_id = $2", term["id"], row["semester_id"])
    name = body.name if "name" in fields and body.name else row["name"]
    if name.strip().lower() != row["name"].strip().lower() and any(s["name"].strip().lower() == name.strip().lower() for s in siblings):
        raise ApiError(409, "ALREADY_EXISTS", "This class already has a draft with that name.", extra={"fields": {"name": "Choose another name."}})
    note = body.note if "note" in fields else row["note"]
    if "is_primary" in fields and body.is_primary is False and row["is_primary"]:
        raise ApiError(409, "PRIMARY_NEEDED", "Every class keeps one primary draft. Make another draft primary instead.")
    async with db.transaction() as conn:
        if body.is_primary and not row["is_primary"]:
            await conn.execute("update public.timetable_drafts set is_primary = false where term_id = $1 and semester_id = $2 and is_primary", term["id"], row["semester_id"])
        await conn.execute(
            "update public.timetable_drafts set name = $2, note = $3, is_primary = $4 where id = $1",
            row["id"], name, note, row["is_primary"] or bool(body.is_primary),
        )
        changes = [x for x, on in (("renamed", name != row["name"]), ("note changed", note != row["note"]), ("made primary", bool(body.is_primary) and not row["is_primary"])) if on]
        if changes:
            await audit.record(conn, user, "update", "timetable_draft", row["id"], f"Draft '{row['name']}': {', '.join(changes)}", school_id=row["school_id"])
    return {"id": str(row["id"]), "name": name, "is_primary": row["is_primary"] or bool(body.is_primary)}


@router.delete("/{draft_id}")
async def delete_draft(draft_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    async with db.transaction() as conn:
        await conn.execute("delete from public.timetable_drafts where id = $1", row["id"])
        promoted = None
        if row["is_primary"]:  # the class keeps a primary draft: the most recently edited one takes over
            promoted = await conn.fetchval(
                "update public.timetable_drafts set is_primary = true where id = (select id from public.timetable_drafts "
                "where term_id = $1 and semester_id = $2 order by updated_at desc limit 1) returning name",
                term["id"], row["semester_id"],
            )
        await audit.record(
            conn, user, "delete", "timetable_draft", row["id"],
            f"Deleted draft '{row['name']}'" + (f"; '{promoted}' is now the primary draft" if promoted else ""), school_id=row["school_id"],
        )
    return {"deleted": True}


# ---------------------------------------------------------------------------------------------------- copy a past term
@router.get("/copyable-terms")
async def copyable_terms(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """Past terms whose drafts can seed the new term ("copy previous semester as a starting base", plan Section 11)."""
    sid = await resolve_school(user, school_id)
    term = await term_svc.require_active_term()
    rows = await db.fetch(
        """
        select t.id, t.kind, t.year, count(*) filter (where d.is_primary) as primaries, count(*) as drafts
        from public.academic_terms t join public.timetable_drafts d on d.term_id = t.id and d.school_id = $1
        where t.id <> $2 group by t.id order by t.year desc, t.kind desc
        """,
        sid, term["id"],
    )
    return {
        "active_term": term_svc.term_out(term),
        "terms": [
            {"id": str(r["id"]), "name": term_svc.term_name(r["kind"], r["year"]), "kind": r["kind"],
             "primaries": r["primaries"], "drafts": r["drafts"], "same_kind": r["kind"] == term["kind"]}
            for r in rows
        ],
    }


class CopyTermIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_id: str
    school_id: str | None = None


@router.post("/copy-term", status_code=201)
async def copy_term(body: CopyTermIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """Copies the PRIMARY drafts of a past term into the active term as a starting base (one new draft per class)."""
    sid = await resolve_school(user, body.school_id)
    term = await term_svc.require_active_term()
    source = await db.fetchrow("select * from public.academic_terms where id = $1", as_uuid(body.term_id, "term"))
    if source is None:
        raise ApiError(404, "NOT_FOUND", "That term was not found.")
    if source["id"] == term["id"]:
        raise ApiError(422, "VALIDATION_ERROR", "Choose a PAST term - the active term cannot be copied onto itself.")
    source_name = term_svc.term_name(source["kind"], source["year"])
    if source["kind"] != term["kind"]:
        raise ApiError(
            409, "KIND_MISMATCH",
            f"{source_name} is an {source['kind']} term: its classes (semesters) do not run in "
            f"{term_svc.term_name(term['kind'], term['year'])}, so there is nothing to copy.",
        )
    L = await load(sid, term)
    ctx = L.ctx
    old_drafts = await db.fetch(
        "select * from public.timetable_drafts where term_id = $1 and school_id = $2 and is_primary", source["id"], sid
    )
    old_entries = await db.fetch(
        "select * from public.draft_entries where draft_id = any($1::uuid[])", [d["id"] for d in old_drafts]
    )
    by_draft: dict[Any, list] = {}
    for e in old_entries:
        by_draft.setdefault(e["draft_id"], []).append(e)
    created, skipped = [], []
    async with db.transaction() as conn:
        for old in old_drafts:
            cid = old["semester_id"]
            if cid not in ctx.labels:
                skipped.append(old["name"])
                continue
            mine = [d for d in ctx.drafts.values() if d.semester_id == cid]
            if len(mine) >= MAX_DRAFTS_PER_CLASS:
                skipped.append(ctx.labels[cid])
                continue
            base = f"From {source_name}"
            name, n = base, 2
            while any(d.name.strip().lower() == name.strip().lower() for d in mine):
                name = f"{base} ({n})"
                n += 1
            new_id = uuid.uuid4()
            await conn.execute(
                "insert into public.timetable_drafts (id, school_id, term_id, semester_id, name, note, is_primary, created_by) "
                "values ($1, $2, $3, $4, $5, $6, $7, $8)",
                new_id, sid, term["id"], cid, name, f"Copied from {source_name} ('{old['name']}')", not mine, uuid.UUID(user.id),
            )
            remap: dict[Any, uuid.UUID] = {}
            for e in by_draft.get(old["id"], []):
                sub = ctx.subjects.get(e["subject_id"])
                if sub is None or sub.semester_id != cid:
                    continue  # the subject was deleted since that term
                pk = remap.setdefault(e["parallel_key"], uuid.uuid4()) if e["parallel_key"] else None
                await conn.execute(_INSERT, uuid.uuid4(), new_id, sid, cid, e["subject_id"], e["day"], e["start_period"], e["length"], pk, uuid.UUID(user.id), e["is_locked"])
            created.append({"semester_id": str(cid), "class_label": ctx.labels[cid], "draft_id": str(new_id), "name": name})
        if created:
            await audit.record(conn, user, "create", "timetable_draft", term["id"],
                               f"Copied {len(created)} primary draft(s) of {source_name} into {term_svc.term_name(term['kind'], term['year'])} as a starting base",
                               school_id=sid)
    if not created and not old_drafts:
        raise ApiError(404, "NOT_FOUND", f"{source_name} has no primary drafts for this school to copy.")
    return {"created": created, "skipped": skipped, "source": source_name}


# ---------------------------------------------------------------------------------------------------- one draft
@router.get("/{draft_id}")
async def get_draft(draft_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    return draft_detail(L, row["id"])


def _unit_ids(ctx: sch.Ctx, sem_id: uuid.UUID, subject_id: str | None, group_id: str | None) -> list[uuid.UUID]:
    if bool(subject_id) == bool(group_id):
        raise ApiError(422, "VALIDATION_ERROR", "Choose either one subject or one elective group.")
    if group_id:
        gid = as_uuid(group_id, "elective group")
        members = [s for s in ctx.subjects_by_class.get(sem_id, []) if s.group_id == gid]
        if not members:
            raise ApiError(404, "NOT_FOUND", "That elective group was not found in this class.")
        return [s.id for s in sorted(members, key=lambda s: s.name)]
    s = ctx.subjects.get(as_uuid(subject_id, "subject"))
    if s is None or s.semester_id != sem_id:
        raise ApiError(404, "NOT_FOUND", "That subject was not found in this class.")
    if s.group_id:
        raise ApiError(
            422, "GROUP_ONLY",
            f"'{s.name}' belongs to the elective group '{s.group_name}'. Place the whole group: all its options run at the same time.",
        )
    return [s.id]


class OptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str | None = None
    group_id: str | None = None
    placement_key: str | None = None  # an existing placement that is being moved / swapped


@router.post("/{draft_id}/options")
async def options(draft_id: str, body: OptionsIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """For the subject / group / placement that is picked up: the state of every cell of the week."""
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    if body.placement_key:
        key = as_uuid(body.placement_key, "placement")
        units = sch.units_of(ctx.entries_by_draft.get(d.id, []))
        if key not in units:
            raise ApiError(404, "NOT_FOUND", "That placement was not found in this draft.")
        rows = units[key]
        ids = [e.subject_id for e in rows]
        cells = sch.options_map(ctx, draft_id=d.id, sem_id=d.semester_id, subject_ids=ids, ignore={key}, me=key, length=rows[0].length)
        swaps = {}
        for other_key, other in units.items():
            if other_key != key:
                r = sch.swap_check(ctx, d.id, d.semester_id, rows, other)
                swaps[str(other_key)] = {"ok": r.ok, "reasons": _msgs(r.hard), "warnings": _msgs(r.soft)}
        length = rows[0].length
    else:
        ids = _unit_ids(ctx, d.semester_id, body.subject_id, body.group_id)
        cells = sch.options_map(ctx, draft_id=d.id, sem_id=d.semester_id, subject_ids=ids)
        swaps = {}
        length = ctx.derived_length(ctx.subjects[ids[0]])
    return {
        "unit": {"subject_ids": [str(i) for i in ids], "names": [ctx.subjects[i].name for i in ids], "length": length},
        "cells": cells, "swaps": swaps,
    }


class PlaceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str | None = None
    group_id: str | None = None
    day: int = Field(ge=1, le=6)
    start_period: int = Field(ge=1, le=30)


def _names(ctx: sch.Ctx, ids) -> str:
    return " ∥ ".join(ctx.subjects[i].name for i in ids)


async def _touch(conn, draft_id) -> None:
    await conn.execute("update public.timetable_drafts set updated_at = now() where id = $1", draft_id)


@router.post("/{draft_id}/placements", status_code=201)
async def place(draft_id: str, body: PlaceIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    ids = _unit_ids(ctx, d.semester_id, body.subject_id, body.group_id)
    res = sch.check_unit(ctx, draft_id=d.id, sem_id=d.semester_id, subject_ids=ids, day=body.day, start=body.start_period)
    if res.hard:
        raise _blocked(res)
    key = uuid.uuid4()
    parallel = key if len(ids) > 1 else None
    new = [
        {"id": key if (len(ids) == 1 and i == 0) else uuid.uuid4(), "draft_id": d.id, "semester_id": d.semester_id, "subject_id": sid,
         "day": body.day, "start": body.start_period, "length": res.length, "parallel_key": parallel}
        for i, sid in enumerate(ids)
    ]
    async with db.transaction() as conn:
        for r in new:
            await conn.execute(_INSERT, r["id"], d.id, row["school_id"], d.semester_id, r["subject_id"], r["day"], r["start"], r["length"], r["parallel_key"], uuid.UUID(user.id), False)
        await _touch(conn, d.id)
        await audit.record(
            conn, user, "create", "draft_placement", key,
            f"{ctx.labels[d.semester_id]}, draft '{d.name}': placed {_names(ctx, ids)} on {DAY_NAMES[body.day]} {sch.span(body.start_period, res.length)}",
            school_id=row["school_id"],
        )
    detail = _detail_after(L, [*ctx.entries, *[_entry_row(r) for r in new]], d.id)
    return {"key": str(new[0]["parallel_key"] or new[0]["id"]), "warnings": _msgs(res.soft), "detail": detail}


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=6)
    start_period: int = Field(ge=1, le=30)


def _unit_rows(ctx: sch.Ctx, draft_id: uuid.UUID, key_text: str) -> list[sch.Entry]:
    key = as_uuid(key_text, "placement")
    rows = sch.units_of(ctx.entries_by_draft.get(draft_id, [])).get(key)
    if not rows:
        raise ApiError(404, "NOT_FOUND", "That placement was not found in this draft.")
    return rows


@router.patch("/{draft_id}/placements/{key}")
async def move(draft_id: str, key: str, body: MoveIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    rows = _unit_rows(ctx, d.id, key)
    if rows[0].is_locked:
        raise ApiError(409, "LOCKED", "This placement is pinned (semi-automatic mode). Unpin it first, then move it.")
    k = rows[0].key
    res = sch.check_unit(
        ctx, draft_id=d.id, sem_id=d.semester_id, subject_ids=[e.subject_id for e in rows], day=body.day, start=body.start_period,
        ignore={k}, me=k, length=rows[0].length,
    )
    if res.hard:
        raise _blocked(res)
    old = rows[0]
    async with db.transaction() as conn:
        await conn.execute("update public.draft_entries set day = $3, start_period = $4 where draft_id = $1 and coalesce(parallel_key, id) = $2", d.id, k, body.day, body.start_period)
        await _touch(conn, d.id)
        await audit.record(
            conn, user, "update", "draft_placement", k,
            f"{ctx.labels[d.semester_id]}, draft '{d.name}': moved {_names(ctx, [e.subject_id for e in rows])} from {DAY_NAMES[old.day]} {sch.span(old.start, old.length)} "
            f"to {DAY_NAMES[body.day]} {sch.span(body.start_period, old.length)}",
            school_id=row["school_id"],
        )
    moved = [replace(e, day=body.day, start=body.start_period) if e.key == k else e for e in ctx.entries]
    return {"warnings": _msgs(res.soft), "detail": _detail_after(L, moved, d.id)}


class SwapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placement_a: str
    placement_b: str


@router.post("/{draft_id}/swap")
async def swap(draft_id: str, body: SwapIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    a, b = _unit_rows(ctx, d.id, body.placement_a), _unit_rows(ctx, d.id, body.placement_b)
    if a[0].is_locked or b[0].is_locked:
        raise ApiError(409, "LOCKED", "A pinned placement cannot be swapped. Unpin it first.")
    if a[0].key == b[0].key:
        raise ApiError(422, "VALIDATION_ERROR", "Choose two different placements.")
    res = sch.swap_check(ctx, d.id, d.semester_id, a, b)
    if res.hard:
        raise _blocked(res)
    ka, kb = a[0].key, b[0].key
    pa, pb = (a[0].day, a[0].start), (b[0].day, b[0].start)
    async with db.transaction() as conn:
        sql = "update public.draft_entries set day = $3, start_period = $4 where draft_id = $1 and coalesce(parallel_key, id) = $2"
        await conn.execute(sql, d.id, ka, 6, 29)  # park A (the database never allows two lectures at one time, not even for a moment)
        await conn.execute(sql, d.id, kb, pa[0], pa[1])
        await conn.execute(sql, d.id, ka, pb[0], pb[1])
        await _touch(conn, d.id)
        await audit.record(
            conn, user, "update", "draft_placement", ka,
            f"{ctx.labels[d.semester_id]}, draft '{d.name}': swapped {_names(ctx, [e.subject_id for e in a])} and {_names(ctx, [e.subject_id for e in b])}",
            school_id=row["school_id"],
        )
    swapped = [
        replace(e, day=pb[0], start=pb[1]) if e.key == ka else replace(e, day=pa[0], start=pa[1]) if e.key == kb else e for e in ctx.entries
    ]
    return {"warnings": _msgs(res.soft), "detail": _detail_after(L, swapped, d.id)}


class LockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locked: bool


@router.post("/{draft_id}/placements/{key}/lock")
async def set_lock(draft_id: str, key: str, body: LockIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    """Pin (or unpin) a placement: a pinned one cannot be moved or removed, and the generator keeps it (semi-automatic mode)."""
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    rows = _unit_rows(ctx, d.id, key)
    k = rows[0].key
    if rows[0].is_locked == body.locked:
        return {"locked": body.locked, "detail": draft_detail(L, d.id)}
    async with db.transaction() as conn:
        await conn.execute("update public.draft_entries set is_locked = $3 where draft_id = $1 and coalesce(parallel_key, id) = $2", d.id, k, body.locked)
        await _touch(conn, d.id)
        await audit.record(
            conn, user, "update", "draft_placement", k,
            f"{ctx.labels[d.semester_id]}, draft '{d.name}': {'pinned' if body.locked else 'unpinned'} {_names(ctx, [e.subject_id for e in rows])} "
            f"({DAY_NAMES[rows[0].day]} {sch.span(rows[0].start, rows[0].length)})",
            school_id=row["school_id"],
        )
    locked_entries = [replace(e, is_locked=body.locked) if e.key == k and e.draft_id == d.id else e for e in ctx.entries]
    return {"locked": body.locked, "detail": _detail_after(L, locked_entries, d.id)}


@router.delete("/{draft_id}/placements/{key}")
async def remove(draft_id: str, key: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    rows = _unit_rows(ctx, d.id, key)
    if rows[0].is_locked:
        raise ApiError(409, "LOCKED", "This placement is pinned (semi-automatic mode). Unpin it first, then remove it.")
    k = rows[0].key
    async with db.transaction() as conn:
        await conn.execute("delete from public.draft_entries where draft_id = $1 and coalesce(parallel_key, id) = $2", d.id, k)
        await _touch(conn, d.id)
        await audit.record(
            conn, user, "delete", "draft_placement", k,
            f"{ctx.labels[d.semester_id]}, draft '{d.name}': removed {_names(ctx, [e.subject_id for e in rows])} ({DAY_NAMES[rows[0].day]} {sch.span(rows[0].start, rows[0].length)})",
            school_id=row["school_id"],
        )
    return {"detail": _detail_after(L, [e for e in ctx.entries if e.key != k or e.draft_id != d.id], d.id)}


@router.post("/{draft_id}/clear")
async def clear(draft_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row, term = await _draft(user, draft_id)
    L = await load(row["school_id"], term)
    ctx = L.ctx
    d = ctx.drafts[row["id"]]
    units = sch.units_of(ctx.entries_by_draft.get(d.id, []))
    kept = sum(1 for rows_ in units.values() if rows_[0].is_locked)
    count = len(units) - kept
    async with db.transaction() as conn:
        await conn.execute("delete from public.draft_entries where draft_id = $1 and not is_locked", d.id)
        await _touch(conn, d.id)
        await audit.record(conn, user, "delete", "draft_placement", d.id,
                           f"{ctx.labels[d.semester_id]}, draft '{d.name}': cleared {count} placements" + (f" ({kept} pinned ones kept)" if kept else ""),
                           school_id=row["school_id"])
    return {"cleared": count, "kept": kept, "detail": _detail_after(L, [e for e in ctx.entries if e.draft_id != d.id or e.is_locked], d.id)}


# ---------------------------------------------------------------------------------------------------- the conflict report
@conflicts_router.get("")
async def conflict_report(
    school_id: str | None = None,
    semester_id: str | None = None,
    scope: str = "primary",
    user: CurrentUser = Depends(require_roles(*ROLES)),
) -> dict:
    """Every clash, every lecture still missing and every warning of the drafts of the active term.
    scope=primary: only the primary draft of each class (what would be published); scope=all: every draft."""
    if scope not in ("primary", "all"):
        raise ApiError(422, "VALIDATION_ERROR", "scope must be 'primary' or 'all'.")
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"active_term": None, "items": [], "counts": {"clashes": 0, "missing": 0, "warnings": 0}, "classes": []}
    L = await load(sid, term)
    ctx = L.ctx
    only = as_uuid(semester_id, "class") if semester_id else None
    if only is not None and only not in ctx.labels:
        raise ApiError(404, "NOT_FOUND", "That class was not found in the active term.")
    items: list[dict[str, Any]] = []

    def base(d: sch.DraftInfo | None, sem_id: uuid.UUID) -> dict[str, Any]:
        return {
            "semester_id": str(sem_id), "class_label": ctx.labels[sem_id],
            "draft_id": str(d.id) if d else None, "draft_name": d.name if d else None, "is_primary": bool(d and d.is_primary),
        }

    for sm in L.sems:
        sem_id = sm["id"]
        if only is not None and sem_id != only:
            continue
        drafts = [d for d in ctx.drafts.values() if d.semester_id == sem_id and (scope == "all" or d.is_primary)]
        if not any(d.semester_id == sem_id for d in ctx.drafts.values()):
            items.append({**base(None, sem_id), "kind": "missing", "code": "NO_DRAFT", "message": f"No timetable draft has been started for {ctx.labels[sem_id]} yet.",
                          "day": None, "start_period": None, "length": None, "placement_key": None, "subjects": None})
            continue
        for d in sorted(drafts, key=lambda d: (not d.is_primary, d.name.lower())):
            a = sch.analyze_draft(ctx, d.id)
            for c in a["clashes"]:
                items.append({**base(d, sem_id), "kind": "clash", "code": c["code"], "message": c["message"], "day": c["day"], "start_period": c["start_period"],
                              "length": c["length"], "placement_key": c["placement_key"], "subjects": c["subjects"]})
            for m in a["missing"]:
                short = m["required"] - m["placed"]
                if m["status"] == "over":
                    msg = f"'{m['label']}' has {m['placed']} placed but needs only {m['required']}."
                else:
                    msg = f"'{m['label']}': {m['placed']} of {m['required']} placed ({short} missing)."
                items.append({**base(d, sem_id), "kind": "missing" if m["status"] != "over" else "warning", "code": "OVER_REQUIRED" if m["status"] == "over" else "UNDER_PLACED",
                              "message": msg, "day": None, "start_period": None, "length": None, "placement_key": None, "subjects": m["label"]})
            seen: set[tuple[str, str]] = set()
            for w in a["warnings"]:
                if (w["code"], w["message"]) in seen:
                    continue
                seen.add((w["code"], w["message"]))
                items.append({**base(d, sem_id), "kind": "warning", "code": w["code"], "message": w["message"], "day": w["day"], "start_period": w["start_period"],
                              "length": w["length"], "placement_key": w["placement_key"], "subjects": w["subjects"]})
    order = {"clash": 0, "missing": 1, "warning": 2}
    items.sort(key=lambda i: (order[i["kind"]], i["class_label"], not i["is_primary"], i["draft_name"] or "", i["day"] or 0, i["start_period"] or 0))
    for n, i in enumerate(items):
        i["day_name"] = DAY_NAMES.get(i["day"]) if i["day"] else None
        i["id"] = str(n)
    counts = {k: sum(1 for i in items if i["kind"] == kind) for k, kind in (("clashes", "clash"), ("missing", "missing"), ("warnings", "warning"))}
    return {
        "active_term": term_svc.term_out(term), "scope": scope, "items": items, "counts": counts,
        "classes": [{"semester_id": str(s["id"]), "label": ctx.labels[s["id"]]} for s in L.sems],
    }
