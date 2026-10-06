"""The daily period grid (plan Section 7) - pure logic, no database.

  college hours + lecture length + short break after every lecture + (school) lunch  ->  numbered periods

Rules:
  * the first period starts when the college opens
  * after every lecture comes the short break
  * a lecture never overlaps lunch: if it would, the next lecture starts when lunch ends
  * a lecture must END by the college closing time (or the class's early-release time)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def to_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class Period:
    number: int
    start: int  # minutes since midnight
    end: int

    def as_dict(self) -> dict:
        return {"number": self.number, "start": to_hm(self.start), "end": to_hm(self.end)}


@dataclass
class Grid:
    periods: list[Period]
    break_minutes: int
    lunch: tuple[int, int] | None
    college_start: int
    college_end: int
    notes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- queries
    def period(self, number: int) -> Period | None:
        return self.periods[number - 1] if 1 <= number <= len(self.periods) else None

    def is_continuous(self, first: int, length: int) -> bool:
        """True when `length` periods starting at `first` follow each other with only the short break in between."""
        for n in range(first, first + length - 1):
            a, b = self.period(n), self.period(n + 1)
            if a is None or b is None or b.start - a.end > self.break_minutes:
                return False
        return True

    def available_periods(self, early_release: time | None) -> int:
        """How many periods a class can use (a lecture may not end after its early-release time)."""
        if early_release is None:
            return len(self.periods)
        limit = to_min(early_release)
        return sum(1 for p in self.periods if p.end <= limit)

    def as_dict(self) -> dict:
        return {
            "periods": [p.as_dict() for p in self.periods],
            "break_minutes": self.break_minutes,
            "lunch": None if self.lunch is None else {"start": to_hm(self.lunch[0]), "end": to_hm(self.lunch[1])},
            "college_start": to_hm(self.college_start),
            "college_end": to_hm(self.college_end),
            "notes": self.notes,
        }


def build_grid(
    college_start: time,
    college_end: time,
    lecture_minutes: int,
    break_minutes: int,
    lunch_start: time | None = None,
    lunch_end: time | None = None,
) -> Grid:
    start, end = to_min(college_start), to_min(college_end)
    if end <= start:
        raise ValueError("College closing time must be after the opening time.")
    if lecture_minutes < 1:
        raise ValueError("A lecture must last at least one minute.")
    lunch = None
    if lunch_start is not None and lunch_end is not None:
        lunch = (to_min(lunch_start), to_min(lunch_end))
        if lunch[1] <= lunch[0]:
            raise ValueError("Lunch must end after it starts.")

    periods: list[Period] = []
    notes: list[str] = []
    cursor = start
    while True:
        if lunch is not None:
            ls, le = lunch
            if ls <= cursor < le:  # we are inside lunch -> wait for it to end
                cursor = le
            elif cursor < ls < cursor + lecture_minutes:  # the lecture would run into lunch
                if ls - cursor > break_minutes:
                    notes.append(f"{to_hm(cursor)}-{to_hm(ls)} is too short for a lecture, so it stays free before lunch.")
                cursor = le
        if cursor + lecture_minutes > end:
            if end - cursor > break_minutes:
                notes.append(f"{to_hm(cursor)}-{to_hm(end)} is too short for another lecture, so it stays free at the end of the day.")
            break
        periods.append(Period(len(periods) + 1, cursor, cursor + lecture_minutes))
        cursor += lecture_minutes + break_minutes
        if len(periods) >= 30:
            break
    if lunch is not None and (lunch[1] <= start or lunch[0] >= end):
        notes.append("Lunch is outside the college hours, so it does not affect the periods.")
    if not periods:
        notes.append("No lecture fits between these timings. Increase the college hours or shorten the lecture.")
    return Grid(periods, break_minutes, lunch, start, end, notes)
