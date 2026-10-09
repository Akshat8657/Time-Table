"""The automatic generation engine (plan Section 10 + Section 19 Phase 3) - pure Python, no database, no web.

One run schedules ALL classes of the school together (teachers are shared, so per-class generation cannot work):

  build_problem()   turns the Phase 2 context (schedule.Ctx) into a solver problem
  precheck()        the capacity / bottleneck findings ("Sem 5 needs 29 periods but its week only offers 24")
  solve()           randomized construction + repair, many attempts, returns the best 2-3 DISTINCT scored solutions
  score_solution()  the soft rules with their 0-100 weights -> total score + a per-rule breakdown
  verify_solution() the final word: the PHASE 2 clash engine (services/schedule.py) re-checks every placement

Hard rules are never traded away: a cell is only ever used when the class is free, every teacher (co-teachers and both
track-group teachers included) is free across fixed slots AND this solution, the day is a working day of the class,
the block fits before the class's end time, and visiting faculty stay inside their days and weekly hours.
If something cannot be placed, the solution says exactly what and why - never a vague failure.

Phase 6: teacher preferences (services/prefs.py).  A teacher's NOT-AVAILABLE slots and daily maximum are HARD rules (they
shape every unit's domain and State.fits, so they are never used); preferred slots + the first / last lecture choices are the
soft rule "preferred_slots" in RULES.  The solver reads every rule from the registry, so nothing else changed.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any, Callable

from ..validation import DAY_NAMES, DAY_SHORT
from .schedule import Ctx, DraftInfo, Entry, analyze_draft, completeness
from .timing import FULL_DAY
from .grid import to_min

Cell = tuple[int, int]  # (day, period)

# ---------------------------------------------------------------------------------------------------- the soft rules
# name -> (label for the screens, default weight 0-100). Scoring lives in score_solution(); the construction
# heuristic reads the same weights, so changing a weight really changes where things land.
RULES: dict[str, tuple[str, int]] = {
    "twice_a_day": ("Same subject not twice in one day", 70),
    "class_gaps": ("No long empty gaps for a class", 80),
    "teacher_gaps": ("No long waits for a teacher", 50),
    "balance_load": ("Balance each teacher's day-to-day load", 40),
    "spread": ("Spread each subject across the week", 60),
    "lab_lunch": ("Keep labs away from the lunch break", 30),
    "visiting_packed": ("Pack visiting faculty's days tightly", 50),
    "preferred_slots": ("Teachers' preferred slots (stars, first / last lecture)", 60),
}


def default_weights() -> dict[str, int]:
    return {name: w for name, (_label, w) in RULES.items()}


def clean_weights(raw: dict[str, Any] | None) -> dict[str, int]:
    out = default_weights()
    for k, v in (raw or {}).items():
        if k in out and isinstance(v, (int, float)) and 0 <= v <= 100:
            out[k] = int(v)
    return out


class Cancelled(Exception):
    pass


# when the first batch of restarts leaves a solvable-looking problem 1-3 lectures short, solve() tries more (see solve())
EXTRA_FACTOR = 4          # at most this many times the usual number of attempts
EXTRA_SECONDS = 45.0      # ... and never beyond this much working time
EXTRA_MAX_MISSING = 3     # ... and only when the best answer is at most this many lectures short


# ---------------------------------------------------------------------------------------------------- the problem
@dataclass(frozen=True)
class Unit:
    """One thing to place: a subject - or a whole elective group (all options together, one parallel slot)."""

    uid: int
    class_id: uuid.UUID
    subject_ids: tuple[uuid.UUID, ...]
    teachers: tuple[uuid.UUID, ...]          # teacher + co-teacher of every option, deduplicated
    length: int                              # 1, or 2 for a lab block
    needed: int                              # how many placements a week (weekly lectures = credits unless overridden; a lab = 1 block)
    name: str                                # "Maths" or "AI Major ∥ Cyber Major"
    is_lab: bool
    group_id: uuid.UUID | None


@dataclass
class Problem:
    classes: list[uuid.UUID]
    labels: dict[uuid.UUID, str]
    units: list[Unit]
    allowed: dict[uuid.UUID, set[Cell]]       # free cells of each class (fixed slots already taken out)
    class_fixed: dict[uuid.UUID, set[Cell]]   # the fixed-slot cells (occupied - they count for gap scoring)
    teacher_fixed: dict[uuid.UUID, set[Cell]]  # cells where a teacher sits in a locked session (combined = once)
    teacher_fixed_hours: dict[uuid.UUID, int]
    teacher_name: dict[uuid.UUID, str]
    visiting_days: dict[uuid.UUID, frozenset[int]]   # only for visiting faculty
    visiting_max: dict[uuid.UUID, int]
    periods_per_day: int
    days_per_week: int
    lunch_between: set[int]                   # start periods whose 2-block runs around lunch
    inactive: set[uuid.UUID] = field(default_factory=set)          # deactivated teachers (their units cannot be placed)
    locked: list[tuple[int, Cell]] = field(default_factory=list)   # (unit uid, cell) pre-placed by hand
    # Phase 6 - teacher preferences
    teacher_unavailable: dict[uuid.UUID, frozenset[Cell]] = field(default_factory=dict)   # HARD: cells a teacher marked "not available"
    teacher_max_day: dict[uuid.UUID, int] = field(default_factory=dict)                   # HARD: most lecture periods in one day
    teacher_fixed_day: dict[uuid.UUID, Counter] = field(default_factory=dict)             # periods a teacher already has per day in locked sessions
    teacher_preferred: dict[uuid.UUID, frozenset[Cell]] = field(default_factory=dict)     # SOFT: starred cells
    prefer_first: set[uuid.UUID] = field(default_factory=set)                             # SOFT: likes period 1
    avoid_last: set[uuid.UUID] = field(default_factory=set)                               # SOFT: dislikes the last period

    @property
    def has_soft_prefs(self) -> bool:
        return bool(self.teacher_preferred or self.prefer_first or self.avoid_last)

    def day_name(self, d: int) -> str:
        return DAY_NAMES.get(d, f"day {d}")


def build_problem(ctx: Ctx, *, locked_by_class: dict[uuid.UUID, list] | None = None) -> Problem:
    """locked_by_class: semester_id -> [schedule.Entry] hand-pinned placements the generator must keep."""
    classes = sorted(ctx.labels, key=lambda c: ctx.labels[c])
    allowed: dict[uuid.UUID, set[Cell]] = {}
    class_fixed: dict[uuid.UUID, set[Cell]] = defaultdict(set)
    total = len(ctx.grid.periods)
    for cid in classes:
        cells: set[Cell] = set()
        for day in range(1, min(6, ctx.days_per_week) + 1):
            rule = ctx.rules.get((cid, day), FULL_DAY)
            if rule.is_off:
                continue
            limit = to_min(rule.end_time) if rule.end_time is not None else None
            for p in range(1, total + 1):
                if limit is not None and ctx.grid.periods[p - 1].end > limit:
                    continue
                cells.add((day, p))
        allowed[cid] = cells
    teacher_fixed: dict[uuid.UUID, set[Cell]] = defaultdict(set)
    teacher_fixed_hours: dict[uuid.UUID, int] = defaultdict(int)
    seen_keys: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for f in ctx.fixed:
        span = {(f.day, p) for p in range(f.start, f.start + f.length)}
        class_fixed[f.semester_id] |= span
        allowed.get(f.semester_id, set()).difference_update(span)
        for t in {f.teacher_id, f.co_teacher_id} - {None}:
            if f.key in seen_keys[t]:  # a combined session is ONE place
                continue
            seen_keys[t].add(f.key)
            teacher_fixed[t] |= span  # type: ignore[index]
            teacher_fixed_hours[t] += f.length  # type: ignore[index]

    units: list[Unit] = []
    for cid in classes:
        done_groups: set[uuid.UUID] = set()
        for s in sorted(ctx.subjects_by_class.get(cid, []), key=lambda x: x.name):
            if s.special_kind:
                continue  # PE / SOL come only from their fixed slots (hard rule 10)
            if s.group_id:
                if s.group_id in done_groups:
                    continue
                done_groups.add(s.group_id)
                members = [m for m in ctx.subjects_by_class[cid] if m.group_id == s.group_id]
                teachers: list[uuid.UUID] = []
                for m in members:
                    teachers.extend(ctx.teachers(m.id))
                units.append(Unit(
                    len(units), cid, tuple(m.id for m in members), tuple(dict.fromkeys(teachers)),
                    2 if s.is_lab else 1, 1 if s.is_lab else max(m.weekly for m in members),
                    " ∥ ".join(m.name for m in members), s.is_lab, s.group_id,
                ))
            else:
                units.append(Unit(
                    len(units), cid, (s.id,), tuple(dict.fromkeys(ctx.teachers(s.id))),
                    2 if s.is_lab else 1, 1 if s.is_lab else s.weekly, s.name, s.is_lab, None,
                ))

    lunch_between = {start for start in range(1, total) if ctx.grid.lunch_inside(start, 2)}
    problem = Problem(
        classes=classes, labels=dict(ctx.labels), units=units, allowed=allowed,
        class_fixed=dict(class_fixed), teacher_fixed=dict(teacher_fixed), teacher_fixed_hours=dict(teacher_fixed_hours),
        teacher_name={tid: p.name for tid, p in ctx.people.items()},
        visiting_days={tid: p.visiting_days for tid, p in ctx.people.items() if p.is_visiting},
        visiting_max={tid: p.max_weekly_hours for tid, p in ctx.people.items() if p.is_visiting and p.max_weekly_hours},
        periods_per_day=total, days_per_week=min(6, ctx.days_per_week), lunch_between=lunch_between,
        inactive={tid for tid, p in ctx.people.items() if not p.is_active},
    )
    for tid, pref in ctx.prefs.items():   # Phase 6: the teachers' own choices
        if pref.unavailable:
            problem.teacher_unavailable[tid] = pref.unavailable
        if pref.max_per_day:
            problem.teacher_max_day[tid] = pref.max_per_day
        if pref.preferred:
            problem.teacher_preferred[tid] = pref.preferred
        if pref.prefer_first:
            problem.prefer_first.add(tid)
        if pref.avoid_last:
            problem.avoid_last.add(tid)
    for tid, cells in teacher_fixed.items():
        problem.teacher_fixed_day[tid] = Counter(d for d, _p in cells)
    for cid, entries in (locked_by_class or {}).items():
        for key, rows in _units_by_key(entries).items():
            first = rows[0]
            subject_set = frozenset(e.subject_id for e in rows)
            unit = next((u for u in units if u.class_id == cid and frozenset(u.subject_ids) == subject_set), None)
            if unit is None:  # a lone option pinned by hand: match the unit that contains it
                unit = next((u for u in units if u.class_id == cid and subject_set <= frozenset(u.subject_ids)), None)
            if unit is not None and first.length == unit.length:
                problem.locked.append((unit.uid, (first.day, first.start)))
    return problem


def _units_by_key(entries) -> dict:
    out: dict = defaultdict(list)
    for e in entries:
        out[e.key].append(e)
    return out


def domain_of(problem: Problem, unit: Unit, *, ignore_prefs: bool = False) -> list[Cell]:
    """Every start cell this unit could ever use (static rules only: class week, block fit, visiting days, and the teachers'
    "not available" slots). `ignore_prefs=True` answers "what would it be without the preferences?" (used to explain a shortfall)."""
    if any(t in problem.inactive for t in unit.teachers):
        return []  # a deactivated teacher can never be scheduled (hard rule)
    days = None
    for t in unit.teachers:
        if t in problem.visiting_days:
            days = problem.visiting_days[t] if days is None else days & problem.visiting_days[t]
    banned: list[frozenset[Cell]] = [] if ignore_prefs else [problem.teacher_unavailable[t] for t in unit.teachers if t in problem.teacher_unavailable]
    cells = []
    for (d, p) in sorted(problem.allowed[unit.class_id]):
        if days is not None and d not in days:
            continue
        if unit.length == 2 and (d, p + 1) not in problem.allowed[unit.class_id]:
            continue
        if banned and any((d, p + j) in b for b in banned for j in range(unit.length)):
            continue  # hard rule 7: a teacher's not-available slot is never used
        cells.append((d, p))
    return cells


# ---------------------------------------------------------------------------------------------------- precheck
def precheck(problem: Problem) -> list[dict[str, str]]:
    """Human findings BEFORE solving: exactly which class / teacher / rule makes a full timetable impossible."""
    findings: list[dict[str, str]] = []
    for cid in problem.classes:
        demand = sum(u.needed * u.length for u in problem.units if u.class_id == cid)
        free = len(problem.allowed[cid])
        if demand > free:
            per_day = Counter(d for d, _p in problem.allowed[cid])
            days = ", ".join(f"{DAY_SHORT[d]} {per_day.get(d, 0)}" for d in range(1, problem.days_per_week + 1) if per_day.get(d, 0))
            findings.append({
                "kind": "class_capacity", "about": problem.labels[cid],
                "message": f"{problem.labels[cid]} needs {demand} lecture periods a week (weekly lectures + lab blocks), "
                           f"but its day-wise schedule leaves only {free} free ({days}). {demand - free} will not fit - "
                           "give the class more periods (Timing Setup) or lower some subjects' weekly lectures.",
            })
    # a teacher inside one class's window
    per_tc: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
    per_t: dict[uuid.UUID, int] = defaultdict(int)
    for u in problem.units:
        for t in u.teachers:
            per_tc[(t, u.class_id)] += u.needed * u.length
            per_t[t] += u.needed * u.length
    for (t, cid), demand in sorted(per_tc.items(), key=lambda kv: -kv[1]):
        days = problem.visiting_days.get(t)
        base = {c for c in problem.allowed[cid] if days is None or c[0] in days} - problem.teacher_fixed.get(t, set())
        window = base - problem.teacher_unavailable.get(t, frozenset())
        if demand > len(window):
            which = f" on their days ({', '.join(DAY_SHORT[d] for d in sorted(days))})" if days else ""
            pref_note = (
                f", after leaving out the {len(base) - len(window)} period(s) they marked not available" if len(window) < len(base) and demand <= len(base) else ""
            )
            findings.append({
                "kind": "teacher_window", "about": problem.teacher_name.get(t, "A teacher"),
                "message": f"{problem.teacher_name.get(t, 'A teacher')} must teach {demand} periods for {problem.labels[cid]}, "
                           f"but that class only has {len(window)} free periods{which}{pref_note}.",
            })
    # a teacher across the whole week
    for t, demand in sorted(per_t.items(), key=lambda kv: -kv[1]):
        days = problem.visiting_days.get(t)
        day_list = sorted(days) if days else list(range(1, problem.days_per_week + 1))
        avail = len(day_list) * problem.periods_per_day - problem.teacher_fixed_hours.get(t, 0)
        if t in problem.visiting_max:
            avail = min(avail, problem.visiting_max[t] - problem.teacher_fixed_hours.get(t, 0))
        if demand > avail:
            why = f"their weekly limit of {problem.visiting_max[t]} hours" if t in problem.visiting_max and avail < len(day_list) * problem.periods_per_day else \
                f"only {avail} periods exist on {', '.join(DAY_SHORT[d] for d in day_list)}"
            findings.append({
                "kind": "teacher_capacity", "about": problem.teacher_name.get(t, "A teacher"),
                "message": f"{problem.teacher_name.get(t, 'A teacher')} would need {demand} teaching periods a week, but {why}.",
            })
            continue
        # Phase 6: what is left after the teacher's own preferences (not-available slots + a daily maximum), day by day
        unavailable, md = problem.teacher_unavailable.get(t, frozenset()), problem.teacher_max_day.get(t)
        if not unavailable and md is None:
            continue
        fixed_cells = problem.teacher_fixed.get(t, set())
        room = 0
        for d in day_list:
            fixed_d = {c for c in fixed_cells if c[0] == d}
            blocked_d = {c for c in unavailable if c[0] == d} - fixed_d
            free_d = problem.periods_per_day - len(fixed_d) - len(blocked_d)
            if md is not None:
                free_d = min(free_d, md - len(fixed_d))
            room += max(0, free_d)
        if t in problem.visiting_max:
            room = min(room, problem.visiting_max[t] - problem.teacher_fixed_hours.get(t, 0))
        if demand > room:
            parts = []
            if unavailable:
                parts.append(f"{len({c for c in unavailable if c[0] in day_list} - fixed_cells)} period(s) marked not available")
            if md is not None:
                parts.append(f"a limit of {md} lecture(s) a day")
            findings.append({
                "kind": "teacher_preferences", "about": problem.teacher_name.get(t, "A teacher"),
                "message": f"{problem.teacher_name.get(t, 'A teacher')} would need {demand} teaching periods a week, but their preferences "
                           f"({' and '.join(parts)}) leave room for only {room}. Ask the teacher to relax them, or lower their load.",
            })
    for u in problem.units:
        if not u.needed:
            continue
        dead = [problem.teacher_name.get(t, "a teacher") for t in u.teachers if t in problem.inactive]
        if dead:
            findings.append({
                "kind": "no_cell", "about": u.name,
                "message": f"'{u.name}' ({problem.labels[u.class_id]}) cannot be scheduled: {', '.join(dead)} is deactivated. "
                           "Assign another teacher first.",
            })
        elif not domain_of(problem, u):
            blocked_by = [problem.teacher_name.get(t, "a teacher") for t in u.teachers if t in problem.teacher_unavailable]
            if blocked_by and domain_of(problem, u, ignore_prefs=True):
                findings.append({
                    "kind": "no_cell", "about": u.name,
                    "message": f"'{u.name}' ({problem.labels[u.class_id]}) has no possible period left: every period is ruled out by "
                               f"{', '.join(blocked_by)}'s not-available slots. Ask them to free some.",
                })
            else:
                findings.append({
                    "kind": "no_cell", "about": u.name,
                    "message": f"'{u.name}' ({problem.labels[u.class_id]}) has no possible period at all - "
                               "check the class's day-wise schedule and the teacher's visiting days.",
                })
    return findings


# ---------------------------------------------------------------------------------------------------- one attempt
@dataclass
class State:
    problem: Problem
    weights: dict[str, float]
    rng: random.Random
    assign: dict[tuple[int, int], Cell] = field(default_factory=dict)     # (uid, instance) -> cell
    locked_keys: set[tuple[int, int]] = field(default_factory=set)
    class_occ: dict[uuid.UUID, set[Cell]] = field(default_factory=lambda: defaultdict(set))
    teacher_occ: dict[uuid.UUID, set[Cell]] = field(default_factory=lambda: defaultdict(set))
    teacher_hours: dict[uuid.UUID, int] = field(default_factory=lambda: defaultdict(int))
    teacher_day: dict[uuid.UUID, Counter] = field(default_factory=lambda: defaultdict(Counter))   # periods per weekday (generated only)
    unit_days: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))
    unplaced: Counter = field(default_factory=Counter)                     # uid -> instances missing

    def cells_of(self, unit: Unit, cell: Cell) -> list[Cell]:
        d, p = cell
        return [(d, p + j) for j in range(unit.length)]

    def day_load(self, t: uuid.UUID, d: int) -> int:
        """Lecture periods a teacher already has on weekday `d`: placed so far + their locked sessions."""
        return self.teacher_day[t][d] + self.problem.teacher_fixed_day.get(t, {}).get(d, 0)

    def fits(self, unit: Unit, cell: Cell) -> bool:
        span = self.cells_of(unit, cell)
        occ = self.class_occ[unit.class_id]
        if any(c in occ for c in span):
            return False
        for t in unit.teachers:
            busy = self.teacher_occ[t]
            fixed = self.problem.teacher_fixed.get(t, ())
            if any(c in busy or c in fixed for c in span):
                return False
            cap = self.problem.visiting_max.get(t)
            if cap is not None and self.teacher_hours[t] + self.problem.teacher_fixed_hours.get(t, 0) + unit.length > cap:
                return False
            per_day = self.problem.teacher_max_day.get(t)   # hard rule 11: the teacher's own daily maximum
            if per_day is not None and self.day_load(t, cell[0]) + unit.length > per_day:
                return False
            banned = self.problem.teacher_unavailable.get(t)  # hard rule 7 (normally already out of the domain; also guards pinned cells)
            if banned and any(c in banned for c in span):
                return False
        return True

    def put(self, key: tuple[int, int], unit: Unit, cell: Cell) -> None:
        span = self.cells_of(unit, cell)
        self.assign[key] = cell
        self.class_occ[unit.class_id].update(span)
        for t in unit.teachers:
            self.teacher_occ[t].update(span)
            self.teacher_hours[t] += unit.length
            self.teacher_day[t][cell[0]] += unit.length
        self.unit_days[unit.uid][cell[0]] += 1

    def take(self, key: tuple[int, int], unit: Unit) -> Cell:
        cell = self.assign.pop(key)
        span = self.cells_of(unit, cell)
        self.class_occ[unit.class_id].difference_update(span)
        for t in unit.teachers:
            self.teacher_occ[t].difference_update(span)
            self.teacher_hours[t] -= unit.length
            self.teacher_day[t][cell[0]] -= unit.length
        self.unit_days[unit.uid][cell[0]] -= 1
        return cell

    # ---- the construction heuristic: a cheap local penalty steered by the SAME weights as the final score
    def penalty(self, unit: Unit, cell: Cell, noise: float = 6.0) -> float:
        w = self.weights
        d, p = cell
        pen = self.rng.random() * noise
        repeats = self.unit_days[unit.uid][d]
        if repeats and not unit.is_lab:
            pen += w["twice_a_day"] * 1.2 * repeats
        if unit.needed > 1:
            pen += w["spread"] * 0.5 * repeats
        span = self.cells_of(unit, cell)
        occ_day = sorted(q for (dd, q) in (self.class_occ[unit.class_id] | self.problem.class_fixed.get(unit.class_id, set())) if dd == d)
        pen += w["class_gaps"] * 0.3 * _gap_delta(occ_day, [q for _dd, q in span])
        for t in unit.teachers:
            t_day = sorted(q for (dd, q) in (self.teacher_occ[t] | self.problem.teacher_fixed.get(t, set())) if dd == d)
            pen += w["teacher_gaps"] * 0.12 * _gap_delta(t_day, [q for _dd, q in span])
            pen += w["balance_load"] * 0.06 * len(t_day)
            if t in self.problem.visiting_days and not t_day and self.teacher_hours[t] > 0:
                pen += w["visiting_packed"] * 0.8  # opening one more day for a visiting teacher
        if unit.length == 2 and p in self.problem.lunch_between:
            pen += w["lab_lunch"] * 1.0
        if self.problem.has_soft_prefs:   # Phase 6: a star is a reward, the last period an extra cost for those who avoid it
            pw = w["preferred_slots"]
            last = self.problem.periods_per_day
            for t in unit.teachers:
                stars = self.problem.teacher_preferred.get(t)
                if stars:
                    pen -= pw * 0.5 * sum(1 for c in span if c in stars)
                if t in self.problem.prefer_first and p == 1:
                    pen -= pw * 0.5
                if t in self.problem.avoid_last and any(q == last for _dd, q in span):
                    pen += pw * 0.6
        return pen


def _gap_delta(occupied: list[int], adding: list[int]) -> float:
    def idle(xs: list[int]) -> int:
        return (xs[-1] - xs[0] + 1 - len(xs)) if len(xs) > 1 else 0

    after = sorted(set(occupied) | set(adding))
    return max(0, idle(after) - idle(occupied))


def _attempt(problem: Problem, weights: dict[str, float], rng: random.Random, deadline: float, is_cancelled: Callable[[], bool]) -> State:
    st = State(problem, weights, rng)
    domains = {u.uid: domain_of(problem, u) for u in problem.units}
    # hand-pinned placements first (semi-automatic mode): they are never touched again
    used_instances: Counter = Counter()
    for uid, cell in problem.locked:
        unit = problem.units[uid]
        key = (uid, used_instances[uid])
        used_instances[uid] += 1
        if (cell in domain_of(problem, unit)) and st.fits(unit, cell):
            st.put(key, unit, cell)
            st.locked_keys.add(key)
        else:  # a pinned placement that no longer fits is reported, never silently dropped
            st.unplaced[unit.uid] += 1
    # most-constrained first: tiny domains and busy teachers early
    order: list[tuple[int, int]] = []
    for u in problem.units:
        for i in range(used_instances[u.uid], u.needed):
            order.append((u.uid, i))
    tightness = {u.uid: len(domains[u.uid]) / max(1, u.needed) - 0.4 * len(u.teachers) for u in problem.units}
    order.sort(key=lambda k: (tightness[k[0]], rng.random()))
    for key in order:
        if is_cancelled():
            raise Cancelled()
        unit = problem.units[key[0]]
        options = [c for c in domains[unit.uid] if st.fits(unit, c)]
        if not options:
            if not _eject_and_place(st, unit, key, domains):
                st.unplaced[unit.uid] += 1
            continue
        st.put(key, unit, min(options, key=lambda c: st.penalty(unit, c)))
    _improve(st, domains, deadline, is_cancelled)
    return st


def _eject_and_place(st: State, unit: Unit, key: tuple[int, int], domains: dict[int, list[Cell]]) -> bool:
    """Depth-1 repair: free a cell by moving exactly one other (unpinned) placement somewhere else."""
    p = st.problem
    cells = list(domains[unit.uid])
    st.rng.shuffle(cells)
    for cell in cells[:40]:
        span = set(st.cells_of(unit, cell))
        blockers: set[tuple[int, int]] = set()
        ok = True
        for other_key, other_cell in st.assign.items():
            other = p.units[other_key[0]]
            other_span = set(st.cells_of(other, other_cell))
            if not (other_span & span):
                continue
            if other.class_id == unit.class_id or set(other.teachers) & set(unit.teachers):
                if other_key in st.locked_keys:
                    ok = False
                    break
                blockers.add(other_key)
        if not ok or not 1 <= len(blockers) <= 2:   # a 2-period block can be blocked by two different lectures (one per period)
            continue
        for t in unit.teachers:  # a fixed slot can never be ejected
            if p.teacher_fixed.get(t) and span & p.teacher_fixed[t]:
                ok = False
        if not ok:
            continue
        taken = []
        for b_key in sorted(blockers):
            b_unit = p.units[b_key[0]]
            taken.append((b_key, b_unit, st.take(b_key, b_unit)))
        if st.fits(unit, cell):
            st.put(key, unit, cell)  # place ours FIRST, so each blocker's new home is checked against the full picture
            back: list[tuple[tuple[int, int], Unit]] = []
            for b_key, b_unit, old in taken:
                homes = [c for c in domains[b_unit.uid] if c != old and st.fits(b_unit, c)]
                if not homes:
                    break
                st.put(b_key, b_unit, min(homes, key=lambda c: st.penalty(b_unit, c)))
                back.append((b_key, b_unit))
            else:
                return True
            for b_key, b_unit in back:   # one blocker could not be re-homed: undo everything
                st.take(b_key, b_unit)
            st.take(key, unit)
        for b_key, b_unit, old in taken:
            st.put(b_key, b_unit, old)
    return False


def _improve(st: State, domains: dict[int, list[Cell]], deadline: float, is_cancelled: Callable[[], bool]) -> None:
    p = st.problem
    for _sweep in range(6):
        if monotonic() > deadline or is_cancelled():
            return
        improved = False
        # fill anything missing first
        for uid in [u for u, n in list(st.unplaced.items()) if n > 0]:
            unit = p.units[uid]
            key = (uid, max((k[1] for k in st.assign if k[0] == uid), default=-1) + 1)
            options = [c for c in domains[uid] if st.fits(unit, c)]
            if options:
                st.put(key, unit, min(options, key=lambda c: st.penalty(unit, c)))
                st.unplaced[uid] -= 1
                improved = True
            elif _eject_and_place(st, unit, key, domains):
                st.unplaced[uid] -= 1
                improved = True
        keys = [k for k in st.assign if k not in st.locked_keys]
        st.rng.shuffle(keys)
        for key in keys:
            unit = p.units[key[0]]
            old = st.take(key, unit)
            base = st.penalty(unit, old, noise=0)
            candidates = [c for c in st.rng.sample(domains[unit.uid], min(14, len(domains[unit.uid]))) if st.fits(unit, c)]
            best, best_pen = old, base
            for c in candidates:
                pen = st.penalty(unit, c, noise=0)
                if pen < best_pen - 1e-9:
                    best, best_pen = c, pen
            st.put(key, unit, best)
            if best != old:
                improved = True
        if not improved:
            return


# ---------------------------------------------------------------------------------------------------- scoring
def score_solution(problem: Problem, st: State, weights: dict[str, int]) -> tuple[float, list[dict[str, Any]]]:
    sats = _satisfactions(problem, st)
    total_w = sum(weights.values()) or 1
    score = sum(weights[name] * sats[name] for name in RULES) / total_w * 100
    breakdown = [
        {"rule": name, "label": RULES[name][0], "weight": weights[name],
         "satisfaction": round(sats[name], 3), "points_lost": round(weights[name] * (1 - sats[name]) / total_w * 100, 1)}
        for name in RULES
    ]
    for row in breakdown:   # Phase 6: show WHICH wishes were honoured (the HOD can see whose preferences were met)
        if row["rule"] == "preferred_slots":
            stats = pref_stats(problem, st)
            row["detail"] = stats["text"]
            row["teachers"] = stats["teachers"]
    return round(score, 2), breakdown


def pref_stats(problem: Problem, st: State) -> dict[str, Any]:
    """How well the teachers' SOFT choices were met (the hard ones - not-available slots, daily maximum - can never be broken).
    Three pooled measures, each `[honoured, possible]`:
      stars  periods taught in a starred slot, out of min(periods taught, starred slots)
      first  days with a period-1 lecture, out of the days a first-lecture lover teaches
      last   days WITHOUT a last-period lecture, out of the days a last-period avoider teaches
    The rule's satisfaction is the average of the measures that apply (1.0 when nobody set any soft choice)."""
    last = problem.periods_per_day
    stars, first, lasts = [0, 0], [0, 0], [0, 0]
    teachers: list[dict[str, Any]] = []
    for t in sorted(set(problem.teacher_preferred) | problem.prefer_first | problem.avoid_last, key=lambda x: problem.teacher_name.get(x, "")):
        occ = st.teacher_occ[t]
        everything = occ | problem.teacher_fixed.get(t, set())
        days = {d for d, _p in everything}
        row: dict[str, Any] = {"teacher_id": str(t), "name": problem.teacher_name.get(t, "A teacher"), "stars": None, "first": None, "last": None}
        wanted = problem.teacher_preferred.get(t)
        if wanted and occ:
            a, b = len(occ & wanted), min(len(occ), len(wanted))
            stars[0] += a
            stars[1] += b
            row["stars"] = [a, b]
        if t in problem.prefer_first and days:
            a = sum(1 for d in days if (d, 1) in everything)
            first[0] += a
            first[1] += len(days)
            row["first"] = [a, len(days)]
        if t in problem.avoid_last and days:
            a = sum(1 for d in days if (d, last) not in everything)
            lasts[0] += a
            lasts[1] += len(days)
            row["last"] = [a, len(days)]
        if row["stars"] or row["first"] or row["last"]:
            teachers.append(row)
    ratios = [a / b for a, b in (stars, first, lasts) if b]
    parts = []
    if stars[1]:
        parts.append(f"starred periods honoured: {stars[0]} of {stars[1]}")
    if first[1]:
        parts.append(f"first lecture given: {first[0]} of {first[1]} days")
    if lasts[1]:
        parts.append(f"last period avoided: {lasts[0]} of {lasts[1]} days")
    return {
        "satisfaction": sum(ratios) / len(ratios) if ratios else 1.0,
        "text": ("; ".join(parts).capitalize() + ".") if parts else "No teacher has set soft preferences.",
        "stars": stars, "first": first, "last": lasts, "teachers": teachers,
    }


def _satisfactions(problem: Problem, st: State) -> dict[str, float]:
    units = {u.uid: u for u in problem.units}
    # same subject twice a day + spread
    viol = possible = 0
    spread_scores: list[float] = []
    for u in problem.units:
        if u.is_lab or u.needed < 2:
            continue
        days = st.unit_days[u.uid]
        placed = sum(days.values())
        if placed:
            viol += sum(max(0, n - 1) for n in days.values())
            possible += max(1, placed - 1)
            avail_days = len({d for d, _p in domain_of(problem, u)})
            spread_scores.append(len([d for d, n in days.items() if n > 0]) / max(1, min(placed, avail_days)))
    twice = 1 - (viol / possible if possible else 0)
    spread = sum(spread_scores) / len(spread_scores) if spread_scores else 1.0

    def gap_ratio(occ_by_owner: dict[Any, set[Cell]]) -> float:
        idle = busy = 0
        for cells in occ_by_owner.values():
            per_day: dict[int, list[int]] = defaultdict(list)
            for d, p in cells:
                per_day[d].append(p)
            for ps in per_day.values():
                ps.sort()
                busy += len(ps)
                if len(ps) > 1:
                    idle += ps[-1] - ps[0] + 1 - len(ps)
        return max(0.0, 1 - idle / busy) if busy else 1.0

    class_all = {cid: st.class_occ[cid] | problem.class_fixed.get(cid, set()) for cid in problem.classes}
    teacher_all = {t: st.teacher_occ[t] | problem.teacher_fixed.get(t, set()) for t in set(st.teacher_occ) | set(problem.teacher_fixed)}
    class_gaps = gap_ratio(class_all)
    teacher_gaps = gap_ratio(teacher_all)

    imbalances: list[float] = []
    packed: list[float] = []
    for t, cells in teacher_all.items():
        if not cells:
            continue
        loads = Counter(d for d, _p in cells)
        if len(loads) >= 2:
            imbalances.append((max(loads.values()) - min(loads.values())) / max(loads.values()))
        if t in problem.visiting_days:
            total = len(cells)
            ideal_days = -(-total // problem.periods_per_day)
            day_eff = ideal_days / len(loads)
            per_day_idle = 0
            for d in loads:
                ps = sorted(p for dd, p in cells if dd == d)
                per_day_idle += ps[-1] - ps[0] + 1 - len(ps)
            tight = max(0.0, 1 - per_day_idle / total)
            packed.append((day_eff + tight) / 2)
    balance = 1 - (sum(imbalances) / len(imbalances) if imbalances else 0)
    visiting_packed = sum(packed) / len(packed) if packed else 1.0

    labs = [k for k in st.assign if units[k[0]].length == 2]
    bad_lunch = sum(1 for k in labs if st.assign[k][1] in problem.lunch_between)
    lab_lunch = 1 - (bad_lunch / len(labs) if labs else 0)
    return {
        "twice_a_day": twice, "class_gaps": class_gaps, "teacher_gaps": teacher_gaps,
        "balance_load": balance, "spread": spread, "lab_lunch": lab_lunch, "visiting_packed": visiting_packed,
        "preferred_slots": pref_stats(problem, st)["satisfaction"] if problem.has_soft_prefs else 1.0,
    }


def unplaced_report(problem: Problem, st: State) -> list[dict[str, Any]]:
    """For every lecture that could not be placed: what, where, and exactly why (never a vague failure)."""
    out: list[dict[str, Any]] = []
    for uid, missing in sorted(st.unplaced.items()):
        if missing <= 0:
            continue
        unit = problem.units[uid]
        reasons: Counter = Counter()
        for cell in domain_of(problem, unit):
            span = st.cells_of(unit, cell)
            if any(c in st.class_occ[unit.class_id] for c in span):
                reasons["class"] += 1
                continue
            blamed = False
            for t in unit.teachers:
                if any(c in st.teacher_occ[t] or c in problem.teacher_fixed.get(t, ()) for c in span):
                    reasons[problem.teacher_name.get(t, "a teacher")] += 1
                    blamed = True
                    break
                cap = problem.visiting_max.get(t)
                if cap is not None and st.teacher_hours[t] + problem.teacher_fixed_hours.get(t, 0) + unit.length > cap:
                    reasons[f"{problem.teacher_name.get(t, 'a teacher')} (weekly hours limit)"] += 1
                    blamed = True
                    break
                per_day = problem.teacher_max_day.get(t)
                if per_day is not None and st.day_load(t, cell[0]) + unit.length > per_day:
                    reasons[f"{problem.teacher_name.get(t, 'a teacher')} (limit of {per_day} lectures a day)"] += 1
                    blamed = True
                    break
            if not blamed:
                reasons["free?!"] += 1
        label = problem.labels[unit.class_id]
        dead = [problem.teacher_name.get(t, "a teacher") for t in unit.teachers if t in problem.inactive]
        if dead:
            why = f"{', '.join(dead)} is deactivated"
        elif not domain_of(problem, unit):
            blocked_by = [problem.teacher_name.get(t, "a teacher") for t in unit.teachers if t in problem.teacher_unavailable]
            if blocked_by and domain_of(problem, unit, ignore_prefs=True):
                why = f"every possible period is ruled out by the not-available slots of {', '.join(blocked_by)}"
            else:
                why = "it has no possible period at all (day-wise schedule / visiting days)"
        else:
            top = ", ".join(
                f"the class is busy in {n} of them" if name == "class"
                else (f"{name} blocks {n} of them" if "(" in name else f"{name} is busy in {n} of them")
                for name, n in reasons.most_common(3)
            )
            ruled_out = len(domain_of(problem, unit, ignore_prefs=True)) - len(domain_of(problem, unit))
            names = [problem.teacher_name.get(t, "a teacher") for t in unit.teachers if t in problem.teacher_unavailable]
            pref_note = f"; another {ruled_out} were ruled out by the not-available slots of {', '.join(names)}" if ruled_out > 0 and names else ""
            why = f"of its {len(domain_of(problem, unit))} possible periods: {top}{pref_note}"
        out.append({
            "class_label": label, "unit": unit.name, "missing": missing, "needed": unit.needed,
            "message": f"'{unit.name}' ({label}): {missing} of {unit.needed} weekly {'blocks' if unit.is_lab else 'lectures'} could not be placed - {why}.",
        })
    return out


# ---------------------------------------------------------------------------------------------------- solve
@dataclass
class Solution:
    state: State
    score: float
    breakdown: list[dict[str, Any]]
    placed: int
    required: int
    unplaced: list[dict[str, Any]]

    def signature(self) -> frozenset:
        return frozenset((k, v) for k, v in self.state.assign.items())


def solve(
    problem: Problem,
    weights: dict[str, int],
    *,
    attempts: int = 12,
    time_limit: float = 90.0,
    seed: int = 1,
    keep: int = 3,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Solution]:
    is_cancelled = is_cancelled or (lambda: False)
    w = {k: float(v) for k, v in clean_weights(weights).items()}  # the heuristic reads the same 0-100 scale
    t0 = monotonic()
    required = sum(u.needed for u in problem.units)
    pool: list[Solution] = []
    a = 0
    total = attempts
    while a < total:
        if is_cancelled():
            raise Cancelled()
        if monotonic() - t0 > time_limit and pool:
            break
        if progress:
            progress(int(5 + 85 * a / total), f"Trying combination {a + 1} of {total}...")
        st = _attempt(problem, w, random.Random(seed * 7919 + a), t0 + time_limit, is_cancelled)
        score, breakdown = score_solution(problem, st, clean_weights(weights))
        placed = required - sum(max(0, n) for n in st.unplaced.values())
        pool.append(Solution(st, score, breakdown, placed, required, unplaced_report(problem, st)))
        a += 1
        # Tight setups (a heavily loaded teacher with a daily maximum and a few "not available" slots) can leave ONE block unplaced in
        # every restart of the first batch. When the best answer is only 1-3 lectures short, the problem is probably solvable, so keep
        # trying more restarts (the attempts are quick) - but never for a hopeless problem and never beyond 4x the usual attempts.
        if a == total and total < attempts * EXTRA_FACTOR and monotonic() - t0 < min(time_limit, EXTRA_SECONDS):
            short = required - max(s.placed for s in pool)
            if 0 < short <= EXTRA_MAX_MISSING:
                total = min(attempts * EXTRA_FACTOR, total + attempts)
    pool.sort(key=lambda s: (-s.placed, -s.score))
    chosen: list[Solution] = []
    for sol in pool:
        if len(chosen) >= keep:
            break
        if all(_difference(sol, c) >= 0.12 for c in chosen):
            chosen.append(sol)
    for sol in pool:  # make the count even if everything converged to near-identical answers
        if len(chosen) >= min(keep, len(pool)):
            break
        if sol not in chosen:
            chosen.append(sol)
    if progress:
        progress(92, "Checking the best solutions with the clash engine...")
    return chosen


def _difference(a: Solution, b: Solution) -> float:
    sa, sb = a.signature(), b.signature()
    union = len(sa | sb)
    return 1 - (len(sa & sb) / union if union else 1)


# ---------------------------------------------------------------------------------------------------- the final word
def solution_entries(problem: Problem, st: State, draft_ids: dict[uuid.UUID, uuid.UUID]) -> list[Entry]:
    """Turns a solution into Phase 2 draft entries (one row per subject; group options share a parallel_key)."""
    locked_cells = {(problem.units[uid].class_id, cell) for uid, cell in problem.locked}
    entries: list[Entry] = []
    for (uid, _i), cell in sorted(st.assign.items()):
        unit = problem.units[uid]
        parallel = uuid.uuid4() if len(unit.subject_ids) > 1 else None
        locked = (unit.class_id, cell) in locked_cells
        for sid in unit.subject_ids:
            entries.append(Entry(
                uuid.uuid4(), draft_ids[unit.class_id], unit.class_id, sid, cell[0], cell[1], unit.length, parallel, locked,
            ))
    return entries


def verify_solution(base_ctx: Ctx, problem: Problem, st: State) -> dict[str, Any]:
    """Re-checks the WHOLE solution with the Phase 2 engine (the single source of truth for validity)."""
    draft_ids = {cid: uuid.uuid4() for cid in problem.classes}
    entries = solution_entries(problem, st, draft_ids)
    ctx = replace(
        base_ctx,
        drafts={draft_ids[cid]: DraftInfo(draft_ids[cid], cid, "candidate", True) for cid in problem.classes},
        entries=entries,
    )
    clashes: list[str] = []
    per_class: dict[str, Any] = {}
    for cid in problem.classes:
        a = analyze_draft(ctx, draft_ids[cid])
        comp = completeness(ctx, cid, draft_ids[cid])
        clashes.extend(c["message"] for c in a["clashes"])
        per_class[str(cid)] = {
            "label": problem.labels[cid], "percent": comp["percent"],
            "placed": comp["placed"], "required": comp["required"], "missing": comp["missing"],
        }
    return {"clashes": clashes, "classes": per_class}
