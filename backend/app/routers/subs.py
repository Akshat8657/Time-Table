"""Substitutions (plan Section 13): the HOD console, candidate ranking, manual assign, auto popup-offers (first-accept-wins),
the teacher's accept / decline, the daily substitution sheet and the monthly summary.

One substitution = one absent teacher's one time-slot on one date.  Only genuinely FREE teachers are ever offered; the
first to accept wins (made safe by locking the substitution row).  Reuses the Phase 4 notification service for every message.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..config import get_settings
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import exporter as ex
from ..services import notify
from ..services import subflow
from ..services import substitution as subsvc
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services import views as vw
from ..services.changes import Sub, load_changes
from ..services.viewdata import ViewData, load_view_data

router = APIRouter(prefix="/api/subs", tags=["substitutions"])
MANAGERS = ("admin", "hod")
TEACHING = ("teacher", "hod")
MAX_OFFER = 8


# ---------------------------------------------------------------------------------------------------- helpers
def _sub_as_change(row: Any) -> Sub:
    return Sub(
        row["id"], row["absence_id"], row["lecture_date"], row["start_period"], row["length"],
        frozenset(row["semester_ids"] or []), frozenset(row["subject_ids"] or []), row["parallel_key"], row["label"],
        row["absent_teacher_id"], row["status"], row["mode"], row["substitute_id"], row["offer_deadline"], row["note"],
    )


def _sub_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"], "lecture_date": row["lecture_date"], "start_period": row["start_period"], "length": row["length"],
        "label": row["label"], "semester_ids": set(row["semester_ids"] or []), "subject_ids": set(row["subject_ids"] or []),
        "absent_teacher_id": row["absent_teacher_id"],
    }


def lecture_start_utc(data: ViewData, d, start: int) -> datetime:
    a, _b = data.period_minutes(start, 1)
    return datetime.combine(d, time(a // 60, a % 60), tzinfo=vw.IST)


def sub_out(data: ViewData, row: Any, candidate_count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "lecture_date": row["lecture_date"].isoformat(), "start_period": row["start_period"], "length": row["length"],
        "time": timing_svc.time_range(data.grid, row["start_period"], row["length"]), "label": row["label"],
        "classes": [data.sems[x].label for x in (row["semester_ids"] or []) if x in data.sems],
        "absent_teacher_id": str(row["absent_teacher_id"]), "absent_teacher_name": data.person_name(row["absent_teacher_id"]),
        "status": row["status"], "mode": row["mode"],
        "substitute_id": str(row["substitute_id"]) if row["substitute_id"] else None,
        "substitute_name": data.person_name(row["substitute_id"]) if row["substitute_id"] else None,
        "offer_deadline": row["offer_deadline"].isoformat() if row["offer_deadline"] else None,
        "candidates": candidate_count,
    }


async def _load(user: CurrentUser, sub_id: str) -> tuple[Any, ViewData, Any]:
    row = await db.fetchrow("select * from public.substitutions where id = $1", as_uuid(sub_id, "substitution"))
    if row is None or (user.role == "hod" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "That substitution was not found.")
    term = await db.fetchrow("select * from public.academic_terms where id = $1", row["term_id"])
    data = await load_view_data(row["school_id"], term, (row["lecture_date"], row["lecture_date"]), with_prefs=True)
    return row, data, term


async def _candidates(data: ViewData, term: Any, row: Any, limit: int | None = None) -> list[subsvc.Candidate]:
    change = await load_changes(row["school_id"], row["term_id"], (row["lecture_date"], row["lecture_date"]))
    can_teach, taught, spec = await subsvc.load_qualification(row["school_id"], row["term_id"])
    return subsvc.rank_candidates(data, change, _sub_as_change(row), specialization=spec, can_teach=can_teach, taught_class=taught, limit=limit)


# ---------------------------------------------------------------------------------------------------- read
@router.get("")
async def list_subs(date: str | None = None, school_id: str | None = None, status: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.active_term()
    if term is None:
        return {"date": None, "substitutions": [], "summary": {}}
    import datetime as _dt

    try:
        d = _dt.date.fromisoformat(date) if date else vw.today_ist()
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "Use a date like 2026-10-12.", extra={"fields": {"date": "Format YYYY-MM-DD."}})
    data = await load_view_data(sid, term, (d, d))
    where = "school_id = $1 and lecture_date = $2"
    args: list[Any] = [sid, d]
    if status in ("open", "offering", "filled", "unfilled", "cancelled"):
        args.append(status)
        where += f" and status = ${len(args)}"
    rows = await db.fetch(f"select * from public.substitutions where {where} order by start_period, label", *args)
    out = [sub_out(data, r) for r in rows]
    summary = {k: sum(1 for r in rows if r["status"] == k) for k in ("open", "offering", "filled", "unfilled", "cancelled")}
    summary["need"] = summary["open"] + summary["offering"] + summary["unfilled"]
    return {"date": d.isoformat(), "substitutions": out, "summary": summary, "school": {"id": str(sid), "name": data.school_name}}


@router.get("/{sub_id}/candidates")
async def candidates(sub_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row, data, term = await _load(user, sub_id)
    ranked = await _candidates(data, term, row)
    return {"substitution": sub_out(data, row), "candidates": [c.as_dict() for c in ranked]}


# ---------------------------------------------------------------------------------------------------- manual assign
class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_id: str


@router.post("/{sub_id}/assign")
async def assign(sub_id: str, body: AssignIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row, data, term = await _load(user, sub_id)
    if row["status"] in ("filled", "cancelled"):
        raise ApiError(409, "CLOSED", f"This substitution is already {row['status']}.")
    tid = as_uuid(body.teacher_id, "teacher")
    if tid == row["absent_teacher_id"]:
        raise ApiError(422, "SAME_TEACHER", "The absent teacher cannot cover their own lecture.")
    change = await load_changes(row["school_id"], row["term_id"], (row["lecture_date"], row["lecture_date"]))
    ok, why = subsvc.free_at(data, change, tid, row["lecture_date"], row["start_period"], row["length"])
    if not ok:
        raise ApiError(409, "NOT_FREE", f"{data.person_name(tid)} cannot take this lecture: {why}.")
    async with db.transaction() as conn:
        locked = await conn.fetchrow("select status from public.substitutions where id = $1 for update", row["id"])
        if locked["status"] in ("filled", "cancelled"):
            raise ApiError(409, "CLOSED", f"This substitution is already {locked['status']}.")
        losers = await conn.fetch("select teacher_id from public.substitution_offers where substitution_id = $1 and status = 'pending' and teacher_id <> $2", row["id"], tid)
        await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where substitution_id = $1 and status = 'pending'", row["id"])
        await conn.execute(
            "update public.substitutions set status = 'filled', substitute_id = $2, mode = 'manual', filled_via = 'manual', filled_at = now(), offer_deadline = null where id = $1",
            row["id"], tid,
        )
        await audit.record(conn, user, "assign", "substitution", row["id"], f"Assigned {data.person_name(tid)} to cover {subflow._sub_line(data, _sub_dict(row))}", school_id=row["school_id"])
        managers = await subflow.managers_of(row["school_id"], exclude=uuid.UUID(user.id))
        notices = subflow.filled_notices(data, {**_sub_dict(row), "id": row["id"]}, winner=tid, losers=[l["teacher_id"] for l in losers], managers=managers, school_id=row["school_id"], via="manual")
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"status": "filled", "substitute_id": str(tid), "substitute_name": data.person_name(tid)}


# ---------------------------------------------------------------------------------------------------- auto offer
class OfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=5, ge=1, le=MAX_OFFER)
    timeout_minutes: int | None = Field(default=None, ge=1, le=240)


@router.post("/{sub_id}/offer")
async def offer(sub_id: str, body: OfferIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row, data, term = await _load(user, sub_id)
    if row["status"] in ("filled", "cancelled"):
        raise ApiError(409, "CLOSED", f"This substitution is already {row['status']}.")
    everyone = await _candidates(data, term, row)
    # Phase 6: a teacher who marked THIS period "not available" is only asked when nobody else is free (and the offer says so)
    willing = [c for c in everyone if not c.marked_unavailable]
    ranked = (willing or everyone)[: body.count]
    last_resort = bool(ranked) and not willing
    if not ranked:
        raise ApiError(409, "NO_CANDIDATE", "No teacher is free for this lecture. Try a smart adjustment (move / swap) or assign manually.")
    minutes = body.timeout_minutes or get_settings().substitution_offer_minutes
    now = datetime.now(vw.IST)
    deadline = min(now + timedelta(minutes=minutes), lecture_start_utc(data, row["lecture_date"], row["start_period"]))
    if deadline <= now:
        raise ApiError(409, "TOO_LATE", "This lecture has already started - assign a substitute manually instead.")
    async with db.transaction() as conn:
        locked = await conn.fetchrow("select status from public.substitutions where id = $1 for update", row["id"])
        if locked["status"] in ("filled", "cancelled"):
            raise ApiError(409, "CLOSED", f"This substitution is already {locked['status']}.")
        await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where substitution_id = $1 and status = 'pending'", row["id"])
        for i, c in enumerate(ranked):
            await conn.execute(
                """
                insert into public.substitution_offers (substitution_id, school_id, teacher_id, rank, score, reasons, expires_at)
                values ($1, $2, $3, $4, $5, $6::jsonb, $7)
                on conflict (substitution_id, teacher_id)
                do update set rank = excluded.rank, score = excluded.score, reasons = excluded.reasons,
                              status = 'pending', responded_at = null, expires_at = excluded.expires_at
                """,
                row["id"], row["school_id"], c.teacher_id, i + 1, c.score, __import__("json").dumps(c.reasons), deadline,
            )
        await conn.execute("update public.substitutions set status = 'offering', mode = 'auto', offer_deadline = $2 where id = $1", row["id"], deadline)
        await audit.record(conn, user, "offer", "substitution", row["id"],
                           f"Sent auto offers to {len(ranked)} teacher(s) for {subflow._sub_line(data, _sub_dict(row))}"
                           + (" (last resort: everyone free had marked the period not available)" if last_resort else ""), school_id=row["school_id"])
        notices = subflow.offer_notices(data, {**_sub_dict(row), "id": row["id"]}, [c.teacher_id for c in ranked], school_id=row["school_id"], deadline=deadline)
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"status": "offering", "offered": [c.as_dict() for c in ranked], "deadline": deadline.isoformat(), "last_resort": last_resort}


# ---------------------------------------------------------------------------------------------------- teacher accept / decline
@router.post("/{sub_id}/accept")
async def accept(sub_id: str, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    sid = as_uuid(sub_id, "substitution")
    me = uuid.UUID(user.id)
    async with db.transaction() as conn:
        sub = await conn.fetchrow("select * from public.substitutions where id = $1 for update", sid)
        if sub is None:
            raise ApiError(404, "NOT_FOUND", "That substitution was not found.")
        offer = await conn.fetchrow("select * from public.substitution_offers where substitution_id = $1 and teacher_id = $2", sid, me)
        if offer is None:
            raise ApiError(403, "NO_OFFER", "You were not offered this lecture.")
        if sub["status"] != "offering":
            await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where id = $1 and status = 'pending'", offer["id"])
            raise ApiError(409, "ALREADY_FILLED", "Too late - this lecture was already filled.")
        if offer["status"] != "pending":
            raise ApiError(409, "OFFER_CLOSED", "This offer is no longer open.")
        if offer["expires_at"] <= datetime.now(vw.IST):
            raise ApiError(409, "EXPIRED", "This offer has expired.")
        term = await db.fetchrow("select * from public.academic_terms where id = $1", sub["term_id"])
        data = await load_view_data(sub["school_id"], term, (sub["lecture_date"], sub["lecture_date"]))
        change = await load_changes(sub["school_id"], sub["term_id"], (sub["lecture_date"], sub["lecture_date"]))
        ok, why = subsvc.free_at(data, change, me, sub["lecture_date"], sub["start_period"], sub["length"])
        if not ok:
            await conn.execute("update public.substitution_offers set status = 'declined', responded_at = now() where id = $1", offer["id"])
            raise ApiError(409, "NOT_FREE", f"You are no longer free for this: {why}.")
        await conn.execute(
            "update public.substitutions set status = 'filled', substitute_id = $2, mode = coalesce(mode, 'auto'), filled_via = 'offer', filled_at = now(), offer_deadline = null where id = $1",
            sid, me,
        )
        await conn.execute("update public.substitution_offers set status = 'accepted', responded_at = now() where id = $1", offer["id"])
        losers = await conn.fetch("select teacher_id from public.substitution_offers where substitution_id = $1 and status = 'pending'", sid)
        await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where substitution_id = $1 and status = 'pending'", sid)
        await audit.record(conn, user, "accept", "substitution", sid, f"{user.full_name} accepted {subflow._sub_line(data, _sub_dict(sub))}", school_id=sub["school_id"])
        managers = await subflow.managers_of(sub["school_id"], exclude=me)
        notices = subflow.filled_notices(data, {**_sub_dict(sub), "id": sub["id"]}, winner=me, losers=[l["teacher_id"] for l in losers], managers=managers, school_id=sub["school_id"], via="offer")
        notices = [n for n in notices if n.user_id != me]  # the winner doesn't need a "you are covering" + they clicked accept
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"status": "filled", "message": f"You are now covering {subflow._sub_line(data, _sub_dict(sub))}."}


@router.post("/{sub_id}/decline")
async def decline(sub_id: str, user: CurrentUser = Depends(require_roles(*TEACHING))) -> dict:
    sid = as_uuid(sub_id, "substitution")
    me = uuid.UUID(user.id)
    async with db.transaction() as conn:
        offer = await conn.fetchrow("select * from public.substitution_offers where substitution_id = $1 and teacher_id = $2", sid, me)
        if offer is None:
            raise ApiError(404, "NO_OFFER", "You were not offered this lecture.")
        if offer["status"] == "pending":
            await conn.execute("update public.substitution_offers set status = 'declined', responded_at = now() where id = $1", offer["id"])
    return {"status": "declined"}


# ---------------------------------------------------------------------------------------------------- cancel a substitution need
@router.post("/{sub_id}/cancel")
async def cancel_sub(sub_id: str, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    row, data, term = await _load(user, sub_id)
    if row["status"] == "cancelled":
        return {"status": "cancelled"}
    async with db.transaction() as conn:
        pend = await conn.fetch("select teacher_id from public.substitution_offers where substitution_id = $1 and status = 'pending'", row["id"])
        await conn.execute("update public.substitution_offers set status = 'superseded', responded_at = now() where substitution_id = $1 and status = 'pending'", row["id"])
        await conn.execute("update public.substitutions set status = 'cancelled', offer_deadline = null where id = $1", row["id"])
        await audit.record(conn, user, "cancel", "substitution", row["id"], f"Cancelled the substitution need for {subflow._sub_line(data, _sub_dict(row))}", school_id=row["school_id"])
        notices = []
        if row["substitute_id"]:
            notices.append(notify.Notice(row["substitute_id"], "sub_cancelled", "Substitution cancelled", f"You no longer need to cover {subflow._sub_line(data, _sub_dict(row))}.", subflow.TEACHER_OFFER_LINK, {}, row["school_id"], f"sub-cxl:{row['id']}:{row['substitute_id']}"))
        for p in pend:
            notices.append(notify.Notice(p["teacher_id"], "offer_closed", "Offer withdrawn", "A substitution offer was withdrawn.", subflow.TEACHER_OFFER_LINK, {}, row["school_id"], f"offer-wd:{row['id']}:{p['teacher_id']}"))
        sent = await notify.create(conn, notices)
    await notify.deliver(sent)
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------------------------------- teacher's own offers + covering
@router.get("/my-offers")
async def my_offers(user: CurrentUser = Depends(require_roles(*TEACHING, touch=False))) -> dict:
    me = uuid.UUID(user.id)
    term = await term_svc.active_term()
    if term is None or user.school_id is None:
        return {"offers": [], "covering": []}
    sid = uuid.UUID(user.school_id)
    now = datetime.now(vw.IST)
    data = await load_view_data(sid, term)
    offers = await db.fetch(
        """
        select o.id as offer_id, o.reasons, o.expires_at, s.*
        from public.substitution_offers o join public.substitutions s on s.id = o.substitution_id
        where o.teacher_id = $1 and o.status = 'pending' and s.status = 'offering' and o.expires_at > $2
        order by o.expires_at
        """,
        me, now,
    )
    import json as _json

    out_offers = []
    for o in offers:
        reasons = o["reasons"]
        out_offers.append({
            **sub_out(data, o), "offer_id": str(o["offer_id"]),
            "reasons": _json.loads(reasons) if isinstance(reasons, str) else reasons,
            "expires_at": o["expires_at"].isoformat(),
        })
    covering = await db.fetch(
        "select * from public.substitutions where substitute_id = $1 and status = 'filled' and lecture_date >= $2 order by lecture_date, start_period",
        me, vw.today_ist(),
    )
    return {
        "offers": out_offers,
        "covering": [{**sub_out(data, c), "absent_teacher_name": data.person_name(c["absent_teacher_id"])} for c in covering],
    }


# ---------------------------------------------------------------------------------------------------- daily sheet (PDF)
@router.get("/sheet")
async def daily_sheet(date: str | None = None, school_id: str | None = None, format: Literal["pdf"] = "pdf", user: CurrentUser = Depends(require_roles(*MANAGERS))) -> Response:
    sid = await resolve_school(user, school_id)
    term = await term_svc.require_active_term()
    import datetime as _dt

    d = _dt.date.fromisoformat(date) if date else vw.today_ist()
    data = await load_view_data(sid, term, (d, d))
    rows = await db.fetch(
        "select s.*, p.full_name as absent_name, q.full_name as sub_name from public.substitutions s "
        "join public.profiles p on p.id = s.absent_teacher_id left join public.profiles q on q.id = s.substitute_id "
        "where s.school_id = $1 and s.lecture_date = $2 and s.status <> 'cancelled' order by s.start_period, p.full_name",
        sid, d,
    )
    content = await __import__("asyncio").to_thread(ex.substitution_sheet_pdf, data, d, [dict(r) for r in rows])
    stem = ex.file_stem("Substitution_Sheet", data.school_name, d.isoformat())
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


# ---------------------------------------------------------------------------------------------------- monthly summary
@router.get("/summary")
async def monthly_summary(month: str | None = None, school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    sid = await resolve_school(user, school_id)
    import datetime as _dt

    today = vw.today_ist()
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            first = _dt.date(y, m, 1)
        except (ValueError, TypeError):
            raise ApiError(422, "VALIDATION_ERROR", "Use a month like 2026-10.", extra={"fields": {"month": "Format YYYY-MM."}})
    else:
        first = today.replace(day=1)
    last = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
    rows = await db.fetch(
        """
        select p.id, p.full_name,
          (select count(*) from public.teacher_absences a where a.teacher_id = p.id and a.absence_date between $2 and $3) as absences,
          (select count(*) from public.substitutions s where s.substitute_id = p.id and s.status = 'filled' and s.lecture_date between $2 and $3) as covered,
          (select count(*) from public.substitutions s where s.absent_teacher_id = p.id and s.lecture_date between $2 and $3) as needed_cover
        from public.profiles p where p.school_id = $1 and p.role in ('teacher','hod')
        order by p.full_name
        """,
        sid, first, last,
    )
    totals = {
        "absences": sum(r["absences"] for r in rows),
        "covered": sum(r["covered"] for r in rows),
        "filled": await db.fetchval("select count(*) from public.substitutions where school_id = $1 and status = 'filled' and lecture_date between $2 and $3", sid, first, last),
        "unfilled": await db.fetchval("select count(*) from public.substitutions where school_id = $1 and status in ('open','offering','unfilled') and lecture_date between $2 and $3", sid, first, last),
    }
    name = await db.fetchval("select name from public.schools where id = $1", sid)
    return {
        "month": first.strftime("%Y-%m"), "month_label": first.strftime("%B %Y"), "school": {"id": str(sid), "name": name},
        "teachers": [{"teacher_id": str(r["id"]), "name": r["full_name"], "absences": r["absences"], "covered": r["covered"], "needed_cover": r["needed_cover"]} for r in rows],
        "totals": totals,
    }
