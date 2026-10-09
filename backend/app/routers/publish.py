"""Publishing the timetable (plan Section 19, Phase 4).

  GET  /api/publish/status    per class: its primary draft, what is published (version), and whether they differ
  POST /api/publish           publish one class or all classes of the active term (HOD: own school, admin: any)
  GET  /api/publish/events    the log of publish clicks

Publishing copies each class's PRIMARY draft (plus the locked slots and the teachers of that moment) into the live tables
that teachers read. Teachers never read drafts. Re-publishing replaces the snapshot, raises the version, writes the audit
log and notifies ONLY the teachers whose own lectures changed - one notification per person per publish click.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import notify
from ..services import publishing as pub
from ..services import schedule as sch
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..validation import DAY_SHORT
from . import drafts as drafts_router

router = APIRouter(prefix="/api/publish", tags=["publish"])
ROLES = ("admin", "hod")

ROW_COLUMNS = (
    "id, semester_id, kind, subject_id, fixed_kind, label, day, start_period, length, group_key, teacher_id, co_teacher_id, combined_with"
)


# ---------------------------------------------------------------------------------------------------- loading
async def fetch_rows(conn: Any, school_id: uuid.UUID, term_id: uuid.UUID, sem_ids: list[uuid.UUID] | None = None) -> list[pub.PubRow]:
    sql = f"select {ROW_COLUMNS} from public.published_entries where school_id = $1 and term_id = $2"
    args: list[Any] = [school_id, term_id]
    if sem_ids is not None:
        sql += " and semester_id = any($3::uuid[])"
        args.append(sem_ids)
    return [pub.row_from_record(r) for r in await conn.fetch(sql, *args)]


async def load_published(school_id: uuid.UUID, term_id: uuid.UUID) -> tuple[dict[uuid.UUID, asyncpg.Record], dict[uuid.UUID, list[pub.PubRow]]]:
    pubs = await db.fetch(
        "select p.*, u.full_name as published_by_name from public.publications p left join public.profiles u on u.id = p.published_by "
        "where p.school_id = $1 and p.term_id = $2",
        school_id, term_id,
    )
    rows = await fetch_rows(db, school_id, term_id)
    by_sem: dict[uuid.UUID, list[pub.PubRow]] = {}
    for r in rows:
        by_sem.setdefault(r.semester_id, []).append(r)
    return {p["semester_id"]: p for p in pubs}, by_sem


# ---------------------------------------------------------------------------------------------------- planning
@dataclass
class ClassPlan:
    sem: asyncpg.Record
    label: str
    primary: sch.DraftInfo | None
    publication: asyncpg.Record | None
    old_rows: list[pub.PubRow]
    new_rows: list[pub.PubRow] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    completeness: dict[str, Any] = field(default_factory=dict)
    clash_count: int = 0
    state: str = "no_draft"
    added: list[pub.PubRow] = field(default_factory=list)
    removed: list[pub.PubRow] = field(default_factory=list)

    @property
    def sem_id(self) -> uuid.UUID:
        return self.sem["id"]


def make_plans(L: drafts_router.Loaded, pubs: dict[uuid.UUID, asyncpg.Record], old: dict[uuid.UUID, list[pub.PubRow]]) -> dict[uuid.UUID, ClassPlan]:
    ctx = L.ctx
    plans: dict[uuid.UUID, ClassPlan] = {}
    for sem in L.sems:
        sid = sem["id"]
        label = ctx.labels[sid]
        primary = next((d for d in ctx.drafts.values() if d.semester_id == sid and d.is_primary), None)
        plan = ClassPlan(sem, label, primary, pubs.get(sid), old.get(sid, []))
        if primary is not None:
            analysis = sch.analyze_draft(ctx, primary.id)
            messages = list(dict.fromkeys(c["message"] for c in analysis["clashes"]))
            plan.clash_count = len(messages)
            plan.blockers = messages
            plan.completeness = {k: analysis["completeness"][k] for k in ("required", "placed", "percent", "missing")}
            plan.new_rows = pub.snapshot_class(ctx, primary.id, sid)
            if not ctx.entries_by_draft.get(primary.id):
                plan.blockers.insert(0, f"The primary draft '{primary.name}' is empty - there is nothing to publish.")
            plan.added, plan.removed = pub.diff_rows(plan.old_rows, plan.new_rows)
            if plan.publication is None:
                plan.state = "not_published"
            else:
                plan.state = "in_sync" if not plan.added and not plan.removed else "changed"
        elif plan.publication is not None:
            plan.state = "draft_removed"
        plans[sid] = plan
    return plans


def resolve_publishable(
    plans: dict[uuid.UUID, ClassPlan], wanted: list[uuid.UUID], labels: dict[uuid.UUID, str], names: dict[uuid.UUID, str]
) -> tuple[list[uuid.UUID], dict[uuid.UUID, list[str]]]:
    """Which of the wanted classes can really go live?  A class with a clash in its own draft cannot; neither can a class
    whose new teacher times would clash with the (older) published timetable of a class that is NOT published now."""
    blocked: dict[uuid.UUID, list[str]] = {}
    ok: list[uuid.UUID] = []
    for sid in wanted:
        p = plans[sid]
        if p.primary is None:
            blocked[sid] = ["This class has no primary draft yet (Generate Timetable)."]
        elif p.blockers:
            blocked[sid] = list(p.blockers)
        elif p.state == "in_sync":
            blocked[sid] = ["Already published and up to date."]
        else:
            ok.append(sid)
    while ok:
        state_rows: list[pub.PubRow] = []
        for sid, p in plans.items():
            state_rows += p.new_rows if sid in ok else p.old_rows
        dropped: set[uuid.UUID] = set()
        for clash in pub.teacher_clashes(state_rows, labels, names):
            involved = clash["semesters"] & set(ok)
            if not involved:
                continue
            for sid in involved:
                others = [labels[s] for s in clash["semesters"] if s != sid]
                blocked.setdefault(sid, []).append(
                    clash["message"] + (f" Publish {', '.join(others)} together with this class, or fix the clash first." if others else "")
                )
                dropped.add(sid)
        if not dropped:
            break
        ok = [s for s in ok if s not in dropped]
    return ok, blocked


# ---------------------------------------------------------------------------------------------------- reading
def _texts(rows: list[pub.PubRow], plan: ClassPlan, grid, limit: int = 12) -> list[str]:
    return [pub.describe_row(r, plan.label, timing_svc.time_range(grid, r.start, r.length)) for r in rows[:limit]]


def class_status(plan: ClassPlan, grid) -> dict[str, Any]:
    p = plan.publication
    return {
        "semester_id": str(plan.sem_id), "class_label": plan.label,
        "primary": {"id": str(plan.primary.id), "name": plan.primary.name} if plan.primary else None,
        "published": None if p is None else {
            "version": p["version"], "published_at": p["published_at"].isoformat(), "published_by": p["published_by_name"],
            "source_draft_name": p["source_draft_name"], "rows": len(plan.old_rows),
            "lectures": sum(1 for r in plan.old_rows if r.kind == "lecture"),
        },
        "state": plan.state,
        "changes": {
            "added_count": len(plan.added), "removed_count": len(plan.removed),
            "added": _texts(plan.added, plan, grid), "removed": _texts(plan.removed, plan, grid),
        } if plan.state in ("changed", "not_published") else None,
        "blockers": plan.blockers[:6], "blocker_count": len(plan.blockers),
        "completeness": plan.completeness or None,
        "can_publish": plan.primary is not None and not plan.blockers and plan.state in ("not_published", "changed"),
    }


@router.get("/status")
async def status(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"active_term": None, "classes": [], "summary": {}}
    L = await drafts_router.load(sid, term)
    pubs, old = await load_published(sid, term["id"])
    plans = make_plans(L, pubs, old)
    classes = [class_status(p, L.ctx.grid) for p in plans.values()]
    summary = {k: sum(1 for c in classes if c["state"] == k) for k in ("no_draft", "not_published", "in_sync", "changed", "draft_removed")}
    summary["unpublished"] = summary["not_published"] + summary["changed"]
    summary["can_publish"] = sum(1 for c in classes if c["can_publish"])
    return {"active_term": term_svc.term_out(term), "classes": classes, "summary": summary}


@router.get("/events")
async def events(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"events": []}
    rows = await db.fetch(
        "select e.*, u.full_name as by_name from public.publish_events e left join public.profiles u on u.id = e.published_by "
        "where e.school_id = $1 and e.term_id = $2 order by e.number desc limit 30",
        sid, term["id"],
    )
    out = []
    for r in rows:
        s = r["summary"]
        s = json.loads(s) if isinstance(s, str) else s
        out.append({
            "id": str(r["id"]), "number": r["number"], "scope": r["scope"], "published_at": r["published_at"].isoformat(),
            "published_by": r["by_name"], "notified": r["notified"], "classes": s.get("classes", []),
        })
    return {"events": out}


# ---------------------------------------------------------------------------------------------------- publishing
class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all", "class"] = "all"
    semester_id: str | None = None
    school_id: str | None = None


def _lecture_word(n: int) -> str:
    return "1 lecture" if n == 1 else f"{n} lectures"


def build_notices(
    *, term_name: str, event_id: uuid.UUID, school_id: uuid.UUID, changes: dict[uuid.UUID, pub.TeacherChange],
    labels: dict[uuid.UUID, str], grid,
) -> list[notify.Notice]:
    """One notification per teacher whose own lectures changed (the dedupe key makes a second one impossible)."""

    def when(r: pub.PubRow) -> str:
        span = f"{r.start}" if r.length == 1 else f"{r.start}-{r.start + r.length - 1}"
        return f"{DAY_SHORT.get(r.day, r.day)} period {span} ({timing_svc.time_range(grid, r.start, r.length)})"

    def what(rows: list[pub.PubRow]) -> str:
        return f"{rows[0].label} ({', '.join(sorted({labels.get(x.semester_id, 'a class') for x in rows}))})"

    notices: list[notify.Notice] = []
    for tid, ch in changes.items():
        fresh = ch.first_time and not ch.removed and not ch.moved
        if fresh:
            title = "Your timetable is published"
            body = f"{term_name}: you teach {_lecture_word(ch.total_after)} a week. Open My Timetable to see them."
        else:
            bits = []
            if ch.added:
                bits.append(f"{len(ch.added)} added")
            if ch.moved:
                bits.append(f"{len(ch.moved)} moved")
            if ch.removed:
                bits.append(f"{len(ch.removed)} removed")
            if ch.moved:
                a, b = ch.moved[0]
                example = f"{what(b)} moved from {when(a[0])} to {when(b[0])}."
            elif ch.added:
                example = f"{what(ch.added[0])} added on {when(ch.added[0][0])}."
            else:
                example = f"{what(ch.removed[0])} on {when(ch.removed[0][0])} was removed."
            title = "Your timetable has changed"
            body = f"{term_name}: {', '.join(bits)} - {_lecture_word(ch.total_after)} a week now. {example}"
        data = {
            "event": str(event_id), "term": term_name, "total": ch.total_after,
            "added": [f"{what(x)}, {when(x[0])}" for x in ch.added[:8]],
            "moved": [{"what": what(b), "from": when(a[0]), "to": when(b[0])} for a, b in ch.moved[:8]],
            "removed": [f"{what(x)}, {when(x[0])}" for x in ch.removed[:8]],
        }
        notices.append(notify.Notice(
            tid, "timetable_published" if fresh else "timetable_changed", title, body, "/my/timetable", data, school_id,
            f"publish:{event_id}",
        ))
    return notices


@router.post("")
async def publish(body: PublishIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, body.school_id)
    term = await term_svc.require_active_term()
    L = await drafts_router.load(sid, term)
    ctx = L.ctx
    pubs, old = await load_published(sid, term["id"])
    plans = make_plans(L, pubs, old)

    if body.scope == "class":
        if not body.semester_id:
            raise ApiError(422, "VALIDATION_ERROR", "Please choose the class to publish.", extra={"fields": {"semester_id": "Required."}})
        one = as_uuid(body.semester_id, "class")
        if one not in plans:
            raise ApiError(404, "NOT_FOUND", "That class is not running in the active term.")
        wanted = [one]
    else:
        wanted = [s for s, p in plans.items() if p.primary is not None]
    names = {pid: p.name for pid, p in ctx.people.items()}
    ok, blocked = resolve_publishable(plans, wanted, ctx.labels, names)
    skipped = [
        {"semester_id": str(s), "class_label": ctx.labels[s], "reasons": r[:5], "up_to_date": r == ["Already published and up to date."]}
        for s, r in blocked.items()
    ]
    if not ok:
        only_current = bool(skipped) and all(x["up_to_date"] for x in skipped)
        if only_current:
            raise ApiError(409, "NOTHING_TO_PUBLISH", "Everything is already published and up to date.", extra={"skipped": skipped})
        raise ApiError(
            409, "PUBLISH_BLOCKED",
            "Nothing could be published: " + " ".join(f"{x['class_label']}: {x['reasons'][0]}" for x in skipped if not x["up_to_date"])[:600],
            extra={"skipped": skipped},
        )
    skipped = [x for x in skipped if not x["up_to_date"] or body.scope == "class"]
    if body.scope == "all":
        skipped += [
            {"semester_id": str(s), "class_label": p.label, "reasons": ["No primary draft yet."], "up_to_date": False}
            for s, p in plans.items() if p.primary is None
        ]

    sem_ids = ok
    term_name = term_svc.term_name(term["kind"], term["year"])
    sent: list[notify.Sent] = []
    report: list[dict[str, Any]] = []
    async with db.transaction() as conn:
        # two people clicking Publish at the same moment are served one after the other
        await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"publish:{sid}:{term['id']}")
        current = await fetch_rows(conn, sid, term["id"], sem_ids)  # re-read under the lock: this is what teachers see right now
        current_by_sem: dict[uuid.UUID, list[pub.PubRow]] = {}
        for r in current:
            current_by_sem.setdefault(r.semester_id, []).append(r)
        number = await conn.fetchval("select coalesce(max(number), 0) + 1 from public.publish_events where school_id = $1 and term_id = $2", sid, term["id"])
        event_id = uuid.uuid4()
        for s in sem_ids:
            p = plans[s]
            added, removed = pub.diff_rows(current_by_sem.get(s, []), p.new_rows)
            row = await conn.fetchrow(
                """
                insert into public.publications (school_id, term_id, semester_id, version, source_draft_id, source_draft_name, published_by)
                values ($1, $2, $3, 1, $4, $5, $6)
                on conflict (term_id, semester_id) do update
                   set version = public.publications.version + 1, source_draft_id = excluded.source_draft_id,
                       source_draft_name = excluded.source_draft_name, published_at = now(), published_by = excluded.published_by
                returning id, version
                """,
                sid, term["id"], s, p.primary.id, p.primary.name, uuid.UUID(user.id),
            )
            await conn.execute("delete from public.published_entries where publication_id = $1", row["id"])
            await conn.executemany(
                """
                insert into public.published_entries (id, publication_id, school_id, term_id, semester_id, kind, subject_id, fixed_kind, label,
                                                      day, start_period, length, group_key, teacher_id, co_teacher_id, combined_with)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                """,
                [
                    (r.id, row["id"], sid, term["id"], s, r.kind, r.subject_id, r.fixed_kind, r.label, r.day, r.start, r.length,
                     r.group_key, r.teacher_id, r.co_teacher_id, r.combined_with)
                    for r in p.new_rows
                ],
            )
            lectures = sum(1 for r in p.new_rows if r.kind == "lecture")
            await audit.record(
                conn, user, "publish", "publication", row["id"],
                f"Published {p.label} - version {row['version']} (draft '{p.primary.name}')", school_id=sid,
                details={"version": row["version"], "draft": p.primary.name, "lectures": lectures, "added": len(added), "removed": len(removed), "event": number},
            )
            report.append({
                "semester_id": str(s), "class_label": p.label, "version": row["version"], "lectures": lectures,
                "added": len(added), "removed": len(removed), "first": row["version"] == 1,
            })

        old_rows = [r for s in sem_ids for r in current_by_sem.get(s, [])]
        new_rows = [r for s in sem_ids for r in plans[s].new_rows]
        changes = pub.teacher_changes(old_rows, new_rows)
        final_rows = new_rows + [r for s, rows in old.items() if s not in sem_ids for r in rows]
        totals = {t: len(c) for t, c in pub.teacher_lectures(final_rows).items()}
        for tid, ch in changes.items():
            ch.total_after = totals.get(tid, 0)
        notices = build_notices(term_name=term_name, event_id=event_id, school_id=sid, changes=changes, labels=ctx.labels, grid=ctx.grid)
        sent = await notify.create(conn, notices)
        summary = {"classes": report, "teachers_changed": len(changes)}
        await conn.execute(
            "insert into public.publish_events (id, school_id, term_id, number, scope, published_by, summary, notified) "
            "values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)",
            event_id, sid, term["id"], number, body.scope, uuid.UUID(user.id), json.dumps(summary), len(sent),
        )
        await audit.record(
            conn, user, "publish", "publish_event", event_id,
            f"Publish #{number}: {len(report)} class(es), {len(sent)} teacher(s) notified", school_id=sid,
            details={"classes": [r["class_label"] for r in report], "notified": len(sent)},
        )
    await notify.deliver(sent)
    return {
        "event": {"id": str(event_id), "number": number, "scope": body.scope},
        "published": report, "skipped": skipped, "notified": len(sent), "teachers_changed": len(changes),
        "message": f"Published {len(report)} class(es). {len(sent)} teacher(s) were notified.",
    }
