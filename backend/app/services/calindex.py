"""Holidays and events as an in-memory index (plan Section 12) - pure logic, no database.

A calendar entry blocks lectures of a class on a date when
  * it covers that date (date_from .. date_to),
  * it is college-wide (school_id is None) OR belongs to the class's school,
  * it lists no classes (= every class of the school) OR lists this class,
  * and it is a full-day entry OR its time window overlaps the lecture.

Both holidays and events block: during an event the class is not in its normal lectures.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable


@dataclass(frozen=True)
class Entry:
    id: uuid.UUID
    school_id: uuid.UUID | None
    kind: str                       # 'holiday' | 'event'
    title: str
    date_from: date
    date_to: date
    full_day: bool
    start_min: int | None           # minutes since midnight, partial-day entries only
    end_min: int | None
    semester_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)  # empty = every class of the school

    def covers(self, d: date) -> bool:
        return self.date_from <= d <= self.date_to

    def applies_to(self, school_id: uuid.UUID | None, semester_id: uuid.UUID | None) -> bool:
        if self.school_id is not None and school_id is not None and self.school_id != school_id:
            return False
        if self.school_id is not None and school_id is None:
            return False
        return not self.semester_ids or semester_id in self.semester_ids

    def window(self) -> str:
        if self.full_day or self.start_min is None or self.end_min is None:
            return "all day"
        return f"{self.start_min // 60:02d}:{self.start_min % 60:02d}-{self.end_min // 60:02d}:{self.end_min % 60:02d}"

    def as_dict(self) -> dict[str, Any]:
        return {"id": str(self.id), "kind": self.kind, "title": self.title, "full_day": self.full_day, "window": self.window()}


class CalendarIndex:
    def __init__(self, entries: Iterable[Entry]):
        self.entries = list(entries)

    def for_class(self, school_id: uuid.UUID | None, semester_id: uuid.UUID | None, d: date) -> list[Entry]:
        """Every entry that touches this class on this date (full-day or partial)."""
        return [e for e in self.entries if e.covers(d) and e.applies_to(school_id, semester_id)]

    def full_day(self, school_id: uuid.UUID | None, semester_id: uuid.UUID | None, d: date) -> Entry | None:
        return next((e for e in self.for_class(school_id, semester_id, d) if e.full_day), None)

    def blocking(
        self, school_id: uuid.UUID | None, semester_id: uuid.UUID | None, d: date, start_min: int, end_min: int
    ) -> Entry | None:
        """The entry that blocks a lecture from start_min to end_min on that date (None = free)."""
        for e in self.for_class(school_id, semester_id, d):
            if e.full_day or e.start_min is None or e.end_min is None:
                return e
            if e.start_min < end_min and start_min < e.end_min:
                return e
        return None


def week_start(d: date) -> date:
    """The Monday of the week that contains d."""
    return d - timedelta(days=d.isoweekday() - 1)


def daterange(first: date, last: date) -> Iterable[date]:
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)
