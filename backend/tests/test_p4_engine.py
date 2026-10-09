"""Phase 4 pure-logic tests (no database, no server): weekly-lectures override, publishing snapshot / diff / clashes,
the holiday calendar, the published views, semester-hours tracking, holiday-compensation slots, PDF / Excel exports and the
notification helpers.  A tiny made-up college: 6 periods a day (10:00-16:50), lunch 13:10-13:40.
"""
from __future__ import annotations

import base64
import io
import os
import threading
import uuid
from datetime import date, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import calindex
from app.services import exporter as ex
from app.services import generator as gen
from app.services import hours as hr
from app.services import notify
from app.services import publishing as pub
from app.services import schedule as sch
from app.services import views as vw
from app.services.grid import build_grid
from app.services.timing import DayRule
from app.services.viewdata import ExtraRow, PersonInfo, SemInfo, SubjInfo, ViewData

from .test_p2_engine import MON, SAT, THU, TUE, WED, World

U = uuid.uuid4


# ====================================================================================================
# 0) the weekly-lectures override
# ====================================================================================================
def subj(credits: int, weekly=None, lab=False, kind=None):
    return sch.Subj(U(), U(), "S", "S1", credits, lab, kind, None, None, weekly)


def test_weekly_lectures_default_to_the_credits_and_the_override_wins():
    assert subj(4).weekly == 4 and subj(4, None).weekly == 4
    assert subj(4, 3).weekly == 3 and subj(4, 3).credits == 4      # credits stay as they are
    assert subj(2, 5).weekly == 5                                    # the override may also be HIGHER


def test_completeness_counts_against_the_override_not_the_credits():
    w = World()
    maths = w.s["maths"]                                             # 3 credits
    for d in (MON, TUE):
        w.put(w.dA, "maths", d, 1)
    base = sch.completeness(w.ctx(), w.A, w.dA)
    row = next(r for r in base["subjects"] if r["subject_id"] == str(maths.id))
    assert row["required"] == 3 and row["status"] == "missing"
    w.s["maths"] = sch.Subj(maths.id, maths.semester_id, maths.name, maths.code, maths.credits, False, None, None, None, 2)
    again = sch.completeness(w.ctx(), w.A, w.dA)
    row = next(r for r in again["subjects"] if r["subject_id"] == str(maths.id))
    assert row["required"] == 2 and row["status"] == "complete"
    assert again["required"] == base["required"] - 1


def test_the_over_required_warning_uses_the_override():
    w = World()
    m = w.s["maths"]
    w.s["maths"] = sch.Subj(m.id, m.semester_id, m.name, m.code, 3, False, None, None, None, 1)
    w.put(w.dA, "maths", MON, 1)
    soft = {i.code for i in w.check("maths", TUE, 1).soft}
    assert "OVER_REQUIRED" in soft                                   # 1 of 1 already placed (credits alone would allow 3)


def test_pe_sol_and_labs_ignore_the_override():
    w = World()
    lab, pe = w.s["chem_lab"], w.s["pe"]
    w.s["chem_lab"] = sch.Subj(lab.id, lab.semester_id, lab.name, lab.code, 1, True, None, None, None, 5)
    w.s["pe"] = sch.Subj(pe.id, pe.semester_id, pe.name, pe.code, 1, False, "PE", None, None, 4)
    comp = sch.completeness(w.ctx(), w.A, w.dA)
    by = {r["subject_id"]: r for r in comp["subjects"]}
    assert by[str(lab.id)]["required"] == 1 and by[str(lab.id)]["kind"] == "lab"      # a lab stays ONE double block
    assert by[str(pe.id)]["required"] == 1 and by[str(pe.id)]["kind"] == "fixed"      # PE / SOL come from the fixed slot


def test_the_generator_and_its_precheck_use_the_override():
    w = World()
    ctx = w.ctx()
    before = {u.name: u.needed for u in gen.build_problem(ctx).units if u.class_id == w.A}
    assert before["Maths"] == 3
    m = w.s["maths"]
    w.s["maths"] = sch.Subj(m.id, m.semester_id, m.name, m.code, 3, False, None, None, None, 1)
    after = gen.build_problem(w.ctx())
    assert {u.name: u.needed for u in after.units if u.class_id == w.A}["Maths"] == 1
    # an elective group takes the LARGEST value of its options
    ai = w.s["ai"]
    w.s["ai"] = sch.Subj(ai.id, ai.semester_id, ai.name, ai.code, 3, False, None, ai.group_id, ai.group_name, 5)
    grp = next(u for u in gen.build_problem(w.ctx()).units if u.group_id == w.g)
    assert grp.needed == 5


# ====================================================================================================
# 1) publishing: snapshot, diff, who must be told, clashes of the published state
# ====================================================================================================
def published_world():
    w = World()
    w.put(w.dA, "maths", MON, 1)
    w.put(w.dA, "phys", MON, 2)
    w.put(w.dA, "co", TUE, 1)                      # maths teacher + a co-teacher
    w.put(w.dA, "chem_lab", WED, 1)
    pk = U()
    w.put(w.dA, "ai", THU, 1, parallel=pk)
    w.put(w.dA, "cyber", THU, 1, parallel=pk)
    return w


def test_snapshot_has_lectures_fixed_slots_teachers_and_combined_text():
    w = published_world()
    key = w.lock(w.A, WED, 5, kind="SOL", teacher="maths", key=U())
    w.lock(w.B, WED, 5, kind="SOL", teacher="maths", key=key)
    ctx = w.ctx()
    rows = pub.snapshot_class(ctx, w.dA, w.A)
    assert len([r for r in rows if r.kind == "lecture"]) == 6 and len([r for r in rows if r.kind == "fixed"]) == 1
    fixed = next(r for r in rows if r.kind == "fixed")
    assert fixed.combined_with == "Class B" and fixed.group_key == key and fixed.teacher_id == w.t["maths"]
    co = next(r for r in rows if r.label == "Co Subject")
    assert co.teacher_id == w.t["maths"] and co.co_teacher_id == w.t["co"]
    assert {r.group_key for r in rows if r.label in ("AI Major", "Cyber Major")} == {next(r.group_key for r in rows if r.label == "AI Major")}
    assert not any(r.label == "B Maths" for r in rows)                  # only THIS class


def test_an_identical_snapshot_has_no_diff_and_a_move_is_one_added_one_removed():
    w = published_world()
    old = pub.snapshot_class(w.ctx(), w.dA, w.A)
    same = pub.snapshot_class(w.ctx(), w.dA, w.A)
    assert pub.diff_rows(old, same) == ([], [])
    moved = next(e for e in w.entries if e.subject_id == w.s["phys"].id)
    w.entries[w.entries.index(moved)] = sch.Entry(moved.id, moved.draft_id, moved.semester_id, moved.subject_id, FRI_, 3, 1, None)
    new = pub.snapshot_class(w.ctx(), w.dA, w.A)
    added, removed = pub.diff_rows(old, new)
    assert len(added) == 1 and len(removed) == 1 and added[0].label == removed[0].label == "Physics"


FRI_ = 5


def test_a_changed_teacher_makes_the_class_differ_without_touching_the_draft():
    w = published_world()
    old = pub.snapshot_class(w.ctx(), w.dA, w.A)
    w.assign[w.s["phys"].id] = (w.t["b1"], None)
    new = pub.snapshot_class(w.ctx(), w.dA, w.A)
    added, removed = pub.diff_rows(old, new)
    assert [r.teacher_id for r in added] == [w.t["b1"]] and [r.teacher_id for r in removed] == [w.t["phys"]]


def test_teacher_changes_only_lists_teachers_whose_own_lectures_changed():
    w = published_world()
    old = pub.snapshot_class(w.ctx(), w.dA, w.A)
    first = pub.teacher_changes([], old)
    assert set(first) >= {w.t["maths"], w.t["phys"], w.t["co"], w.t["lab"]}
    assert all(c.first_time for c in first.values())
    # move ONLY the physics lecture: only the physics teacher is affected
    e = next(e for e in w.entries if e.subject_id == w.s["phys"].id)
    w.entries[w.entries.index(e)] = sch.Entry(e.id, e.draft_id, e.semester_id, e.subject_id, FRI_, 4, 1, None)
    new = pub.snapshot_class(w.ctx(), w.dA, w.A)
    ch = pub.teacher_changes(old, new)
    assert set(ch) == {w.t["phys"]}
    c = ch[w.t["phys"]]
    assert len(c.moved) == 1 and not c.added and not c.removed and not c.first_time
    assert pub.teacher_changes(new, new) == {}                                           # nothing changed = nobody is told


def test_a_co_teacher_and_a_removed_lecture_are_both_noticed():
    w = published_world()
    old = pub.snapshot_class(w.ctx(), w.dA, w.A)
    w.entries[:] = [e for e in w.entries if e.subject_id != w.s["co"].id]
    ch = pub.teacher_changes(old, pub.snapshot_class(w.ctx(), w.dA, w.A))
    assert set(ch) == {w.t["maths"], w.t["co"]}
    assert all(len(c.removed) == 1 for c in ch.values())


def test_a_combined_session_counts_as_one_lecture_for_the_teacher():
    w = World()
    key = w.lock(w.A, WED, 5, kind="SOL", teacher="maths")
    w.lock(w.B, WED, 5, kind="SOL", teacher="maths", key=key)
    rows = pub.snapshot_class(w.ctx(), None, w.A) + pub.snapshot_class(w.ctx(), None, w.B)
    assert sum(len(v) for v in pub.teacher_lectures(rows)[w.t["maths"]].values()) == 2    # one cell, two class rows
    assert len(pub.teacher_lectures(rows)[w.t["maths"]]) == 1


def test_published_clashes_find_a_teacher_in_two_places_but_not_combined_or_parallel_rows():
    w = World()
    w.put(w.dA, "maths", MON, 1)
    w.put(w.dB, "b_maths", MON, 1)                                     # the same maths teacher, the same time, another class
    rows = pub.snapshot_class(w.ctx(), w.dA, w.A) + pub.snapshot_class(w.ctx(), w.dB, w.B)
    labels = {w.A: "Class A", w.B: "Class B"}
    clashes = pub.teacher_clashes(rows, labels, {w.t["maths"]: "Maths Sir"})
    assert len(clashes) == 1 and clashes[0]["semesters"] == {w.A, w.B} and "Maths Sir" in clashes[0]["message"]
    # a COMBINED session is one place
    w2 = World()
    k = w2.lock(w2.A, WED, 5, kind="SOL", teacher="maths")
    w2.lock(w2.B, WED, 5, kind="SOL", teacher="maths", key=k)
    rows2 = pub.snapshot_class(w2.ctx(), None, w2.A) + pub.snapshot_class(w2.ctx(), None, w2.B)
    assert pub.teacher_clashes(rows2, labels, {}) == []
    # a different time is fine
    w3 = World()
    w3.put(w3.dA, "maths", MON, 1)
    w3.put(w3.dB, "b_maths", MON, 2)
    rows3 = pub.snapshot_class(w3.ctx(), w3.dA, w3.A) + pub.snapshot_class(w3.ctx(), w3.dB, w3.B)
    assert pub.teacher_clashes(rows3, labels, {}) == []


# ====================================================================================================
# 2) the holiday calendar
# ====================================================================================================
def cal_entry(**kw):
    base = dict(id=U(), school_id=None, kind="holiday", title="Holiday", date_from=date(2026, 10, 20), date_to=date(2026, 10, 20),
                full_day=True, start_min=None, end_min=None, semester_ids=frozenset())
    base.update(kw)
    return calindex.Entry(**base)


def test_college_wide_and_school_entries_and_class_lists():
    school, other, cls_a, cls_b = U(), U(), U(), U()
    idx = calindex.CalendarIndex([
        cal_entry(title="Diwali", date_from=date(2026, 11, 8), date_to=date(2026, 11, 9)),
        cal_entry(title="Fest", school_id=school, kind="event", date_from=date(2026, 10, 20), date_to=date(2026, 10, 20)),
        cal_entry(title="Only A", school_id=school, date_from=date(2026, 10, 21), date_to=date(2026, 10, 21), semester_ids=frozenset({cls_a})),
    ])
    d = date(2026, 11, 9)
    assert idx.full_day(school, cls_a, d).title == "Diwali" and idx.full_day(other, cls_b, d).title == "Diwali"      # everybody
    assert idx.full_day(school, cls_a, date(2026, 10, 20)).title == "Fest"
    assert idx.full_day(other, cls_b, date(2026, 10, 20)) is None                                                   # another school's event
    assert idx.full_day(school, cls_a, date(2026, 10, 21)) is not None and idx.full_day(school, cls_b, date(2026, 10, 21)) is None
    assert idx.full_day(school, cls_a, date(2026, 11, 10)) is None


def test_a_part_day_event_blocks_only_the_overlapping_periods():
    school, c = U(), U()
    idx = calindex.CalendarIndex([cal_entry(school_id=school, kind="event", title="Seminar", full_day=False, start_min=12 * 60, end_min=13 * 60 + 10)])
    d = date(2026, 10, 20)
    assert idx.blocking(school, c, d, 10 * 60, 11 * 60) is None                    # period 1: free
    assert idx.blocking(school, c, d, 12 * 60 + 10, 13 * 60 + 10).title == "Seminar"
    assert idx.blocking(school, c, d, 11 * 60 + 5, 12 * 60 + 5) is not None          # period 2 overlaps by 5 minutes
    assert idx.full_day(school, c, d) is None


def test_week_helpers():
    assert calindex.week_start(date(2026, 10, 9)) == date(2026, 10, 5)              # Friday -> Monday
    assert calindex.week_start(date(2026, 10, 11)) == date(2026, 10, 5)             # Sunday belongs to the week before
    assert len(list(calindex.daterange(date(2026, 10, 1), date(2026, 10, 3)))) == 3


# ====================================================================================================
# helpers: a small ViewData (what the views, the hours and the exports read)
# ====================================================================================================
class Fake:
    """A published college: class A + class B, 5-day week, semester 2026-10-05 .. 2026-11-29 (8 weeks)."""

    def __init__(self, days_per_week: int = 5):
        self.w = World(days_per_week)
        self.school = U()
        self.grid = build_grid(time(10, 0), time(16, 50), 60, 5, time(13, 10), time(13, 40))
        self.entries_a: list[tuple[str, int, int]] = []
        self.calendar: list[calindex.Entry] = []
        self.extras: list[ExtraRow] = []
        self.dates = (date(2026, 10, 5), date(2026, 11, 29))
        self.published = {self.w.A, self.w.B}

    def data(self, today_rows=None) -> ViewData:
        w = self.w
        rows = pub.snapshot_class(w.ctx(), w.dA, w.A) + pub.snapshot_class(w.ctx(), w.dB, w.B)
        subjects = {
            s.id: SubjInfo(s.id, s.semester_id, s.name, s.code, s.credits, s.weekly, s.credits * 15, s.is_lab, s.special_kind, s.group_id)
            for s in w.s.values()
        }
        people = {p.id: PersonInfo(p.id, p.name, "teacher", p.is_active, p.is_visiting, p.visiting_days, p.max_weekly_hours) for p in w.people.values()}
        pubs = {s: {"version": 1, "published_at": __import__("datetime").datetime(2026, 10, 1, 9, 0, tzinfo=vw.IST), "published_by_name": "HOD", "source_draft_name": "Plan A", "semester_id": s} for s in self.published}
        return ViewData(
            school_id=self.school, school_name="School of Tests", college_name="Test College", term={"kind": "odd", "year": 2026, "id": U()},
            grid=self.grid, days_per_week=w.days_per_week,
            sems={w.A: SemInfo(w.A, "Class A", "Course", 1), w.B: SemInfo(w.B, "Class B", "Course", 1)}, rules=w.rules, rows=rows, pubs=pubs,
            subjects=subjects, people=people, calendar=calindex.CalendarIndex(self.calendar),
            term_dates={w.A: self.dates, w.B: self.dates}, extras=self.extras,
        )


def fake_with_lectures() -> Fake:
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)          # Monday P1
    w.put(w.dA, "maths", WED, 1)          # Wednesday P1
    w.put(w.dA, "phys", TUE, 2)
    w.put(w.dA, "chem_lab", THU, 1)       # a 2-period block
    w.put(w.dB, "b_maths", FRI_, 3)
    return f


# ====================================================================================================
# 3) the published views
# ====================================================================================================
def test_cells_per_class_and_per_teacher_with_dates_and_status():
    f = fake_with_lectures()
    f.calendar.append(cal_entry(title="Diwali", date_from=date(2026, 10, 14), date_to=date(2026, 10, 14)))      # a Wednesday
    data = f.data()
    w = f.w
    monday = date(2026, 10, 12)
    dates = vw.week_dates(monday, 5)
    cells = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, dates=dates)
    assert len(cells) == 4
    wed = next(c for c in cells if c["day"] == WED)
    assert wed["status"] == "holiday" and wed["status_note"] == "Diwali" and wed["date"] == "2026-10-14"
    mon = next(c for c in cells if c["day"] == MON)
    assert mon["status"] is None and mon["time"] == "10:00-11:00" and mon["classes"][0]["label"] == "Class A"
    lab = next(c for c in cells if c["is_lab"])
    assert lab["length"] == 2 and lab["time"] == "10:00-12:05"
    assert vw.stats_of(cells) == {"lectures": 3, "hours": 4, "cancelled": 1, "extra": 0}
    mine = vw.build_cells(data, data.rows_of_teacher(w.t["maths"]), across_classes=True, teacher_id=w.t["maths"])
    assert {c["items"][0]["title"] for c in mine} == {"Maths", "B Maths"}                       # one teacher, two classes
    assert all(it["teacher_id"] == str(w.t["maths"]) for c in mine for it in c["items"])


def test_an_elective_group_is_one_parallel_cell_and_a_teacher_sees_only_their_option():
    f = Fake()
    w = f.w
    pk = U()
    w.put(w.dA, "ai", THU, 1, parallel=pk)
    w.put(w.dA, "cyber", THU, 1, parallel=pk)
    data = f.data()
    cells = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False)
    assert len(cells) == 1 and cells[0]["parallel"] and {i["title"] for i in cells[0]["items"]} == {"AI Major", "Cyber Major"}
    mine = vw.build_cells(data, data.rows_of_teacher(w.t["ai"]), across_classes=True, teacher_id=w.t["ai"])
    assert len(mine) == 1 and [i["title"] for i in mine[0]["items"]] == ["AI Major"] and mine[0]["parallel_with"] == ["Cyber Major"]


def test_a_combined_session_is_one_cell_with_both_classes_for_the_teacher_but_one_per_class_in_the_class_view():
    f = Fake()
    w = f.w
    k = w.lock(w.A, WED, 5, kind="SOL", teacher="maths")
    w.lock(w.B, WED, 5, kind="SOL", teacher="maths", key=k)
    data = f.data()
    teacher_cells = vw.build_cells(data, data.rows_of_teacher(w.t["maths"]), across_classes=True, teacher_id=w.t["maths"])
    assert len(teacher_cells) == 1 and teacher_cells[0]["combined"] and len(teacher_cells[0]["classes"]) == 2 and len(teacher_cells[0]["items"]) == 1
    one_class = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False)
    assert len(one_class) == 1 and one_class[0]["combined_with"] == "Class B" and len(one_class[0]["classes"]) == 1


def test_outside_the_semester_and_part_day_events_are_flagged():
    f = fake_with_lectures()
    f.calendar.append(cal_entry(school_id=f.school, kind="event", title="Workshop", date_from=date(2026, 10, 12), date_to=date(2026, 10, 12),
                                full_day=False, start_min=10 * 60, end_min=11 * 60))
    data = f.data()
    w = f.w
    mon = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, day=MON, dates={MON: date(2026, 10, 12)})[0]
    assert mon["status"] == "event" and "Workshop" in mon["status_note"]
    before = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, day=MON, dates={MON: date(2026, 9, 28)})[0]
    assert before["status"] == "not_running"
    after = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, day=MON, dates={MON: date(2026, 12, 7)})[0]
    assert after["status"] == "not_running"


def test_extra_lectures_appear_only_in_their_own_week():
    f = fake_with_lectures()
    w = f.w
    x = ExtraRow(U(), w.A, w.s["maths"].id, date(2026, 10, 16), 5, 1, None, w.t["maths"], None, "make-up")        # a Friday
    f.extras.append(x)
    data = f.data()
    wk = vw.week_dates(date(2026, 10, 12), 5)
    cells = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, dates=wk, with_extras=True, sem_ids={w.A})
    extra = [c for c in cells if c["kind"] == "extra"]
    assert len(extra) == 1 and extra[0]["date"] == "2026-10-16" and extra[0]["start_period"] == 5 and extra[0]["extra"]["reason"] == "make-up"
    nxt = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, dates=vw.week_dates(date(2026, 10, 19), 5), with_extras=True, sem_ids={w.A})
    assert not [c for c in nxt if c["kind"] == "extra"]


def test_today_in_india_is_computed_without_a_timezone_database():
    assert (vw.today_ist() - date.today()).days in (-1, 0, 1)
    assert vw.monday_of(date(2026, 10, 9)) == date(2026, 10, 5)


# ====================================================================================================
# 4) semester-hours tracking
# ====================================================================================================
def hours_of(data: ViewData, w, name: str, today=date(2026, 10, 12)) -> hr.SubjectHours:
    return next(h for h in hr.compute_hours(data, today, {w.A})[w.A] if h.subject.name == name)


def test_deliverable_hours_are_the_weekly_timetable_times_the_working_weeks():
    f = fake_with_lectures()
    data = f.data()
    h = hours_of(data, f.w, "Maths")                        # 2 lectures a week, 8 full weeks (5 Oct - 29 Nov)
    assert h.weekly == 2 and h.scheduled == 16 and h.lost == 0 and h.extra == 0 and h.deliverable == 16
    assert h.required == 45 and h.shortfall == 29 and h.status == "short"
    lab = hours_of(data, f.w, "Chem Lab")
    assert lab.weekly == 2 and lab.scheduled == 16                                # a lab block is 2 hours, once a week


def test_a_holiday_removes_exactly_the_lectures_that_fall_on_it():
    f = fake_with_lectures()
    base = hours_of(f.data(), f.w, "Maths").scheduled
    f.calendar.append(cal_entry(title="Break", date_from=date(2026, 10, 19), date_to=date(2026, 10, 25)))      # one whole week
    h = hours_of(f.data(), f.w, "Maths")
    assert h.lost == 2 and h.scheduled == base - 2
    f.calendar.append(cal_entry(school_id=f.school, kind="event", title="Fest", date_from=date(2026, 11, 2), date_to=date(2026, 11, 2)))       # a Monday
    assert hours_of(f.data(), f.w, "Maths").lost == 3
    # an event for the OTHER class only changes nothing here
    f.calendar.append(cal_entry(school_id=f.school, title="Only B", date_from=date(2026, 11, 3), date_to=date(2026, 11, 3), semester_ids=frozenset({f.w.B})))
    assert hours_of(f.data(), f.w, "Maths").lost == 3


def test_delivered_so_far_counts_only_dates_up_to_today():
    f = fake_with_lectures()
    h = hours_of(f.data(), f.w, "Maths", today=date(2026, 10, 14))            # Mon 5, Wed 7, Mon 12, Wed 14
    assert h.delivered == 4 and h.remaining == h.deliverable - 4
    assert hours_of(f.data(), f.w, "Maths", today=date(2026, 10, 1)).delivered == 0


def test_extra_lectures_close_the_gap_and_the_status_follows():
    f = fake_with_lectures()
    w = f.w
    base = hours_of(f.data(), w, "Maths")
    f.extras.append(ExtraRow(U(), w.A, w.s["maths"].id, date(2026, 10, 30), 5, 1, None, w.t["maths"], None, None))
    h = hours_of(f.data(), w, "Maths")
    assert h.extra == 1 and h.deliverable == base.deliverable + 1 and h.shortfall == base.shortfall - 1
    # a subject with plenty is "ok"
    w.s["maths"] = sch.Subj(w.s["maths"].id, w.A, "Maths", "Maths", 1, False, None, None, None, 2)
    # (hours come from credits * 15 in this fake: 1 credit = 15 hours, 16 deliverable)
    assert hours_of(f.data(), w, "Maths").status == "ok"


def test_unpublished_and_dateless_classes_are_said_so_not_miscounted():
    f = fake_with_lectures()
    f.published = {f.w.B}
    h = hours_of(f.data(), f.w, "Maths")
    assert h.status == "unpublished" and h.shortfall == 0
    f.published = {f.w.A, f.w.B}
    f.dates = None
    data = f.data()
    data.term_dates = {}
    h2 = hours_of(data, f.w, "Maths")
    assert h2.status == "no_dates" and h2.shortfall == 0


def test_pe_without_a_linked_subject_still_counts_for_the_classes_pe_subject():
    f = Fake()
    w = f.w
    w.lock(w.A, MON, 5, kind="PE", teacher="pe")
    data = f.data()
    h = hours_of(data, w, "PE")
    assert h.weekly == 2 and h.scheduled == 16


def test_summary_counts_the_short_subjects():
    f = fake_with_lectures()
    s = hr.summarize(hr.compute_hours(f.data(), date(2026, 10, 12)))
    assert s["short_subjects"] >= 1 and s["short_hours"] > 0 and s["worst"][0]["shortfall"] >= s["worst"][-1]["shortfall"]


# ====================================================================================================
# 5) holiday compensation: clash-free extra slots
# ====================================================================================================
def planner_for(f: Fake, subject: str) -> hr.Planner:
    data = f.data()
    sid = f.w.s[subject].id
    teacher_map = hr.teachers_of_subjects(data, {})
    return hr.Planner(data, hr.unit_of(data, sid), teacher_map)


def test_a_suggested_slot_is_free_for_the_class_and_the_teacher():
    f = fake_with_lectures()
    w = f.w
    pl = planner_for(f, "maths")
    today = date(2026, 10, 12)
    slots = pl.suggest(5, today)
    assert len(slots) == 5
    data = f.data()
    for s in slots:
        wd = s.day_date.isoweekday()
        assert s.day_date > today and wd <= 5
        assert not any(r.day == wd and sch.overlap(s.start, s.length, r.start, r.length) for r in data.rows if r.semester_id == w.A)     # class free
        assert not any(r.day == wd and w.t["maths"] in r.teachers and sch.overlap(s.start, s.length, r.start, r.length) for r in data.rows)   # teacher free
    assert len({(s.day_date, s.start) for s in slots}) == 5                                                                                 # no double use


def test_nothing_is_suggested_on_a_holiday_or_outside_the_semester():
    f = fake_with_lectures()
    f.calendar.append(cal_entry(title="Closed", date_from=date(2026, 10, 13), date_to=date(2026, 11, 29)))
    pl = planner_for(f, "maths")
    got = pl.suggest(3, date(2026, 10, 12))
    assert [s.day_date for s in got] == []                                                    # the only open day left is a Monday that is full... or none
    assert pl.problems(date(2026, 12, 7), 2) and "outside the semester" in pl.problems(date(2026, 12, 7), 2)[0]
    assert any("Closed" in m for m in pl.problems(date(2026, 10, 20), 2))


def test_a_teacher_who_is_busy_with_another_class_blocks_the_slot():
    f = fake_with_lectures()
    w = f.w
    pl = planner_for(f, "maths")
    # Friday P3: the same maths teacher teaches "B Maths" to class B at that time
    msgs = pl.problems(date(2026, 10, 16), 3)
    assert any("teaching another class" in m for m in msgs)
    assert pl.problems(date(2026, 10, 16), 4) == []


def test_visiting_faculty_days_and_weekly_hours_are_respected():
    f = Fake()
    w = f.w
    w.put(w.dA, "visit", MON, 1)                 # the visiting teacher: Mon-Wed only, max 4 hours a week
    pl = planner_for(f, "visit")
    assert any("does not come on Thursday" in m for m in pl.problems(date(2026, 10, 15), 2))
    assert pl.problems(date(2026, 10, 13), 2) == []                                       # a Tuesday: fine
    for start in (2, 3, 4):                                                               # 1 published hour + 3 extra = 4 = the limit
        s = hr.Slot(date(2026, 10, 13), start, 1)
        assert pl.problems(s.day_date, s.start) == []
        pl.add(s)
    assert any("weekly limit of 4 hours" in m for m in pl.problems(date(2026, 10, 14), 2))   # the 5th hour of that week is refused


def test_a_deactivated_teacher_cannot_be_given_extra_lectures():
    f = Fake()
    w = f.w
    w.put(w.dA, "gone", MON, 1)
    pl = planner_for(f, "gone")
    assert any("deactivated" in m for m in pl.problems(date(2026, 10, 13), 2))


def test_an_elective_group_gets_extra_slots_together_and_a_lab_gets_a_double_block():
    f = Fake()
    w = f.w
    pk = U()
    w.put(w.dA, "ai", THU, 1, parallel=pk)
    w.put(w.dA, "cyber", THU, 1, parallel=pk)
    data = f.data()
    unit = hr.unit_of(data, w.s["ai"].id)
    assert set(unit) == {w.s["ai"].id, w.s["cyber"].id}
    pl = hr.Planner(data, unit, hr.teachers_of_subjects(data, {}))
    assert set(pl.teachers) == {w.t["ai"], w.t["cyber"]}
    lab = planner_for(f, "chem_lab")
    assert lab.length == 2
    s = lab.suggest(1, date(2026, 10, 12))[0]
    assert s.length == 2 and s.start + 1 <= len(data.grid.periods)


def test_the_best_slots_prefer_no_gap_and_spread_over_different_weeks():
    f = fake_with_lectures()
    pl = planner_for(f, "maths")
    got = pl.suggest(4, date(2026, 10, 12))
    weeks = {calindex.week_start(s.day_date) for s in got}
    assert len(weeks) >= 3                                                                 # not four in one week
    assert got[0].score >= got[-1].score - 60
    assert hr.why_not_more(pl) == "" or "Most slots were refused" in hr.why_not_more(pl)


# ====================================================================================================
# 6) PDF and Excel exports
# ====================================================================================================
def export_data():
    f = fake_with_lectures()
    w = f.w
    k = w.lock(w.A, WED, 5, kind="SOL", teacher="maths")
    w.lock(w.B, WED, 5, kind="SOL", teacher="maths", key=k)
    pk = U()
    w.put(w.dA, "ai", FRI_, 1, parallel=pk)
    w.put(w.dA, "cyber", FRI_, 1, parallel=pk)
    f.extras.append(ExtraRow(U(), w.A, w.s["maths"].id, date(2099, 1, 4), 5, 1, None, w.t["maths"], None, "make-up"))
    return f, f.data()


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)


def test_class_pdf_has_the_headers_the_breaks_the_labels_and_fits_one_page():
    from pypdf import PdfReader

    f, data = export_data()
    w = f.w
    cells = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, sem_ids={w.A})
    raw = ex.class_pdf(data, w.A, cells, date(2026, 10, 12))
    assert raw[:5] == b"%PDF-" and len(PdfReader(io.BytesIO(raw)).pages) == 1
    t = pdf_text(raw)
    for needle in ("Test College", "School of Tests", "Class Timetable - Class A", "Odd 2026", "Monday", "Friday",
                   "LUNCH", "13:10-13:40", "break", "Maths", "Physics", "Chem Lab", "PARALLEL", "AI Major", "Combined with Class B", "Printed"):
        assert needle in t, needle
    assert "make-up" in t and "Extra (compensation) lectures" in t


def test_teacher_pdf_lists_the_classes_and_the_combined_session():
    f, data = export_data()
    w = f.w
    cells = vw.build_cells(data, data.rows_of_teacher(w.t["maths"]), across_classes=True, teacher_id=w.t["maths"])
    t = pdf_text(ex.teacher_pdf(data, w.t["maths"], cells, date(2026, 10, 12)))
    assert "Teacher Timetable - Maths Sir" in t and "Class A" in t and "Class B" in t and "Combined session" in t


def test_a_co_taught_cell_names_the_other_teacher_not_the_owner():
    f = Fake()
    w = f.w
    w.put(w.dA, "co", MON, 2)                               # Maths Sir + Co Teacher
    data = f.data()
    for owner, other, self_name in ((w.t["maths"], "Co Teacher", "Maths Sir"), (w.t["co"], "Maths Sir", "Co Teacher")):
        cells = vw.build_cells(data, data.rows_of_teacher(owner), across_classes=True, teacher_id=owner)
        text = pdf_text(ex.teacher_pdf(data, owner, cells, date(2026, 10, 12)))
        assert f"With: {other}" in text and f"With: {self_name}" not in text


def test_master_pdf_has_one_page_per_day_with_lunch_and_both_classes():
    from pypdf import PdfReader

    f, data = export_data()
    w = f.w
    cells = vw.build_cells(data, data.rows, across_classes=True)
    raw = ex.master_pdf(data, cells, [data.sems[w.A], data.sems[w.B]], [1, 2, 3, 4, 5], "")
    reader = PdfReader(io.BytesIO(raw))
    assert len(reader.pages) == 5
    first = reader.pages[0].extract_text()
    assert "School Master Timetable - Monday" in first and "Class A" in first and "Class B" in first and "L = lunch break" in first


def test_excel_exports_open_with_merged_blocks_headers_and_print_setup():
    from openpyxl import load_workbook

    f, data = export_data()
    w = f.w
    cells = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, sem_ids={w.A})
    wb = load_workbook(io.BytesIO(ex.class_xlsx(data, w.A, cells, date(2026, 10, 12))))
    ws = wb["Timetable"]
    assert ws["A1"].value == "Test College" and ws["A3"].value == "Class Timetable - Class A"
    assert ws.page_setup.orientation == "landscape" and ws.page_setup.fitToWidth == 1
    assert any("LUNCH" in str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert ws.merged_cells.ranges                                                       # lab block + lunch + title rows
    assert "Subjects" in wb.sheetnames
    mw = load_workbook(io.BytesIO(ex.master_xlsx(data, vw.build_cells(data, data.rows, across_classes=True), [data.sems[w.A], data.sems[w.B]], [1, 2, 3, 4, 5], "")))
    assert mw.sheetnames[:5] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"] and "All lectures" in mw.sheetnames
    tw = load_workbook(io.BytesIO(ex.teacher_xlsx(data, w.t["maths"], vw.build_cells(data, data.rows_of_teacher(w.t["maths"]), across_classes=True, teacher_id=w.t["maths"]), date(2026, 10, 12))))
    assert "Lectures" in tw.sheetnames


def test_a_name_that_looks_like_a_formula_stays_text_in_excel():
    from openpyxl import load_workbook

    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)
    m = w.s["maths"]
    w.s["maths"] = sch.Subj(m.id, m.semester_id, "=HYPERLINK(\"http://x\",\"click\")", m.code, m.credits, False, None, None, None, None)
    data = f.data()
    raw = ex.class_xlsx(data, w.A, vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, sem_ids={w.A}), date(2026, 10, 12))
    wb = load_workbook(io.BytesIO(raw))
    assert not [c for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.data_type == "f"]
    assert any("HYPERLINK" in str(c.value) for row in wb["Timetable"].iter_rows() for c in row if c.value)


def test_unusual_characters_never_break_a_pdf_and_names_are_short_enough():
    assert ex.safe("A ∥ B → हिंदी") .startswith("A | B -> ")
    assert ex.short_name("Dr. Papri Das", 10) == "P. Das" and ex.short_name("External Faculty (AI&ML)") == "Ext. Faculty (AI&ML)"
    assert ex.file_stem("Timetable", "B.Sc. IT - Sem 3", "odd", "2026") == "Timetable_B_Sc_IT_Sem_3_odd_2026"


# ====================================================================================================
# 7) notifications: the hub, the push allow-list and a real Web Push delivery (checked by decrypting what the "browser" receives)
# ====================================================================================================
@pytest.mark.asyncio
async def test_the_hub_reaches_only_the_right_listener():
    h = notify.Hub()
    a, b = h.subscribe("alice"), h.subscribe("bob")
    assert h.publish("alice", {"x": 1}) == 1 and h.listeners("alice") == 1
    assert a.get_nowait() == {"x": 1} and b.empty()
    h.unsubscribe("alice", a)
    assert h.publish("alice", {"x": 2}) == 0 and h.listeners("alice") == 0


def test_only_real_push_services_are_allowed_as_endpoints(monkeypatch):
    ok = notify.endpoint_allowed
    assert ok("https://fcm.googleapis.com/fcm/send/abc") and ok("https://updates.push.services.mozilla.com/wpush/v2/abc")
    assert ok("https://wns2-par02p.notify.windows.com/w/?token=x") and ok("https://web.push.apple.com/abc")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.example/x", "https://169.254.169.254/x", "https://fcm.googleapis.com.evil.com/x",
                "http://127.0.0.1:8000/api/health", "ftp://fcm.googleapis.com/x", "not a url", ""):
        assert not ok(bad), bad
    monkeypatch.setattr(notify, "ALLOW_LOCAL_ENDPOINTS", True)
    assert ok("http://127.0.0.1:9999/push") and not ok("http://10.0.0.5/push")


def test_a_push_reaches_the_browser_encrypted_and_readable_only_by_it(monkeypatch):
    """A tiny local 'push service' receives the request; we decrypt it with the 'browser's' key exactly as a real browser would."""
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.config import get_settings

    if not get_settings().push_enabled:
        pytest.skip("no VAPID keys in .env")
    browser_key = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    p256dh = b64(browser_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            got["body"], got["headers"] = self.rfile.read(n), dict(self.headers)
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s = notify.Sent(U(), U(), "timetable_published", "Your timetable is published", "You teach 16 lectures a week.", "/my/timetable", {}, __import__("datetime").datetime.now())
        outcome, _status = notify._push_one(f"http://127.0.0.1:{server.server_port}/push", p256dh, b64(auth), notify._push_payload(s))
    finally:
        server.shutdown()
    assert outcome == "ok"
    h = {k.lower(): v for k, v in got["headers"].items()}
    assert h["content-encoding"] == "aes128gcm" and h["authorization"].lower().startswith("vapid ")     # the browser can verify us
    plain = http_ece.decrypt(got["body"], private_key=browser_key, auth_secret=auth)
    import json

    data = json.loads(plain)
    assert data["title"] == "Your timetable is published" and data["url"] == "/my/timetable" and data["tag"] == "timetable_published"
    assert b"Your timetable" not in got["body"]                                                          # the wire shows only ciphertext


def test_a_dead_subscription_is_reported_as_gone():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.config import get_settings

    if not get_settings().push_enabled:
        pytest.skip("no VAPID keys in .env")
    key = ec.generate_private_key(ec.SECP256R1())
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    p256dh = b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))

    class Gone(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(410)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Gone)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        outcome, status = notify._push_one(f"http://127.0.0.1:{server.server_port}/x", p256dh, b64(os.urandom(16)), "{}")
    finally:
        server.shutdown()
    assert (outcome, status) == ("gone", 410)
