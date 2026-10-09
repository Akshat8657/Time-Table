"""Loads everything the PUBLISHED views, the exports and the semester-hours tracking need (one parallel round of queries).

Teachers and the printed timetables read ONLY the published tables - never the drafts.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import asyncpg

from .. import db
from . import calindex
from . import publishing as pub
from . import terms as term_svc
from . import timing as timing_svc
from .grid import Grid, to_min
from .timing import DayRule


@dataclass(frozen=True)
class SemInfo:
    id: uuid.UUID
    label: str
    course_name: str
    number: int


@dataclass(frozen=True)
class SubjInfo:
    id: uuid.UUID
    semester_id: uuid.UUID
    name: str
    code: str
    credits: int
    weekly: int               # required lectures a week (credits unless overridden)
    hours: int                # semester hours
    is_lab: bool
    special_kind: str | None
    group_id: uuid.UUID | None


@dataclass(frozen=True)
class PersonInfo:
    id: uuid.UUID
    name: str
    role: str
    is_active: bool
    is_visiting: bool
    visiting_days: frozenset[int]
    max_weekly_hours: int | None


@dataclass(frozen=True)
class ExtraRow:
    id: uuid.UUID
    semester_id: uuid.UUID
    subject_id: uuid.UUID
    day_date: date
    start: int
    length: int
    parallel_key: uuid.UUID | None
    teacher_id: uuid.UUID | None
    co_teacher_id: uuid.UUID | None
    reason: str | None

    @property
    def key(self) -> uuid.UUID:
        return self.parallel_key or self.id

    @property
    def teachers(self) -> tuple[uuid.UUID, ...]:
        return tuple(t for t in (self.teacher_id, self.co_teacher_id) if t is not None)


@dataclass
class ViewData:
    school_id: uuid.UUID
    school_name: str
    college_name: str
    term: asyncpg.Record
    grid: Grid
    days_per_week: int
    sems: dict[uuid.UUID, SemInfo]
    rules: dict[tuple[uuid.UUID, int], DayRule]
    rows: list[pub.PubRow]
    pubs: dict[uuid.UUID, asyncpg.Record]
    subjects: dict[uuid.UUID, SubjInfo]
    people: dict[uuid.UUID, PersonInfo]
    calendar: calindex.CalendarIndex
    term_dates: dict[uuid.UUID, tuple[date, date]]
    extras: list[ExtraRow] = field(default_factory=list)

    def person_name(self, pid: uuid.UUID | None) -> str | None:
        if pid is None:
            return None
        p = self.people.get(pid)
        return p.name if p else "A teacher"

    def rows_of_class(self, sem_id: uuid.UUID) -> list[pub.PubRow]:
        return [r for r in self.rows if r.semester_id == sem_id]

    def rows_of_teacher(self, teacher_id: uuid.UUID) -> list[pub.PubRow]:
        """The teacher's own rows, plus the other options that run in PARALLEL with theirs (so the cell can say what runs beside it)."""
        mine = [r for r in self.rows if teacher_id in r.teachers]
        mine_ids = {r.id for r in mine}
        keys = {(r.semester_id, r.group_key) for r in mine if r.kind == "lecture" and r.group_key}
        return mine + [r for r in self.rows if r.id not in mine_ids and r.kind == "lecture" and r.group_key and (r.semester_id, r.group_key) in keys]

    def period_minutes(self, start: int, length: int) -> tuple[int, int]:
        a, b = self.grid.period(start), self.grid.period(start + length - 1)
        if a is None or b is None:
            return 0, 0
        return a.start, b.end

    def rule(self, sem_id: uuid.UUID, day: int) -> DayRule:
        return self.rules.get((sem_id, day), timing_svc.FULL_DAY)


def _to_min(t: Any) -> int | None:
    return None if t is None else to_min(t)


async def load_view_data(school_id: uuid.UUID, term: asyncpg.Record, window: tuple[date, date] | None = None) -> ViewData:
    """`window` = the dates the calendar and the extra lectures are needed for (None = the whole term)."""
    (grid, college), sems, rule_rows, pubs, rows, subs, people, tdates, school = await asyncio.gather(
        timing_svc.school_grid(school_id),
        db.fetch(
            """
            select sm.id, sm.number, c.name as course_name
            from public.semesters sm join public.courses c on c.id = sm.course_id
            where sm.school_id = $1 and c.is_active and (sm.number % 2 = 1) = ($2 = 'odd')
            order by c.name, sm.number
            """,
            school_id, term["kind"],
        ),
        db.fetch("select semester_id, day, is_off, end_time from public.semester_days where school_id = $1", school_id),
        db.fetch(
            "select p.*, u.full_name as published_by_name from public.publications p left join public.profiles u on u.id = p.published_by "
            "where p.school_id = $1 and p.term_id = $2",
            school_id, term["id"],
        ),
        db.fetch(
            "select id, semester_id, kind, subject_id, fixed_kind, label, day, start_period, length, group_key, teacher_id, co_teacher_id, "
            "combined_with from public.published_entries where school_id = $1 and term_id = $2",
            school_id, term["id"],
        ),
        db.fetch(
            """
            select s.id, s.semester_id, s.name, s.code, s.credits, s.weekly_lectures, s.semester_hours, s.is_lab, s.special_kind, s.elective_group_id
            from public.subjects s where s.school_id = $1
            """,
            school_id,
        ),
        db.fetch(
            "select id, full_name, role, is_active, is_visiting, visiting_days, max_weekly_hours from public.profiles "
            "where school_id = $1 and role in ('teacher', 'hod')",
            school_id,
        ),
        db.fetch("select semester_id, start_date, end_date from public.term_semesters where school_id = $1 and term_id = $2", school_id, term["id"]),
        db.fetchrow("select name from public.schools where id = $1", school_id),
    )
    term_dates = {r["semester_id"]: (r["start_date"], r["end_date"]) for r in tdates}
    if window is None and term_dates:
        window = (min(a for a, _ in term_dates.values()), max(b for _, b in term_dates.values()))
    entries: list[calindex.Entry] = []
    extras: list[ExtraRow] = []
    if window is not None:
        cal_rows, extra_rows = await asyncio.gather(
            db.fetch(
                "select id, school_id, kind, title, date_from, date_to, full_day, start_time, end_time from public.calendar_entries "
                "where (school_id is null or school_id = $1) and date_to >= $2 and date_from <= $3",
                school_id, window[0], window[1],
            ),
            db.fetch(
                "select id, semester_id, subject_id, lecture_date, start_period, length, parallel_key, teacher_id, co_teacher_id, reason "
                "from public.extra_lectures where school_id = $1 and term_id = $2 and lecture_date between $3 and $4 order by lecture_date, start_period",
                school_id, term["id"], window[0], window[1],
            ),
        )
        links = await db.fetch(
            "select entry_id, semester_id from public.calendar_entry_classes where entry_id = any($1::uuid[])",
            [r["id"] for r in cal_rows],
        ) if cal_rows else []
        by_entry: dict[uuid.UUID, set[uuid.UUID]] = {}
        for link in links:
            by_entry.setdefault(link["entry_id"], set()).add(link["semester_id"])
        entries = [
            calindex.Entry(
                r["id"], r["school_id"], r["kind"], r["title"], r["date_from"], r["date_to"], r["full_day"],
                _to_min(r["start_time"]), _to_min(r["end_time"]), frozenset(by_entry.get(r["id"], ())),
            )
            for r in cal_rows
        ]
        extras = [
            ExtraRow(r["id"], r["semester_id"], r["subject_id"], r["lecture_date"], r["start_period"], r["length"], r["parallel_key"],
                     r["teacher_id"], r["co_teacher_id"], r["reason"])
            for r in extra_rows
        ]
    return ViewData(
        school_id=school_id, school_name=school["name"] if school else "", college_name=(college["college_name"] or "College"),
        term=term, grid=grid, days_per_week=college["days_per_week"],
        sems={s["id"]: SemInfo(s["id"], term_svc.semester_label(s["course_name"], s["number"]), s["course_name"], s["number"]) for s in sems},
        rules=timing_svc.rules_from_rows(rule_rows),
        rows=[pub.row_from_record(r) for r in rows],
        pubs={p["semester_id"]: p for p in pubs},
        subjects={
            s["id"]: SubjInfo(
                s["id"], s["semester_id"], s["name"], s["code"], s["credits"],
                s["weekly_lectures"] if s["weekly_lectures"] is not None else s["credits"], s["semester_hours"], s["is_lab"],
                s["special_kind"], s["elective_group_id"],
            )
            for s in subs
        },
        people={
            p["id"]: PersonInfo(p["id"], p["full_name"], p["role"], p["is_active"], p["is_visiting"], frozenset(p["visiting_days"] or []), p["max_weekly_hours"])
            for p in people
        },
        calendar=calindex.CalendarIndex(entries), term_dates=term_dates, extras=extras,
    )
