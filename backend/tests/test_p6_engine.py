"""Phase 6 pure-logic tests (no database, no server): the preference rules, the HARD rules in the manual grid (not-available slots,
daily maximum), the generator (hard rules never broken, the new soft rule scored and visible), the extra-lecture planner, the
substitution ranking (a not-available teacher is the last resort, clearly labelled), the preference window helpers.
Reuses the Phase 2/4 made-up college (6 periods a day, lunch 13:10-13:40)."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import generator as gen
from app.services import hours as hr
from app.services import prefs as pf
from app.services import schedule as sch
from app.services import substitution as subsvc
from app.services.changes import DayChanges, Sub
from app.services.prefs import TeacherPref

from .test_p2_engine import FRI, MON, SAT, THU, TUE, WED, World, codes
from .test_p4_engine import Fake

U = uuid.uuid4
MONDAY = date(2026, 10, 12)


def pref(w: World, who: str, *, no=(), star=(), first=False, last=False, max_day=None) -> TeacherPref:
    return TeacherPref(w.t[who], frozenset(no), frozenset(star), first, last, max_day)


def with_prefs(w: World, *prefs: TeacherPref) -> sch.Ctx:
    return replace(w.ctx(), prefs={p.teacher_id: p for p in prefs})


def check(w: World, ctx: sch.Ctx, subject, day: int, start: int, draft=None, **kw) -> sch.Result:
    names = [subject] if isinstance(subject, str) else subject
    d = draft or w.dA
    sem = w.A if d in (w.dA, w.dA2) else w.B
    return sch.check_unit(ctx, draft_id=d, sem_id=sem, subject_ids=[w.s[n].id for n in names], day=day, start=start, **kw)


@pytest.fixture()
def w() -> World:
    world = World()
    world.assign[world.s["gone"].id] = (world.t["phys"], None)   # a real teacher for the subject that normally has a deactivated one
    return world


# ====================================================================================================
# 1) the preference rules themselves
# ====================================================================================================
def test_build_prefs_merges_the_master_row_and_the_slot_rows():
    a, b, c = U(), U(), U()
    out = pf.build_prefs(
        [{"teacher_id": a, "prefer_first": True, "avoid_last": False, "max_per_day": 4}],
        [{"teacher_id": a, "day": 1, "period": 2, "kind": "unavailable"}, {"teacher_id": a, "day": 2, "period": 1, "kind": "preferred"},
         {"teacher_id": b, "day": 3, "period": 3, "kind": "unavailable"}],     # b has slots but no master row
    )
    assert out[a].unavailable == {(1, 2)} and out[a].preferred == {(2, 1)} and out[a].prefer_first and out[a].max_per_day == 4
    assert out[b].unavailable == {(3, 3)} and out[b].max_per_day is None and not out[b].prefer_first
    assert c not in out
    assert out[a].counts() == {"unavailable": 1, "preferred": 1} and not out[a].is_blank


def test_a_blank_preference_is_blank_and_has_a_plain_summary():
    blank = TeacherPref(U())
    assert blank.is_blank and pf.summary_text(blank) == "No preferences set" and pf.summary_text(None) == "No preferences set"
    text = pf.summary_text(TeacherPref(U(), frozenset({(1, 1), (1, 2)}), frozenset({(2, 3)}), True, True, 4))
    assert text == "2 not available · 1 preferred · max 4 a day · likes the first lecture · avoids the last lecture"


def test_periods_text_reads_naturally():
    assert pf.periods_text([3]) == "period 3" and pf.periods_text([3, 4]) == "periods 3-4" and pf.periods_text([1, 3]) == "periods 1, 3"


def save(slots: dict, **over):
    args = dict(working_days=5, periods=6, visiting_days=None, limit=3, max_per_day=None)
    args.update(over)
    pf.check_save(slots, **args)


def test_saving_more_not_available_slots_than_the_school_allows_is_refused_with_a_clear_message():
    save({(1, 1): "unavailable", (1, 2): "unavailable", (1, 3): "unavailable"})            # exactly the limit: fine
    with pytest.raises(pf.PrefProblem) as err:
        save({(1, 1): "unavailable", (1, 2): "unavailable", (1, 3): "unavailable", (2, 1): "unavailable"})
    assert err.value.code == "TOO_MANY_UNAVAILABLE" and err.value.extra == {"limit": 3, "marked": 4}
    assert "at most 3" in err.value.message and "you marked 4" in err.value.message and "change 1 of them back to Available" in err.value.message
    save({(1, 1): "preferred", (1, 2): "preferred", (1, 3): "preferred", (2, 1): "preferred", (2, 2): "unavailable"})   # stars are not limited


def test_saving_rejects_nonsense_slots_visiting_off_days_and_a_bad_daily_maximum():
    with pytest.raises(pf.PrefProblem, match="not a working day"):
        save({(6, 1): "preferred"})                                                       # a 5-day college
    with pytest.raises(pf.PrefProblem, match="only has 6 periods"):
        save({(1, 7): "preferred"})
    with pytest.raises(pf.PrefProblem, match="Each slot"):
        save({(1, 1): "maybe"})
    with pytest.raises(pf.PrefProblem) as err:
        save({(4, 1): "unavailable"}, visiting_days=frozenset({1, 2, 3}))
    assert err.value.code == "VISITING_DAY" and "Monday, Tuesday, Wednesday" in err.value.message
    save({(2, 1): "unavailable"}, visiting_days=frozenset({1, 2, 3}))                     # on a visiting day: fine
    for bad in (0, 7, 99):
        with pytest.raises(pf.PrefProblem) as err2:
            save({}, max_per_day=bad)
        assert err2.value.code == "BAD_MAX"
    save({}, max_per_day=4)


def test_the_reminder_time_is_a_day_before_but_never_before_the_halfway_point_of_a_short_window():
    opened = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    assert pf.reminder_at(opened, opened + timedelta(days=5), 24) == opened + timedelta(days=4)
    assert pf.reminder_at(opened, opened + timedelta(hours=10), 24) == opened + timedelta(hours=5)       # a short window: half-way
    assert pf.window_is_open("open", opened + timedelta(hours=1), opened)
    assert not pf.window_is_open("open", opened, opened)            # the deadline moment itself is over
    assert not pf.window_is_open("closed", opened + timedelta(hours=1), opened)


# ====================================================================================================
# 2) the manual grid: not-available slots and the daily maximum are HARD
# ====================================================================================================
def test_a_not_available_slot_blocks_the_drop_and_names_the_reason(w):
    ctx = with_prefs(w, pref(w, "maths", no={(MON, 1)}))
    r = check(w, ctx, "maths", MON, 1)
    assert codes(r) == {"TEACHER_UNAVAILABLE"}
    assert "Maths Sir marked Monday period 1 as not available" in r.hard[0].message
    assert check(w, ctx, "maths", MON, 2).ok and check(w, ctx, "maths", TUE, 1).ok          # only that one cell
    assert check(w, with_prefs(w), "maths", MON, 1).ok                                       # without the preference it is fine


def test_a_lab_block_is_blocked_by_either_of_its_two_periods(w):
    ctx = with_prefs(w, pref(w, "lab", no={(TUE, 2)}))
    assert "TEACHER_UNAVAILABLE" in codes(check(w, ctx, "chem_lab", TUE, 1))                 # P1+P2 touches P2
    assert "TEACHER_UNAVAILABLE" in codes(check(w, ctx, "chem_lab", TUE, 2))                 # P2+P3 touches P2
    assert check(w, ctx, "chem_lab", TUE, 3).ok                                              # P3+P4 is clear
    r = check(w, with_prefs(w, pref(w, "lab", no={(TUE, 1), (TUE, 2)})), "chem_lab", TUE, 1)
    assert "periods 1-2" in r.hard[0].message


def test_the_co_teachers_mark_blocks_too(w):
    ctx = with_prefs(w, pref(w, "co", no={(WED, 3)}))
    r = check(w, ctx, "co", WED, 3)                                                           # Co Subject: Maths Sir + Co Teacher
    assert codes(r) == {"TEACHER_UNAVAILABLE"} and "Co Teacher" in r.hard[0].message


def test_one_elective_option_teachers_mark_blocks_the_whole_parallel_group(w):
    ctx = with_prefs(w, pref(w, "cyber", no={(THU, 2)}))
    r = check(w, ctx, ["ai", "cyber"], THU, 2)
    assert codes(r) == {"TEACHER_UNAVAILABLE"} and "Cyber Faculty" in r.hard[0].message
    assert check(w, ctx, ["ai", "cyber"], THU, 3).ok


def test_the_daily_maximum_counts_everything_the_teacher_holds_that_day_across_classes(w):
    w.put(w.dA, "maths", MON, 1)                      # Maths Sir: class A Monday P1
    w.put(w.dB, "b_maths", MON, 2)                    # ... and class B Monday P2 (another class's PRIMARY draft counts)
    ctx = with_prefs(w, pref(w, "maths", max_day=2))
    r = check(w, ctx, "maths", MON, 3)
    assert codes(r) == {"TEACHER_MAX_DAY"} and "at most 2 lecture(s) a day" in r.hard[0].message and "make 3 on Monday" in r.hard[0].message
    assert check(w, ctx, "maths", TUE, 3).ok          # another day is empty
    assert check(w, with_prefs(w, pref(w, "maths", max_day=3)), "maths", MON, 3).ok


def test_the_daily_maximum_counts_locked_slots_and_lab_blocks_as_two(w):
    w.lock(w.A, WED, 5, "PE", "pe")                   # PE Sir: a locked double block on Wednesday P5-P6 = 2 lectures
    ctx = with_prefs(w, pref(w, "pe", max_day=2))
    assert codes(check(w, ctx, "maths", WED, 1, ), hard=True) == set()     # Maths Sir is not PE Sir
    w.assign[w.s["phys"].id] = (w.t["pe"], None)      # PE Sir also teaches Physics
    ctx = with_prefs(w, pref(w, "pe", max_day=2))
    assert codes(check(w, ctx, "phys", WED, 1)) == {"TEACHER_MAX_DAY"}      # 2 locked periods + 1 more = 3 > 2
    ctx = with_prefs(w, pref(w, "lab", max_day=1))
    assert codes(check(w, ctx, "chem_lab", FRI, 1)) == {"TEACHER_MAX_DAY"}  # a 2-period lab block alone is already 2 lectures


def test_moving_a_placement_does_not_count_it_twice(w):
    e = w.put(w.dA, "maths", MON, 1)
    w.put(w.dA, "maths", MON, 2)
    ctx = with_prefs(w, pref(w, "maths", max_day=2))
    assert codes(check(w, ctx, "maths", MON, 3)) == {"TEACHER_MAX_DAY"}               # a third one would be too many
    assert check(w, ctx, "maths", MON, 3, ignore={e.key}).ok                          # ... but MOVING one of the two is fine


def test_the_conflict_report_flags_a_saved_lecture_on_a_not_available_slot(w):
    w.put(w.dA, "maths", MON, 1)
    ctx = with_prefs(w, pref(w, "maths", no={(MON, 1)}))
    report = sch.analyze_draft(ctx, w.dA)
    assert any(c["code"] == "TEACHER_UNAVAILABLE" and c["day"] == MON and c["start_period"] == 1 for c in report["clashes"])
    assert not sch.analyze_draft(with_prefs(w), w.dA)["clashes"]


def test_the_options_map_colours_a_not_available_cell_blocked(w):
    ctx = with_prefs(w, pref(w, "maths", no={(WED, 4)}))
    cells = sch.options_map(ctx, draft_id=w.dA, sem_id=w.A, subject_ids=[w.s["maths"].id])
    assert cells[f"{WED}:4"]["state"] == "blocked" and "not available" in cells[f"{WED}:4"]["hard"][0]
    assert cells[f"{WED}:3"]["state"] in ("ok", "warn")


def test_avoid_last_is_only_a_warning_and_a_star_is_good_news(w):
    ctx = with_prefs(w, pref(w, "maths", last=True, star={(TUE, 2)}), pref(w, "phys", first=True))
    r = check(w, ctx, "maths", MON, 6)
    assert r.ok and "PREF_AVOID_LAST" in codes(r, hard=False)                    # allowed, but warned
    assert "PREF_AVOID_LAST" not in codes(check(w, ctx, "maths", MON, 5), hard=False)
    assert any("marked this slot as preferred" in n for n in check(w, ctx, "maths", TUE, 2).notes)
    assert any("likes to teach the first lecture" in n for n in check(w, ctx, "phys", WED, 1).notes)


def test_visiting_days_stay_a_separate_hard_rule(w):
    ctx = with_prefs(w, pref(w, "visit", star={(THU, 1)}))                       # a star on a day the visitor does not come
    r = check(w, ctx, "visit", THU, 1)
    assert codes(r) == {"VISITING_DAY"}                                           # the star does not override the hard rule


# ====================================================================================================
# 3) the generator: hard rules never broken, the soft rule scored and visible
# ====================================================================================================
def solve_best(w: World, ctx: sch.Ctx, weights=None, *, seed=1, attempts=4):
    problem = gen.build_problem(ctx)
    sols = gen.solve(problem, gen.clean_weights(weights), attempts=attempts, time_limit=20, seed=seed)
    return problem, sols[0]


def teacher_cells(problem: gen.Problem, sol: gen.Solution, tid) -> list:
    out = []
    for (uid, _i), cell in sol.state.assign.items():
        unit = problem.units[uid]
        if tid in unit.teachers:
            out.extend(sol.state.cells_of(unit, cell))
    return out


def test_the_new_soft_rule_is_in_the_registry_with_a_label_and_a_default_weight():
    assert "preferred_slots" in gen.RULES and gen.RULES["preferred_slots"][1] == 60
    assert gen.clean_weights({})["preferred_slots"] == 60
    assert gen.clean_weights({"preferred_slots": 100})["preferred_slots"] == 100


def test_not_available_cells_leave_the_domain_and_a_pin_there_is_refused(w):
    no = {(MON, 1), (MON, 2), (TUE, 3)}
    problem = gen.build_problem(with_prefs(w, pref(w, "maths", no=no)))
    maths = next(u for u in problem.units if u.name == "Maths")
    assert not (set(gen.domain_of(problem, maths)) & no)
    assert set(gen.domain_of(problem, maths, ignore_prefs=True)) >= no                 # ... they WERE possible before
    lab = next(u for u in problem.units if u.name == "Chem Lab")
    other = gen.build_problem(with_prefs(w, pref(w, "lab", no={(WED, 2)})))
    lab2 = next(u for u in other.units if u.name == "Chem Lab")
    assert (WED, 1) not in gen.domain_of(other, lab2) and (WED, 2) not in gen.domain_of(other, lab2) and (WED, 3) in gen.domain_of(other, lab2)
    assert (WED, 1) in gen.domain_of(problem, lab)                                      # others are unaffected


def test_a_full_solve_never_uses_a_not_available_slot_and_the_phase2_engine_agrees(w):
    w.lock(w.A, MON, 5, "PE", "pe")
    no = {(d, p) for d in (MON, TUE) for p in (1, 2, 3)} | {(WED, 1), (THU, 6)}
    ctx = with_prefs(w, pref(w, "maths", no=no), pref(w, "phys", no={(FRI, 1), (FRI, 2)}))
    problem, sol = solve_best(w, ctx)
    assert sol.placed == sol.required, sol.unplaced
    assert not (set(teacher_cells(problem, sol, w.t["maths"])) & no)
    assert not (set(teacher_cells(problem, sol, w.t["phys"])) & {(FRI, 1), (FRI, 2)})
    check_all = gen.verify_solution(ctx, problem, sol.state)
    assert check_all["clashes"] == []                                                   # zero hard-rule violations


def test_the_daily_maximum_is_never_crossed_by_the_generator(w):
    ctx = with_prefs(w, pref(w, "maths", max_day=2))
    problem, sol = solve_best(w, ctx, seed=2)
    per_day: dict = {}
    for d, _p in teacher_cells(problem, sol, w.t["maths"]):
        per_day[d] = per_day.get(d, 0) + 1
    assert per_day and max(per_day.values()) <= 2
    assert gen.verify_solution(ctx, problem, sol.state)["clashes"] == []


def test_the_daily_maximum_counts_the_teachers_locked_session(w):
    w.lock(w.A, MON, 5, "PE", "pe")                      # PE Sir has 2 locked periods on Monday
    w.assign[w.s["phys"].id] = (w.t["pe"], None)
    ctx = with_prefs(w, pref(w, "pe", max_day=3))
    problem, sol = solve_best(w, ctx, seed=4)
    monday = [c for c in teacher_cells(problem, sol, w.t["pe"]) if c[0] == MON]
    assert len(monday) + 2 <= 3                          # at most ONE generated period on Monday next to the locked double block


def test_a_timetable_that_the_preferences_make_impossible_is_explained_before_solving(w):
    # Maths Sir teaches Maths (3) + Co Subject (2) + B Maths (3) = 8 periods a week; one lecture a day (5 days) cannot hold them
    problem = gen.build_problem(with_prefs(w, pref(w, "maths", max_day=1)))
    findings = gen.precheck(problem)
    f = next(x for x in findings if x["kind"] == "teacher_preferences")
    assert "Maths Sir" in f["message"] and "8 teaching periods" in f["message"] and "limit of 1 lecture(s) a day" in f["message"] and "room for only 5" in f["message"]
    clean = gen.precheck(gen.build_problem(with_prefs(w, pref(w, "maths", max_day=3))))
    assert not [x for x in clean if x["kind"] == "teacher_preferences"]               # 3 a day = 15: plenty


def test_marked_slots_that_leave_a_unit_no_period_are_named_in_the_precheck_and_the_unplaced_report(w):
    keep = {(MON, 1), (MON, 2)}
    no = {(d, p) for d in range(1, 6) for p in range(1, 7)} - keep               # Maths Sir only has two free cells in the whole week
    ctx = with_prefs(w, pref(w, "maths", no=no))
    problem = gen.build_problem(ctx)
    findings = gen.precheck(problem)
    assert any(f["kind"] == "teacher_preferences" and "Maths Sir" in f["message"] for f in findings)
    sols = gen.solve(problem, gen.clean_weights({}), attempts=2, time_limit=20, seed=1)
    msgs = " ".join(u["message"] for u in sols[0].unplaced)
    assert "not-available" in msgs and "Maths Sir" in msgs
    # and still: whatever WAS placed respects the marks
    assert not (set(teacher_cells(problem, sols[0], w.t["maths"])) & no)


def test_stars_are_honoured_and_scored_when_the_rule_has_weight(w):
    stars = {(WED, 2), (WED, 3), (WED, 4)}
    ctx = with_prefs(w, pref(w, "phys", star=stars))
    only = {k: 0 for k in gen.RULES}
    only["preferred_slots"] = 100
    problem, sol = solve_best(w, ctx, only, seed=3)
    got = set(teacher_cells(problem, sol, w.t["phys"])) & stars
    assert len(got) == 3                                                                 # Physics Madam's 3 lectures all landed on her stars
    row = next(r for r in sol.breakdown if r["rule"] == "preferred_slots")
    assert row["satisfaction"] == 1.0 and row["points_lost"] == 0
    assert "Starred periods honoured: 3 of" in row["detail"]
    mine = next(t for t in row["teachers"] if t["name"] == "Physics Madam")
    assert mine["stars"][0] == 3 and mine["stars"][1] >= 3


def test_the_breakdown_shows_the_rule_even_when_nobody_set_a_preference(w):
    problem, sol = solve_best(w, with_prefs(w))
    row = next(r for r in sol.breakdown if r["rule"] == "preferred_slots")
    assert row["satisfaction"] == 1.0 and row["detail"] == "No teacher has set soft preferences." and row["teachers"] == []
    assert {r["rule"] for r in sol.breakdown} == set(gen.RULES)


def test_avoiding_the_last_period_and_liking_the_first_are_scored_per_day(w):
    ctx = with_prefs(w, pref(w, "phys", last=True, first=True))
    only = {k: 0 for k in gen.RULES}
    only["preferred_slots"] = 100
    problem, sol = solve_best(w, ctx, only, seed=5)
    last = problem.periods_per_day
    cells = teacher_cells(problem, sol, w.t["phys"])
    assert not [c for c in cells if c[1] == last]                                        # never the last period
    stats = gen.pref_stats(problem, sol.state)
    assert stats["last"] == [stats["last"][1], stats["last"][1]] and stats["last"][1] > 0   # every teaching day avoided it
    assert stats["first"][1] > 0 and 0 <= stats["first"][0] <= stats["first"][1]
    assert "last period avoided" in stats["text"].lower() and "first lecture given" in stats["text"].lower()


def test_the_weight_really_steers_the_stars(w):
    stars = {(TUE, 1), (TUE, 2), (TUE, 3), (TUE, 4)}
    ctx = with_prefs(w, pref(w, "maths", star=stars))
    on, off = {k: 0 for k in gen.RULES}, {k: 0 for k in gen.RULES}
    on["preferred_slots"] = 100
    problem, sol_on = solve_best(w, ctx, on, seed=6)
    hit_on = len(set(teacher_cells(problem, sol_on, w.t["maths"])) & stars)
    assert hit_on >= 3                                                                    # Maths Sir has 8 periods but only 4 starred cells: most stars used
    # the score of any answer is judged by the same yardstick: more stars = a higher preferred_slots satisfaction
    problem2, sol_off = solve_best(w, ctx, off, seed=6)
    s_on = next(r for r in gen.score_solution(problem, sol_on.state, gen.clean_weights(on))[1] if r["rule"] == "preferred_slots")["satisfaction"]
    s_off = next(r for r in gen.score_solution(problem2, sol_off.state, gen.clean_weights(on))[1] if r["rule"] == "preferred_slots")["satisfaction"]
    assert s_on >= s_off


# ====================================================================================================
# 4) the extra-lecture planner respects the same hard rules
# ====================================================================================================
def planner_for(f: Fake, subject: str, *prefs: TeacherPref) -> hr.Planner:
    data = f.data()
    data.prefs = {p.teacher_id: p for p in prefs}
    sid = f.w.s[subject].id
    return hr.Planner(data, hr.unit_of(data, sid), hr.teachers_of_subjects(data, {}))


def test_the_planner_refuses_a_not_available_slot_and_a_slot_over_the_daily_maximum():
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)                                    # Maths Sir has Monday P1 in the published timetable
    d = date(2026, 10, 19)                                          # a later Monday inside the fake semester
    plain = planner_for(f, "maths")
    assert plain.coded(d, 3) == []                                  # nothing in the way
    blocked = planner_for(f, "maths", pref(w, "maths", no={(MON, 3)}))
    out = blocked.coded(d, 3)
    assert [c for c, _m in out] == ["pref_unavailable"] and "marked Monday period 3 as not available" in out[0][1]
    assert blocked.coded(d, 4) == []
    capped = planner_for(f, "maths", pref(w, "maths", max_day=1))
    assert [c for c, _m in capped.coded(d, 3)] == ["pref_max_day"]  # P1 is already a lecture that weekday; one more passes the cap of 1
    assert capped.coded(date(2026, 10, 20), 3) == []                # a Tuesday: nothing yet, one lecture is fine
    assert "pref_unavailable" in hr.REJECT_WORDS and "pref_max_day" in hr.REJECT_WORDS


def test_suggested_extra_slots_never_fall_on_a_not_available_period():
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)
    no = {(d, p) for d in (MON, TUE, WED) for p in range(1, 7)}      # Maths Sir is unavailable Mon-Wed entirely
    pl = planner_for(f, "maths", pref(w, "maths", no=no))
    slots = pl.suggest(4, date(2026, 10, 5))
    assert slots and all((s.day_date.isoweekday(), s.start) not in no for s in slots)


# ====================================================================================================
# 5) the substitution ranking: a not-available teacher is the last resort, clearly labelled
# ====================================================================================================
def ranked_for(f: Fake, prefs: list[TeacherPref]):
    w = f.w
    data = f.data()
    data.prefs = {p.teacher_id: p for p in prefs}
    sub = Sub(U(), None, MONDAY, 3, 1, frozenset({w.A}), frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], "open", None, None, None, None)
    change = DayChanges()
    return subsvc.rank_candidates(data, change, sub, can_teach={w.t["phys"]: {w.s["maths"].id}, w.t["b1"]: {w.s["maths"].id}}), data


def test_a_teacher_who_marked_the_period_not_available_is_ranked_last_and_labelled():
    f = Fake()
    w = f.w
    base, _ = ranked_for(f, [])
    assert base[0].teacher_id in (w.t["phys"], w.t["b1"])             # normally a qualified teacher leads
    ranked, _ = ranked_for(f, [pref(w, "phys", no={(MON, 3)})])
    last = ranked[-1]
    assert last.teacher_id == w.t["phys"] and last.marked_unavailable
    assert "Last resort: marked this period as not available" in last.reasons
    assert all(not c.marked_unavailable for c in ranked[:-1]) and ranked[0].teacher_id != w.t["phys"]
    assert last.as_dict()["marked_unavailable"] is True and ranked[0].as_dict()["marked_unavailable"] is False
    # still OFFERABLE: it is in the list, only at the bottom
    assert w.t["phys"] in [c.teacher_id for c in ranked]


def test_a_star_lifts_a_teacher_a_little_and_an_overfull_day_is_flagged():
    f = Fake()
    w = f.w
    base, _ = ranked_for(f, [])
    starred, _ = ranked_for(f, [pref(w, "b1", star={(MON, 3)})])
    b_base = next(c for c in base if c.teacher_id == w.t["b1"])
    b_star = next(c for c in starred if c.teacher_id == w.t["b1"])
    assert b_star.starred and b_star.score == b_base.score + 8 and "Marked this period as preferred" in b_star.reasons
    w.put(w.dB, "b_phys", MON, 1)                                     # b1 already teaches Monday P1 (a published lecture)
    over, _ = ranked_for(f, [pref(w, "b1", max_day=1)])
    b_over = next(c for c in over if c.teacher_id == w.t["b1"])
    assert b_over.over_daily_max and any("limit of 1 lectures a day" in r for r in b_over.reasons)
    assert not b_over.marked_unavailable


def test_without_preferences_the_ranking_is_exactly_what_phase_5_gave():
    f = Fake()
    ranked, _ = ranked_for(f, [])
    assert all(not (c.marked_unavailable or c.starred or c.over_daily_max) for c in ranked)
    assert [c.score for c in ranked] == sorted((c.score for c in ranked), reverse=True)


# ====================================================================================================
# 6) locked sessions are never moved by preferences - but the teacher and the HOD are told
# ====================================================================================================
def test_a_locked_session_on_a_not_available_slot_or_past_the_daily_maximum_is_reported_not_moved():
    me = U()
    pe = {"label": "PE Monday (combined)", "day": MON, "start_period": 5, "length": 2}
    sol = {"label": "SOL III", "day": MON, "start_period": 3, "length": 2}
    assert pf.fixed_notes(TeacherPref(me), [pe]) == []
    notes = pf.fixed_notes(TeacherPref(me, frozenset({(MON, 6)})), [pe])
    assert len(notes) == 1 and "PE Monday (combined)" in notes[0] and "Monday periods 5-6" in notes[0] and "Monday period 6" in notes[0] and "stay where they are" in notes[0]
    over = pf.fixed_notes(TeacherPref(me, max_per_day=3), [pe, sol])                  # 2 + 2 locked periods on Monday = 4 > 3
    assert len(over) == 1 and "Monday already has 4 locked periods" in over[0] and "daily maximum of 3" in over[0]
    assert pf.fixed_notes(TeacherPref(me, max_per_day=4), [pe, sol]) == []


# ====================================================================================================
# 7) the generator keeps trying when a solvable-looking problem is 1-3 lectures short
# ====================================================================================================
def test_the_solver_uses_more_restarts_only_for_a_nearly_complete_answer(w, monkeypatch):
    problem = gen.build_problem(with_prefs(w))
    calls = []
    real = gen._attempt

    def fake_attempt(problem_, weights, rng, deadline, is_cancelled):
        st = real(problem_, weights, rng, deadline, is_cancelled)
        calls.append(1)
        victim = problem_.units[0]
        if len(calls) < 9:                           # pretend the first 8 restarts all end 1 lecture short
            st.unplaced[victim.uid] = 1
        return st

    monkeypatch.setattr(gen, "_attempt", fake_attempt)
    sols = gen.solve(problem, gen.default_weights(), attempts=4, time_limit=30, seed=1)
    assert len(calls) == 12 and sols[0].placed == sols[0].required         # 4 usual + 4 more + a 3rd batch found a COMPLETE answer
    calls.clear()

    def hopeless(problem_, weights, rng, deadline, is_cancelled):
        st = real(problem_, weights, rng, deadline, is_cancelled)
        calls.append(1)
        for u in problem_.units[:5]:
            st.unplaced[u.uid] = 1                   # 5 lectures short every time: not "nearly complete"
        return st

    monkeypatch.setattr(gen, "_attempt", hopeless)
    gen.solve(problem, gen.default_weights(), attempts=4, time_limit=30, seed=1)
    assert len(calls) == 4                                                  # no wasted extra restarts for a hopeless problem


def test_a_two_period_block_blocked_by_two_different_lectures_is_placed_by_moving_both():
    """One class, one day, 6 periods:  A(P1) B(P2) _(P3) C(P4) D(P5) _(P6)  and a 2-period lab to place.
    Each lecture can only move within a small set of periods, so every span that has ONE blocker fails - except P4-P5,
    where BOTH C and D must move (C to P3, D to P6). The old one-blocker repair could not do that."""
    c, rng = U(), __import__("random").Random(1)
    teachers = [U() for _ in range(5)]
    units = [gen.Unit(0, c, (U(),), (teachers[0],), 2, 1, "LAB", True, None)] + [
        gen.Unit(i, c, (U(),), (teachers[i],), 1, 1, name, False, None) for i, name in ((1, "A"), (2, "B"), (3, "C"), (4, "D"))
    ]
    problem = gen.Problem(
        classes=[c], labels={c: "C"}, units=units, allowed={c: {(1, n) for n in range(1, 7)}}, class_fixed={}, teacher_fixed={},
        teacher_fixed_hours={}, teacher_name={}, visiting_days={}, visiting_max={}, periods_per_day=6, days_per_week=1, lunch_between=set(),
    )
    st = gen.State(problem, {k: 0.0 for k in gen.RULES}, rng)
    for uid, period in ((1, 1), (2, 2), (3, 4), (4, 5)):
        st.put((uid, 0), units[uid], (1, period))
    domains = {0: [(1, n) for n in range(1, 6)], 1: [(1, 1)], 2: [(1, 2), (1, 3)], 3: [(1, 3), (1, 4)], 4: [(1, 5), (1, 6)]}
    assert not [cell for cell in domains[0] if st.fits(units[0], cell)]                     # nowhere to put the lab as things stand
    assert gen._eject_and_place(st, units[0], (0, 0), domains) is True
    assert st.assign == {(0, 0): (1, 4), (1, 0): (1, 1), (2, 0): (1, 2), (3, 0): (1, 3), (4, 0): (1, 6)}
    cells = [x for key, cell in st.assign.items() for x in st.cells_of(units[key[0]], cell)]
    assert len(cells) == len(set(cells)) == 6                                               # nobody on top of anybody


def test_a_failed_two_blocker_repair_leaves_everything_exactly_as_it_was():
    c = U()
    teachers = [U() for _ in range(4)]
    units = [gen.Unit(0, c, (U(),), (teachers[0],), 2, 1, "LAB", True, None)] + [
        gen.Unit(i, c, (U(),), (teachers[i],), 1, 1, name, False, None) for i, name in ((1, "A"), (2, "B"), (3, "C"))
    ]
    problem = gen.Problem(
        classes=[c], labels={c: "C"}, units=units, allowed={c: {(1, n) for n in range(1, 5)}}, class_fixed={}, teacher_fixed={},
        teacher_fixed_hours={}, teacher_name={}, visiting_days={}, visiting_max={}, periods_per_day=4, days_per_week=1, lunch_between=set(),
    )
    st = gen.State(problem, {k: 0.0 for k in gen.RULES}, __import__("random").Random(2))
    for uid, period in ((1, 1), (2, 2), (3, 4)):
        st.put((uid, 0), units[uid], (1, period))
    before = dict(st.assign)
    occ_before = {k: set(v) for k, v in st.class_occ.items()}
    domains = {0: [(1, n) for n in range(1, 4)], 1: [(1, 1)], 2: [(1, 2)], 3: [(1, 4)]}     # nobody can move anywhere
    assert gen._eject_and_place(st, units[0], (0, 0), domains) is False
    assert st.assign == before and {k: set(v) for k, v in st.class_occ.items()} == occ_before   # fully rolled back
    assert st.teacher_hours[teachers[1]] == 1 and st.teacher_day[teachers[2]][1] == 1
