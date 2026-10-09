"""Phase 3: the automatic generation engine (app/services/generator.py) - pure logic, no database, no server.

Uses the same little made-up college as the Phase 2 engine tests (tests/test_p2_engine.py: 6 periods a day,
lunch 13:10-13:40 between P3 and P4, class A and class B, an elective group, labs, a visiting teacher).
The Phase 2 clash engine stays the single source of truth: every solution here is re-checked with it.
"""
from __future__ import annotations

import uuid
from datetime import time

import pytest

from app.services import generator as gen
from app.services import schedule as sch
from app.services.timing import DayRule

from .test_p2_engine import MON, TUE, WED, THU, FRI, SAT, World

U = uuid.uuid4


@pytest.fixture()
def w() -> World:
    world = World()
    # the Phase 2 world has one subject with a DEACTIVATED teacher on purpose; give it a real teacher here
    world.assign[world.s["gone"].id] = (world.t["phys"], None)
    return world


def problem_of(world: World, locked=None) -> gen.Problem:
    return gen.build_problem(world.ctx(), locked_by_class=locked)


def best(world: World, weights=None, *, seed=1, attempts=4, locked=None) -> tuple[gen.Problem, gen.Solution]:
    problem = problem_of(world, locked)
    sols = gen.solve(problem, gen.clean_weights(weights), attempts=attempts, time_limit=20, seed=seed)
    return problem, sols[0]


# ================================================================================================ the problem
def test_units_are_built_right(w):
    p = problem_of(w)
    by_name = {u.name: u for u in p.units}
    maths = by_name["Maths"]
    assert (maths.needed, maths.length, maths.is_lab) == (3, 1, False) and maths.teachers == (w.t["maths"],)
    lab = by_name["Chem Lab"]
    assert (lab.needed, lab.length, lab.is_lab) == (1, 2, True)
    group = by_name["AI Major ∥ Cyber Major"]
    assert group.needed == 3 and set(group.teachers) == {w.t["ai"], w.t["cyber"]} and len(group.subject_ids) == 2
    glab = by_name["AI Lab ∥ Cyber Lab"]
    assert glab.needed == 1 and glab.length == 2 and set(glab.teachers) == {w.t["ailab"], w.t["cyblab"]}
    assert not any(u.name == "PE" for u in p.units)                       # PE / SOL come only from fixed slots
    co = by_name["Co Subject"]
    assert set(co.teachers) == {w.t["maths"], w.t["co"]}                  # the co-teacher occupies a place too


def test_the_domain_respects_the_week_the_end_time_and_visiting_days(w):
    w.rules[(w.A, THU)] = DayRule(is_off=True)
    w.rules[(w.A, WED)] = DayRule(end_time=time(14, 40))                  # Wednesday has P1-P4 only
    p = problem_of(w)
    by_name = {u.name: u for u in p.units}
    maths_cells = set(gen.domain_of(p, by_name["Maths"]))
    assert not any(d == THU for d, _ in maths_cells) and not any(d == SAT for d, _ in maths_cells)
    assert (WED, 4) in maths_cells and (WED, 5) not in maths_cells
    lab_cells = set(gen.domain_of(p, by_name["Chem Lab"]))
    assert (WED, 4) not in lab_cells and (WED, 3) in lab_cells            # a block must END inside the day too
    visit_cells = {d for d, _ in gen.domain_of(p, by_name["Visiting Subject"])}
    assert visit_cells == {MON, TUE, WED}                                 # visiting days are a hard rule


def test_fixed_slots_block_the_class_and_the_teacher(w):
    key = w.lock(w.A, MON, 5, "PE", "pe")
    w.lock(w.B, MON, 5, "PE", "pe", key=key)                              # a combined session
    p = problem_of(w)
    assert (MON, 5) not in p.allowed[w.A] and (MON, 6) not in p.allowed[w.A]
    assert p.teacher_fixed[w.t["pe"]] == {(MON, 5), (MON, 6)} and p.teacher_fixed_hours[w.t["pe"]] == 2   # once, not twice


# ================================================================================================ a full solve
def test_a_feasible_school_is_fully_placed_and_the_phase2_engine_finds_nothing(w):
    w.lock(w.A, MON, 5, "PE", "pe")                                       # class A's PE double block (its completeness needs it)
    problem, sol = best(w)
    assert sol.placed == sol.required and not sol.unplaced
    check = gen.verify_solution(w.ctx(), problem, sol.state)
    assert check["clashes"] == []                                         # ZERO hard-rule violations, says the Phase 2 engine
    for info in check["classes"].values():
        assert info["missing"] == 0 and info["percent"] == 100


def test_solutions_are_deterministic_for_a_seed_and_distinct_between_them(w):
    p = problem_of(w)
    a = gen.solve(p, gen.default_weights(), attempts=4, time_limit=20, seed=7)
    b = gen.solve(p, gen.default_weights(), attempts=4, time_limit=20, seed=7)
    assert a[0].signature() == b[0].signature() and a[0].score == b[0].score
    assert len(a) >= 2
    assert a[0].signature() != a[1].signature()                            # the 2-3 answers really differ


def test_the_same_teacher_across_both_classes_is_never_double_booked(w):
    # Maths Sir teaches Maths (A, 3x) and B Maths (B, 3x): the solution must interleave them
    problem, sol = best(w)
    cells: list = []
    for (uid, _i), cell in sol.state.assign.items():
        unit = problem.units[uid]
        if w.t["maths"] in unit.teachers:
            cells.extend(sol.state.cells_of(unit, cell))
    assert len(cells) == len(set(cells))


def test_visiting_weekly_hours_are_never_crossed(w):
    problem, sol = best(w)
    hours = 0
    for (uid, _i), _cell in sol.state.assign.items():
        unit = problem.units[uid]
        if w.t["visit"] in unit.teachers:
            hours += unit.length
    assert hours <= 4                                                     # Visiting Vik's limit (fixed busies: none here)


def test_parallel_group_entries_share_a_key_and_a_cell(w):
    problem, sol = best(w)
    draft_ids = {cid: U() for cid in problem.classes}
    entries = gen.solution_entries(problem, sol.state, draft_ids)
    groups = [e for e in entries if e.parallel_key is not None]
    by_key: dict = {}
    for e in groups:
        by_key.setdefault(e.parallel_key, []).append(e)
    assert by_key and all(len(v) == 2 and len({(x.day, x.start) for x in v}) == 1 for v in by_key.values())


# ================================================================================================ weights steer it
def test_lab_lunch_weight_keeps_labs_away_from_lunch(w):
    weights = {k: 0 for k in gen.RULES}
    weights["lab_lunch"] = 100
    problem, sol = best(w, weights, seed=3)
    labs = [(problem.units[uid], cell) for (uid, _i), cell in sol.state.assign.items() if problem.units[uid].length == 2]
    assert labs and all(cell[1] not in problem.lunch_between for _u, cell in labs)
    assert next(r for r in sol.breakdown if r["rule"] == "lab_lunch")["satisfaction"] == 1.0


def test_spread_weight_pushes_a_subject_onto_different_days(w):
    weights = {k: 0 for k in gen.RULES}
    weights["spread"] = 100
    weights["twice_a_day"] = 100
    problem, sol = best(w, weights, seed=3)
    maths = next(u for u in problem.units if u.name == "Maths")
    days = {cell[0] for (uid, _i), cell in sol.state.assign.items() if uid == maths.uid}
    assert len(days) == 3                                                  # 3 lectures on 3 different days
    assert next(r for r in sol.breakdown if r["rule"] == "spread")["satisfaction"] == 1.0


def test_the_breakdown_shows_where_points_went(w):
    _problem, sol = best(w)
    assert {r["rule"] for r in sol.breakdown} == set(gen.RULES)
    assert all(0 <= r["satisfaction"] <= 1 and r["points_lost"] >= 0 for r in sol.breakdown)
    assert 0 <= sol.score <= 100


# ================================================================================================ impossible = clear words
def test_class_capacity_finding_names_the_numbers(w):
    for d in (MON, TUE, WED, THU):
        w.rules[(w.A, d)] = DayRule(is_off=True)                           # class A: only Friday is left (6 periods)
    p = problem_of(w)
    findings = gen.precheck(p)
    msg = next(f for f in findings if f["kind"] == "class_capacity" and f["about"] == "Class A")["message"]
    assert "21 lecture periods" in msg and "only 6 free" in msg and "Fri 6" in msg
    sols = gen.solve(p, gen.default_weights(), attempts=3, time_limit=10, seed=1)
    assert sols[0].placed < sols[0].required and sols[0].unplaced
    assert all("could not be placed" in u["message"] for u in sols[0].unplaced)


def test_teacher_window_finding_reads_like_the_plan_example(w):
    # Visiting Vik may only come Mon-Wed; cut class A to Thursday + Friday -> his subject has no cell at all
    for d in (MON, TUE, WED):
        w.rules[(w.A, d)] = DayRule(is_off=True)
    p = problem_of(w)
    findings = gen.precheck(p)
    about_vik = [f["message"] for f in findings if f["about"] == "Visiting Vik"]
    assert any("must teach 2 periods for Class A" in m and "0 free periods on their days (Mon, Tue, Wed)" in m for m in about_vik)
    no_cell = [f["message"] for f in findings if f["kind"] == "no_cell"]
    assert any("Visiting Subject" in m for m in no_cell)


def test_a_deactivated_teacher_is_named_not_hidden(w):
    w.assign[w.s["gone"].id] = (w.t["gone"], None)                         # back to the deactivated teacher
    p = problem_of(w)
    assert any("Gone Sir" in f["message"] and "deactivated" in f["message"] for f in gen.precheck(p))
    sols = gen.solve(p, gen.default_weights(), attempts=2, time_limit=10, seed=1)
    assert any("Gone Sir is deactivated" in u["message"] for u in sols[0].unplaced)


def test_the_visiting_hours_limit_is_explained(w):
    w.people[w.t["visit"]] = sch.Person(w.t["visit"], "Visiting Vik", True, True, frozenset({MON, TUE, WED}), 2)
    p = problem_of(w)   # Vik needs 2 (class A) + 2 (class B) = 4 > limit 2
    findings = gen.precheck(p)
    assert any("Visiting Vik" in f["message"] and "weekly limit of 2 hours" in f["message"] for f in findings)
    sols = gen.solve(p, gen.default_weights(), attempts=3, time_limit=10, seed=2)
    assert sols[0].required - sols[0].placed == 2
    assert any("weekly hours limit" in u["message"] for u in sols[0].unplaced)


# ================================================================================================ semi-automatic mode
def test_locked_placements_are_kept_exactly_where_they_are(w):
    p0 = problem_of(w)
    maths = next(u for u in p0.units if u.name == "Maths")
    locked_entry = sch.Entry(U(), U(), w.A, w.s["maths"].id, FRI, 6, 1, None, True)
    problem = gen.build_problem(w.ctx(), locked_by_class={w.A: [locked_entry]})
    assert problem.locked == [(maths.uid, (FRI, 6))]
    sols = gen.solve(problem, gen.default_weights(), attempts=3, time_limit=15, seed=5)
    sol = sols[0]
    assert sol.state.assign[(maths.uid, 0)] == (FRI, 6) and (maths.uid, 0) in sol.state.locked_keys
    assert sol.placed == sol.required
    entries = gen.solution_entries(problem, sol.state, {cid: U() for cid in problem.classes})
    pinned = [e for e in entries if e.is_locked]
    assert [(e.day, e.start) for e in pinned] == [(FRI, 6)]                # the pin survives into the new drafts


def test_a_pinned_cell_that_no_longer_fits_is_reported_not_dropped(w):
    w.rules[(w.A, FRI)] = DayRule(is_off=True)
    maths = next(u for u in problem_of(w).units if u.name == "Maths")
    problem = gen.build_problem(w.ctx(), locked_by_class={w.A: [sch.Entry(U(), U(), w.A, w.s["maths"].id, FRI, 6, 1, None, True)]})
    sols = gen.solve(problem, gen.default_weights(), attempts=2, time_limit=10, seed=1)
    placed_maths = sum(1 for (uid, _i) in sols[0].state.assign if uid == maths.uid)
    assert placed_maths + sum(u["missing"] for u in sols[0].unplaced if u["unit"] == "Maths") == 3


# ================================================================================================ control
def test_cancel_stops_the_run(w):
    with pytest.raises(gen.Cancelled):
        gen.solve(problem_of(w), gen.default_weights(), attempts=4, time_limit=20, seed=1, is_cancelled=lambda: True)


def test_progress_is_reported(w):
    seen: list[tuple[int, str]] = []
    gen.solve(problem_of(w), gen.default_weights(), attempts=3, time_limit=20, seed=1, progress=lambda p, m: seen.append((p, m)))
    assert seen and seen[0][0] <= seen[-1][0] <= 100 and any("combination" in m for _p, m in seen)
