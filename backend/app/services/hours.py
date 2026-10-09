"""Semester-hours tracking and holiday compensation (plan Section 12) - pure logic, no database.

    deliverable hours of a subject = its published weekly lectures, counted on every working date of the semester
                                     (start date .. end date) that is not stopped by a holiday / event, + approved extra lectures
    shortfall                      = required semester hours - deliverable hours       ("DBMS - 4 hours short")

One period counts as one teaching hour (a lab block of 2 periods = 2 hours).  The tracking is "live": it is recomputed from the
published timetable, the calendar and the semester dates every time a screen asks, so a new holiday shows up at once.

suggest(): when a subject is short, find clash-free EXTRA slots on specific dates.  Every hard rule is checked again on the
exact date: the class is free, the teacher(s) are free, a working day, the class's end time, no holiday / event / semester end,
visiting-faculty days and weekly hours, active teacher, an elective group together, a lab as a double block.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from ..validation import DAY_NAMES, DAY_SHORT
from . import calindex
from . import publishing as pub
from .schedule import GAP_LIMIT, gap_runs
from .viewdata import ExtraRow, SubjInfo, ViewData


# ---------------------------------------------------------------------------------------------------- tracking
@dataclass
class SubjectHours:
    subject: SubjInfo
    class_label: str
    teachers: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    weekly: int = 0            # periods a week in the published timetable
    required: int = 0
    scheduled: int = 0         # hours the weekly timetable delivers over the whole semester (holidays already taken out)
    lost: int = 0              # hours the weekly timetable loses to holidays / events
    extra: int = 0             # hours of approved extra lectures
    delivered: int = 0         # hours up to and including today
    status: str = "ok"         # ok | short | unpublished | no_dates
    note: str | None = None

    @property
    def deliverable(self) -> int:
        return self.scheduled + self.extra

    @property
    def shortfall(self) -> int:
        if self.status in ("unpublished", "no_dates"):
            return 0
        return max(0, self.required - self.deliverable)

    @property
    def surplus(self) -> int:
        return max(0, self.deliverable - self.required) if self.status not in ("unpublished", "no_dates") else 0

    @property
    def remaining(self) -> int:
        return max(0, self.deliverable - self.delivered)

    @property
    def unit_length(self) -> int:
        return 2 if (self.subject.is_lab or self.subject.special_kind) else 1

    def as_dict(self) -> dict[str, Any]:
        s = self.subject
        return {
            "subject_id": str(s.id), "semester_id": str(s.semester_id), "class_label": self.class_label, "name": s.name, "code": s.code,
            "is_lab": s.is_lab, "special_kind": s.special_kind, "group_id": str(s.group_id) if s.group_id else None,
            "teachers": [{"id": str(i), "name": n} for i, n in self.teachers],
            "required": self.required, "weekly": self.weekly, "scheduled": self.scheduled, "lost": self.lost, "extra": self.extra,
            "deliverable": self.deliverable, "delivered": self.delivered, "remaining": self.remaining,
            "shortfall": self.shortfall, "surplus": self.surplus, "status": self.status, "note": self.note,
            "extra_lectures_needed": -(-self.shortfall // self.unit_length) if self.shortfall else 0,
            "percent_delivered": round(100 * min(self.delivered, self.required) / self.required) if self.required else 0,
            "percent_planned": round(100 * min(self.deliverable, self.required) / self.required) if self.required else 0,
        }


def _row_subject(data: ViewData, r: pub.PubRow) -> uuid.UUID | None:
    """The subject a published row counts for. A PE / SOL slot that is not linked to a subject counts for its class's PE / SOL subject."""
    if r.subject_id is not None:
        return r.subject_id
    if r.kind == "fixed" and r.fixed_kind in ("PE", "SOL"):
        for s in data.subjects.values():
            if s.semester_id == r.semester_id and s.special_kind == r.fixed_kind:
                return s.id
    return None


def compute_hours(
    data: ViewData, today: date, only_sems: Iterable[uuid.UUID] | None = None,
    assigned: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]] | None = None,
) -> dict[uuid.UUID, list[SubjectHours]]:
    """Per running class: every subject with its required / deliverable / delivered hours.
    `assigned` (optional) names the teacher of a subject that is not in the published timetable (yet)."""
    wanted = set(only_sems) if only_sems is not None else set(data.sems)
    extra_by_subject: dict[uuid.UUID, list[ExtraRow]] = defaultdict(list)
    for x in data.extras:
        extra_by_subject[x.subject_id].append(x)
    out: dict[uuid.UUID, list[SubjectHours]] = {}
    for sem_id, info in data.sems.items():
        if sem_id not in wanted:
            continue
        publication = data.pubs.get(sem_id)
        dates = data.term_dates.get(sem_id)
        placements: dict[uuid.UUID, list[tuple[int, int, int]]] = defaultdict(list)   # subject -> (day, start, length)
        teachers: dict[uuid.UUID, dict[uuid.UUID, str]] = defaultdict(dict)
        for r in data.rows_of_class(sem_id):
            sid = _row_subject(data, r)
            if sid is None:
                continue
            placements[sid].append((r.day, r.start, r.length))
            for t in r.teachers:
                teachers[sid][t] = data.person_name(t) or "A teacher"
        rows: list[SubjectHours] = []
        for s in sorted((x for x in data.subjects.values() if x.semester_id == sem_id), key=lambda x: x.name.lower()):
            names = dict(teachers[s.id])
            if not names and assigned:
                for t in assigned.get(s.id, (None, None)):
                    if t is not None:
                        names[t] = data.person_name(t) or "A teacher"
            h = SubjectHours(s, info.label, sorted(names.items(), key=lambda kv: kv[1]), required=s.hours)
            h.weekly = sum(length for _d, _st, length in placements[s.id])
            if publication is None:
                h.status, h.note = "unpublished", "This class's timetable is not published yet."
            elif dates is None:
                h.status, h.note = "no_dates", "Set the semester start and end dates on the Active Term screen."
            else:
                first, last = dates
                for d in calindex.daterange(first, last):
                    wd = d.isoweekday()
                    if wd > min(6, data.days_per_week):
                        continue
                    for day, start, length in placements[s.id]:
                        if day != wd:
                            continue
                        a, b = data.period_minutes(start, length)
                        if data.calendar.blocking(data.school_id, sem_id, d, a, b) is not None:
                            h.lost += length
                        else:
                            h.scheduled += length
                            if d <= today:
                                h.delivered += length
                for x in extra_by_subject.get(s.id, []):
                    h.extra += x.length
                    if x.day_date <= today:
                        h.delivered += x.length
                if h.weekly == 0:
                    h.note = "No lecture of this subject is in the published timetable."
                h.status = "short" if h.required > h.deliverable else "ok"
            rows.append(h)
        out[sem_id] = rows
    return out


def summarize(per_class: dict[uuid.UUID, list[SubjectHours]]) -> dict[str, Any]:
    all_rows = [h for rows in per_class.values() for h in rows]
    short = [h for h in all_rows if h.shortfall > 0]
    return {
        "subjects": len(all_rows),
        "short_subjects": len(short),
        "short_hours": sum(h.shortfall for h in short),
        "unpublished_classes": len({h.subject.semester_id for h in all_rows if h.status == "unpublished"}),
        "worst": [
            {"subject_id": str(h.subject.id), "name": h.subject.name, "class_label": h.class_label, "shortfall": h.shortfall}
            for h in sorted(short, key=lambda h: -h.shortfall)[:5]
        ],
    }


# ---------------------------------------------------------------------------------------------------- compensation slots
def unit_of(data: ViewData, subject_id: uuid.UUID) -> list[uuid.UUID]:
    """A subject - or, for an elective option, ALL options of its group (they always run together)."""
    s = data.subjects[subject_id]
    if s.group_id is None:
        return [s.id]
    return sorted((x.id for x in data.subjects.values() if x.group_id == s.group_id and x.semester_id == s.semester_id), key=str)


def teachers_of_subjects(data: ViewData, assigned: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]]) -> dict[uuid.UUID, tuple[uuid.UUID, ...]]:
    """Who teaches each subject: the PUBLISHED timetable first (what is live), the current assignment as a fallback."""
    found: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for r in data.rows:
        sid = _row_subject(data, r)
        if sid is not None:
            for t in r.teachers:
                if t not in found[sid]:
                    found[sid].append(t)
    out: dict[uuid.UUID, tuple[uuid.UUID, ...]] = {}
    for sid in data.subjects:
        if found.get(sid):
            out[sid] = tuple(found[sid])
        else:
            t, co = assigned.get(sid, (None, None))
            out[sid] = tuple(x for x in (t, co) if x is not None)
    return out


@dataclass
class Slot:
    day_date: date
    start: int
    length: int
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


class Planner:
    """Knows who is busy when (published timetable + extra lectures + slots already chosen) and checks / ranks extra slots."""

    def __init__(self, data: ViewData, subject_ids: list[uuid.UUID], teacher_map: dict[uuid.UUID, tuple[uuid.UUID, ...]]):
        self.data = data
        self.subject_ids = subject_ids
        self.subs = [data.subjects[s] for s in subject_ids]
        self.sem = self.subs[0].semester_id
        self.length = 2 if (self.subs[0].is_lab or self.subs[0].special_kind) else 1
        self.teachers = tuple(dict.fromkeys(t for s in subject_ids for t in teacher_map.get(s, ())))
        self.label = data.sems[self.sem].label
        self.class_busy: dict[tuple[uuid.UUID, int], list[tuple[int, int]]] = defaultdict(list)       # (class, weekday) -> [start, end)
        self.teacher_busy: dict[tuple[uuid.UUID, int], list[tuple[int, int]]] = defaultdict(list)
        self.class_extra: dict[tuple[uuid.UUID, date], list[tuple[int, int]]] = defaultdict(list)
        self.teacher_extra: dict[tuple[uuid.UUID, date], list[tuple[int, int]]] = defaultdict(list)
        self.week_hours: dict[uuid.UUID, int] = defaultdict(int)                                       # published hours a week
        self.extra_week_hours: dict[tuple[uuid.UUID, date], int] = defaultdict(int)
        self.chosen: list[Slot] = []
        self.rejections: Counter = Counter()  # why candidate slots were refused (code -> how many slots)
        self.unit_days = {r.day for r in data.rows if r.subject_id in set(subject_ids)}  # weekdays this subject already has a lecture
        self.unit_extra_dates = {x.day_date for x in data.extras if x.subject_id in set(subject_ids)}
        seen: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for r in data.rows:
            self.class_busy[(r.semester_id, r.day)].append((r.start, r.start + r.length))
            for t in r.teachers:
                self.teacher_busy[(t, r.day)].append((r.start, r.start + r.length))
                if r.key not in seen[t]:
                    seen[t].add(r.key)
                    self.week_hours[t] += r.length
        counted: set[tuple[uuid.UUID, uuid.UUID, date]] = set()
        for x in data.extras:
            self.class_extra[(x.semester_id, x.day_date)].append((x.start, x.start + x.length))
            for t in x.teachers:
                self.teacher_extra[(t, x.day_date)].append((x.start, x.start + x.length))
                k = (t, x.key, x.day_date)
                if k not in counted:
                    counted.add(k)
                    self.extra_week_hours[(t, calindex.week_start(x.day_date))] += x.length

    # -------------------------------------------------------------------------------- checks
    @staticmethod
    def _hits(spans: list[tuple[int, int]], start: int, end: int) -> bool:
        return any(a < end and start < b for a, b in spans)

    def coded(self, d: date, start: int) -> list[tuple[str, str]]:
        """Hard-rule problems of an extra lecture at that date / first period as (code, message). Empty = it is possible."""
        data, length = self.data, self.length
        end = start + length
        wd = d.isoweekday()
        out: list[tuple[str, str]] = []
        dates = data.term_dates.get(self.sem)
        if dates is None:
            return [("no_dates", "The semester dates of this class are not set (Active Term screen).")]
        if not dates[0] <= d <= dates[1]:
            return [("outside", f"{d.strftime('%d %b %Y')} is outside the semester ({dates[0].strftime('%d %b')} - {dates[1].strftime('%d %b %Y')}).")]
        if wd > min(6, data.days_per_week):
            return [("not_working", f"{DAY_NAMES.get(wd, 'Sunday')} is not a working day.")]
        rule = data.rule(self.sem, wd)
        if rule.is_off:
            return [("day_off", f"{self.label} has no lectures on {DAY_NAMES[wd]} (the day is OFF).")]
        total = len(data.grid.periods)
        if start < 1 or end - 1 > total:
            return [("no_period", f"The day only has {total} periods.")]
        if rule.end_time is not None:
            limit = rule.end_time.hour * 60 + rule.end_time.minute
            if any(data.grid.periods[n - 1].end > limit for n in range(start, end)):
                out.append(("after_end", f"That is after {self.label}'s end time on {DAY_NAMES[wd]}."))
        a, b = data.period_minutes(start, length)
        blocker = data.calendar.blocking(data.school_id, self.sem, d, a, b)
        if blocker is not None:
            out.append(("holiday", f"{blocker.title} ({blocker.kind}) stops lectures at that time."))
        if self._hits(self.class_busy[(self.sem, wd)], start, end):
            out.append(("class_busy", f"{self.label} already has a lecture at that time on {DAY_NAMES[wd]}."))
        if self._hits(self.class_extra[(self.sem, d)], start, end):
            out.append(("class_busy", f"{self.label} already has an extra lecture at that time on {d.strftime('%d %b')}."))
        for t in self.teachers:
            p = data.people.get(t)
            name = data.person_name(t) or "The teacher"
            if p is not None and not p.is_active:
                out.append(("inactive", f"{name} is deactivated."))
                continue
            if p is not None and p.is_visiting and wd not in p.visiting_days:
                out.append(("visiting_day", f"{name} is visiting faculty and does not come on {DAY_NAMES[wd]}."))
            if self._hits(self.teacher_busy[(t, wd)], start, end):
                out.append(("teacher_busy", f"{name} is teaching another class at that time on {DAY_NAMES[wd]}."))
            if self._hits(self.teacher_extra[(t, d)], start, end):
                out.append(("teacher_busy", f"{name} already has an extra lecture at that time on {d.strftime('%d %b')}."))
            if p is not None and p.is_visiting and p.max_weekly_hours:
                monday = calindex.week_start(d)
                if self.week_hours.get(t, 0) + self.extra_week_hours.get((t, monday), 0) + length > p.max_weekly_hours:
                    out.append(("visiting_hours", f"{name} would pass the weekly limit of {p.max_weekly_hours} hours that week."))
        return list(dict.fromkeys(out))

    def problems(self, d: date, start: int) -> list[str]:
        return [m for _c, m in self.coded(d, start)]

    def add(self, slot: Slot) -> None:
        """Remember a chosen slot so the next ones do not collide with it."""
        d, start, length = slot.day_date, slot.start, slot.length
        self.class_extra[(self.sem, d)].append((start, start + length))
        for t in self.teachers:
            self.teacher_extra[(t, d)].append((start, start + length))
            self.extra_week_hours[(t, calindex.week_start(d))] += length
        self.chosen.append(slot)

    # -------------------------------------------------------------------------------- ranking
    def _score(self, d: date, start: int, today: date) -> tuple[float, list[str]]:
        wd = d.isoweekday()
        end = start + self.length
        reasons = ["The class is free", *(f"{self.data.person_name(t)} is free" for t in self.teachers)]
        score = 100.0
        occupied = {p for a, b in self.class_busy[(self.sem, wd)] for p in range(a, b)} | {
            p for a, b in self.class_extra[(self.sem, d)] for p in range(a, b)
        }
        new = set(range(start, end))
        if occupied:
            if start - 1 in occupied or end in occupied:
                score += 15
                reasons.append("Right next to another lecture - no gap")
            if set(gap_runs(occupied | new)) - set(gap_runs(occupied)):
                score -= 25
                reasons.append("Leaves a long gap in the class's day")
            score -= 2 * len({p for p in occupied})
        else:
            score -= 10
        for t in self.teachers:
            t_occ = {p for a, b in self.teacher_busy[(t, wd)] for p in range(a, b)} | {
                p for a, b in self.teacher_extra[(t, d)] for p in range(a, b)
            }
            if t_occ:
                if start - 1 in t_occ or end in t_occ:
                    score += 5
                if set(gap_runs(t_occ | new)) - set(gap_runs(t_occ)):
                    score -= 12
        if self.length > 1 and self.data.grid.lunch_inside(start, self.length):
            score -= 10
        if wd in self.unit_days or d in self.unit_extra_dates or any(c.day_date == d for c in self.chosen):
            score -= 30
            reasons.append("The subject is already taught that day (not ideal)")
        score -= 0.4 * (d - today).days
        same_week = sum(1 for c in self.chosen if calindex.week_start(c.day_date) == calindex.week_start(d))
        score -= 12 * same_week
        return score, reasons

    def candidates(self, today: date) -> list[Slot]:
        dates = self.data.term_dates.get(self.sem)
        if dates is None:
            return []
        first = max(today + timedelta(days=1), dates[0])
        out: list[Slot] = []
        for d in calindex.daterange(first, dates[1]):
            wd = d.isoweekday()
            if wd > min(6, self.data.days_per_week) or self.data.rule(self.sem, wd).is_off:
                continue
            if self.data.calendar.full_day(self.data.school_id, self.sem, d) is not None:
                continue
            for start in range(1, len(self.data.grid.periods) - self.length + 2):
                found = self.coded(d, start)
                if found:
                    for code in {c for c, _m in found}:
                        self.rejections[code] += 1
                    continue
                score, reasons = self._score(d, start, today)
                out.append(Slot(d, start, self.length, score, reasons))
        return out

    def suggest(self, count: int, today: date) -> list[Slot]:
        """The best `count` clash-free extra slots (greedy: after each pick the others are re-checked against it)."""
        for i in range(count):
            self.rejections = Counter()
            cands = self.candidates(today)
            if not cands:
                break
            best = max(cands, key=lambda s: (s.score, -s.day_date.toordinal(), -s.start))
            self.add(best)
        return list(self.chosen)


def slot_out(data: ViewData, planner: Planner, s: Slot) -> dict[str, Any]:
    from . import timing as timing_svc

    return {
        "date": s.day_date.isoformat(), "weekday": s.day_date.isoweekday(), "weekday_name": DAY_NAMES.get(s.day_date.isoweekday(), ""),
        "start_period": s.start, "length": s.length, "time": timing_svc.time_range(data.grid, s.start, s.length),
        "score": round(s.score, 1), "reasons": s.reasons[:5],
        "subjects": [{"id": str(x.id), "name": x.name} for x in planner.subs], "class_label": planner.label,
    }


REJECT_WORDS = {
    "class_busy": "the class already has a lecture then",
    "teacher_busy": "the teacher is teaching another class then",
    "visiting_hours": "the visiting teacher's weekly hour limit would be passed",
    "visiting_day": "the visiting teacher does not come on that day",
    "holiday": "a holiday / event stops lectures",
    "day_off": "the class has that day off",
    "after_end": "the class has already ended for the day",
    "inactive": "the teacher is deactivated",
}


def why_not_more(planner: Planner) -> str:
    """One plain sentence on what blocked the most slots (used when fewer extra slots were found than needed)."""
    top = [(REJECT_WORDS[c], n) for c, n in planner.rejections.most_common() if c in REJECT_WORDS][:3]
    if not top:
        return ""
    return "Most slots were refused because " + "; ".join(f"{w} ({n} slots)" for w, n in top) + "."
