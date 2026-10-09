"""Substitution ranking and the "is this teacher free then?" check (plan Section 13) - pure logic, no database, no web.

For an absent teacher's one time-slot on one date, the backend ranks the FREE teachers by the plan's four things:
  (a) can teach that subject / specialization match   (b) free in that slot that day
  (c) lower workload that day                          (d) has taught that class before

Only teachers who are genuinely free (not absent, not already teaching then, visiting faculty only on their days) are
offered; the score decides the order and carries human reasons so the HOD sees WHY.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .. import db
from .changes import EMPTY, DayChanges, Sub
from .viewdata import ViewData

_WORD = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------------------------------- affected lectures
@dataclass(frozen=True)
class AffectedSlot:
    start: int
    length: int
    semester_ids: frozenset[uuid.UUID]
    subject_ids: frozenset[uuid.UUID]
    parallel_key: uuid.UUID | None
    label: str
    kind: str                                  # 'lecture' | 'fixed' | 'extra'
    present_others: tuple[uuid.UUID, ...]      # other teachers of the same lecture who are NOT absent that day (they cover it)

    @property
    def covered_by_colleague(self) -> bool:
        return bool(self.present_others)


def affected_lectures(data: ViewData, teacher_id: uuid.UUID, d: date, change: DayChanges = EMPTY) -> list[AffectedSlot]:
    """The lectures this teacher is scheduled to take on this date (published + fixed + extra), skipping ones a holiday /
    event / one-day cancel already removes. A combined session is one slot across its classes; a parallel elective option
    is one slot for that option only."""
    wd = d.isoweekday()
    slots: list[AffectedSlot] = []
    groups: dict[uuid.UUID, list] = {}
    for r in data.rows:
        if r.day == wd and teacher_id in r.teachers:
            groups.setdefault(r.key, []).append(r)
    for rows in groups.values():
        first = rows[0]
        sem_ids = frozenset(r.semester_id for r in rows)
        a, b = data.period_minutes(first.start, first.length)
        if any(data.calendar.blocking(data.school_id, sem, d, a, b) is not None for sem in sem_ids):
            continue
        adj = change.adjustment_for(d, sem_ids, first.start)
        if adj is not None and adj.kind == "cancel":
            continue
        subj = frozenset(r.subject_id for r in rows if r.subject_id and teacher_id in r.teachers)
        others = {t for r in rows for t in r.teachers if t != teacher_id}
        present = tuple(t for t in others if not change.is_absent(t, d))
        label = " / ".join(sorted(data.subjects[s].name for s in subj if s in data.subjects)) or first.label
        slots.append(AffectedSlot(
            first.start, first.length, sem_ids, subj, first.group_key, label,
            "fixed" if first.kind == "fixed" else "lecture", present,
        ))
    for x in data.extras:
        if x.day_date == d and teacher_id in x.teachers:
            others = {t for t in x.teachers if t != teacher_id}
            present = tuple(t for t in others if not change.is_absent(t, d))
            label = data.subjects[x.subject_id].name if x.subject_id in data.subjects else "Extra lecture"
            slots.append(AffectedSlot(x.start, x.length, frozenset({x.semester_id}), frozenset({x.subject_id}), x.parallel_key, label, "extra", present))
    slots.sort(key=lambda s: s.start)
    return slots


def _keywords(text: str | None) -> set[str]:
    stop = {"and", "the", "of", "for", "to", "in", "a", "an", "with", "using", "its", "or", "amp"}
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in stop}


# ---------------------------------------------------------------------------------------------------- is a teacher free?
def occupied_periods(data: ViewData, change: DayChanges, teacher_id: uuid.UUID, d: date) -> list[tuple[int, int]]:
    """Every (start, end-exclusive) period range this teacher is already busy in on this date (base week + that day's changes)."""
    wd = d.isoweekday()
    out: list[tuple[int, int]] = []
    seen_keys: set = set()
    for r in data.rows:
        if r.day != wd or teacher_id not in r.teachers:
            continue
        if r.key in seen_keys:
            continue  # a combined session is one place
        seen_keys.add(r.key)
        adj = change.adjustment_for(d, {r.semester_id}, r.start)
        if adj is not None and adj.kind == "cancel":
            continue
        start = adj.new_start if (adj is not None and adj.kind == "move" and adj.new_start) else r.start
        out.append((start, start + r.length))
    for x in data.extras:
        if x.day_date == d and teacher_id in x.teachers:
            out.append((x.start, x.start + x.length))
    for s in change.subs:
        if s.day_date == d and s.substitute_id == teacher_id and s.status == "filled":
            out.append((s.start, s.start + s.length))
    return out


def free_at(data: ViewData, change: DayChanges, teacher_id: uuid.UUID, d: date, start: int, length: int) -> tuple[bool, str]:
    """(is free, reason). A teacher is free when active, not absent that day, allowed on that day (visiting), and not already busy."""
    wd = d.isoweekday()
    p = data.people.get(teacher_id)
    if p is None or not p.is_active:
        return False, "not an active teacher"
    if change.is_absent(teacher_id, d):
        return False, "absent that day"
    if p.is_visiting and wd not in p.visiting_days:
        return False, "visiting faculty - does not come that day"
    end = start + length
    for a, b in occupied_periods(data, change, teacher_id, d):
        if a < end and start < b:
            return False, "already teaching then"
    return True, "free at that time"


# ---------------------------------------------------------------------------------------------------- ranking
@dataclass
class Candidate:
    teacher_id: uuid.UUID
    name: str
    score: float
    reasons: list[str] = field(default_factory=list)
    load_today: int = 0
    can_teach: bool = False
    taught_before: bool = False
    is_visiting: bool = False
    marked_unavailable: bool = False   # Phase 6: the teacher marked THIS period "not available" - offered only as a last resort
    starred: bool = False              # Phase 6: the teacher marked this period as preferred
    over_daily_max: bool = False       # Phase 6: covering it would pass the teacher's own "max lectures a day"

    def as_dict(self) -> dict:
        return {
            "teacher_id": str(self.teacher_id), "name": self.name, "score": round(self.score, 1), "reasons": self.reasons,
            "load_today": self.load_today, "can_teach": self.can_teach, "taught_before": self.taught_before, "is_visiting": self.is_visiting,
            "marked_unavailable": self.marked_unavailable, "starred": self.starred, "over_daily_max": self.over_daily_max,
        }


def _load_today(data: ViewData, change: DayChanges, teacher_id: uuid.UUID, d: date) -> int:
    return len(occupied_periods(data, change, teacher_id, d))


def score_candidate(
    data: ViewData, change: DayChanges, sub: Sub, teacher_id: uuid.UUID,
    can_teach: dict[uuid.UUID, set[uuid.UUID]], taught_class: dict[uuid.UUID, set[uuid.UUID]],
    spec_text: str | None = None,
) -> Candidate:
    p = data.people[teacher_id]
    cand = Candidate(teacher_id, p.name, 0.0, [], is_visiting=p.is_visiting)
    reasons = cand.reasons

    # (a) subject / specialization
    if can_teach.get(teacher_id, set()) & sub.subject_ids:
        cand.can_teach = True
        cand.score += 100
        reasons.append("Can teach this subject")
    else:
        want = set()
        for s in sub.subject_ids:
            si = data.subjects.get(s)
            if si:
                want |= _keywords(si.name)
        overlap = want & _keywords(spec_text)
        if overlap:
            cand.score += 45
            reasons.append("Specialization matches (" + ", ".join(sorted(overlap)) + ")")
        else:
            cand.score += 8
            reasons.append("Available to supervise")

    # (d) has taught this class before
    if taught_class.get(teacher_id, set()) & sub.semester_ids:
        cand.taught_before = True
        cand.score += 25
        reasons.append("Has taught this class before")

    # (c) lower workload today
    cand.load_today = _load_today(data, change, teacher_id, sub.day_date)
    cand.score += max(0.0, 24 - 6 * cand.load_today)
    if cand.load_today == 0:
        reasons.append("Free all day")
    else:
        reasons.append(f"{cand.load_today} lecture(s) that day")

    # prefer regular staff slightly over visiting faculty
    if p.is_visiting:
        cand.score -= 6
        reasons.append("Visiting faculty")

    # Phase 6 - the teacher's own preferences. A "not available" mark does not forbid the HOD from asking (it is a one-day
    # request, not the weekly timetable) but sinks the teacher to the bottom and says so clearly.
    pref = data.prefs.get(teacher_id)
    if pref is not None:
        wd = sub.day_date.isoweekday()
        covered = range(sub.start, sub.start + sub.length)
        if any((wd, n) in pref.unavailable for n in covered):
            cand.marked_unavailable = True
            cand.score -= 200
            reasons.append("Last resort: marked this period as not available")
        elif any((wd, n) in pref.preferred for n in covered):
            cand.starred = True
            cand.score += 8
            reasons.append("Marked this period as preferred")
        if pref.max_per_day:
            busy_periods = sum(b - a for a, b in occupied_periods(data, change, teacher_id, sub.day_date))
            if busy_periods + sub.length > pref.max_per_day:
                cand.over_daily_max = True
                cand.score -= 25
                reasons.append(f"Would pass their own limit of {pref.max_per_day} lectures a day")

    # (b) free - always true for a candidate (they were filtered); state it last so the order of reasons reads well
    reasons.append("Free in that slot")
    return cand


def rank_candidates(
    data: ViewData, change: DayChanges, sub: Sub, *,
    specialization: dict[uuid.UUID, str] | None = None,
    can_teach: dict[uuid.UUID, set[uuid.UUID]] | None = None,
    taught_class: dict[uuid.UUID, set[uuid.UUID]] | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """Every FREE teacher who could cover this lecture, best first."""
    can_teach = can_teach or {}
    taught_class = taught_class or {}
    specialization = specialization or {}
    out: list[Candidate] = []
    for tid, p in data.people.items():
        if tid == sub.absent_teacher_id:
            continue
        ok, _why = free_at(data, change, tid, sub.day_date, sub.start, sub.length)
        if not ok:
            continue
        cand = score_candidate(data, change, sub, tid, can_teach, taught_class, specialization.get(tid))
        out.append(cand)
    out.sort(key=lambda c: (c.marked_unavailable, -c.score, c.name.lower()))   # 'not available' teachers always come last
    return out[:limit] if limit else out


# ---------------------------------------------------------------------------------------------------- qualification maps
async def load_qualification(
    school_id: uuid.UUID, term_id: uuid.UUID,
) -> tuple[dict[uuid.UUID, set[uuid.UUID]], dict[uuid.UUID, set[uuid.UUID]], dict[uuid.UUID, str]]:
    """(can_teach: teacher -> subjects they may teach, taught_class: teacher -> classes they teach / have taught,
    specialization: teacher -> text) - for the substitution ranking."""
    ts, assigns, pubs, specs = (
        await db.fetch("select teacher_id, subject_id from public.teacher_subjects where school_id = $1", school_id),
        await db.fetch(
            "select a.teacher_id, a.co_teacher_id, s.semester_id from public.assignments a "
            "join public.subjects s on s.id = a.subject_id where a.school_id = $1",
            school_id,
        ),
        await db.fetch(
            "select distinct semester_id, teacher_id, co_teacher_id from public.published_entries where school_id = $1 and term_id = $2",
            school_id, term_id,
        ),
        await db.fetch("select id, specialization from public.profiles where school_id = $1 and specialization is not null", school_id),
    )
    can_teach: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for r in ts:
        can_teach[r["teacher_id"]].add(r["subject_id"])
    taught: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for r in [*assigns, *pubs]:
        for t in (r["teacher_id"], r["co_teacher_id"]):
            if t is not None:
                taught[t].add(r["semester_id"])
    return dict(can_teach), dict(taught), {r["id"]: r["specialization"] for r in specs}
