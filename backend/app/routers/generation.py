"""Phase 3: the automatic generation runs (plan Sections 10, 11, 19).

  GET    /api/generation/weights            the school's soft-rule weights (0-100) with labels + defaults
  PUT    /api/generation/weights            save them (admin / HOD of the school)
  POST   /api/generation/runs               start a run: ALL classes of the active term together, in the background
  GET    /api/generation/runs               the latest runs with their scored solutions
  GET    /api/generation/runs/{id}          live progress of one run (polled by the screen) + its solutions
  POST   /api/generation/runs/{id}/cancel   stop a running run
  DELETE /api/generation/runs/{id}          discard a run and the drafts of its solutions (refused while one is primary)
  POST   /api/generation/solutions/{id}/use make every draft of that solution the primary draft of its class

The engine itself lives in services/generator.py; validity is ALWAYS re-checked with the Phase 2 clash engine before a
solution is saved, and each solution becomes ordinary drafts - fully editable in the existing grid editor.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db, readcache
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import generator as gen
from ..services import terms as term_svc
from .drafts import _INSERT, load

router = APIRouter(prefix="/api/generation", tags=["generation"])
ROLES = ("admin", "hod")
log = logging.getLogger("tt.generation")

# the running jobs of THIS server process: run id -> live progress + the cancel flag
JOBS: dict[str, SimpleNamespace] = {}


# ---------------------------------------------------------------------------------------------------- weights
@router.get("/weights")
async def get_weights(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    row = await db.fetchrow("select weights from public.generation_settings where school_id = $1", sid)
    weights = gen.clean_weights(_jload(row["weights"]) if row else None)
    return {
        "weights": weights,
        "rules": [{"name": name, "label": label, "default": default} for name, (label, default) in gen.RULES.items()],
    }


class WeightsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    weights: dict[str, int]


@router.put("/weights")
async def save_weights(body: WeightsIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, body.school_id)
    unknown = set(body.weights) - set(gen.RULES)
    bad = {k: v for k, v in body.weights.items() if not (0 <= v <= 100)}
    if unknown or bad:
        raise ApiError(422, "VALIDATION_ERROR", "Every weight must be a known rule with a value from 0 to 100.",
                       extra={"fields": {k: "0 to 100" for k in (unknown | set(bad))}})
    weights = gen.clean_weights(body.weights)
    async with db.transaction() as conn:
        await conn.execute(
            "insert into public.generation_settings (school_id, weights, updated_by) values ($1, $2::jsonb, $3) "
            "on conflict (school_id) do update set weights = excluded.weights, updated_by = excluded.updated_by",
            sid, _json(weights), uuid.UUID(user.id),
        )
        await audit.record(conn, user, "update", "generation_weights", sid,
                           "Changed the soft-rule weights: " + ", ".join(f"{gen.RULES[k][0]} = {v}" for k, v in weights.items()),
                           school_id=sid)
    return {"weights": weights}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _jload(value: Any) -> Any:
    """asyncpg hands jsonb back as text unless a codec is set - accept both."""
    import json

    return json.loads(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------------------------------- start a run
class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str | None = None
    name: str | None = Field(default=None, max_length=80)
    mode: Literal["fresh", "keep_locked"] = "fresh"
    time_limit_s: int = Field(default=90, ge=10, le=600)
    weights: dict[str, int] | None = None     # one-off override; the saved weights stay as they are


@router.post("/runs", status_code=201)
async def start_run(body: RunIn, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, body.school_id)
    term = await term_svc.require_active_term()
    busy = await db.fetchval("select id from public.generation_runs where school_id = $1 and status = 'running'", sid)
    if busy is not None and str(busy) in JOBS:
        raise ApiError(409, "RUN_ACTIVE", "A generation run is already working for this school. Wait for it or cancel it first.")
    if busy is not None:  # a run the process lost (crash / restart): close it honestly
        await db.execute("update public.generation_runs set status = 'failed', error = 'The server restarted while this run was working.', finished_at = now() where id = $1", busy)

    L = await load(sid, term)
    if not L.sems:
        raise ApiError(409, "NOTHING_TO_DO", "No class runs in the active term, so there is nothing to generate.")
    locked_by_class: dict[uuid.UUID, list] = {}
    if body.mode == "keep_locked":
        primary = {d.semester_id: d.id for d in L.ctx.drafts.values() if d.is_primary}
        for cid, draft_id in primary.items():
            pinned = [e for e in L.ctx.entries_by_draft.get(draft_id, []) if e.is_locked]
            if pinned:
                locked_by_class[cid] = pinned
    problem = gen.build_problem(L.ctx, locked_by_class=locked_by_class)
    findings = gen.precheck(problem)
    if body.weights is not None:
        weights = gen.clean_weights(body.weights)
    else:
        row = await db.fetchrow("select weights from public.generation_settings where school_id = $1", sid)
        weights = gen.clean_weights(_jload(row["weights"]) if row else None)

    run_id = uuid.uuid4()
    name = (body.name or "").strip() or f"Run {datetime.now().strftime('%d %b %H:%M')}"
    async with db.transaction() as conn:
        await conn.execute(
            "insert into public.generation_runs (id, school_id, term_id, name, mode, status, progress, message, weights, precheck, created_by) "
            "values ($1, $2, $3, $4, $5, 'running', 1, 'Starting...', $6::jsonb, $7::jsonb, $8)",
            run_id, sid, term["id"], name, body.mode, _json(weights), _json(findings), uuid.UUID(user.id),
        )
        await audit.record(conn, user, "create", "generation_run", run_id,
                           f"Started timetable generation '{name}' ({len(L.sems)} classes, mode: "
                           f"{'keep pinned placements' if body.mode == 'keep_locked' else 'fresh'})", school_id=sid)
    job = SimpleNamespace(progress=1, message="Starting...", cancel=threading.Event(), school_id=str(sid), task=None)
    JOBS[str(run_id)] = job
    job.task = asyncio.get_running_loop().create_task(
        _run_job(run_id, sid, term, name, problem, weights, body.time_limit_s, L, user)
    )
    return {"id": str(run_id), "name": name, "precheck": findings, "classes": len(L.sems)}


async def _run_job(run_id, sid, term, name, problem, weights, time_limit, L, user: CurrentUser) -> None:
    job = JOBS[str(run_id)]

    def progress(pct: int, msg: str) -> None:
        job.progress, job.message = pct, msg

    try:
        solutions = await asyncio.to_thread(
            gen.solve, problem, weights,
            attempts=12, time_limit=float(time_limit), seed=int(run_id.int % 10_000) + 1,
            progress=progress, is_cancelled=job.cancel.is_set,
        )
        kept = []
        for sol in solutions:
            check = gen.verify_solution(L.ctx, problem, sol.state)
            if check["clashes"]:  # the engine is the law: a solution it refuses is never shown
                log.error("generation %s: a candidate broke a hard rule and was dropped: %s", run_id, check["clashes"][:2])
                continue
            kept.append((sol, check))
        if not kept:
            raise RuntimeError("No valid timetable came out of this run. See the findings above for what is blocking.")
        progress(96, "Saving the solutions as drafts...")
        taken: dict[uuid.UUID, set[str]] = {}
        for sm in L.sems:
            rows = await db.fetch("select name from public.timetable_drafts where term_id = $1 and semester_id = $2", term["id"], sm["id"])
            taken[sm["id"]] = {r["name"].strip().lower() for r in rows}
        async with db.transaction() as conn:
            for label, (sol, check) in zip("ABC", kept):
                solution_id = uuid.uuid4()
                breakdown = {
                    "rules": sol.breakdown, "classes": check["classes"], "unplaced": sol.unplaced,
                    "placed": sol.placed, "required": sol.required,
                }
                await conn.execute(
                    "insert into public.generation_solutions (id, run_id, school_id, label, score, breakdown) values ($1, $2, $3, $4, $5, $6::jsonb)",
                    solution_id, run_id, sid, label, sol.score, _json(breakdown),
                )
                draft_ids = {cid: uuid.uuid4() for cid in problem.classes}
                for cid, draft_id in draft_ids.items():
                    base = f"Auto {label} · {name}"[:60]
                    draft_name = base
                    n = 2
                    while draft_name.strip().lower() in taken[cid]:
                        draft_name = f"{base[:54]} ({n})"
                        n += 1
                    taken[cid].add(draft_name.strip().lower())
                    await conn.execute(
                        "insert into public.timetable_drafts (id, school_id, term_id, semester_id, name, note, is_primary, created_by, solution_id) "
                        "values ($1, $2, $3, $4, $5, $6, false, $7, $8)",
                        draft_id, sid, term["id"], cid, draft_name,
                        f"Made automatically · score {sol.score}", uuid.UUID(user.id), solution_id,
                    )
                for e in gen.solution_entries(problem, sol.state, draft_ids):
                    await conn.execute(_INSERT, e.id, e.draft_id, sid, e.semester_id, e.subject_id, e.day, e.start, e.length, e.parallel_key, uuid.UUID(user.id), e.is_locked)
            best = kept[0][0]
            summary = f"{len(kept)} solution(s); best score {best.score} with {best.placed} of {best.required} lectures placed"
            await conn.execute(
                "update public.generation_runs set status = 'done', progress = 100, message = $2, finished_at = now() where id = $1",
                run_id, summary,
            )
            await audit.record(conn, user, "update", "generation_run", run_id, f"Generation '{name}' finished: {summary}", school_id=sid)
    except gen.Cancelled:
        await db.execute("update public.generation_runs set status = 'cancelled', message = 'Stopped by you.', finished_at = now() where id = $1", run_id)
    except Exception as exc:  # noqa: BLE001 - a failed run must never take the app down
        log.exception("generation run %s failed", run_id)
        await db.execute(
            "update public.generation_runs set status = 'failed', error = $2, finished_at = now() where id = $1",
            run_id, str(exc)[:500],
        )
    finally:
        JOBS.pop(str(run_id), None)
        readcache.bump()  # the run saved drafts in the background (no change request): cached screens must re-read


# ---------------------------------------------------------------------------------------------------- read runs
async def _run_row(user: CurrentUser, run_id: str) -> asyncpg.Record:
    row = await db.fetchrow("select * from public.generation_runs where id = $1", as_uuid(run_id, "run"))
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That generation run was not found.")
    return row


async def _solutions_out(run_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not run_ids:
        return {}
    sols, drafts = await asyncio.gather(
        db.fetch("select * from public.generation_solutions where run_id = any($1::uuid[]) order by label", run_ids),
        db.fetch(
            "select d.id, d.solution_id, d.semester_id, d.name, d.is_primary from public.timetable_drafts d where d.solution_id is not null "
            "and d.solution_id in (select id from public.generation_solutions where run_id = any($1::uuid[]))",
            run_ids,
        ),
    )
    by_solution: dict[uuid.UUID, list] = {}
    for d in drafts:
        by_solution.setdefault(d["solution_id"], []).append(d)
    import json

    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for s in sols:
        mine = by_solution.get(s["id"], [])
        breakdown = _jload(s["breakdown"])
        out.setdefault(s["run_id"], []).append({
            "id": str(s["id"]), "label": s["label"], "score": float(s["score"]), "is_used": s["is_used"],
            "in_use": bool(mine) and all(d["is_primary"] for d in mine),
            "breakdown": breakdown,
            "complete": breakdown.get("placed") == breakdown.get("required"),
            "drafts": [{"draft_id": str(d["id"]), "semester_id": str(d["semester_id"]), "name": d["name"]} for d in mine],
        })
    return out


def _run_out(row: asyncpg.Record, solutions: list[dict[str, Any]]) -> dict[str, Any]:
    job = JOBS.get(str(row["id"]))
    return {
        "id": str(row["id"]), "name": row["name"], "mode": row["mode"], "status": row["status"],
        "progress": job.progress if job else row["progress"],
        "message": job.message if job else row["message"],
        "error": row["error"],
        "precheck": _jload(row["precheck"]),
        "weights": _jload(row["weights"]),
        "created_at": row["created_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "solutions": solutions,
    }


@router.get("/runs")
async def list_runs(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"active_term": None, "runs": []}
    rows = await db.fetch(
        "select * from public.generation_runs where school_id = $1 and term_id = $2 order by created_at desc limit 8", sid, term["id"]
    )
    sols = await _solutions_out([r["id"] for r in rows])
    return {"active_term": term_svc.term_out(term), "runs": [_run_out(r, sols.get(r["id"], [])) for r in rows]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row = await _run_row(user, run_id)
    sols = await _solutions_out([row["id"]])
    return _run_out(row, sols.get(row["id"], []))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row = await _run_row(user, run_id)
    job = JOBS.get(str(row["id"]))
    if row["status"] != "running":
        return {"status": row["status"]}
    if job is not None:
        job.cancel.set()
        job.message = "Stopping..."
    else:  # the process that ran it is gone
        await db.execute("update public.generation_runs set status = 'cancelled', message = 'Stopped.', finished_at = now() where id = $1", row["id"])
    async with db.transaction() as conn:
        await audit.record(conn, user, "update", "generation_run", row["id"], f"Cancelled generation '{row['name']}'", school_id=row["school_id"])
    return {"status": "cancelling" if job is not None else "cancelled"}


@router.delete("/runs/{run_id}")
async def discard_run(run_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    row = await _run_row(user, run_id)
    if row["status"] == "running":
        raise ApiError(409, "RUN_ACTIVE", "This run is still working. Cancel it first.")
    primary = await db.fetchval(
        "select count(*) from public.timetable_drafts d join public.generation_solutions s on s.id = d.solution_id "
        "where s.run_id = $1 and d.is_primary",
        row["id"],
    )
    if primary:
        raise ApiError(409, "IN_USE", "One of this run's drafts is the primary draft of its class. Make another draft primary first, or keep the run.")
    async with db.transaction() as conn:
        n = await conn.fetchval(
            "with gone as (delete from public.timetable_drafts where solution_id in "
            "(select id from public.generation_solutions where run_id = $1) returning 1) select count(*) from gone",
            row["id"],
        )
        await conn.execute("delete from public.generation_runs where id = $1", row["id"])
        await audit.record(conn, user, "delete", "generation_run", row["id"],
                           f"Discarded generation '{row['name']}' and its {n} draft(s)", school_id=row["school_id"])
    return {"deleted": True, "drafts_removed": n}


# ---------------------------------------------------------------------------------------------------- pick a solution
@router.post("/solutions/{solution_id}/use")
async def use_solution(solution_id: str, user: CurrentUser = Depends(require_roles(*ROLES))) -> dict:
    sol = await db.fetchrow("select * from public.generation_solutions where id = $1", as_uuid(solution_id, "solution"))
    if sol is None or (user.role != "admin" and str(sol["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That solution was not found.")
    run = await db.fetchrow("select * from public.generation_runs where id = $1", sol["run_id"])
    term = await term_svc.require_active_term()
    if run["term_id"] != term["id"]:
        raise ApiError(409, "TERM_NOT_ACTIVE", "This solution belongs to an old term and can only be looked at.")
    drafts = await db.fetch("select id, semester_id, name from public.timetable_drafts where solution_id = $1", sol["id"])
    if not drafts:
        raise ApiError(409, "IN_USE", "The drafts of this solution were deleted, so it cannot be used any more.")
    async with db.transaction() as conn:
        for d in drafts:
            await conn.execute(
                "update public.timetable_drafts set is_primary = false where term_id = $1 and semester_id = $2 and is_primary and id <> $3",
                term["id"], d["semester_id"], d["id"],
            )
            await conn.execute("update public.timetable_drafts set is_primary = true where id = $1", d["id"])
        await conn.execute("update public.generation_solutions set is_used = true where id = $1", sol["id"])
        await audit.record(conn, user, "update", "generation_solution", sol["id"],
                           f"Solution {sol['label']} of '{run['name']}' is now the timetable of all {len(drafts)} classes (score {sol['score']})",
                           school_id=sol["school_id"])
    return {"used": True, "classes": len(drafts)}
