"""Phase 2: the draft engine (app/services/schedule.py) - pure logic, no database, no server.

Every clash type of the master plan (Section 10), the soft rules, parallel (elective-group) placements, combined locked slots,
move / swap and the completeness counting. A tiny made-up college: 6 periods a day (10:00-16:50), lunch 13:10-13:40.
"""
from __future__ import annotations

import uuid
from datetime import time

import pytest

from app.services import schedule as sch
from app.services.grid import build_grid
from app.services.timing import DayRule

U = uuid.uuid4
MON, TUE, WED, THU, FRI, SAT = 1, 2, 3, 4, 5, 6


class World:
    """Class A (the one we edit) and class B, with a handful of subjects and teachers."""

    def __init__(self, days_per_week: int = 5) -> None:
        self.A, self.B = U(), U()
        self.dA, self.dB, self.dA2 = U(), U(), U()  # primary draft of A, primary draft of B, a second (alternative) draft of A
        self.t = {n: U() for n in ("maths", "phys", "lab", "ai", "cyber", "ailab", "cyblab", "visit", "gone", "co", "b1", "pe")}
        self.people = {
            self.t["maths"]: sch.Person(self.t["maths"], "Maths Sir", True, False, frozenset(), None),
            self.t["phys"]: sch.Person(self.t["phys"], "Physics Madam", True, False, frozenset(), None),
            self.t["lab"]: sch.Person(self.t["lab"], "Lab Sir", True, False, frozenset(), None),
            self.t["ai"]: sch.Person(self.t["ai"], "AI Faculty", True, False, frozenset(), None),
            self.t["cyber"]: sch.Person(self.t["cyber"], "Cyber Faculty", True, False, frozenset(), None),
            self.t["ailab"]: sch.Person(self.t["ailab"], "AI Lab Faculty", True, False, frozenset(), None),
            self.t["cyblab"]: sch.Person(self.t["cyblab"], "Cyber Lab Faculty", True, False, frozenset(), None),
            self.t["visit"]: sch.Person(self.t["visit"], "Visiting Vik", True, True, frozenset({MON, TUE, WED}), 4),
            self.t["gone"]: sch.Person(self.t["gone"], "Gone Sir", False, False, frozenset(), None),
            self.t["co"]: sch.Person(self.t["co"], "Co Teacher", True, False, frozenset(), None),
            self.t["b1"]: sch.Person(self.t["b1"], "B Teacher", True, False, frozenset(), None),
            self.t["pe"]: sch.Person(self.t["pe"], "PE Sir", True, False, frozenset(), None),
        }
        g, gl = U(), U()
        mk = lambda sem, name, credits, **kw: sch.Subj(U(), sem, name, name[:6], credits, kw.get("lab", False), kw.get("kind"), kw.get("group"), kw.get("gname"))  # noqa: E731
        self.s = {
            "maths": mk(self.A, "Maths", 3), "phys": mk(self.A, "Physics", 3), "chem_lab": mk(self.A, "Chem Lab", 1, lab=True),
            "ai": mk(self.A, "AI Major", 3, group=g, gname="Major 1"), "cyber": mk(self.A, "Cyber Major", 3, group=g, gname="Major 1"),
            "ai_lab": mk(self.A, "AI Lab", 1, lab=True, group=gl, gname="Major 1 LAB"), "cyber_lab": mk(self.A, "Cyber Lab", 1, lab=True, group=gl, gname="Major 1 LAB"),
            "visit": mk(self.A, "Visiting Subject", 2), "gone": mk(self.A, "Gone Subject", 2), "co": mk(self.A, "Co Subject", 2),
            "nobody": mk(self.A, "Nobody Subject", 2), "pe": mk(self.A, "PE", 1, kind="PE"),
            "b_maths": mk(self.B, "B Maths", 3), "b_phys": mk(self.B, "B Phys", 3), "b_visit": mk(self.B, "B Visit", 2),
        }
        self.g, self.gl = g, gl
        self.assign = {
            self.s["maths"].id: (self.t["maths"], None), self.s["phys"].id: (self.t["phys"], None), self.s["chem_lab"].id: (self.t["lab"], None),
            self.s["ai"].id: (self.t["ai"], None), self.s["cyber"].id: (self.t["cyber"], None),
            self.s["ai_lab"].id: (self.t["ailab"], None), self.s["cyber_lab"].id: (self.t["cyblab"], None),
            self.s["visit"].id: (self.t["visit"], None), self.s["gone"].id: (self.t["gone"], None), self.s["co"].id: (self.t["maths"], self.t["co"]),
            self.s["b_maths"].id: (self.t["maths"], None), self.s["b_phys"].id: (self.t["b1"], None), self.s["b_visit"].id: (self.t["visit"], None),
            self.s["pe"].id: (self.t["pe"], None),
        }
        self.days_per_week = days_per_week
        self.rules: dict = {}
        self.fixed: list[sch.Fixed] = []
        self.entries: list[sch.Entry] = []
        self.drafts = {
            self.dA: sch.DraftInfo(self.dA, self.A, "Plan A", True), self.dA2: sch.DraftInfo(self.dA2, self.A, "Plan B", False),
            self.dB: sch.DraftInfo(self.dB, self.B, "B main", True),
        }

    # ---- building
    def put(self, draft: uuid.UUID, subject: str, day: int, start: int, length: int | None = None, parallel: uuid.UUID | None = None):
        s = self.s[subject]
        sem = self.A if draft in (self.dA, self.dA2) else self.B
        e = sch.Entry(U(), draft, sem, s.id, day, start, length or (2 if s.is_lab else 1), parallel)
        self.entries.append(e)
        return e

    def lock(self, sem: uuid.UUID, day: int, start: int, kind: str = "SOL", teacher: str | None = None, key: uuid.UUID | None = None, length: int = 2):
        k = key or U()
        self.fixed.append(sch.Fixed(U(), k, sem, day, start, length, kind, f"{kind} session", self.t[teacher] if teacher else None, None))
        return k

    def ctx(self) -> sch.Ctx:
        grid = build_grid(time(10, 0), time(16, 50), 60, 5, time(13, 10), time(13, 40))
        return sch.Ctx(
            grid=grid, days_per_week=self.days_per_week, labels={self.A: "Class A", self.B: "Class B"}, rules=self.rules,
            subjects={s.id: s for s in self.s.values()}, assign=self.assign, people=self.people, fixed=self.fixed, drafts=self.drafts, entries=self.entries,
        )

    def check(self, subject: str | list[str], day: int, start: int, draft: uuid.UUID | None = None, **kw) -> sch.Result:
        names = [subject] if isinstance(subject, str) else subject
        d = draft or self.dA
        sem = self.A if d in (self.dA, self.dA2) else self.B
        return sch.check_unit(self.ctx(), draft_id=d, sem_id=sem, subject_ids=[self.s[n].id for n in names], day=day, start=start, **kw)


def codes(res: sch.Result, hard: bool = True) -> set[str]:
    return {i.code for i in (res.hard if hard else res.soft)}


@pytest.fixture()
def w() -> World:
    return World()


# ================================================================================================ the plain case
def test_a_free_cell_is_fine(w):
    r = w.check("maths", MON, 1)
    assert r.ok and r.length == 1 and not r.soft


def test_a_lab_is_one_block_of_two_periods(w):
    r = w.check("chem_lab", TUE, 1)
    assert r.ok and r.length == 2


# ================================================================================================ time rules (hard)
def test_a_day_the_college_does_not_work_is_refused(w):
    assert codes(w.check("maths", SAT, 1)) == {"NOT_WORKING_DAY"}
    w.days_per_week = 6
    assert w.check("maths", SAT, 1).ok


def test_an_off_day_of_the_class_is_refused(w):
    w.rules[(w.A, THU)] = DayRule(is_off=True)
    r = w.check("maths", THU, 1)
    assert codes(r) == {"DAY_OFF"} and "OFF" in r.hard[0].message
    assert w.check("maths", WED, 1).ok                       # other days are fine


def test_after_the_early_end_time_is_refused(w):
    w.rules[(w.A, WED)] = DayRule(end_time=time(14, 40))     # P4 ends 14:40
    assert w.check("maths", WED, 4).ok
    r = w.check("maths", WED, 5)
    assert codes(r) == {"AFTER_END"} and "14:40" in r.hard[0].message
    assert codes(w.check("chem_lab", WED, 4)) == {"AFTER_END"}      # a lab P4+P5 would end too late


def test_a_period_that_does_not_exist_is_refused(w):
    assert codes(w.check("maths", MON, 7)) == {"NO_SUCH_PERIOD"}
    assert codes(w.check("chem_lab", MON, 6)) == {"NO_SUCH_PERIOD"}      # P6 + P7


def test_the_lunch_break_is_never_a_lecture(w):
    # periods never overlap lunch by construction; the check still guards against a grid that does
    ctx = w.ctx()
    assert not [i for i in sch.time_issues(ctx, w.A, MON, 3, 1)]
    from app.services.grid import Grid, Period
    bad = Grid([Period(1, 600, 660), Period(2, 780, 850)], 5, (790, 820), 600, 1000)       # period 2 (13:00-14:10) runs over lunch (13:10-13:40)
    ctx.grid = bad
    assert [i.code for i in sch.time_issues(ctx, w.A, MON, 2, 1)] == ["LUNCH"]


# ================================================================================================ block shape (hard)
def test_a_lab_must_be_exactly_two_periods_and_a_lecture_one(w):
    assert codes(w.check("chem_lab", MON, 1, length=1)) == {"BLOCK_BROKEN"}
    assert codes(w.check("maths", MON, 1, length=2)) == {"BLOCK_BROKEN"}
    assert w.check("chem_lab", MON, 1, length=2).ok


def test_a_lab_around_lunch_is_allowed_with_a_warning(w):
    r = w.check("chem_lab", MON, 3)                                      # P3 + P4 with lunch between
    assert r.ok and "LAB_AROUND_LUNCH" in codes(r, hard=False)


def test_pe_and_sol_cannot_be_placed_here(w):
    r = w.check("pe", MON, 1)
    assert codes(r) == {"PE_SOL_FIXED"} and "Fixed Slots" in r.hard[0].message


def test_a_subject_of_another_class_is_refused(w):
    assert codes(w.check("b_maths", MON, 1)) == {"WRONG_CLASS"}


# ================================================================================================ the class (hard)
def test_the_class_cannot_have_two_lectures_at_once(w):
    w.put(w.dA, "maths", MON, 2)
    r = w.check("phys", MON, 2)
    assert codes(r) == {"CLASS_BUSY"} and "Maths" in r.hard[0].message
    assert w.check("phys", MON, 3).ok


def test_a_lab_block_blocks_both_of_its_periods(w):
    w.put(w.dA, "chem_lab", TUE, 4)                                      # P4 + P5
    assert codes(w.check("maths", TUE, 4)) == {"CLASS_BUSY"}
    assert codes(w.check("maths", TUE, 5)) == {"CLASS_BUSY"}
    assert w.check("maths", TUE, 3).ok and w.check("maths", TUE, 6).ok
    assert codes(w.check("chem_lab", TUE, 3)) == {"CLASS_BUSY"}          # a second lab block P3+P4 would overlap P4


def test_a_locked_slot_of_the_class_cannot_be_overwritten(w):
    w.lock(w.A, MON, 5, "PE", "pe")
    r = w.check("maths", MON, 6)
    assert codes(r) == {"CLASS_LOCKED"} and "locked slot" in r.hard[0].message
    assert w.check("maths", MON, 4).ok


def test_a_combined_locked_slot_locks_every_covered_class(w):
    key = w.lock(w.A, WED, 5, "SOL", "pe")
    w.lock(w.B, WED, 5, "SOL", "pe", key=key)                            # the same session, class B too
    ra = w.check("maths", WED, 5)
    rb = w.check("b_maths", WED, 6, draft=w.dB)
    assert codes(ra) == {"CLASS_LOCKED"} and "combined" in ra.hard[0].message and "Class B" in ra.hard[0].message
    assert codes(rb) == {"CLASS_LOCKED"} and "Class A" in rb.hard[0].message


def test_two_drafts_of_one_class_never_clash(w):
    w.put(w.dA, "maths", MON, 1)
    r = w.check("maths", MON, 1, draft=w.dA2)                            # the alternative draft may use the same cell
    assert r.ok


# ================================================================================================ teachers (hard)
def test_a_teacher_cannot_be_in_two_classes_at_once(w):
    w.put(w.dB, "b_maths", MON, 3)                                       # Maths Sir teaches in class B at P3 (B's primary draft)
    r = w.check("maths", MON, 3)
    assert codes(r) == {"TEACHER_BUSY"} and "Maths Sir" in r.hard[0].message and "Class B" in r.hard[0].message
    assert w.check("maths", MON, 4).ok


def test_only_the_primary_draft_of_another_class_counts(w):
    other = U()
    w.drafts[other] = sch.DraftInfo(other, w.B, "B alternative", False)
    w.entries.append(sch.Entry(U(), other, w.B, w.s["b_maths"].id, MON, 3, 1, None))
    assert w.check("maths", MON, 3).ok                                   # B's NON-primary draft is only an alternative
    w.drafts[other] = sch.DraftInfo(other, w.B, "B alternative", True)
    w.drafts[w.dB] = sch.DraftInfo(w.dB, w.B, "B main", False)
    assert codes(w.check("maths", MON, 3)) == {"TEACHER_BUSY"}           # ... until it is made primary


def test_a_teacher_in_a_locked_slot_of_another_class_is_busy(w):
    w.lock(w.B, THU, 1, "PE", "maths")
    assert codes(w.check("maths", THU, 2)) == {"TEACHER_BUSY"}
    assert w.check("maths", THU, 3).ok


def test_a_combined_session_busies_the_teacher_once(w):
    key = U()
    w.lock(w.A, FRI, 5, "PE", "pe", key=key)
    w.lock(w.B, FRI, 5, "PE", "pe", key=key)
    w.assign[w.s["b_phys"].id] = (w.t["pe"], None)                       # PE Sir also teaches B Phys
    r = w.check("b_phys", FRI, 5, draft=w.dB)
    assert [i.code for i in r.hard].count("TEACHER_BUSY") == 1 and "CLASS_LOCKED" in codes(r)


def test_the_co_teacher_is_busy_too(w):
    w.put(w.dB, "b_maths", TUE, 2)                                       # Maths Sir
    w.assign[w.s["b_phys"].id] = (w.t["b1"], w.t["co"])
    w.put(w.dB, "b_phys", WED, 1)                                        # co teacher busy in class B at Wed P1
    r = w.check("co", WED, 1)                                            # 'Co Subject' = Maths Sir + Co Teacher
    assert codes(r) == {"TEACHER_BUSY"} and "Co Teacher" in r.hard[0].message
    assert codes(w.check("co", TUE, 2)) == {"TEACHER_BUSY"}              # the main teacher too


def test_visiting_faculty_only_on_their_days(w):
    assert w.check("visit", WED, 1).ok
    r = w.check("visit", THU, 1)
    assert codes(r) == {"VISITING_DAY"} and "Monday, Tuesday, Wednesday" in r.hard[0].message
    assert codes(w.check("visit", SAT, 1)) == {"NOT_WORKING_DAY", "VISITING_DAY"} or codes(w.check("visit", SAT, 1)) == {"NOT_WORKING_DAY"}


def test_visiting_faculty_weekly_hours_limit(w):
    for start in (1, 2, 3):
        w.put(w.dB, "b_visit", MON, start)                               # 3 hours already in class B (limit 4)
    assert w.check("visit", TUE, 1).ok                                   # the 4th hour fits
    w.put(w.dA, "visit", TUE, 1)
    assert codes(w.check("visit", WED, 1)) == {"VISITING_HOURS"}        # a 5th hour does not


def test_a_deactivated_teacher_cannot_be_used(w):
    assert codes(w.check("gone", MON, 1)) == {"TEACHER_INACTIVE"}


# ================================================================================================ parallel (elective group) placements
def test_the_options_of_an_elective_group_go_together_without_a_false_clash(w):
    r = w.check(["ai", "cyber"], MON, 1)
    assert r.ok and r.length == 1 and not r.soft
    r = w.check(["ai_lab", "cyber_lab"], TUE, 1)
    assert r.ok and r.length == 2                                        # parallel labs: one 2-period block


def test_one_option_alone_is_refused_the_whole_group_must_run_together(w):
    r = w.check("ai", MON, 1)
    assert codes(r) == {"PARALLEL_BROKEN"} and "same slot" in r.hard[0].message


def test_options_of_different_groups_or_mixed_kinds_cannot_be_combined(w):
    assert codes(w.check(["ai", "ai_lab"], MON, 1)) == {"PARALLEL_MIXED"}
    assert codes(w.check(["ai", "maths"], MON, 1)) == {"PARALLEL_MIXED"}


def test_a_placed_parallel_group_blocks_the_class_like_any_lecture(w):
    key = U()
    w.put(w.dA, "ai", MON, 2, parallel=key)
    w.put(w.dA, "cyber", MON, 2, parallel=key)
    assert codes(w.check("maths", MON, 2)) == {"CLASS_BUSY"}
    r = w.check(["ai", "cyber"], MON, 2, ignore={key}, me=key)           # moving it onto itself is fine
    assert r.ok


def test_the_same_teacher_for_two_parallel_options_is_a_clash(w):
    w.assign[w.s["cyber"].id] = (w.t["ai"], None)                         # one person cannot teach both options at once
    r = w.check(["ai", "cyber"], MON, 1)
    assert codes(r) == {"TEACHER_BUSY"} and "AI Faculty" in r.hard[0].message


def test_each_option_teacher_is_checked_against_other_classes(w):
    w.assign[w.s["b_phys"].id] = (w.t["cyber"], None)
    w.put(w.dB, "b_phys", MON, 1)                                        # Cyber Faculty busy in class B at P1
    r = w.check(["ai", "cyber"], MON, 1)
    assert codes(r) == {"TEACHER_BUSY"} and "Cyber Faculty" in r.hard[0].message


# ================================================================================================ soft rules (warnings, allowed)
def test_the_same_subject_twice_in_a_day_is_only_a_warning(w):
    w.put(w.dA, "maths", MON, 1)
    r = w.check("maths", MON, 3)
    assert r.ok and "SAME_SUBJECT_TWICE_DAY" in codes(r, hard=False)
    lab = w.put(w.dA, "chem_lab", TUE, 1)
    r = w.check("chem_lab", TUE, 4)
    assert "SAME_SUBJECT_TWICE_DAY" not in codes(r, hard=False)          # labs are excluded
    assert lab


def test_a_long_gap_for_the_class_is_a_warning(w):
    w.put(w.dA, "maths", MON, 1)
    r = w.check("phys", MON, 5)                                          # P2-P4 would stay empty
    assert r.ok and "GAP_CLASS" in codes(r, hard=False)
    assert "GAP_CLASS" not in codes(w.check("phys", MON, 2), hard=False)


def test_a_long_gap_for_the_teacher_is_a_warning(w):
    w.put(w.dB, "b_maths", MON, 1)                                       # Maths Sir teaches class B at P1
    r = w.check("maths", MON, 5)                                         # ... and would wait until P5
    assert r.ok and "GAP_TEACHER" in codes(r, hard=False) and "Maths Sir" in [i for i in r.soft if i.code == "GAP_TEACHER"][0].message


def test_more_lectures_than_the_credits_is_a_warning(w):
    for start in (1, 3, 5):
        w.put(w.dA, "maths", MON + (start // 3), start)
    r = w.check("maths", FRI, 1)
    assert r.ok and "OVER_REQUIRED" in codes(r, hard=False)


def test_a_subject_without_a_teacher_warns(w):
    r = w.check("nobody", MON, 1)
    assert r.ok and "NO_TEACHER" in codes(r, hard=False)


# ================================================================================================ moving, swapping, the whole week
def test_a_placement_may_move_over_its_own_old_place(w):
    e = w.put(w.dA, "chem_lab", MON, 1)                                  # P1 + P2
    assert w.check("chem_lab", MON, 2, ignore={e.key}, me=e.key, length=2).ok          # shift by one period
    assert codes(w.check("chem_lab", MON, 2)) == {"CLASS_BUSY"}                          # ... but a NEW lab there would clash


def test_swapping_two_lectures(w):
    a = w.put(w.dA, "maths", MON, 1)
    b = w.put(w.dA, "phys", MON, 4)
    assert sch.swap_check(w.ctx(), w.dA, w.A, [a], [b]).ok


def test_a_swap_that_breaks_a_rule_is_refused(w):
    a = w.put(w.dA, "visit", MON, 1)                                     # visiting faculty (Mon-Wed)
    b = w.put(w.dA, "phys", THU, 2)
    r = sch.swap_check(w.ctx(), w.dA, w.A, [a], [b])
    assert codes(r) == {"VISITING_DAY"}


def test_a_lab_block_and_a_lecture_cannot_be_swapped(w):
    a = w.put(w.dA, "chem_lab", MON, 1)
    b = w.put(w.dA, "maths", MON, 4)
    assert codes(sch.swap_check(w.ctx(), w.dA, w.A, [a], [b])) == {"SWAP_SIZE"}


def test_the_options_map_colours_every_cell_of_the_week(w):
    w.rules[(w.A, THU)] = DayRule(is_off=True)
    w.put(w.dA, "phys", MON, 1)
    w.lock(w.A, MON, 5, "PE", "pe")
    cells = sch.options_map(w.ctx(), draft_id=w.dA, sem_id=w.A, subject_ids=[w.s["maths"].id])
    assert len(cells) == 5 * 6                                           # 5 working days x 6 periods
    assert cells["1:2"]["state"] in ("ok", "warn")
    assert cells["1:1"]["state"] == "blocked" and "Physics" in cells["1:1"]["hard"][0]
    assert cells["1:5"]["state"] == "blocked" and "locked" in cells["1:5"]["hard"][0]
    assert all(cells[f"4:{p}"]["state"] == "blocked" and "OFF" in cells[f"4:{p}"]["hard"][0] for p in range(1, 7))


# ================================================================================================ completeness + the saved draft
def test_completeness_counts_lectures_blocks_groups_and_fixed_slots(w):
    for start in (1, 2):
        w.put(w.dA, "maths", MON, start)
    w.put(w.dA, "chem_lab", TUE, 1)
    key = U()
    w.put(w.dA, "ai", WED, 1, parallel=key)
    w.put(w.dA, "cyber", WED, 1, parallel=key)
    w.lock(w.A, FRI, 5, "PE", "pe")
    c = sch.completeness(w.ctx(), w.A, w.dA)
    by = {r["subject_id"]: r for r in c["subjects"]}
    assert (by[str(w.s["maths"].id)]["placed"], by[str(w.s["maths"].id)]["required"], by[str(w.s["maths"].id)]["status"]) == (2, 3, "missing")
    assert by[str(w.s["chem_lab"].id)]["status"] == "complete" and by[str(w.s["chem_lab"].id)]["required"] == 1
    assert by[str(w.s["pe"].id)]["status"] == "complete"                  # PE comes from its locked slot
    groups = {g["name"]: g for g in c["groups"]}
    assert groups["Major 1"]["placed"] == 1 and groups["Major 1"]["required"] == 3 and groups["Major 1"]["status"] == "missing"
    assert groups["Major 1 LAB"]["status"] == "missing" and groups["Major 1 LAB"]["required"] == 1
    assert 0 < c["percent"] < 100 and c["missing"] > 0


def test_a_saved_draft_shows_the_clashes_that_appeared_later(w):
    w.put(w.dA, "maths", MON, 1)
    w.put(w.dA, "phys", MON, 5)
    w.put(w.dB, "b_maths", MON, 1)                                       # same teacher at the same time as A's Maths
    w.lock(w.A, MON, 5, "PE", "pe")                                      # and a locked slot added later on A's Physics
    a = sch.analyze_draft(w.ctx(), w.dA)
    got = {c["code"] for c in a["clashes"]}
    assert got == {"TEACHER_BUSY", "CLASS_LOCKED"}
    b = sch.analyze_draft(w.ctx(), w.dB)
    assert {c["code"] for c in b["clashes"]} == {"TEACHER_BUSY"}         # class B sees its side of the same clash
    assert a["missing"] and a["completeness"]["percent"] < 100


def test_a_broken_lab_block_in_a_saved_draft_is_reported(w):
    w.entries.append(sch.Entry(U(), w.dA, w.A, w.s["chem_lab"].id, MON, 1, 1, None))      # a lab saved with 1 period
    a = sch.analyze_draft(w.ctx(), w.dA)
    assert "BLOCK_BROKEN" in {c["code"] for c in a["clashes"]}


def test_gap_runs_helper():
    assert sch.gap_runs({1, 4}) == [(2, 3)]
    assert sch.gap_runs({1, 3}) == []              # one free period is not a "long gap"
    assert sch.gap_runs({2}) == [] and sch.gap_runs(set()) == []
