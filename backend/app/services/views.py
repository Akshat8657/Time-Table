"""Turns PUBLISHED rows into what the timetable screens, the PDF and the Excel export show - pure logic, no database.

A "cell" is one thing in the week grid: a lecture (or the parallel options of an elective group), a locked slot (PE / SOL /
combined sessions) or a one-off extra (compensation) lecture on a date.  Cells know when they happen (day, period, time), who
teaches, which class(es) attend, whether they are combined / parallel, and - when a date is given - whether a holiday, an
event or the end of the semester stops them that day.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from ..validation import DAY_NAMES, DAY_SHORT
from . import calindex
from . import publishing as pub
from . import timing as timing_svc
from .viewdata import ExtraRow, ViewData

IST = timezone(timedelta(hours=5, minutes=30))  # India has no daylight saving: a fixed offset is exact


def today_ist() -> date:
    return datetime.now(IST).date()


def monday_of(d: date) -> date:
    return calindex.week_start(d)


def week_dates(monday: date, days_per_week: int) -> dict[int, date]:
    return {day: monday + timedelta(days=day - 1) for day in range(1, min(6, days_per_week) + 1)}


# ---------------------------------------------------------------------------------------------------- date status
def day_status(data: ViewData, sem_ids: Iterable[uuid.UUID], d: date, start: int, length: int) -> dict[str, str] | None:
    """None = the lecture happens as usual; otherwise why not on that date."""
    a, b = data.period_minutes(start, length)
    for sem in sem_ids:
        dates = data.term_dates.get(sem)
        if dates and not (dates[0] <= d <= dates[1]):
            return {"status": "not_running", "note": f"The semester runs {dates[0].strftime('%d %b')} to {dates[1].strftime('%d %b %Y')}."}
        e = data.calendar.blocking(data.school_id, sem, d, a, b)
        if e is not None:
            return {"status": e.kind, "note": e.title + ("" if e.full_day else f" ({e.window()})")}
    return None


def banners_for(data: ViewData, d: date, sem_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Holidays / events to show on a day: the ones for everybody, plus those of the chosen class."""
    if sem_id is not None:
        found = data.calendar.for_class(data.school_id, sem_id, d)
    else:
        found = [e for e in data.calendar.entries if e.covers(d) and not e.semester_ids]
    return [e.as_dict() for e in found]


# ---------------------------------------------------------------------------------------------------- cells
def _item(data: ViewData, r: pub.PubRow) -> dict[str, Any]:
    subj = data.subjects.get(r.subject_id) if r.subject_id else None
    return {
        "subject_id": str(r.subject_id) if r.subject_id else None,
        "title": r.label, "code": subj.code if subj else None, "is_lab": bool(subj and subj.is_lab),
        "teacher_id": str(r.teacher_id) if r.teacher_id else None, "teacher_name": data.person_name(r.teacher_id),
        "co_teacher_id": str(r.co_teacher_id) if r.co_teacher_id else None, "co_teacher_name": data.person_name(r.co_teacher_id),
    }


def _finish_cell(data: ViewData, cell: dict[str, Any], sem_ids: list[uuid.UUID], teacher_id: uuid.UUID | None) -> dict[str, Any] | None:
    items = cell["items"]
    if teacher_id is not None:
        mine = [i for i in items if str(teacher_id) in (i["teacher_id"], i["co_teacher_id"])]
        if not mine:
            return None
        cell["parallel_with"] = [i["title"] for i in items if i not in mine]
        cell["items"] = items = mine
    cell["classes"] = [{"id": str(s), "label": data.sems[s].label} for s in sem_ids if s in data.sems]
    cell["parallel"] = len(items) > 1 or bool(cell.get("parallel_with"))
    cell["is_lab"] = any(i["is_lab"] for i in items)
    cell["time"] = timing_svc.time_range(data.grid, cell["start_period"], cell["length"])
    cell["day_name"] = DAY_SHORT.get(cell["day"])
    return cell


def build_cells(
    data: ViewData,
    rows: Iterable[pub.PubRow],
    *,
    across_classes: bool,
    teacher_id: uuid.UUID | None = None,
    sem_ids: set[uuid.UUID] | None = None,
    day: int | None = None,
    dates: dict[int, date] | None = None,
    with_extras: bool = False,
) -> list[dict[str, Any]]:
    """The cells of the week.  across_classes=True merges a combined session of several classes into one cell (teacher /
    master views); False keeps one cell per class (class view)."""
    groups: dict[tuple, list[pub.PubRow]] = {}
    for r in rows:
        if sem_ids is not None and r.semester_id not in sem_ids:
            continue
        if day is not None and r.day != day:
            continue
        key = (r.key,) if across_classes else (r.semester_id, r.key)
        groups.setdefault(key, []).append(r)
    cells: list[dict[str, Any]] = []
    for rs in groups.values():
        first = rs[0]
        items: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        for r in sorted(rs, key=lambda x: x.label.lower()):
            sig = (r.label, r.teacher_id, r.co_teacher_id) if r.kind == "fixed" else (r.subject_id, r.label, r.teacher_id, r.co_teacher_id)
            if sig not in seen:
                seen.add(sig)
                items.append(_item(data, r))
        kind = "fixed" if all(r.kind == "fixed" for r in rs) else "lecture"
        sems = sorted({r.semester_id for r in rs}, key=lambda s: data.sems[s].label if s in data.sems else "")
        cell = {
            "key": str(first.key), "kind": kind, "fixed_kind": first.fixed_kind if kind == "fixed" else None,
            "day": first.day, "start_period": first.start, "length": first.length, "items": items,
            "combined": len(sems) > 1 or any(r.combined_with for r in rs), "combined_with": first.combined_with,
            "date": None, "status": None, "status_note": None, "extra": None,
        }
        built = _finish_cell(data, cell, sems, teacher_id)
        if built is None:
            continue
        if dates and built["day"] in dates:
            built["date"] = dates[built["day"]].isoformat()
            st = day_status(data, sems, dates[built["day"]], built["start_period"], built["length"])
            if st:
                built["status"], built["status_note"] = st["status"], st["note"]
        cells.append(built)
    if with_extras and dates:
        cells += extra_cells(data, dates, teacher_id=teacher_id, sem_ids=sem_ids, day=day, across_classes=across_classes)
    cells.sort(key=lambda c: (c["day"], c["start_period"], c["classes"][0]["label"] if c["classes"] else ""))
    return cells


def extra_cells(
    data: ViewData, dates: dict[int, date], *, teacher_id: uuid.UUID | None, sem_ids: set[uuid.UUID] | None, day: int | None,
    across_classes: bool,
) -> list[dict[str, Any]]:
    by_day = {d: n for n, d in dates.items()}
    groups: dict[tuple, list[ExtraRow]] = {}
    for x in data.extras:
        if x.day_date not in by_day or (sem_ids is not None and x.semester_id not in sem_ids):
            continue
        if day is not None and by_day[x.day_date] != day:
            continue
        groups.setdefault((x.day_date, x.key), []).append(x)
    out: list[dict[str, Any]] = []
    for (d, key), xs in groups.items():
        first = xs[0]
        items = []
        for x in sorted(xs, key=lambda x: data.subjects[x.subject_id].name if x.subject_id in data.subjects else ""):
            s = data.subjects.get(x.subject_id)
            items.append({
                "subject_id": str(x.subject_id), "title": s.name if s else "Extra lecture", "code": s.code if s else None,
                "is_lab": bool(s and s.is_lab),
                "teacher_id": str(x.teacher_id) if x.teacher_id else None, "teacher_name": data.person_name(x.teacher_id),
                "co_teacher_id": str(x.co_teacher_id) if x.co_teacher_id else None, "co_teacher_name": data.person_name(x.co_teacher_id),
            })
        cell = {
            "key": f"extra:{first.id}", "kind": "extra", "fixed_kind": None, "day": by_day[d], "start_period": first.start,
            "length": first.length, "items": items, "combined": False, "combined_with": None, "date": d.isoformat(),
            "status": None, "status_note": None,
            "extra": {"id": str(first.id), "date": d.isoformat(), "reason": first.reason},
        }
        built = _finish_cell(data, cell, [first.semester_id], teacher_id)
        if built is not None:
            out.append(built)
    return out


# ---------------------------------------------------------------------------------------------------- summaries
def stats_of(cells: list[dict[str, Any]]) -> dict[str, int]:
    live = [c for c in cells if c["status"] is None]
    return {
        "lectures": len(live), "hours": sum(c["length"] for c in live), "cancelled": len(cells) - len(live),
        "extra": sum(1 for c in live if c["kind"] == "extra"),
    }


def day_headers(data: ViewData, dates: dict[int, date], sem_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    today = today_ist()
    return [
        {
            "day": n, "name": DAY_NAMES[n], "short": DAY_SHORT[n], "date": d.isoformat(), "is_today": d == today,
            "banners": banners_for(data, d, sem_id),
        }
        for n, d in dates.items()
    ]


def day_off_map(data: ViewData, sem_ids: Iterable[uuid.UUID]) -> dict[str, dict[str, Any]]:
    """Per class and weekday: is the day OFF, and when does it end?  (the grids grey those periods out)"""
    out: dict[str, dict[str, Any]] = {}
    for sem in sem_ids:
        out[str(sem)] = {
            str(d): {"is_off": data.rule(sem, d).is_off, "end_time": data.rule(sem, d).end_time.strftime("%H:%M") if data.rule(sem, d).end_time else None}
            for d in range(1, min(6, data.days_per_week) + 1)
        }
    return out
