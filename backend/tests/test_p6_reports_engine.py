"""Phase 6 report calculations (app/services/reports.py) - pure logic, no database, no server.
Reuses the Phase 2/4 made-up college: class A + class B, 6 periods a day (10:00-16:50), lunch 13:10-13:40, a published week."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services import hours as hr
from app.services import reports as rp
from app.services.changes import Absence, DayChanges, Sub
from app.services.viewdata import SubjInfo
from app.services.prefs import TeacherPref

from .test_p2_engine import MON, TUE, WED
from .test_p4_engine import Fake, fake_with_lectures

U = uuid.uuid4
MONDAY = date(2026, 10, 12)
THURSDAY = date(2026, 10, 15)
SATURDAY = date(2026, 10, 17)


def test_weekly_load_counts_a_combined_session_once_and_a_co_taught_lecture_for_both():
    f = fake_with_lectures()
    w = f.w
    k = w.lock(w.A, MON, 5, "PE", "pe")
    w.lock(w.B, MON, 5, "PE", "pe", key=k)                      # ONE session for two classes
    w.put(w.dA, "co", TUE, 4)                                   # co-taught by Maths Sir and Co Teacher
    load = rp.weekly_load(f.data())
    assert (load[w.t["pe"]].periods, load[w.t["pe"]].lectures) == (2, 1)                     # not 4 / 2
    assert load[w.t["maths"]].periods == 4 and load[w.t["maths"]].lectures == 4              # Mon P1, Wed P1, class B's Fri P3 + the co-taught Tue P4
    assert (load[w.t["co"]].periods, load[w.t["co"]].lectures) == (1, 1)                     # the co-teacher counts it too
    assert load[w.t["lab"]].periods == 2 and load[w.t["lab"]].lectures == 1                  # a lab block = 2 periods, 1 lecture
    assert dict(load[w.t["maths"]].per_day) == {MON: 1, WED: 1, TUE: 1, 5: 1}


def test_workload_status_bands_and_the_visiting_exception():
    f = fake_with_lectures()
    data = f.data()
    norm = rp.Norm(norm_hours=3, min_hours=2, label="X", source="school")
    rows = {r["teacher"]: r for r in rp.workload_rows(data, norm)}
    assert rows["Maths Sir"]["hours"] == 3.0 and rows["Maths Sir"]["status"] == "within" and rows["Maths Sir"]["load_pct"] == 100     # Mon P1, Wed P1 (A) + Fri P3 (B)
    assert rows["Lab Sir"]["status"] == "within"                                             # 2 periods: exactly at the lowest healthy load
    assert rows["Physics Madam"]["status"] == "under" and rows["Physics Madam"]["hours"] == 1.0
    assert rows["Visiting Vik"]["status"] == "visiting" and rows["Visiting Vik"]["visiting_limit"] == 4 and rows["Visiting Vik"]["role"] == "Visiting"
    assert "Gone Sir" not in rows                                                           # a deactivated teacher is not listed
    over = {r["teacher"]: r for r in rp.workload_rows(data, rp.Norm(1, 1))}
    assert over["Maths Sir"]["status"] == "over" and over["Maths Sir"]["load_pct"] == 300
    # visiting faculty may only use their own days in the capacity: Mon-Wed = 3 days x 6 periods
    assert rows["Visiting Vik"]["capacity"] == 18 and rows["Maths Sir"]["capacity"] == 30
    assert rp.status_of(5, rp.Norm(), is_visiting=False, published=False) == "none"         # nothing published: no verdict
    s = rp.workload_summary(list(rows.values()), norm)
    regular = [r for r in rows.values() if not r["is_visiting"]]
    assert s["visiting"] == 1 and s["under"] + s["within"] + s["over"] == len(regular) and s["norm_hours"] == 3
    assert s["avg_hours"] == round(sum(r["hours"] for r in regular) / len(regular), 1)


def test_an_unpublished_school_gets_no_verdict_instead_of_everybody_under_norm():
    f = Fake()                                                  # nothing placed -> nothing published
    rows = rp.workload_rows(f.data(), rp.Norm())
    assert rows and all(r["status"] == "none" for r in rows)
    assert rp.workload_summary(rows, rp.Norm())["published"] is False


def test_the_norm_is_picked_school_then_college_then_built_in():
    a, b = U(), U()
    rows = [{"school_id": None, "norm_hours": 14, "min_hours": 10, "label": "College"}, {"school_id": a, "norm_hours": 18, "min_hours": 12, "label": None}]
    assert rp.pick_norm(rows, a) == rp.Norm(18, 12, "UGC / AICTE norm", "school")
    assert rp.pick_norm(rows, b) == rp.Norm(14, 10, "College", "college")
    assert rp.pick_norm(rows, None).source == "college"
    assert rp.pick_norm([], a) == rp.Norm(16, 12, "UGC / AICTE norm", "default")


def test_free_periods_follow_the_published_week_the_absence_the_visiting_days_and_accepted_subs():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    free, why = rp.free_periods_of(data, DayChanges(), w.t["maths"], MONDAY)
    assert why is None and free == [2, 3, 4, 5, 6]                                           # busy at Mon P1 only
    assert rp.free_periods_of(data, DayChanges(absences=[Absence(U(), w.t["maths"], MONDAY, None, "self")]), w.t["maths"], MONDAY) == ([], "absent")
    assert rp.free_periods_of(data, DayChanges(), w.t["visit"], THURSDAY)[1] == "visiting faculty - does not come that day"
    assert rp.free_periods_of(data, DayChanges(), w.t["gone"], MONDAY) == ([], "deactivated")
    assert rp.free_periods_of(data, DayChanges(), w.t["phys"], SATURDAY)[1] == "not a working day"
    covering = Sub(U(), None, MONDAY, 3, 1, frozenset({w.A}), frozenset(), None, "Cover", w.t["lab"], "filled", "manual", w.t["maths"], None, None)
    assert rp.free_periods_of(data, DayChanges(subs=[covering]), w.t["maths"], MONDAY)[0] == [2, 4, 5, 6]                    # now covering Mon P3


def test_free_teachers_filters_by_period_orders_by_load_and_puts_a_not_available_mark_last():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    data.prefs = {w.t["phys"]: TeacherPref(w.t["phys"], frozenset({(MON, 3)}))}
    res = rp.free_teachers(data, DayChanges(), MONDAY, start=3)
    names = [t["name"] for t in res["teachers"]]
    assert "Maths Sir" in names and "Gone Sir" not in names and res["period"] == 3 and res["time"] == "12:10-13:10"
    assert names[-1] == "Physics Madam" and res["teachers"][-1]["marked_unavailable"] is True and not any(t["marked_unavailable"] for t in res["teachers"][:-1])
    busy_at_1 = {t["name"] for t in rp.free_teachers(data, DayChanges(), MONDAY, start=1)["teachers"]}
    assert "Maths Sir" not in busy_at_1                                                       # teaching Maths Mon P1
    two = {t["name"] for t in rp.free_teachers(data, DayChanges(), THURSDAY, start=1, length=2)["teachers"]}
    assert "Lab Sir" not in two                                                               # the lab block is Thu P1-P2
    day = rp.free_teachers(data, DayChanges(), MONDAY)
    maths = next(t for t in day["teachers"] if t["name"] == "Maths Sir")
    assert maths["free_mask"] == [False, True, True, True, True, True] and maths["free_count"] == 5 and maths["load_today"] == 1
    assert len(day["periods"]) == 6 and day["periods"][0] == {"period": 1, "time": "10:00-11:00"}


def test_the_current_period_lookup_handles_lectures_breaks_lunch_and_after_hours():
    data = fake_with_lectures().data()
    assert rp.period_at(data, 10 * 60 + 30) == (1, None)                                      # 10:30 -> period 1
    assert rp.period_at(data, 11 * 60 + 2) == (None, 2)                                       # the 5-minute break -> next is period 2
    assert rp.period_at(data, 13 * 60 + 20) == (None, 4)                                      # lunch (13:10-13:40) -> period 4 is next
    assert rp.period_at(data, 18 * 60) == (None, None)                                        # after college hours
    assert rp.period_at(data, 8 * 60) == (None, 1)                                            # before the day starts


def test_rankings_are_ordered_limited_and_name_the_right_numbers():
    rows = [
        {"teacher_id": str(i), "teacher": n, "school": "S", "role": "Teacher", "hours": h, "lectures": h, "teaching_days": 1, "max_day": 1,
         "load_pct": h * 5, "status": "under", "free_periods": 30 - h, "capacity": 30, "subs_covered": c}
        for i, (n, h, c) in enumerate([("A", 10, 0), ("B", 14, 3), ("C", 2, 5), ("D", 14, 0)])
    ]
    r = rp.rankings(rows, limit=3)
    assert [x["teacher"] for x in r["busiest"]] == ["B", "D", "A"] and [x["rank"] for x in r["busiest"]] == [1, 2, 3]   # ties by name
    assert [x["teacher"] for x in r["most_free"]] == ["C", "A", "B"]
    assert [x["teacher"] for x in r["substitutes"]] == ["C", "B"]                           # only people who covered something
    assert rp.rankings(rows, limit=1)["busiest"][0]["teacher"] == "B"


def test_month_helpers():
    assert rp.month_bounds("2026-02", date(2026, 10, 9)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert rp.month_bounds("2028-02", date(2026, 10, 9)) == (date(2028, 2, 1), date(2028, 2, 29))      # a leap year
    assert rp.month_bounds(None, date(2026, 10, 9)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert rp.month_bounds("2026-12", date(2026, 1, 1))[1] == date(2026, 12, 31)
    with pytest.raises(ValueError):
        rp.month_bounds("oct", date(2026, 10, 9))
    assert rp.month_range(date(2026, 2, 1), 4) == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]    # across the new year
    assert rp.pct(1, 3) == 33 and rp.pct(1, 0) == 0


def test_hours_report_flattens_subjects_and_classes():
    f = fake_with_lectures()
    data = f.data()
    per_class = hr.compute_hours(data, date(2026, 10, 12))
    rows, classes = rp.hours_report(per_class, "School of Tests", f.school)
    maths = next(r for r in rows if r["subject"] == "Maths")
    assert maths["school"] == "School of Tests" and maths["class"] == "Class A" and maths["required"] == 45 and maths["teachers"] == "Maths Sir"
    assert maths["shortfall"] == max(0, 45 - maths["deliverable"]) and maths["percent_planned"] == round(100 * min(maths["deliverable"], 45) / 45)
    assert rows[0]["shortfall"] == max(r["shortfall"] for r in rows)                          # the shortest subject first
    a = next(c for c in classes if c["class"] == "Class A")
    assert a["required"] == sum(r["required"] for r in rows if r["class"] == "Class A" and r["status"] in ("ok", "short"))
    assert a["short_subjects"] == sum(1 for r in rows if r["class"] == "Class A" and r["shortfall"] > 0)
    assert a["planned"] == a["required"] - a["shortfall"] and a["percent_planned"] == round(100 * a["planned"] / a["required"])


def test_one_subjects_surplus_never_hides_another_subjects_shortfall_in_the_class_percentages():
    def mk(name: str, required: int, scheduled: int) -> hr.SubjectHours:
        info = SubjInfo(U(), U(), name, name[:4], 1, 1, required, False, None, None)
        return hr.SubjectHours(info, "Class X", [], weekly=1, required=required, scheduled=scheduled, delivered=scheduled, status="ok")

    sem = U()
    rows, classes = rp.hours_report({sem: [mk("Surplus", 10, 30), mk("Short", 40, 20)]}, "S", U())
    c = classes[0]
    assert (c["required"], c["deliverable"], c["shortfall"]) == (50, 50, 20)          # the raw sum is 50 hours - exactly what is required...
    assert c["planned"] == 30 and c["percent_planned"] == 60                           # ...but only 30 of them count (10 + 20), so the class is 60 % planned, not 100 %
    assert c["taught"] == 30 and c["percent_delivered"] == 60
    assert c["planned"] == c["required"] - c["shortfall"] and c["short_subjects"] == 1
    assert {r["subject"]: r["state"] for r in rows} == {"Surplus": "on track", "Short": "short"}
