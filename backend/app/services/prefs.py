"""Teacher preferences (plan Section 14) - the data, the rules for saving them, and small pure helpers.

A teacher's weekly grid has three states per slot:  Available (nothing stored)  /  Preferred (star)  /  Not available (cross).
On top of that: prefer the first lecture, avoid the last lecture, and a maximum number of lectures per day.

How the rest of the app uses them
  HARD   not available slots + the daily maximum are never used - by the generator AND by the manual grid (services/schedule.py),
         and by the extra-lecture planner (services/hours.py).
  SOFT   preferred slots + first / last choices are the generator's new soft rule "preferred_slots" (services/generator.py).
  LABEL  the substitution ranking puts a teacher with a "not available" mark LAST and says so (services/substitution.py).

Visiting-faculty days / weekly hours stay separate, hard rules (they come from the teacher's profile, not from this grid).
This module is pure logic plus two small loaders - no web.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from .. import db
from ..validation import DAY_NAMES

Cell = tuple[int, int]  # (weekday 1-6, period 1..N)
KINDS = ("preferred", "unavailable")
DEFAULT_MAX_UNAVAILABLE = 8
DEFAULT_REMINDER_HOURS = 24
MAX_PER_DAY_LIMIT = 12


# ---------------------------------------------------------------------------------------------------- plain data
@dataclass(frozen=True)
class TeacherPref:
    teacher_id: uuid.UUID
    unavailable: frozenset[Cell] = frozenset()
    preferred: frozenset[Cell] = frozenset()
    prefer_first: bool = False
    avoid_last: bool = False
    max_per_day: int | None = None

    @property
    def is_blank(self) -> bool:
        return not (self.unavailable or self.preferred or self.prefer_first or self.avoid_last or self.max_per_day)

    def counts(self) -> dict[str, int]:
        return {"unavailable": len(self.unavailable), "preferred": len(self.preferred)}


def build_prefs(masters: Iterable[Mapping[str, Any]], slots: Iterable[Mapping[str, Any]]) -> dict[uuid.UUID, TeacherPref]:
    """Turns the two table reads into one TeacherPref per teacher (a teacher may have slots but no master row, or the reverse)."""
    un: dict[uuid.UUID, set[Cell]] = {}
    pr: dict[uuid.UUID, set[Cell]] = {}
    for s in slots:
        (un if s["kind"] == "unavailable" else pr).setdefault(s["teacher_id"], set()).add((s["day"], s["period"]))
    master = {m["teacher_id"]: m for m in masters}
    out: dict[uuid.UUID, TeacherPref] = {}
    for tid in set(master) | set(un) | set(pr):
        m = master.get(tid)
        out[tid] = TeacherPref(
            tid, frozenset(un.get(tid, ())), frozenset(pr.get(tid, ())),
            bool(m["prefer_first"]) if m else False, bool(m["avoid_last"]) if m else False,
            (int(m["max_per_day"]) if m and m["max_per_day"] is not None else None),
        )
    return out


async def load_prefs(school_id: uuid.UUID) -> dict[uuid.UUID, TeacherPref]:
    """Every teacher's preferences of one school (two small queries, run together)."""
    masters, slots = await asyncio.gather(
        db.fetch("select teacher_id, prefer_first, avoid_last, max_per_day from public.teacher_preferences where school_id = $1", school_id),
        db.fetch("select teacher_id, day, period, kind from public.teacher_pref_slots where school_id = $1", school_id),
    )
    return build_prefs(masters, slots)


async def load_teacher_pref(teacher_id: uuid.UUID) -> TeacherPref:
    """One teacher's preferences (blank if they never set any)."""
    masters, slots = await asyncio.gather(
        db.fetch("select teacher_id, prefer_first, avoid_last, max_per_day from public.teacher_preferences where teacher_id = $1", teacher_id),
        db.fetch("select teacher_id, day, period, kind from public.teacher_pref_slots where teacher_id = $1", teacher_id),
    )
    return build_prefs(masters, slots).get(teacher_id, TeacherPref(teacher_id))


async def load_settings(school_id: uuid.UUID) -> dict[str, int]:
    row = await db.fetchrow("select max_unavailable, reminder_hours from public.preference_settings where school_id = $1", school_id)
    if row is None:
        return {"max_unavailable": DEFAULT_MAX_UNAVAILABLE, "reminder_hours": DEFAULT_REMINDER_HOURS}
    return {"max_unavailable": row["max_unavailable"], "reminder_hours": row["reminder_hours"]}


# ---------------------------------------------------------------------------------------------------- words
def periods_text(periods: Iterable[int]) -> str:
    """[3] -> 'period 3'   [3, 4] -> 'periods 3-4'   [1, 3] -> 'periods 1, 3'"""
    ps = sorted(set(periods))
    if len(ps) == 1:
        return f"period {ps[0]}"
    if ps == list(range(ps[0], ps[-1] + 1)):
        return f"periods {ps[0]}-{ps[-1]}"
    return "periods " + ", ".join(str(p) for p in ps)


def day_name(day: int) -> str:
    return DAY_NAMES.get(day, f"day {day}")


# ---------------------------------------------------------------------------------------------------- saving rules
class PrefProblem(Exception):
    """The choices cannot be saved. `message` is written for the teacher (plain English)."""

    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra or {}


def check_save(
    slots: Mapping[Cell, str],
    *,
    working_days: int,
    periods: int,
    visiting_days: frozenset[int] | None,
    limit: int,
    max_per_day: int | None,
) -> None:
    """Raises PrefProblem when these choices are not allowed:
      * a slot outside the college week / outside the day's periods
      * a slot on a day a visiting teacher does not come (that rule is fixed by the HOD and stays separate)
      * more "not available" marks than the school allows
      * a daily maximum that is not a sensible number."""
    for (day, period), kind in slots.items():
        if kind not in KINDS:
            raise PrefProblem("BAD_SLOT", "Each slot must be Available, Preferred or Not available.")
        if day < 1 or day > min(6, working_days):
            raise PrefProblem("BAD_SLOT", f"{day_name(day)} is not a working day of the college.")
        if period < 1 or period > periods:
            raise PrefProblem("BAD_SLOT", f"The day only has {periods} periods - there is no period {period}.")
        if visiting_days is not None and day not in visiting_days:
            days = ", ".join(DAY_NAMES[d] for d in sorted(visiting_days)) or "none"
            raise PrefProblem(
                "VISITING_DAY",
                f"{day_name(day)} is not one of your visiting days ({days}). Visiting days are set by your HOD, so they cannot be edited here.",
            )
    marked = sum(1 for k in slots.values() if k == "unavailable")
    if marked > limit:
        raise PrefProblem(
            "TOO_MANY_UNAVAILABLE",
            f"You can mark at most {limit} slot(s) as Not available, but you marked {marked}. "
            f"Please change {marked - limit} of them back to Available.",
            {"limit": limit, "marked": marked},
        )
    if max_per_day is not None and not 1 <= max_per_day <= min(periods, MAX_PER_DAY_LIMIT):
        raise PrefProblem("BAD_MAX", f"Maximum lectures per day must be a number from 1 to {min(periods, MAX_PER_DAY_LIMIT)} (or empty for no limit).")


def fixed_notes(pref: TeacherPref, fixed: Iterable[Mapping[str, Any]]) -> list[str]:
    """Locked sessions (PE / SOL / combined / anything the HOD locked) are NOT moved by preferences - the HOD set them on purpose.
    But when one falls on a 'not available' slot, or a day's locked periods already pass the daily maximum, the teacher and the HOD
    should know. `fixed` rows carry label, day, start_period, length."""
    out: list[str] = []
    per_day: dict[int, int] = {}
    for f in fixed:
        periods = list(range(f["start_period"], f["start_period"] + f["length"]))
        per_day[f["day"]] = per_day.get(f["day"], 0) + len(periods)
        hit = [n for n in periods if (f["day"], n) in pref.unavailable]
        if hit:
            out.append(f"The locked session '{f['label']}' ({day_name(f['day'])} {periods_text(periods)}) falls on {day_name(f['day'])} {periods_text(hit)}, "
                       "which is marked 'Not available'. Locked sessions are set by the HOD and stay where they are.")
    if pref.max_per_day:
        for day, n in sorted(per_day.items()):
            if n > pref.max_per_day:
                out.append(f"{day_name(day)} already has {n} locked periods, more than the daily maximum of {pref.max_per_day}. Locked sessions stay as they are.")
    return out


def summary_text(pref: TeacherPref | None) -> str:
    """One line for lists: '3 not available · 5 preferred · max 4 a day · first lecture'."""
    if pref is None or pref.is_blank:
        return "No preferences set"
    parts: list[str] = []
    if pref.unavailable:
        parts.append(f"{len(pref.unavailable)} not available")
    if pref.preferred:
        parts.append(f"{len(pref.preferred)} preferred")
    if pref.max_per_day:
        parts.append(f"max {pref.max_per_day} a day")
    if pref.prefer_first:
        parts.append("likes the first lecture")
    if pref.avoid_last:
        parts.append("avoids the last lecture")
    return " · ".join(parts)


# ---------------------------------------------------------------------------------------------------- the window
def reminder_at(opened_at: datetime, deadline: datetime, reminder_hours: int) -> datetime:
    """When the reminder goes out: `reminder_hours` before the deadline - but never earlier than the half-way point of a short window."""
    lead = min(timedelta(hours=reminder_hours), (deadline - opened_at) / 2)
    return deadline - lead


def window_is_open(status: str, deadline: datetime, now: datetime) -> bool:
    return status == "open" and now < deadline
