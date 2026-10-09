"""The timetable DRAFT engine (plan Sections 8-11, Phase 2) - pure logic, no database, no web.

Everything the draft screens need to know is decided HERE (plan rule: all logic lives in Python):

  check_unit()     is this placement possible?  -> hard problems (the drop is refused) + soft warnings (allowed)
  options_map()    check_unit() for every cell of the week (drag-and-drop / click-to-place colouring)
  completeness()   placed lectures vs the lectures each subject needs
  analyze_draft()  every problem of a saved draft (the conflict report)

Vocabulary
  placement ("unit")  one subject at one time - or, for an elective GROUP (AI track || Cyber track), all options of the
                      group together at the same time: ONE parallel placement, never a clash with itself.
  locked slot         a Fixed Slot (PE / SOL / anything locked by hand). It occupies its class(es) and its teacher(s).
  reference set       what a draft is checked against: its own placements + the PRIMARY draft of every OTHER class
                      + all locked slots. (Two drafts of the same class are alternatives, they never clash.)

Hard rules (plan Section 10) block the placement; soft rules only warn.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..validation import DAY_NAMES
from .grid import Grid, to_hm, to_min
from .timing import FULL_DAY, DayRule

GAP_LIMIT = 2  # this many free periods between two lectures of a class / teacher count as a "long gap" (soft rule)


# ---------------------------------------------------------------------------------------------------- plain data
@dataclass(frozen=True)
class Subj:
    id: uuid.UUID
    semester_id: uuid.UUID
    name: str
    code: str
    credits: int
    is_lab: bool
    special_kind: str | None  # 'PE' / 'SOL' (their slots come from Fixed Slots only)
    group_id: uuid.UUID | None
    group_name: str | None


@dataclass(frozen=True)
class Person:
    id: uuid.UUID
    name: str
    is_active: bool
    is_visiting: bool
    visiting_days: frozenset[int]
    max_weekly_hours: int | None


@dataclass(frozen=True)
class Fixed:
    id: uuid.UUID
    key: uuid.UUID  # one key per locked SESSION (a combined session = several rows, one key)
    semester_id: uuid.UUID
    day: int
    start: int
    length: int
    kind: str
    label: str
    teacher_id: uuid.UUID | None
    co_teacher_id: uuid.UUID | None


@dataclass(frozen=True)
class DraftInfo:
    id: uuid.UUID
    semester_id: uuid.UUID
    name: str
    is_primary: bool


@dataclass(frozen=True)
class Entry:
    id: uuid.UUID
    draft_id: uuid.UUID
    semester_id: uuid.UUID
    subject_id: uuid.UUID
    day: int
    start: int
    length: int
    parallel_key: uuid.UUID | None
    is_locked: bool = False  # pinned by hand (semi-automatic mode): the generator keeps it, the editor refuses to move it

    @property
    def key(self) -> uuid.UUID:
        """One key per PLACEMENT: the rows of a parallel placement share it."""
        return self.parallel_key or self.id


@dataclass(frozen=True)
class Issue:
    code: str
    hard: bool
    message: str
    pair: frozenset = frozenset()  # keys of the parties involved (a clash between two placements is reported once)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "hard": self.hard, "message": self.message}


@dataclass
class Result:
    length: int = 1
    hard: list[Issue] = field(default_factory=list)
    soft: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard

    def state(self) -> str:
        return "blocked" if self.hard else ("warn" if self.soft else "ok")


def overlap(a_start: int, a_len: int, b_start: int, b_len: int) -> bool:
    return a_start < b_start + b_len and b_start < a_start + a_len


def span(start: int, length: int) -> str:
    return f"period {start}" if length == 1 else f"periods {start}-{start + length - 1}"


def _uniq(items: Iterable[Issue]) -> list[Issue]:
    seen: set[tuple[str, str]] = set()
    out: list[Issue] = []
    for i in items:
        k = (i.code, i.message)
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


# ---------------------------------------------------------------------------------------------------- the context
@dataclass
class Ctx:
    """Everything one check needs, loaded once per request (see routers/drafts.py)."""

    grid: Grid
    days_per_week: int
    labels: dict[uuid.UUID, str]  # semester id -> "M.Sc. AI - Sem 1"
    rules: dict[tuple[uuid.UUID, int], DayRule]
    subjects: dict[uuid.UUID, Subj]
    assign: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]]  # subject id -> (teacher, co-teacher)
    people: dict[uuid.UUID, Person]
    fixed: list[Fixed]
    drafts: dict[uuid.UUID, DraftInfo]
    entries: list[Entry]

    def __post_init__(self) -> None:
        self.fixed_by_class: dict[tuple[uuid.UUID, int], list[Fixed]] = defaultdict(list)
        self.fixed_by_teacher: dict[tuple[uuid.UUID, int], list[Fixed]] = defaultdict(list)
        self.fixed_by_teacher_all: dict[uuid.UUID, dict[uuid.UUID, Fixed]] = defaultdict(dict)
        self.fixed_classes: dict[uuid.UUID, list[str]] = defaultdict(list)
        for f in self.fixed:
            self.fixed_by_class[(f.semester_id, f.day)].append(f)
            self.fixed_classes[f.key].append(self.labels.get(f.semester_id, "a class"))
            for t in {f.teacher_id, f.co_teacher_id} - {None}:
                self.fixed_by_teacher[(t, f.day)].append(f)  # type: ignore[index]
                self.fixed_by_teacher_all[t][f.key] = f  # type: ignore[index]
        self.entries_by_draft_day: dict[tuple[uuid.UUID, int], list[Entry]] = defaultdict(list)
        self.entries_by_draft: dict[uuid.UUID, list[Entry]] = defaultdict(list)
        self.entries_by_teacher_day: dict[tuple[uuid.UUID, int], list[Entry]] = defaultdict(list)
        self.entries_by_teacher_all: dict[uuid.UUID, list[Entry]] = defaultdict(list)
        for e in self.entries:
            self.entries_by_draft_day[(e.draft_id, e.day)].append(e)
            self.entries_by_draft[e.draft_id].append(e)
            for t in self.teachers(e.subject_id):
                self.entries_by_teacher_day[(t, e.day)].append(e)
                self.entries_by_teacher_all[t].append(e)
        self.subjects_by_class: dict[uuid.UUID, list[Subj]] = defaultdict(list)
        for s in self.subjects.values():
            self.subjects_by_class[s.semester_id].append(s)

    # ------------------------------------------------------------ small answers
    def teachers(self, subject_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        t, co = self.assign.get(subject_id, (None, None))
        return tuple(x for x in (t, co) if x is not None)

    def person_name(self, tid: uuid.UUID) -> str:
        p = self.people.get(tid)
        return p.name if p else "A teacher"

    def is_ref(self, e: Entry, draft_id: uuid.UUID, sem_id: uuid.UUID) -> bool:
        """Does this placement count when `draft_id` (of class `sem_id`) is checked?"""
        if e.draft_id == draft_id:
            return True
        info = self.drafts.get(e.draft_id)
        return bool(info and info.is_primary and e.semester_id != sem_id)

    def derived_length(self, subject: Subj) -> int:
        return 2 if subject.is_lab else 1

    def class_occupied(self, sem_id: uuid.UUID, draft_id: uuid.UUID, day: int, ignore: frozenset | set) -> set[int]:
        out: set[int] = set()
        for f in self.fixed_by_class.get((sem_id, day), []):
            out.update(range(f.start, f.start + f.length))
        for e in self.entries_by_draft_day.get((draft_id, day), []):
            if e.key not in ignore:
                out.update(range(e.start, e.start + e.length))
        return out

    def teacher_occupied(self, tid: uuid.UUID, draft_id: uuid.UUID, sem_id: uuid.UUID, day: int, ignore: frozenset | set) -> set[int]:
        out: set[int] = set()
        for f in self.fixed_by_teacher.get((tid, day), []):
            out.update(range(f.start, f.start + f.length))
        for e in self.entries_by_teacher_day.get((tid, day), []):
            if e.key not in ignore and self.is_ref(e, draft_id, sem_id):
                out.update(range(e.start, e.start + e.length))
        return out


def gap_runs(occupied: set[int]) -> list[tuple[int, int]]:
    """Runs of free periods BETWEEN two lectures of one day that are at least GAP_LIMIT long."""
    if len(occupied) < 2:
        return []
    low, high = min(occupied), max(occupied)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for p in range(low, high + 1):
        if p in occupied:
            if start is not None:
                runs.append((start, p - 1))
                start = None
        elif start is None:
            start = p
    return [(a, b) for a, b in runs if b - a + 1 >= GAP_LIMIT]


# ---------------------------------------------------------------------------------------------------- time rules
def time_issues(ctx: Ctx, sem_id: uuid.UUID, day: int, start: int, length: int) -> list[Issue]:
    """Working day, OFF day, existing periods, the class's end time, lunch (plan rules 8 and 9)."""
    label = ctx.labels[sem_id]
    rule = ctx.rules.get((sem_id, day), FULL_DAY)
    if day < 1 or day > 6 or day > ctx.days_per_week:
        working = "Monday to Friday" if ctx.days_per_week == 5 else "Monday to Saturday"
        return [Issue("NOT_WORKING_DAY", True, f"{DAY_NAMES.get(day, 'That day')} is not a working day (the college works {working}).")]
    if rule.is_off:
        return [Issue("DAY_OFF", True, f"{label} has no lectures on {DAY_NAMES[day]} (the day is marked OFF).")]
    total = len(ctx.grid.periods)
    last = start + length - 1
    if start < 1 or last > total:
        what = f"periods {start}-{last} do not exist" if length > 1 else f"period {start} does not exist"
        return [Issue("NO_SUCH_PERIOD", True, f"The day only has {total} periods, so {what}.")]
    out: list[Issue] = []
    if rule.end_time is not None:
        limit = to_min(rule.end_time)
        late = [n for n in range(start, last + 1) if ctx.grid.periods[n - 1].end > limit]
        if late:
            p = ctx.grid.periods[late[0] - 1]
            out.append(Issue(
                "AFTER_END", True,
                f"Period {late[0]} ({to_hm(p.start)}-{to_hm(p.end)}) is after {label}'s end time on {DAY_NAMES[day]} ({to_hm(limit)}).",
            ))
    if ctx.grid.lunch is not None:
        ls, le = ctx.grid.lunch
        for n in range(start, last + 1):
            p = ctx.grid.periods[n - 1]
            if p.start < le and ls < p.end:
                out.append(Issue("LUNCH", True, f"Period {n} runs into the lunch break ({to_hm(ls)}-{to_hm(le)})."))
                break
    return out


# ---------------------------------------------------------------------------------------------------- the check
def check_unit(
    ctx: Ctx,
    *,
    draft_id: uuid.UUID,
    sem_id: uuid.UUID,
    subject_ids: list[uuid.UUID],
    day: int,
    start: int,
    ignore: frozenset | set = frozenset(),
    me: uuid.UUID | None = None,
    length: int | None = None,
) -> Result:
    """Is it possible to put these subjects (one subject, or all options of an elective group together) at day / period `start`?

    `ignore` = placement keys that do not count (the placement being moved, or the one being checked in a saved draft).
    `length` = only for placements that are already saved: the length they really have (checked against the subject's kind)."""
    res = Result()
    subs = [ctx.subjects[s] for s in subject_ids]
    label = ctx.labels[sem_id]
    day_name = DAY_NAMES.get(day, "that day")

    # --- shape of the placement ------------------------------------------------------------------------------
    for s in subs:
        if s.semester_id != sem_id:
            res.hard.append(Issue("WRONG_CLASS", True, f"'{s.name}' does not belong to {label}."))
        if s.special_kind:
            res.hard.append(Issue("PE_SOL_FIXED", True, f"'{s.name}' is {s.special_kind}: its slot is set on the Fixed Slots screen, not here."))
    if res.hard:
        return res
    if len({s.is_lab for s in subs}) > 1:
        res.hard.append(Issue("PARALLEL_MIXED", True, "A lab and an ordinary lecture cannot be in the same parallel placement."))
        return res
    group_id = subs[0].group_id
    if len(subs) > 1 or group_id is not None:
        if any(s.group_id != group_id for s in subs):
            res.hard.append(Issue("PARALLEL_MIXED", True, "Only the options of ONE elective group can be placed together."))
            return res
        members = {s.id for s in ctx.subjects_by_class[sem_id] if s.group_id == group_id}
        if members != set(subject_ids):
            res.hard.append(Issue(
                "PARALLEL_BROKEN", True,
                f"The elective group '{subs[0].group_name}' must run in the same slot: all its options go together ({len(members)} subjects).",
            ))
            return res
    proper = ctx.derived_length(subs[0])
    res.length = proper
    use = length or proper
    if length is not None and length != proper:
        what = "a lab must be one block of 2 periods" if proper == 2 else "an ordinary lecture is exactly 1 period"
        res.hard.append(Issue("BLOCK_BROKEN", True, f"'{subs[0].name}' is placed for {length} period(s), but {what}."))
    res.length = use

    # --- the time (day schedule, end time, lunch) ------------------------------------------------------------
    res.hard.extend(time_issues(ctx, sem_id, day, start, use))
    if any(i.code in ("NOT_WORKING_DAY", "DAY_OFF", "NO_SUCH_PERIOD") for i in res.hard):
        return res
    if use > 1 and ctx.grid.lunch_inside(start, use):
        res.soft.append(Issue("LAB_AROUND_LUNCH", False, "This block runs around the lunch break. Allowed, but it is better to keep labs away from lunch."))

    # --- the class: locked slots and the other placements of this draft --------------------------------------
    for f in ctx.fixed_by_class.get((sem_id, day), []):
        if overlap(start, use, f.start, f.length):
            extra = f" (combined: {' + '.join(sorted(ctx.fixed_classes[f.key]))})" if len(ctx.fixed_classes[f.key]) > 1 else ""
            res.hard.append(Issue(
                "CLASS_LOCKED", True,
                f"{label} has the locked slot '{f.label}'{extra} at {span(f.start, f.length)} on {day_name}.",
                frozenset({me or "me", f.key}),
            ))
    for e in ctx.entries_by_draft_day.get((draft_id, day), []):
        if e.key in ignore or not overlap(start, use, e.start, e.length):
            continue
        res.hard.append(Issue(
            "CLASS_BUSY", True,
            f"{label} already has '{ctx.subjects[e.subject_id].name}' at {span(e.start, e.length)} on {day_name}.",
            frozenset({me or "me", e.key}),
        ))

    # --- the teachers ----------------------------------------------------------------------------------------
    per_teacher: dict[uuid.UUID, list[Subj]] = defaultdict(list)
    for s in subs:
        for tid in ctx.teachers(s.id):
            per_teacher[tid].append(s)
    for tid, taught in per_teacher.items():
        name = ctx.person_name(tid)
        person = ctx.people.get(tid)
        if len(taught) > 1:
            res.hard.append(Issue("TEACHER_BUSY", True, f"{name} is the teacher of {len(taught)} options of this group, but can only be in one place."))
        if person is not None:
            if not person.is_active:
                res.hard.append(Issue("TEACHER_INACTIVE", True, f"{name} is deactivated."))
            if person.is_visiting and day not in person.visiting_days:
                days = ", ".join(DAY_NAMES[d] for d in sorted(person.visiting_days)) or "no days"
                res.hard.append(Issue("VISITING_DAY", True, f"{name} is visiting faculty and can only come on: {days}."))
        seen: set[uuid.UUID] = set()
        for f in ctx.fixed_by_teacher.get((tid, day), []):
            if f.key in seen or not overlap(start, use, f.start, f.length):
                continue
            seen.add(f.key)
            where = " + ".join(sorted(ctx.fixed_classes[f.key]))
            res.hard.append(Issue(
                "TEACHER_BUSY", True,
                f"{name} is in the locked slot '{f.label}' ({where}) at {span(f.start, f.length)} on {day_name}.",
                frozenset({me or "me", f.key}),
            ))
        for e in ctx.entries_by_teacher_day.get((tid, day), []):
            if e.key in ignore or e.key in seen or not ctx.is_ref(e, draft_id, sem_id) or not overlap(start, use, e.start, e.length):
                continue
            if e.draft_id == draft_id:
                continue  # the same class: already reported as "the class is busy"
            seen.add(e.key)
            info = ctx.drafts[e.draft_id]
            res.hard.append(Issue(
                "TEACHER_BUSY", True,
                f"{name} already teaches '{ctx.subjects[e.subject_id].name}' in {ctx.labels.get(e.semester_id, 'another class')} "
                f"(draft '{info.name}') at {span(e.start, e.length)} on {day_name}.",
                frozenset({me or "me", e.key}),
            ))
        if person is not None and person.is_visiting and person.max_weekly_hours:
            hours = sum(f.length for f in ctx.fixed_by_teacher_all.get(tid, {}).values())
            hours += sum(e.length for e in ctx.entries_by_teacher_all.get(tid, []) if e.key not in ignore and ctx.is_ref(e, draft_id, sem_id))
            if hours + use > person.max_weekly_hours:
                res.hard.append(Issue(
                    "VISITING_HOURS", True,
                    f"{name} is visiting faculty with a limit of {person.max_weekly_hours} hours a week and already has {hours}.",
                ))

    # --- soft rules ------------------------------------------------------------------------------------------
    mine = ctx.entries_by_draft_day.get((draft_id, day), [])
    for s in subs:
        if not s.is_lab and any(e.subject_id == s.id and e.key not in ignore for e in mine):
            res.soft.append(Issue("SAME_SUBJECT_TWICE_DAY", False, f"'{s.name}' is already taught on {day_name}. Twice in one day is not ideal."))
        if not ctx.teachers(s.id):
            res.soft.append(Issue("NO_TEACHER", False, f"'{s.name}' has no teacher yet (Assignment screen)."))
        have = sum(1 for e in ctx.entries_by_draft.get(draft_id, []) if e.subject_id == s.id and e.key not in ignore)
        need = 1 if s.is_lab else s.credits
        if have + 1 > need:
            res.soft.append(Issue("OVER_REQUIRED", False, f"'{s.name}' already has all its {need} {'block' if s.is_lab else 'lecture(s)'} - this would be one more."))
    if not res.hard:
        before = set(gap_runs(ctx.class_occupied(sem_id, draft_id, day, ignore)))
        after = set(gap_runs(ctx.class_occupied(sem_id, draft_id, day, ignore) | set(range(start, start + use))))
        for a, b in sorted(after - before):
            res.soft.append(Issue("GAP_CLASS", False, f"{label} would have {b - a + 1} free periods in a row ({a}-{b}) on {day_name}."))
        for tid in per_teacher:
            occ = ctx.teacher_occupied(tid, draft_id, sem_id, day, ignore)
            before_t = set(gap_runs(occ))
            after_t = set(gap_runs(occ | set(range(start, start + use))))
            for a, b in sorted(after_t - before_t):
                res.soft.append(Issue("GAP_TEACHER", False, f"{ctx.person_name(tid)} would wait {b - a + 1} free periods ({a}-{b}) between lectures on {day_name}."))
        res.notes.extend([f"Occupies {span(start, use)}."])
    res.hard = _uniq(res.hard)
    res.soft = _uniq(res.soft)
    return res


# ---------------------------------------------------------------------------------------------------- the whole week at once
def options_map(
    ctx: Ctx, *, draft_id: uuid.UUID, sem_id: uuid.UUID, subject_ids: list[uuid.UUID],
    ignore: frozenset | set = frozenset(), me: uuid.UUID | None = None, length: int | None = None,
) -> dict[str, dict[str, Any]]:
    """check_unit() for every start cell of the week: what the drag-and-drop colouring is made of."""
    cells: dict[str, dict[str, Any]] = {}
    for day in range(1, min(6, ctx.days_per_week) + 1):
        for start in range(1, len(ctx.grid.periods) + 1):
            r = check_unit(ctx, draft_id=draft_id, sem_id=sem_id, subject_ids=subject_ids, day=day, start=start, ignore=ignore, me=me, length=length)
            cells[f"{day}:{start}"] = {"state": r.state(), "hard": [i.message for i in r.hard], "soft": [i.message for i in r.soft]}
    return cells


def swap_check(ctx: Ctx, draft_id: uuid.UUID, sem_id: uuid.UUID, a: list[Entry], b: list[Entry]) -> Result:
    """Can the two placements exchange their places? (equal block sizes only; both new positions are checked)"""
    ra, rb = Result(), Result()
    if a[0].length != b[0].length:
        ra.hard.append(Issue("SWAP_SIZE", True, "A lab block (2 periods) can only be swapped with another 2-period block."))
        return ra
    ignore = {a[0].key, b[0].key}
    ra = check_unit(ctx, draft_id=draft_id, sem_id=sem_id, subject_ids=[e.subject_id for e in a], day=b[0].day, start=b[0].start,
                    ignore=ignore, me=a[0].key, length=a[0].length)
    rb = check_unit(ctx, draft_id=draft_id, sem_id=sem_id, subject_ids=[e.subject_id for e in b], day=a[0].day, start=a[0].start,
                    ignore=ignore, me=b[0].key, length=b[0].length)
    out = Result(length=a[0].length)
    out.hard = _uniq([*ra.hard, *rb.hard])
    out.soft = _uniq([*ra.soft, *rb.soft])
    return out


# ---------------------------------------------------------------------------------------------------- completeness
def completeness(ctx: Ctx, sem_id: uuid.UUID, draft_id: uuid.UUID | None) -> dict[str, Any]:
    """Per subject (and per elective group): the lectures placed against the lectures required (credits; a lab = 1 block;
    PE / SOL = their locked slot)."""
    placed = Counter(e.subject_id for e in ctx.entries_by_draft.get(draft_id, [])) if draft_id else Counter()
    fixed_kinds: dict[str, set[uuid.UUID]] = defaultdict(set)
    for (sid, _day), rows in ctx.fixed_by_class.items():
        if sid == sem_id:
            for f in rows:
                if f.kind in ("PE", "SOL"):
                    fixed_kinds[f.kind].add(f.key)
    rows: list[dict[str, Any]] = []
    for s in sorted(ctx.subjects_by_class.get(sem_id, []), key=lambda x: (x.group_name or "", x.name)):
        if s.special_kind:
            required, got = 1, min(1, len(fixed_kinds.get(s.special_kind, ())))
            kind = "fixed"
        elif s.is_lab:
            required, got, kind = 1, placed[s.id], "lab"
        else:
            required, got, kind = s.credits, placed[s.id], "lecture"
        rows.append({
            "subject_id": str(s.id), "required": required, "placed": got, "kind": kind,
            "status": "missing" if got < required else ("over" if got > required else "complete"),
        })
    by_id = {r["subject_id"]: r for r in rows}
    groups: dict[uuid.UUID, dict[str, Any]] = {}
    for s in ctx.subjects_by_class.get(sem_id, []):
        if s.group_id:
            g = groups.setdefault(s.group_id, {"group_id": str(s.group_id), "name": s.group_name, "members": []})
            g["members"].append(str(s.id))
    for g in groups.values():
        members = [by_id[m] for m in g["members"]]
        g["required"] = max(m["required"] for m in members)
        g["placed"] = min(m["placed"] for m in members)
        g["kind"] = members[0]["kind"]
        g["status"] = "missing" if g["placed"] < g["required"] else ("over" if g["placed"] > g["required"] else "complete")
    units = [r for r in rows if not any(r["subject_id"] in g["members"] for g in groups.values())] + list(groups.values())
    required_total = sum(u["required"] for u in units)
    placed_total = sum(min(u["placed"], u["required"]) for u in units)
    return {
        "subjects": rows,
        "groups": list(groups.values()),
        "required": required_total,
        "placed": placed_total,
        "percent": round(100 * placed_total / required_total) if required_total else 0,
        "missing": sum(max(0, u["required"] - u["placed"]) for u in units),
    }


# ---------------------------------------------------------------------------------------------------- saved drafts
def units_of(entries: Iterable[Entry]) -> dict[uuid.UUID, list[Entry]]:
    out: dict[uuid.UUID, list[Entry]] = defaultdict(list)
    for e in entries:
        out[e.key].append(e)
    return out


def check_saved_unit(ctx: Ctx, draft_id: uuid.UUID, rows: list[Entry]) -> Result:
    """The problems of one saved placement, judged against everything else (not against itself)."""
    first = rows[0]
    return check_unit(
        ctx, draft_id=draft_id, sem_id=first.semester_id, subject_ids=[e.subject_id for e in rows], day=first.day, start=first.start,
        ignore={first.key}, me=first.key, length=first.length,
    )


def analyze_draft(ctx: Ctx, draft_id: uuid.UUID) -> dict[str, Any]:
    """Every problem of a saved draft: clashes (hard), warnings (soft) and what is still missing."""
    info = ctx.drafts[draft_id]
    sem_id = info.semester_id
    label = ctx.labels.get(sem_id, "a class")
    clashes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for key, rows in units_of(ctx.entries_by_draft.get(draft_id, [])).items():
        first = rows[0]
        res = check_saved_unit(ctx, draft_id, rows)
        names = " ∥ ".join(ctx.subjects[e.subject_id].name for e in rows)
        where = {"day": first.day, "start_period": first.start, "length": first.length, "placement_key": str(key), "subjects": names}
        for i in res.hard:
            clashes.append({**where, "code": i.code, "message": i.message, "pair": sorted(str(p) for p in i.pair)})
        for i in res.soft:
            if i.code != "OVER_REQUIRED":  # reported once per subject through the completeness part
                warnings.append({**where, "code": i.code, "message": i.message})
    comp = completeness(ctx, sem_id, draft_id)
    names = {str(s.id): s.name for s in ctx.subjects.values()}
    missing: list[dict[str, Any]] = []
    for g in comp["groups"]:
        if g["status"] != "complete":
            missing.append({**g, "label": f"{g['name']} ({' ∥ '.join(names[m] for m in g['members'])})"})
    grouped = {m for g in comp["groups"] for m in g["members"]}
    for r in comp["subjects"]:
        if r["subject_id"] not in grouped and r["status"] != "complete":
            missing.append({**r, "label": names[r["subject_id"]]})
    return {"draft_id": draft_id, "class_label": label, "clashes": clashes, "warnings": warnings, "missing": missing, "completeness": comp}
