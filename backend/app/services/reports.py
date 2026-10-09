"""Reports & analytics (master plan V9 analytics, Phase 6) - the numbers, as pure functions over the PUBLISHED timetable.

  Norm / pick_norm      the configurable UGC / AICTE weekly teaching-hours norm (school override > college default > built-in 16 h)
  weekly_load()         per teacher: periods, lectures, per-weekday load from the published rows (a combined session counts once)
  workload_rows()       faculty workload against the norm  ->  under / within / over
  rankings()            busiest / most-free teachers (+ who covered the most substitutions)
  free_teachers()       who is free on a date - at one period, or all day  (also the engine behind the Phase 7 assistant's "who is free?")
  hours_report()        subject hours progress, flattened for tables and charts
  month_range() ...     small helpers for the monthly reports

No database, no web: the router (routers/reports.py) loads, this module computes, services/report_xlsx.py writes Excel.
Unit of work: 1 period = the college's lecture length in clock hours (60 min by default, so 1 period = 1 hour).
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from . import substitution as subsvc
from . import timing as timing_svc
from . import views as vw
from .changes import DayChanges
from .viewdata import ViewData

BUILT_IN_NORM = 16
BUILT_IN_MIN = 12


# ---------------------------------------------------------------------------------------------------- the norm
@dataclass(frozen=True)
class Norm:
    norm_hours: int = BUILT_IN_NORM
    min_hours: int = BUILT_IN_MIN
    label: str = "UGC / AICTE norm"
    source: str = "default"           # 'school' | 'college' | 'default'

    def as_dict(self) -> dict[str, Any]:
        return {"norm_hours": self.norm_hours, "min_hours": self.min_hours, "label": self.label, "source": self.source}


def pick_norm(rows: Iterable[Mapping[str, Any]], school_id: uuid.UUID | None) -> Norm:
    """The school's own norm if the HOD / admin set one, else the college-wide default, else the built-in 16 / 12 hours."""
    by_scope = {r["school_id"]: r for r in rows}
    if school_id is not None and school_id in by_scope:
        r, source = by_scope[school_id], "school"
    elif None in by_scope:
        r, source = by_scope[None], "college"
    else:
        return Norm()
    return Norm(r["norm_hours"], r["min_hours"], r["label"] or "UGC / AICTE norm", source)


# ---------------------------------------------------------------------------------------------------- workload
@dataclass
class WeeklyLoad:
    periods: int = 0
    lectures: int = 0
    per_day: Counter = field(default_factory=Counter)


def weekly_load(data: ViewData) -> dict[uuid.UUID, WeeklyLoad]:
    """What each teacher teaches in a normal published week. A combined session (one key, several class rows) counts ONCE;
    a co-taught lecture counts for BOTH teachers; a parallel elective counts only for the teacher of that option."""
    out: dict[uuid.UUID, WeeklyLoad] = defaultdict(WeeklyLoad)
    seen: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for r in data.rows:
        for t in r.teachers:
            if r.key in seen[t]:
                continue
            seen[t].add(r.key)
            w = out[t]
            w.periods += r.length
            w.lectures += 1
            w.per_day[r.day] += r.length
    return dict(out)


def period_hours(data: ViewData) -> float:
    """Clock hours in one period (a 60-minute lecture = 1.0)."""
    if not data.grid.periods:
        return 1.0
    p = data.grid.periods[0]
    return (p.end - p.start) / 60


def _available_days(data: ViewData, person) -> list[int]:
    days = list(range(1, min(6, data.days_per_week) + 1))
    return [d for d in days if d in person.visiting_days] if person.is_visiting else days


def status_of(hours: float, norm: Norm, *, is_visiting: bool, published: bool) -> str:
    if not published:
        return "none"
    if is_visiting:
        return "visiting"
    if hours > norm.norm_hours:
        return "over"
    if hours < norm.min_hours:
        return "under"
    return "within"


def workload_rows(
    data: ViewData, norm: Norm, covered_month: Mapping[uuid.UUID, int] | None = None,
) -> list[dict[str, Any]]:
    """One row per active teacher of the school: weekly workload against the norm, free periods, substitutions covered this month."""
    loads = weekly_load(data)
    ph = period_hours(data)
    published = bool(data.rows)
    total = len(data.grid.periods)
    covered_month = covered_month or {}
    rows: list[dict[str, Any]] = []
    for tid, p in data.people.items():
        if not p.is_active:
            continue
        load = loads.get(tid, WeeklyLoad())
        hours = round(load.periods * ph, 1)
        days = _available_days(data, p)
        capacity = len(days) * total
        rows.append({
            "teacher_id": str(tid), "teacher": p.name, "school": data.school_name, "school_id": str(data.school_id),
            "role": "HOD" if p.role == "hod" else ("Visiting" if p.is_visiting else "Teacher"), "is_visiting": p.is_visiting,
            "specialization": p.specialization, "periods": load.periods, "hours": hours, "lectures": load.lectures,
            "teaching_days": len([d for d in load.per_day if load.per_day[d]]), "max_day": max(load.per_day.values(), default=0),
            "capacity": capacity, "free_periods": max(0, capacity - load.periods),
            "norm_hours": norm.norm_hours, "load_pct": round(100 * hours / norm.norm_hours) if norm.norm_hours else 0,
            "status": status_of(hours, norm, is_visiting=p.is_visiting, published=published),
            "visiting_limit": p.max_weekly_hours if p.is_visiting else None,
            "subs_covered": covered_month.get(tid, 0),
        })
    rows.sort(key=lambda r: (-r["hours"], r["teacher"].lower()))
    return rows


def workload_summary(rows: list[dict[str, Any]], norm: Norm) -> dict[str, Any]:
    regular = [r for r in rows if not r["is_visiting"]]
    counts = Counter(r["status"] for r in rows)
    hours = [r["hours"] for r in regular]
    return {
        "teachers": len(rows), "regular": len(regular), "visiting": counts.get("visiting", 0),
        "total_hours": round(sum(r["hours"] for r in rows), 1),
        "avg_hours": round(sum(hours) / len(hours), 1) if hours else 0.0,
        "avg_pct": round(100 * (sum(hours) / len(hours)) / norm.norm_hours) if hours and norm.norm_hours else 0,
        "under": counts.get("under", 0), "within": counts.get("within", 0), "over": counts.get("over", 0),
        "published": any(r["status"] != "none" for r in rows),
        "norm_hours": norm.norm_hours, "min_hours": norm.min_hours, "norm_label": norm.label, "norm_source": norm.source,
    }


# ---------------------------------------------------------------------------------------------------- rankings
def rankings(rows: list[dict[str, Any]], limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Busiest (most teaching hours), most free (most free periods in the week) and who covered the most substitutions."""
    def pick(sorted_rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
        return [{"rank": i + 1, "teacher_id": r["teacher_id"], "teacher": r["teacher"], "school": r["school"], "role": r["role"], **{k: r[k] for k in keys}}
                for i, r in enumerate(sorted_rows[:limit])]

    busiest = sorted(rows, key=lambda r: (-r["hours"], -r["lectures"], r["teacher"].lower()))
    free = sorted(rows, key=lambda r: (-r["free_periods"], r["hours"], r["teacher"].lower()))
    helpers = [r for r in sorted(rows, key=lambda r: (-r["subs_covered"], r["teacher"].lower())) if r["subs_covered"] > 0]
    return {
        "busiest": pick(busiest, "hours", "lectures", "teaching_days", "max_day", "load_pct", "status"),
        "most_free": pick(free, "free_periods", "capacity", "hours", "status"),
        "substitutes": pick(helpers, "subs_covered", "hours"),
    }


# ---------------------------------------------------------------------------------------------------- free-teacher finder
def free_periods_of(data: ViewData, change: DayChanges, tid: uuid.UUID, d: date) -> tuple[list[int], str | None]:
    """The periods this teacher is free on that date - or ([], why) when they are not available that day at all."""
    p = data.people.get(tid)
    if p is None or not p.is_active:
        return [], "deactivated"
    if change.is_absent(tid, d):
        return [], "absent"
    wd = d.isoweekday()
    if wd > min(6, data.days_per_week):
        return [], "not a working day"
    if p.is_visiting and wd not in p.visiting_days:
        return [], "visiting faculty - does not come that day"
    busy: set[int] = set()
    for a, b in subsvc.occupied_periods(data, change, tid, d):
        busy.update(range(a, b))
    return [n for n in range(1, len(data.grid.periods) + 1) if n not in busy], None


def period_at(data: ViewData, minutes: int) -> tuple[int | None, int | None]:
    """(the period running at that minute of the day, the next period that starts after it)."""
    for p in data.grid.periods:
        if p.start <= minutes < p.end:
            return p.number, None
    return None, next((p.number for p in data.grid.periods if p.start > minutes), None)


def free_teachers(data: ViewData, change: DayChanges, d: date, *, start: int | None = None, length: int = 1) -> dict[str, Any]:
    """Who is free on `d`. With `start`: those free for periods start..start+length-1 (a teacher who marked it 'not available' is
    kept but flagged and listed last). Without: everyone with at least one free period, plus a free / busy mask per period."""
    total = len(data.grid.periods)
    wd = d.isoweekday()
    out: list[dict[str, Any]] = []
    for tid, p in data.people.items():
        free, why = free_periods_of(data, change, tid, d)
        if why is not None or not free:
            continue
        if start is not None and not all(n in free for n in range(start, start + length)):
            continue
        pref = data.prefs.get(tid)
        marked = bool(start is not None and pref and any((wd, n) in pref.unavailable for n in range(start, start + length)))
        out.append({
            "teacher_id": str(tid), "name": p.name, "school": data.school_name, "school_id": str(data.school_id),
            "role": "HOD" if p.role == "hod" else ("Visiting" if p.is_visiting else "Teacher"), "is_visiting": p.is_visiting,
            "specialization": p.specialization,
            "free_periods": [{"period": n, "time": timing_svc.time_range(data.grid, n, 1)} for n in free],
            "free_mask": [n in free for n in range(1, total + 1)], "free_count": len(free), "load_today": total - len(free),
            "marked_unavailable": marked,
        })
    out.sort(key=lambda r: (r["marked_unavailable"], r["load_today"], r["name"].lower()))
    return {
        "date": d.isoformat(), "weekday": wd, "period": start, "length": length,
        "time": timing_svc.time_range(data.grid, start, length) if start is not None else None,
        "periods": [{"period": p.number, "time": timing_svc.time_range(data.grid, p.number, 1)} for p in data.grid.periods],
        "teachers": out, "banners": vw.banners_for(data, d),
    }


# ---------------------------------------------------------------------------------------------------- subject hours
def hours_report(per_class: Mapping[uuid.UUID, list[Any]], school_name: str, school_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(one row per subject, one row per class) from hours.compute_hours() output."""
    rows: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    for sem_id, subjects in per_class.items():
        req = dlv = pln = short = taught = planned = 0
        label = subjects[0].class_label if subjects else ""
        for h in subjects:
            d = h.as_dict()
            rows.append({
                "school": school_name, "school_id": str(school_id), "class": h.class_label, "semester_id": str(sem_id), "subject": d["name"], "code": d["code"],
                "teachers": ", ".join(t["name"] for t in d["teachers"]) or "-", "required": d["required"], "delivered": d["delivered"],
                "deliverable": d["deliverable"], "shortfall": d["shortfall"], "lost": d["lost"], "extra": d["extra"],
                "percent_delivered": d["percent_delivered"], "percent_planned": d["percent_planned"], "status": d["status"],
                "state": "short" if d["shortfall"] > 0 else ("on track" if d["status"] in ("ok", "short") else d["status"].replace("_", " ")),
            })
            if h.status in ("ok", "short"):
                req += d["required"]
                dlv += d["delivered"]
                pln += d["deliverable"]
                short += d["shortfall"]
                # each subject counts only up to what it needs: one subject's surplus must never hide another one's shortfall
                taught += min(d["delivered"], d["required"])
                planned += min(d["deliverable"], d["required"])
        classes.append({
            "school": school_name, "class": label, "required": req, "delivered": dlv, "deliverable": pln, "shortfall": short,
            "taught": taught, "planned": planned, "percent_delivered": pct(taught, req), "percent_planned": pct(planned, req),
            "short_subjects": sum(1 for h in subjects if h.shortfall > 0), "subjects": len(subjects),
        })
    rows.sort(key=lambda r: (-r["shortfall"], r["class"], r["subject"].lower()))
    classes.sort(key=lambda c: c["class"])
    return rows, classes


# ---------------------------------------------------------------------------------------------------- months
def month_bounds(month: str | None, today: date) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-10-31); None -> the month of `today`. Raises ValueError on a bad value."""
    if month:
        y, m = (int(x) for x in month.split("-"))
        first = date(y, m, 1)
    else:
        first = today.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def month_range(end_first: date, count: int) -> list[date]:
    """The first day of each of the last `count` months, oldest first, ending with `end_first`'s month."""
    out: list[date] = []
    y, m = end_first.year, end_first.month
    for _ in range(count):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def pct(part: int | float, whole: int | float) -> int:
    return round(100 * part / whole) if whole else 0
