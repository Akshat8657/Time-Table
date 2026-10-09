"""Reports & analytics (master plan V9 analytics, Phase 6) - admin + HOD only.

  GET /api/reports/workload       faculty workload against the configurable UGC / AICTE norm
  GET /api/reports/rankings       busiest / most-free teachers (+ who covered the most substitutions)
  GET /api/reports/free-teachers  who is free - right now, or at any chosen date and period (or all day)
  GET /api/reports/hours          subject hours progress (required / delivered / possible, short subjects)
  GET /api/reports/cancelled      cancelled sessions of a month (+ the hours lost to holidays and events)
  GET /api/reports/substitutions  monthly absence + substitution counts, fill rate, trend
  GET /api/reports/compare        school-vs-school comparison
  GET /api/reports/norms          the norm in force        PUT / DELETE   change it (HOD: own school, admin: any + the college default)
  GET /api/reports/export/{name}  any of the above as an Excel file (same filters)

Scope is decided HERE, never by the screen: an HOD only ever gets his own school (another school_id -> 403); the admin gets
every active school together, or one school through the filter. Teachers get nothing (403).
All numbers come from the PUBLISHED timetable + the day changes (absences, substitutions, one-day cancels).
"""
from __future__ import annotations

import asyncio
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import exporter as ex
from ..services import hours as hr
from ..services import prefs as pf
from ..services import report_xlsx
from ..services import reports as rp
from ..services import terms as term_svc
from ..services import timing as timing_svc
from ..services import views as vw
from ..services.changes import load_changes
from ..services.viewdata import ViewData, load_view_data
from ..validation import DAY_NAMES, optional_text
from .hours import _cancelled as cancelled_slots

router = APIRouter(prefix="/api/reports", tags=["reports"])
MANAGERS = ("admin", "hod")


# ---------------------------------------------------------------------------------------------------- scope + params
async def _scope(user: CurrentUser, school_id: str | None) -> list[tuple[uuid.UUID, str]]:
    """The schools this request may look at. HOD: his own, always (asking for another one is refused). Admin: the chosen school,
    or - with no school / 'all' - every active school."""
    if user.role == "hod" or (school_id and school_id != "all"):
        sid = await resolve_school(user, school_id if school_id != "all" else None)
        return [(sid, await db.fetchval("select name from public.schools where id = $1", sid))]
    rows = await db.fetch("select id, name from public.schools where is_active order by name")
    return [(r["id"], r["name"]) for r in rows]


def params(
    school_id: str | None = None, month: str | None = None, date: str | None = None, period: int | None = None,
    now: bool = False, length: int = 1, limit: int = 10, class_id: str | None = None, only_short: bool = False,
) -> dict[str, Any]:
    return {"school_id": school_id, "month": month, "date": date, "period": period, "now": now, "length": max(1, min(length, 4)),
            "limit": max(1, min(limit, 50)), "class_id": class_id, "only_short": only_short}


def _month(p: dict[str, Any], today: date) -> tuple[date, date]:
    try:
        return rp.month_bounds(p.get("month"), today)
    except (ValueError, TypeError):
        raise ApiError(422, "VALIDATION_ERROR", "Use a month like 2026-10.", extra={"fields": {"month": "Format YYYY-MM."}}) from None


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "Use a date like 2026-10-12.", extra={"fields": {"date": "Format YYYY-MM-DD."}}) from None


def col(key: str, label: str, kind: str = "text", width: int = 14, tones: dict[str, str] | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"key": key, "label": label, "type": kind, "width": width}
    if tones:
        c["tones"] = tones
    return c


STATE_TONES = {"under": "warning", "within": "success", "over": "danger", "visiting": "info", "none": "neutral"}
HOURS_TONES = {"short": "danger", "on track": "success", "unpublished": "neutral", "no dates": "warning"}


async def _need(user: CurrentUser, p: dict[str, Any]):
    """(schools in scope, the active term or None). The term is college-wide."""
    return await _scope(user, p["school_id"]), await term_svc.active_term()


async def _datas(scope, term, window=None, with_prefs: bool = False) -> list[ViewData]:
    return list(await asyncio.gather(*[load_view_data(sid, term, window, with_prefs=with_prefs) for sid, _n in scope]))


def _head(term, scope, user: CurrentUser) -> dict[str, Any]:
    return {
        "term": term_svc.term_out(term) if term else None,
        "scope": {"schools": [{"id": str(s), "name": n} for s, n in scope], "all": user.role == "admin" and len(scope) > 1},
        "scope_text": ", ".join(n for _s, n in scope) if len(scope) <= 3 else f"All schools ({len(scope)})",
    }


def _no_term(name: str, title: str, user: CurrentUser, scope) -> dict[str, Any]:
    return {"report": name, "title": title, "sheet": title[:31], "term": None, "rows": [], "columns": [], "summary": {}, "xl_summary": [],
            "scope": {"schools": [{"id": str(s), "name": n} for s, n in scope], "all": False}, "scope_text": "", "meta": "No active term",
            "empty_text": "No active term is selected yet (Active Term screen)."}


async def _norm_rows() -> list[Any]:
    return await db.fetch("select school_id, norm_hours, min_hours, label from public.workload_norms")


# ---------------------------------------------------------------------------------------------------- 1) workload
async def build_workload(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("workload", "Faculty workload", user, scope)
    today = vw.today_ist()
    first, last = rp.month_bounds(None, today)
    datas, norm_rows, covered = await asyncio.gather(
        _datas(scope, term), _norm_rows(),
        db.fetch(
            "select substitute_id, sum(length) as periods from public.substitutions where school_id = any($1::uuid[]) and status = 'filled' "
            "and substitute_id is not null and lecture_date between $2 and $3 group by substitute_id",
            [s for s, _n in scope], first, last,
        ),
    )
    covered_map = {r["substitute_id"]: int(r["periods"]) for r in covered}
    rows: list[dict[str, Any]] = []
    per_school: list[dict[str, Any]] = []
    norms = []
    for (sid, name), data in zip(scope, datas):
        norm = rp.pick_norm(norm_rows, sid)
        norms.append(norm)
        school_rows = rp.workload_rows(data, norm, covered_map)
        rows += school_rows
        s = rp.workload_summary(school_rows, norm)
        per_school.append({"school_id": str(sid), "school": name, **{k: s[k] for k in ("teachers", "avg_hours", "avg_pct", "under", "within", "over", "norm_hours")}})
    single = norms[0] if len(scope) == 1 else None
    same = norms[0] if norms and all(n.norm_hours == norms[0].norm_hours and n.min_hours == norms[0].min_hours for n in norms) else None
    summary = rp.workload_summary(rows, single or same)
    rows.sort(key=lambda r: (-r["hours"], r["teacher"].lower()))
    multi = len(scope) > 1
    cols = [col("teacher", "Teacher", width=26)] + ([col("school", "School", width=22)] if multi else []) + [
        col("role", "Role", width=10), col("hours", "Hours / week", "float", 12), col("lectures", "Lectures", "int", 10), col("teaching_days", "Days", "int", 8),
        col("max_day", "Max / day", "int", 10), col("free_periods", "Free periods", "int", 12), col("load_pct", "% of norm", "pct", 11),
        col("status", "Status", "state", 12, STATE_TONES), col("subs_covered", "Substitutions this month", "int", 14),
    ]
    norm_text = f"{summary['norm_hours']} h/week ({summary['norm_label']}, {summary['norm_source']})" if summary["norm_hours"] else "varies by school"
    return {
        "report": "workload", "title": "Faculty workload vs norm", "sheet": "Workload", **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | Norm: {norm_text}",
        "norm": (single or same).as_dict() if (single or same) else None, "summary": summary, "per_school": per_school,
        "xl_summary": [("Teachers", summary["teachers"]), ("Average hours / week (regular faculty)", summary["avg_hours"]), ("Under norm", summary["under"]),
                       ("Within norm", summary["within"]), ("Over norm", summary["over"]), ("Norm", norm_text)],
        "columns": cols, "rows": rows, "empty_text": "No teachers found.",
        "note": "1 period = 1 clock hour of the lecture length. A combined session counts once; a co-taught lecture counts for both teachers. Visiting faculty are compared with their own weekly limit, not the norm.",
    }


# ---------------------------------------------------------------------------------------------------- 2) rankings
async def build_rankings(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    wl = await build_workload(user, p)
    if wl["term"] is None:
        return {**wl, "report": "rankings", "title": "Teacher rankings", "lists": {"busiest": [], "most_free": [], "substitutes": []}}
    lists = rp.rankings(wl["rows"], p["limit"])
    multi = len(wl["scope"]["schools"]) > 1
    sch = [col("school", "School", width=22)] if multi else []
    busy_cols = [col("rank", "#", "int", 5), col("teacher", "Teacher", width=26), *sch, col("role", "Role", width=10), col("hours", "Hours / week", "float", 12),
                 col("lectures", "Lectures", "int", 10), col("load_pct", "% of norm", "pct", 11), col("status", "Status", "state", 12, STATE_TONES)]
    free_cols = [col("rank", "#", "int", 5), col("teacher", "Teacher", width=26), *sch, col("role", "Role", width=10), col("free_periods", "Free periods", "int", 12),
                 col("capacity", "Out of", "int", 8), col("hours", "Hours / week", "float", 12)]
    sub_cols = [col("rank", "#", "int", 5), col("teacher", "Teacher", width=26), *sch, col("subs_covered", "Substitutions covered (this month)", "int", 16)]
    return {
        **{k: wl[k] for k in ("term", "scope", "scope_text", "norm")}, "report": "rankings", "title": f"Busiest and most free teachers (top {p['limit']})", "sheet": "Busiest",
        "meta": wl["meta"], "summary": wl["summary"], "xl_summary": wl["xl_summary"][:1], "lists": lists,
        "columns": busy_cols, "rows": lists["busiest"],
        "sections": [{"title": "Most free", "columns": free_cols, "rows": lists["most_free"]}, {"title": "Substitutes this month", "columns": sub_cols, "rows": lists["substitutes"]}],
        "empty_text": "No teachers found.",
    }


# ---------------------------------------------------------------------------------------------------- 3) free-teacher finder
async def build_free(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("free-teachers", "Free teachers", user, scope)
    now_ist = datetime.now(vw.IST)
    d = now_ist.date() if p["now"] else (_date(p["date"]) or vw.today_ist())
    datas = await _datas(scope, term, (d, d), with_prefs=True)
    changes = await asyncio.gather(*[load_changes(sid, term["id"], (d, d)) for sid, _n in scope])
    rows: list[dict[str, Any]] = []
    schools: list[dict[str, Any]] = []
    notes: list[str] = []
    for (sid, name), data, change in zip(scope, datas, changes):
        start = p["period"]
        note = None
        if p["now"]:
            cur, nxt = rp.period_at(data, now_ist.hour * 60 + now_ist.minute)
            start = cur if cur is not None else nxt
            if cur is None and nxt is not None:
                note = f"{name}: no lecture is running right now - showing who is free for the next period (period {nxt})."
            elif cur is None:
                note = f"{name}: today's lectures are over."
        if p["now"] and start is None:
            res = {"periods": [], "teachers": [], "time": None, "banners": [], "period": None}
        else:
            res = rp.free_teachers(data, change, d, start=start, length=p["length"])
        if note:
            notes.append(note)
        if d.isoweekday() > min(6, data.days_per_week):
            notes.append(f"{name}: {DAY_NAMES.get(d.isoweekday(), 'That day')} is not a working day.")
        schools.append({"id": str(sid), "name": name, "periods": res["periods"], "time": res["time"], "period": res["period"], "banners": res["banners"]})
        for t in res["teachers"]:
            t["free_text"] = ", ".join(f"P{x['period']} {x['time']}" for x in t["free_periods"])
            rows.append(t)
    rows.sort(key=lambda r: (r["marked_unavailable"], r["load_today"], r["name"].lower()))
    multi = len(scope) > 1
    one = schools[0] if schools else {}
    at_text = f"period {one.get('period')} ({one.get('time')})" if one.get("period") and len(schools) == 1 else ("the chosen period" if p["period"] or p["now"] else "all day")
    cols = [col("name", "Teacher", width=26)] + ([col("school", "School", width=22)] if multi else []) + [
        col("role", "Role", width=10), col("specialization", "Specialization", width=30), col("free_text", "Free periods", width=46),
        col("free_count", "Free", "int", 8), col("load_today", "Teaching today", "int", 14), col("marked_unavailable", "Prefers not then", width=14),
    ]
    return {
        "report": "free-teachers", "title": f"Free teachers - {d.strftime('%a %d %b %Y')}, {at_text}", "sheet": "Free teachers",
        **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | {d.isoformat()}",
        "date": d.isoformat(), "weekday": d.isoweekday(), "now": bool(p["now"]), "period": p["period"], "schools": schools, "notes": notes,
        "summary": {"free": len(rows), "date": d.isoformat()}, "xl_summary": [("Date", d.isoformat()), ("Free teachers", len(rows))],
        "columns": cols, "rows": rows, "empty_text": "Nobody is free then (or it is not a working day).",
    }


# ---------------------------------------------------------------------------------------------------- 4) subject hours progress
async def build_hours(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("hours", "Subject hours progress", user, scope)
    today = vw.today_ist()
    datas = await _datas(scope, term)
    cancels = await asyncio.gather(*[cancelled_slots(sid, term["id"]) for sid, _n in scope])
    rows: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    for (sid, name), data, cancelled in zip(scope, datas, cancels):
        only = None
        if p["class_id"]:
            only = {uuid.UUID(p["class_id"])} & set(data.sems) if _is_uuid(p["class_id"]) else set()
        per_class = hr.compute_hours(data, today, only, cancelled=cancelled)
        r, c = rp.hours_report(per_class, name, sid)
        rows += r
        classes += c
    if p["only_short"]:
        rows = [r for r in rows if r["shortfall"] > 0]
    rows.sort(key=lambda r: (-r["shortfall"], r["school"], r["class"], r["subject"].lower()))
    tracked = [c for c in classes if c["required"]]
    summary = {
        "subjects": len(rows) if not p["only_short"] else sum(c["subjects"] for c in classes),
        "short_subjects": sum(c["short_subjects"] for c in classes), "short_hours": sum(c["shortfall"] for c in classes),
        "required": sum(c["required"] for c in classes), "delivered": sum(c["delivered"] for c in classes), "deliverable": sum(c["deliverable"] for c in classes),
        "taught": sum(c["taught"] for c in classes), "planned": sum(c["planned"] for c in classes),   # each subject capped at what it needs
        "percent_delivered": rp.pct(sum(c["taught"] for c in tracked), sum(c["required"] for c in tracked)),
        "percent_planned": rp.pct(sum(c["planned"] for c in tracked), sum(c["required"] for c in tracked)),
        "classes": len(classes), "unpublished_classes": sum(1 for c in classes if not c["required"]),
    }
    multi = len(scope) > 1
    cols = ([col("school", "School", width=22)] if multi else []) + [
        col("class", "Class", width=20), col("subject", "Subject", width=32), col("code", "Code", width=11), col("teachers", "Teacher(s)", width=26),
        col("required", "Required h", "int", 11), col("delivered", "Taught so far", "int", 12), col("deliverable", "Possible by semester end", "int", 14),
        col("shortfall", "Short by", "int", 9), col("percent_planned", "% planned", "pct", 10), col("state", "State", "state", 12, HOURS_TONES),
    ]
    class_cols = ([col("school", "School", width=22)] if multi else []) + [
        col("class", "Class", width=20), col("required", "Required h", "int", 11), col("taught", "Taught (toward the requirement)", "int", 16),
        col("planned", "Planned (toward the requirement)", "int", 16), col("shortfall", "Short by", "int", 9), col("short_subjects", "Short subjects", "int", 12),
        col("percent_delivered", "% taught", "pct", 10), col("percent_planned", "% planned", "pct", 10),
    ]
    return {
        "report": "hours", "title": "Subject hours progress", "sheet": "Subjects", **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | as of {today.isoformat()}",
        "today": today.isoformat(), "summary": summary, "classes": classes,
        "xl_summary": [("Subjects short", summary["short_subjects"]), ("Hours short", summary["short_hours"]), ("% of required hours planned", f"{summary['percent_planned']}%")],
        "columns": cols, "rows": rows, "sections": [{"title": "By class", "columns": class_cols, "rows": classes}],
        "empty_text": "No subjects to show (nothing is published yet, or no subject is short).",
    }


def _is_uuid(v: str) -> bool:
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------------------------------- 5) cancelled sessions
async def build_cancelled(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("cancelled", "Cancelled sessions", user, scope)
    today = vw.today_ist()
    first, last = _month(p, today)
    ids = [s for s, _n in scope]
    names = dict(scope)
    adj, labels, datas, cancels = await asyncio.gather(
        db.fetch(
            "select a.*, u.full_name as by_name from public.day_adjustments a left join public.profiles u on u.id = a.created_by "
            "where a.school_id = any($1::uuid[]) and a.kind = 'cancel' and a.lecture_date between $2 and $3 order by a.lecture_date, a.orig_start",
            ids, first, last,
        ),
        db.fetch("select sm.id, sm.number, c.name as course_name from public.semesters sm join public.courses c on c.id = sm.course_id where sm.school_id = any($1::uuid[])", ids),
        _datas(scope, term),
        asyncio.gather(*[cancelled_slots(sid, term["id"]) for sid in ids]),
    )
    label_of = {r["id"]: term_svc.semester_label(r["course_name"], r["number"]) for r in labels}
    people = {r["id"]: r["full_name"] for r in await db.fetch(
        "select id, full_name from public.profiles where id = any($1::uuid[])",
        list({t for a in adj for t in (a["teacher_id"], a["co_teacher_id"]) if t is not None}),
    )} if adj else {}
    grids = {sid: d.grid for (sid, _n), d in zip(scope, datas)}
    rows: list[dict[str, Any]] = []
    by_teacher: Counter = Counter()
    by_class: Counter = Counter()
    by_day: Counter = Counter()
    for a in adj:
        classes = [label_of.get(s, "a class") for s in (a["semester_ids"] or [])]
        teachers = [people[t] for t in (a["teacher_id"], a["co_teacher_id"]) if t in people]
        grid = grids.get(a["school_id"])
        when = timing_svc.time_range(grid, a["orig_start"], a["length"]) if grid else ""
        rows.append({
            "date": a["lecture_date"].isoformat(), "weekday": DAY_NAMES.get(a["lecture_date"].isoweekday(), ""), "school": names.get(a["school_id"], ""),
            "class": ", ".join(classes), "subject": a["label"], "period": a["orig_start"], "time": when, "length": a["length"],
            "teachers": ", ".join(teachers) or "-", "reason": a["reason"] or "", "by": a["by_name"] or "",
        })
        for t in teachers:
            by_teacher[t] += 1
        for c in classes:
            by_class[c] += 1
        by_day[a["lecture_date"].day] += 1
    days_in_month = (last - first).days + 1
    per_day = [{"day": d, "label": str(d), "count": by_day.get(d, 0)} for d in range(1, days_in_month + 1)]
    lost = []
    for (sid, name), data, cancelled in zip(scope, datas, cancels):
        for sem_id, subjects in hr.compute_hours(data, today, cancelled=cancelled).items():
            if subjects:
                lost.append({"school": name, "class": subjects[0].class_label, "lost_hours": sum(h.lost for h in subjects),
                             "cancelled_by_hod": sum(1 for (s, _d, _st) in cancelled if s == sem_id)})
    lost.sort(key=lambda r: (-r["lost_hours"], r["school"], r["class"]))
    multi = len(scope) > 1
    cols = [col("date", "Date", width=12), col("weekday", "Day", width=11)] + ([col("school", "School", width=22)] if multi else []) + [
        col("class", "Class", width=22), col("subject", "Subject", width=32), col("period", "Period", "int", 8), col("time", "Time", width=13),
        col("teachers", "Teacher(s)", width=26), col("reason", "Reason", width=30), col("by", "Cancelled by", width=20),
    ]
    lost_cols = ([col("school", "School", width=22)] if multi else []) + [
        col("class", "Class", width=22), col("lost_hours", "Hours lost to holidays / events / cancels", "int", 20), col("cancelled_by_hod", "One-day cancels (all term)", "int", 18)]
    summary = {
        "total": len(rows), "month": first.strftime("%Y-%m"), "month_label": first.strftime("%B %Y"),
        "top_teachers": [{"name": n, "count": c} for n, c in by_teacher.most_common(5)], "top_classes": [{"name": n, "count": c} for n, c in by_class.most_common(5)],
        "lost_hours_total": sum(r["lost_hours"] for r in lost),
    }
    return {
        "report": "cancelled", "title": f"Cancelled sessions - {first.strftime('%B %Y')}", "sheet": "Cancelled", **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | {first.strftime('%B %Y')}",
        "summary": summary, "per_day": per_day, "lost": lost,
        "xl_summary": [("Month", first.strftime("%B %Y")), ("Cancelled sessions", len(rows)), ("Hours lost to holidays / events (term to date)", summary["lost_hours_total"])],
        "columns": cols, "rows": rows, "sections": [{"title": "Hours lost per class", "columns": lost_cols, "rows": lost}],
        "empty_text": "No session was cancelled in this month.",
    }


# ---------------------------------------------------------------------------------------------------- 6) monthly substitution counts
async def build_substitutions(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("substitutions", "Substitutions", user, scope)
    today = vw.today_ist()
    first, last = _month(p, today)
    ids = [s for s, _n in scope]
    months = rp.month_range(first, 6)
    trend_from = months[0]
    status_rows, absences, covered, needed, absent_days, trend = await asyncio.gather(
        db.fetch(
            "select status, mode, filled_via, count(*) as n, avg(extract(epoch from (filled_at - created_at)) / 60) filter (where status = 'filled') as mins "
            "from public.substitutions where school_id = any($1::uuid[]) and lecture_date between $2 and $3 group by status, mode, filled_via",
            ids, first, last,
        ),
        db.fetchrow("select count(*) as n, count(distinct teacher_id) as people from public.teacher_absences where school_id = any($1::uuid[]) and absence_date between $2 and $3", ids, first, last),
        db.fetch(
            "select substitute_id as t, count(*) as n, sum(length) as periods from public.substitutions where school_id = any($1::uuid[]) and status = 'filled' "
            "and substitute_id is not null and lecture_date between $2 and $3 group by substitute_id", ids, first, last),
        db.fetch(
            "select absent_teacher_id as t, count(*) as n from public.substitutions where school_id = any($1::uuid[]) and status <> 'cancelled' "
            "and lecture_date between $2 and $3 group by absent_teacher_id", ids, first, last),
        db.fetch("select teacher_id as t, count(*) as n from public.teacher_absences where school_id = any($1::uuid[]) and absence_date between $2 and $3 group by teacher_id", ids, first, last),
        db.fetch(
            "select date_trunc('month', lecture_date)::date as m, count(*) filter (where status <> 'cancelled') as needs, count(*) filter (where status = 'filled') as filled "
            "from public.substitutions where school_id = any($1::uuid[]) and lecture_date >= $2 and lecture_date <= $3 group by 1", ids, trend_from, last),
    )
    by_status: Counter = Counter()
    filled_manual = filled_auto = 0
    weighted = 0.0
    for r in status_rows:
        by_status[r["status"]] += r["n"]
        if r["status"] == "filled":
            if r["filled_via"] == "offer":
                filled_auto += r["n"]
            else:
                filled_manual += r["n"]
            weighted += float(r["mins"] or 0) * r["n"]
    needs = sum(n for s, n in by_status.items() if s != "cancelled")
    filled = by_status["filled"]
    teacher_ids = {r["t"] for r in (*covered, *needed, *absent_days)}
    info = {r["id"]: r for r in await db.fetch(
        "select p.id, p.full_name, s.name as school from public.profiles p left join public.schools s on s.id = p.school_id where p.id = any($1::uuid[])", list(teacher_ids))} if teacher_ids else {}
    cov = {r["t"]: r for r in covered}
    need = {r["t"]: r["n"] for r in needed}
    days = {r["t"]: r["n"] for r in absent_days}
    rows = [{
        "teacher": info[t]["full_name"] if t in info else "A removed account", "school": (info[t]["school"] if t in info else "") or "",
        "absent_days": days.get(t, 0), "lectures_needing_cover": need.get(t, 0),
        "subs_covered": cov[t]["n"] if t in cov else 0, "periods_covered": int(cov[t]["periods"]) if t in cov else 0,
    } for t in teacher_ids]
    rows.sort(key=lambda r: (-r["subs_covered"], -r["absent_days"], r["teacher"].lower()))
    by_month = {r["m"]: r for r in trend}
    series = [{"month": m.strftime("%Y-%m"), "label": m.strftime("%b"), "needs": by_month[m]["needs"] if m in by_month else 0,
               "filled": by_month[m]["filled"] if m in by_month else 0} for m in months]
    summary = {
        "month": first.strftime("%Y-%m"), "month_label": first.strftime("%B %Y"), "absences": absences["n"], "absent_teachers": absences["people"],
        "needs": needs, "filled": filled, "unfilled": by_status["unfilled"] + by_status["open"] + by_status["offering"], "cancelled": by_status["cancelled"],
        "fill_rate": rp.pct(filled, needs), "filled_auto": filled_auto, "filled_manual": filled_manual,
        "avg_minutes_to_fill": round(weighted / filled, 1) if filled else None,
    }
    multi = len(scope) > 1
    cols = [col("teacher", "Teacher", width=26)] + ([col("school", "School", width=22)] if multi else []) + [
        col("absent_days", "Days absent", "int", 11), col("lectures_needing_cover", "Lectures needing cover", "int", 16),
        col("subs_covered", "Substitutions covered", "int", 16), col("periods_covered", "Periods covered", "int", 14)]
    trend_cols = [col("month", "Month", width=10), col("needs", "Needing a substitute", "int", 18), col("filled", "Filled", "int", 10)]
    return {
        "report": "substitutions", "title": f"Substitutions - {first.strftime('%B %Y')}", "sheet": "Substitutions", **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | {first.strftime('%B %Y')}",
        "summary": summary, "trend": series,
        "xl_summary": [("Month", first.strftime("%B %Y")), ("Absences", summary["absences"]), ("Lectures needing a substitute", needs), ("Filled", filled),
                       ("Fill rate", f"{summary['fill_rate']}%"), ("Filled by auto-offer / by HOD", f"{filled_auto} / {filled_manual}")],
        "columns": cols, "rows": rows, "sections": [{"title": "Last 6 months", "columns": trend_cols, "rows": series}],
        "empty_text": "No absence or substitution happened in this month.",
    }


# ---------------------------------------------------------------------------------------------------- 7) school vs school
async def build_compare(user: CurrentUser, p: dict[str, Any]) -> dict[str, Any]:
    scope, term = await _need(user, p)
    if term is None:
        return _no_term("compare", "School comparison", user, scope)
    today = vw.today_ist()
    first, last = _month(p, today)
    ids = [s for s, _n in scope]
    datas, norm_rows, cancels, subs, absences, cancelled_month, windows = await asyncio.gather(
        _datas(scope, term), _norm_rows(), asyncio.gather(*[cancelled_slots(sid, term["id"]) for sid in ids]),
        db.fetch(
            "select school_id, count(*) filter (where status <> 'cancelled') as needs, count(*) filter (where status = 'filled') as filled "
            "from public.substitutions where school_id = any($1::uuid[]) and lecture_date between $2 and $3 group by school_id", ids, first, last),
        db.fetch("select school_id, count(*) as n from public.teacher_absences where school_id = any($1::uuid[]) and absence_date between $2 and $3 group by school_id", ids, first, last),
        db.fetch("select school_id, count(*) as n from public.day_adjustments where school_id = any($1::uuid[]) and kind = 'cancel' and lecture_date between $2 and $3 group by school_id", ids, first, last),
        db.fetch(
            "select distinct on (w.school_id) w.school_id, w.id, w.status, (select count(*) from public.preference_responses r where r.window_id = w.id) as filled "
            "from public.preference_windows w where w.school_id = any($1::uuid[]) order by w.school_id, w.opened_at desc", ids),
    )
    sub_by = {r["school_id"]: r for r in subs}
    abs_by = {r["school_id"]: r["n"] for r in absences}
    can_by = {r["school_id"]: r["n"] for r in cancelled_month}
    win_by = {r["school_id"]: r for r in windows}
    rows: list[dict[str, Any]] = []
    for (sid, name), data, cancelled in zip(scope, datas, cancels):
        norm = rp.pick_norm(norm_rows, sid)
        wrows = rp.workload_rows(data, norm)
        ws = rp.workload_summary(wrows, norm)
        hs = hr.summarize(hr.compute_hours(data, today, cancelled=cancelled))
        s = sub_by.get(sid)
        needs, filled = (s["needs"], s["filled"]) if s else (0, 0)
        win = win_by.get(sid)
        teachers = len([w for w in wrows])
        rows.append({
            "school": name, "school_id": str(sid), "teachers": teachers, "classes": len(data.sems), "published_classes": len(data.pubs),
            "avg_hours": ws["avg_hours"], "avg_pct": ws["avg_pct"], "under": ws["under"], "within": ws["within"], "over": ws["over"], "norm_hours": norm.norm_hours,
            "short_subjects": hs["short_subjects"], "short_hours": hs["short_hours"],
            "absences": abs_by.get(sid, 0), "sub_needs": needs, "sub_filled": filled, "fill_rate": rp.pct(filled, needs), "cancelled": can_by.get(sid, 0),
            "pref_filled": win["filled"] if win else None, "pref_pct": rp.pct(win["filled"], teachers) if win else None,
        })
    rows.sort(key=lambda r: r["school"])
    cols = [
        col("school", "School", width=24), col("teachers", "Teachers", "int", 10), col("classes", "Classes", "int", 9), col("published_classes", "Published", "int", 10),
        col("avg_hours", "Avg hours / week", "float", 14), col("avg_pct", "Avg % of norm", "pct", 13), col("under", "Under", "int", 8), col("within", "Within", "int", 8),
        col("over", "Over", "int", 8), col("short_subjects", "Subjects short", "int", 13), col("short_hours", "Hours short", "int", 11), col("absences", "Absences", "int", 10),
        col("sub_needs", "Need a sub", "int", 11), col("sub_filled", "Filled", "int", 8), col("fill_rate", "Fill rate", "pct", 10), col("cancelled", "Cancelled", "int", 10),
        col("pref_pct", "Preferences filled", "pct", 15),
    ]
    return {
        "report": "compare", "title": f"School vs school - {first.strftime('%B %Y')}", "sheet": "Compare", **_head(term, scope, user), "meta": f"{term_svc.term_out(term)['name']} | {first.strftime('%B %Y')}",
        "month": first.strftime("%Y-%m"), "month_label": first.strftime("%B %Y"),
        "summary": {"schools": len(rows), "teachers": sum(r["teachers"] for r in rows)}, "xl_summary": [("Schools", len(rows)), ("Month", first.strftime("%B %Y"))],
        "columns": cols, "rows": rows, "empty_text": "No schools to compare.",
    }


BUILDERS: dict[str, Callable[[CurrentUser, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "workload": build_workload, "rankings": build_rankings, "free-teachers": build_free, "hours": build_hours,
    "cancelled": build_cancelled, "substitutions": build_substitutions, "compare": build_compare,
}


# ---------------------------------------------------------------------------------------------------- the routes
@router.get("/workload")
async def workload(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_workload(user, p)


@router.get("/rankings")
async def rankings(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_rankings(user, p)


@router.get("/free-teachers")
async def free_teachers(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_free(user, p)


@router.get("/hours")
async def hours(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_hours(user, p)


@router.get("/cancelled")
async def cancelled(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_cancelled(user, p)


@router.get("/substitutions")
async def substitutions(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_substitutions(user, p)


@router.get("/compare")
async def compare(p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    return await build_compare(user, p)


@router.get("/export/{name}")
async def export(name: str, p: dict = Depends(params), user: CurrentUser = Depends(require_roles(*MANAGERS))) -> Response:
    builder = BUILDERS.get(name)
    if builder is None:
        raise ApiError(404, "NOT_FOUND", "That report does not exist.")
    report = await builder(user, p)
    college = await db.fetchval("select college_name from public.college_settings limit 1") or "College"
    content = await asyncio.to_thread(report_xlsx.report_xlsx, report, college)
    stem = ex.file_stem("Report", name, report.get("scope_text") or "all", report.get("summary", {}).get("month") or p.get("month") or vw.today_ist().isoformat())
    return Response(
        content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


# ---------------------------------------------------------------------------------------------------- the norm
class NormIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["school", "college"] = "school"
    school_id: str | None = None
    norm_hours: int = Field(ge=1, le=60)
    min_hours: int = Field(ge=0, le=60)
    label: optional_text(80) = None  # type: ignore[valid-type]


def _norm_out(row: Any) -> dict[str, Any] | None:
    return None if row is None else {"norm_hours": row["norm_hours"], "min_hours": row["min_hours"], "label": row["label"]}


@router.get("/norms")
async def get_norms(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    rows = await db.fetch("select school_id, norm_hours, min_hours, label from public.workload_norms")
    college = next((r for r in rows if r["school_id"] is None), None)
    if user.role == "admin" and (school_id is None or school_id == "all"):
        return {"college": _norm_out(college), "school": None, "effective": rp.pick_norm(rows, None).as_dict(), "built_in": {"norm_hours": rp.BUILT_IN_NORM, "min_hours": rp.BUILT_IN_MIN}}
    sid = await resolve_school(user, school_id)
    return {
        "college": _norm_out(college), "school": _norm_out(next((r for r in rows if r["school_id"] == sid), None)),
        "effective": rp.pick_norm(rows, sid).as_dict(), "built_in": {"norm_hours": rp.BUILT_IN_NORM, "min_hours": rp.BUILT_IN_MIN},
    }


@router.put("/norms")
async def save_norm(body: NormIn, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    if body.min_hours > body.norm_hours:
        raise ApiError(422, "VALIDATION_ERROR", "The lowest healthy load cannot be above the norm.", extra={"fields": {"min_hours": "Must not be above the norm hours."}})
    if body.scope == "college":
        if user.role != "admin":
            raise ApiError(403, "FORBIDDEN", "Only the administrator can change the college-wide default.")
        sid = None
    else:
        sid = await resolve_school(user, body.school_id)
    async with db.transaction() as conn:
        existing = await conn.fetchval("select id from public.workload_norms where school_id is not distinct from $1", sid)
        if existing:
            await conn.execute(
                "update public.workload_norms set norm_hours = $2, min_hours = $3, label = $4, updated_at = now(), updated_by = $5 where id = $1",
                existing, body.norm_hours, body.min_hours, body.label, uuid.UUID(user.id),
            )
        else:
            await conn.execute(
                "insert into public.workload_norms (school_id, norm_hours, min_hours, label, updated_by) values ($1, $2, $3, $4, $5)",
                sid, body.norm_hours, body.min_hours, body.label, uuid.UUID(user.id),
            )
        await audit.record(conn, user, "update", "workload_norm", sid, f"Workload norm set to {body.norm_hours} h/week (healthy from {body.min_hours} h) for "
                           + ("the whole college" if sid is None else "one school"), school_id=sid, details={"norm_hours": body.norm_hours, "min_hours": body.min_hours})
    return await get_norms(str(sid) if sid else None, user)


@router.delete("/norms")
async def remove_school_norm(school_id: str | None = None, user: CurrentUser = Depends(require_roles(*MANAGERS))) -> dict:
    """Drops the school's own norm so the college-wide default applies again."""
    sid = await resolve_school(user, school_id)
    async with db.transaction() as conn:
        n = await conn.execute("delete from public.workload_norms where school_id = $1", sid)
        await audit.record(conn, user, "delete", "workload_norm", sid, "Removed the school's own workload norm (the college default applies again)", school_id=sid)
    return await get_norms(str(sid), user)
