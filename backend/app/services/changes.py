"""Phase 5 one-off changes to a specific date (absence, substitution, one-day adjustment) - the data model + a loader.

The weekly PUBLISHED timetable (Phase 4) never changes here.  On top of it, for a given DATE, three things can differ:
  * an ABSENCE     a teacher is away that day
  * a SUBSTITUTION one absent teacher's one time-slot is (or needs to be) covered by someone else
  * an ADJUSTMENT  one class's lecture is moved to another period, or cancelled, for that date only (a swap = two moves)

`DayChanges` holds these for a date window and answers the small questions the day views and the ranking ask.
Pure data + indexing here; the async loader at the bottom is the only part that touches the database.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from .. import db


# ---------------------------------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Absence:
    id: uuid.UUID
    teacher_id: uuid.UUID
    day_date: date
    reason: str | None
    source: str


@dataclass(frozen=True)
class Adjustment:
    id: uuid.UUID
    day_date: date
    kind: str                                  # 'move' | 'cancel'
    semester_ids: frozenset[uuid.UUID]
    orig_start: int
    length: int
    new_start: int | None
    subject_ids: frozenset[uuid.UUID]
    parallel_key: uuid.UUID | None
    label: str
    teacher_id: uuid.UUID | None
    co_teacher_id: uuid.UUID | None
    swap_group: uuid.UUID | None
    reason: str | None

    @property
    def teachers(self) -> tuple[uuid.UUID, ...]:
        return tuple(t for t in (self.teacher_id, self.co_teacher_id) if t is not None)


@dataclass(frozen=True)
class Sub:
    id: uuid.UUID
    absence_id: uuid.UUID | None
    day_date: date
    start: int
    length: int
    semester_ids: frozenset[uuid.UUID]
    subject_ids: frozenset[uuid.UUID]
    parallel_key: uuid.UUID | None
    label: str
    absent_teacher_id: uuid.UUID
    status: str                                # 'open' | 'offering' | 'filled' | 'unfilled' | 'cancelled'
    mode: str | None
    substitute_id: uuid.UUID | None
    offer_deadline: datetime | None
    note: str | None

    @property
    def active(self) -> bool:
        """Still matters for the day's timetable (not a cancelled/withdrawn need)."""
        return self.status != "cancelled"


@dataclass
class DayChanges:
    absences: list[Absence] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    subs: list[Sub] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._absent: set[tuple[uuid.UUID, date]] = {(a.teacher_id, a.day_date) for a in self.absences}
        self._absence_by: dict[tuple[uuid.UUID, date], Absence] = {(a.teacher_id, a.day_date): a for a in self.absences}
        self._adj_by_date: dict[date, list[Adjustment]] = defaultdict(list)
        for adj in self.adjustments:
            self._adj_by_date[adj.day_date].append(adj)
        self._subs_by_date: dict[date, list[Sub]] = defaultdict(list)
        self._cover: dict[tuple[uuid.UUID, date], list[Sub]] = defaultdict(list)
        for s in self.subs:
            if not s.active:
                continue
            self._subs_by_date[s.day_date].append(s)
            if s.substitute_id is not None and s.status == "filled":
                self._cover[(s.substitute_id, s.day_date)].append(s)

    # ------------------------------------------------------------ small answers
    def is_absent(self, teacher_id: uuid.UUID, d: date) -> bool:
        return (teacher_id, d) in self._absent

    def absence(self, teacher_id: uuid.UUID, d: date) -> Absence | None:
        return self._absence_by.get((teacher_id, d))

    def adjustment_for(self, d: date, sems: Iterable[uuid.UUID], orig_start: int) -> Adjustment | None:
        """The move/cancel that touches a lecture of these classes at this base period on this date (if any)."""
        wanted = set(sems)
        for adj in self._adj_by_date.get(d, ()):
            if adj.orig_start == orig_start and (adj.semester_ids & wanted):
                return adj
        return None

    def subs_at(self, d: date, start: int, teachers: Iterable[uuid.UUID]) -> list[Sub]:
        """Active substitutions on this date at this base period whose absent teacher is one of `teachers`."""
        tset = set(teachers)
        return [s for s in self._subs_by_date.get(d, ()) if s.start == start and s.absent_teacher_id in tset]

    def covering(self, teacher_id: uuid.UUID, d: date) -> list[Sub]:
        """Filled substitutions on this date where this teacher is the substitute."""
        return list(self._cover.get((teacher_id, d), ()))

    def subs_on(self, d: date) -> list[Sub]:
        return list(self._subs_by_date.get(d, ()))


EMPTY = DayChanges()


# ---------------------------------------------------------------------------------------------------- loader
def _ids(value: Any) -> frozenset[uuid.UUID]:
    return frozenset(value or ())


async def load_changes(school_id: uuid.UUID, term_id: uuid.UUID, window: tuple[date, date]) -> DayChanges:
    """Every absence, substitution and one-day adjustment of this school that touches the date window."""
    lo, hi = window
    absences, subs, adjusts = await db.fetch(
        "select id, teacher_id, absence_date, reason, source from public.teacher_absences "
        "where school_id = $1 and term_id = $2 and absence_date between $3 and $4",
        school_id, term_id, lo, hi,
    ), await db.fetch(
        "select id, absence_id, lecture_date, start_period, length, semester_ids, subject_ids, parallel_key, label, "
        "absent_teacher_id, status, mode, substitute_id, offer_deadline, note from public.substitutions "
        "where school_id = $1 and term_id = $2 and lecture_date between $3 and $4",
        school_id, term_id, lo, hi,
    ), await db.fetch(
        "select id, lecture_date, kind, semester_ids, orig_start, length, new_start, subject_ids, parallel_key, label, "
        "teacher_id, co_teacher_id, swap_group, reason from public.day_adjustments "
        "where school_id = $1 and term_id = $2 and lecture_date between $3 and $4",
        school_id, term_id, lo, hi,
    )
    return DayChanges(
        absences=[Absence(a["id"], a["teacher_id"], a["absence_date"], a["reason"], a["source"]) for a in absences],
        adjustments=[
            Adjustment(
                j["id"], j["lecture_date"], j["kind"], _ids(j["semester_ids"]), j["orig_start"], j["length"], j["new_start"],
                _ids(j["subject_ids"]), j["parallel_key"], j["label"], j["teacher_id"], j["co_teacher_id"], j["swap_group"], j["reason"],
            )
            for j in adjusts
        ],
        subs=[
            Sub(
                s["id"], s["absence_id"], s["lecture_date"], s["start_period"], s["length"], _ids(s["semester_ids"]),
                _ids(s["subject_ids"]), s["parallel_key"], s["label"], s["absent_teacher_id"], s["status"], s["mode"],
                s["substitute_id"], s["offer_deadline"], s["note"],
            )
            for s in subs
        ],
    )
