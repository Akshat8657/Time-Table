"""One-day adjustments (plan Section 13 items 8-9) - pure logic, no database, no web.

Move / cancel / swap ONE class's lecture for ONE date, with live clash checks against that date's effective timetable
(published base for the weekday + extras + the day's existing moves/cancels).  The weekly base never changes.
Also the smart suggestions: where a lecture could move today, or which of the class's lectures it could swap with.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable

from . import timing as timing_svc
from .changes import DayChanges
from .viewdata import ViewData


def _overlap(a_start: int, a_len: int, b_start: int, b_len: int) -> bool:
    return a_start < b_start + b_len and b_start < a_start + a_len


def class_occupied(data: ViewData, change: DayChanges, sem_id: uuid.UUID, d: date, exclude_base: Iterable[int] = ()) -> set[int]:
    """The periods this class is busy in on this date (base week for the weekday, moved/cancelled by the day's changes, plus
    extra lectures), skipping the base lectures whose ORIGINAL period is in `exclude_base`."""
    wd = d.isoweekday()
    skip = set(exclude_base)
    occ: set[int] = set()
    seen: set = set()
    for r in data.rows:
        if r.semester_id != sem_id or r.day != wd or r.start in skip or r.key in seen:
            continue
        seen.add(r.key)
        adj = change.adjustment_for(d, {sem_id}, r.start)
        if adj is not None and adj.kind == "cancel":
            continue
        start = adj.new_start if (adj is not None and adj.kind == "move" and adj.new_start) else r.start
        occ.update(range(start, start + r.length))
    for x in data.extras:
        if x.semester_id == sem_id and x.day_date == d:
            occ.update(range(x.start, x.start + x.length))
    return occ


def teacher_occupied(data: ViewData, change: DayChanges, teacher_id: uuid.UUID, d: date, exclude_base: Iterable[int] = ()) -> set[int]:
    """The periods this teacher is busy in on this date, skipping their base lectures whose original period is excluded."""
    wd = d.isoweekday()
    skip = set(exclude_base)
    occ: set[int] = set()
    seen: set = set()
    for r in data.rows:
        if r.day != wd or teacher_id not in r.teachers or r.start in skip or r.key in seen:
            continue
        seen.add(r.key)
        adj = change.adjustment_for(d, {r.semester_id}, r.start)
        if adj is not None and adj.kind == "cancel":
            continue
        start = adj.new_start if (adj is not None and adj.kind == "move" and adj.new_start) else r.start
        occ.update(range(start, start + r.length))
    for x in data.extras:
        if x.day_date == d and teacher_id in x.teachers:
            occ.update(range(x.start, x.start + x.length))
    for s in change.subs:
        if s.day_date == d and s.substitute_id == teacher_id and s.status == "filled":
            occ.update(range(s.start, s.start + s.length))
    return occ


def _slot_valid(data: ViewData, sem_id: uuid.UUID, d: date, start: int, length: int) -> str | None:
    """A reason the class cannot have a lecture at this slot on this date, or None if it is a usable slot."""
    total = len(data.grid.periods)
    if start < 1 or start + length - 1 > total:
        return f"the day only has {total} periods"
    rule = data.rule(sem_id, d.isoweekday())
    if rule.is_off:
        return "the class has that day off"
    if rule.end_time is not None:
        limit = rule.end_time.hour * 60 + rule.end_time.minute
        if any(data.grid.periods[n - 1].end > limit for n in range(start, start + length)):
            return "that is after the class's end time"
    a, b = data.period_minutes(start, length)
    if data.calendar.blocking(data.school_id, sem_id, d, a, b) is not None:
        return "a holiday / event blocks that slot"
    return None


def can_move(
    data: ViewData, change: DayChanges, *, sem_ids: Iterable[uuid.UUID], d: date, orig_start: int, length: int,
    new_start: int, teachers: Iterable[uuid.UUID], exclude_base: Iterable[int] = (),
) -> tuple[bool, str]:
    """Can this lecture move to `new_start` on this date? Checks the class(es) and the teacher(s) are free there."""
    sem_ids = list(sem_ids)
    exclude = set(exclude_base) | {orig_start}
    if new_start == orig_start:
        return False, "that is its current period"
    if _overlap(new_start, length, orig_start, length):
        return False, "that overlaps its own current period"
    for sem in sem_ids:
        reason = _slot_valid(data, sem, d, new_start, length)
        if reason is not None:
            return False, reason
        occ = class_occupied(data, change, sem, d, exclude_base=exclude)
        if any(p in occ for p in range(new_start, new_start + length)):
            return False, f"{data.sems[sem].label if sem in data.sems else 'the class'} already has a lecture then"
    for t in teachers:
        occ = teacher_occupied(data, change, t, d, exclude_base=exclude)
        if any(p in occ for p in range(new_start, new_start + length)):
            return False, f"{data.person_name(t)} is teaching another class then"
    return True, "free"


def suggest_moves(data: ViewData, change: DayChanges, *, sem_ids: Iterable[uuid.UUID], d: date, orig_start: int, length: int, teachers: Iterable[uuid.UUID]) -> list[dict]:
    sem_ids, teachers = list(sem_ids), list(teachers)
    out: list[dict] = []
    for p in range(1, len(data.grid.periods) - length + 2):
        ok, _why = can_move(data, change, sem_ids=sem_ids, d=d, orig_start=orig_start, length=length, new_start=p, teachers=teachers)
        if ok:
            reasons = ["The class is free then"] + [f"{data.person_name(t)} is free then" for t in teachers]
            out.append({
                "kind": "move", "new_start": p, "time": timing_svc.time_range(data.grid, p, length),
                "reasons": reasons, "direction": "earlier" if p < orig_start else "later",
            })
    out.sort(key=lambda s: abs(s["new_start"] - orig_start))
    return out


def suggest_swaps(data: ViewData, change: DayChanges, *, sem_id: uuid.UUID, d: date, orig_start: int, length: int, teachers: Iterable[uuid.UUID]) -> list[dict]:
    """Swaps with another of THIS class's lectures that day (same length), where both halves stay clash-free."""
    wd = d.isoweekday()
    teachers = list(teachers)
    others: dict[int, dict] = {}
    seen: set = set()
    for r in data.rows:
        if r.semester_id != sem_id or r.day != wd or r.start == orig_start or r.key in seen:
            continue
        seen.add(r.key)
        adj = change.adjustment_for(d, {sem_id}, r.start)
        if (adj is not None and adj.kind == "cancel") or r.length != length:
            continue
        grp = [x for x in data.rows if x.key == r.key]
        others[r.start] = {
            "start": r.start, "length": r.length,
            "label": " / ".join(sorted(data.subjects[x.subject_id].name for x in grp if x.subject_id)) or r.label,
            "teachers": {t for x in grp for t in x.teachers},
        }
    out: list[dict] = []
    for b in others.values():
        exclude = {orig_start, b["start"]}
        ok_a, _ = can_move(data, change, sem_ids=[sem_id], d=d, orig_start=orig_start, length=length, new_start=b["start"], teachers=teachers, exclude_base=exclude)
        ok_b, _ = can_move(data, change, sem_ids=[sem_id], d=d, orig_start=b["start"], length=length, new_start=orig_start, teachers=b["teachers"], exclude_base=exclude)
        if ok_a and ok_b:
            out.append({
                "kind": "swap", "swap_with_start": b["start"], "time": timing_svc.time_range(data.grid, b["start"], length),
                "with_label": b["label"],
                "reasons": [f"Swap with {b['label']} (period {b['start']})", "Both classes and teachers stay free"],
            })
    out.sort(key=lambda s: s["swap_with_start"])
    return out
