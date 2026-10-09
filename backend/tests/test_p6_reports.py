"""Phase 6 report tests: every report's numbers against a small PUBLISHED timetable whose answers are known, the configurable norm,
the free-teacher finder (absences, a teacher's not-available mark), cancelled sessions, monthly substitutions, school-vs-school,
the Excel download of each report, and - above all - who may see what (teacher: nothing, HOD: only his school, admin: everything).

Runs in a throw-away school against the REAL API + Supabase. Tests build on each other.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

from app.services import views as vw

from .conftest import db_exec, db_fetch, db_val, mail_for
from .test_p6_api import clean_slate, notes_of, set_prefs  # noqa: F401  (shared helpers)

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5
REPORTS = ["workload", "rankings", "free-teachers", "hours", "cancelled", "substitutions", "compare"]


def first_weekday_of_month(wd: int) -> date:
    """The first Monday (wd=1) / Wednesday (wd=3) ... of the CURRENT month - always inside it, even if it already passed."""
    first = vw.today_ist().replace(day=1)
    return first + timedelta(days=(wd - first.isoweekday()) % 7)


def upcoming(wd: int) -> date:
    base = vw.today_ist()
    return base + timedelta(days=(wd - base.isoweekday()) % 7 or 7)


def fday() -> date:
    """A Monday two weeks out: after every absence this module creates (they are all in the first half of THIS month)."""
    return upcoming(MON) + timedelta(days=14)


def cls() -> str:
    return S["sem"]


def mk_subject(p1, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, subject_ids: list[str], **over) -> str:
    body = {"full_name": f"P6R {key}", "email": mail_for(f"p6r-{key}"), "subject_ids": subject_ids}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def rep(p1, who, name: str, **params):
    return p1.api(who, "GET", f"/api/reports/{name}", params=params)


def row_of(rows: list[dict], key: str, value) -> dict:
    return next(r for r in rows if r[key] == value)


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    clean_slate(p1)
    db_exec("delete from public.workload_norms where school_id is null")                  # the real college has no default norm of its own
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ P6R Course', 'ZZP6R', 3) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 3) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = next(str(r["id"]) for r in sems if r["number"] == 1)
    S["term"] = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    today = vw.today_ist()
    db_exec("insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date) values ($1,$2,$3,$4,$5)",
            uuid.UUID(S["term"]), uuid.UUID(S["sem"]), uuid.UUID(p1.school), today.replace(day=1) - timedelta(days=30), today + timedelta(days=100))
    yield
    db_exec("delete from public.workload_norms where school_id is null")
    clean_slate(p1)


# ====================================================================================================
# 0) a published class with known answers
# ====================================================================================================
def test_00_setup_a_published_timetable_with_known_numbers(p1):
    S["maths"] = mk_subject(p1, "R-M", "P6R Maths", credits=3, semester_hours=200)         # far too many hours: it will be SHORT
    S["phys"] = mk_subject(p1, "R-P", "P6R Phys", credits=1, semester_hours=10)
    S["ta"] = mk_teacher(p1, "ta", [S["maths"]], specialization="Algebra and statistics")
    S["tb"] = mk_teacher(p1, "tb", [S["phys"]])
    S["tc"] = mk_teacher(p1, "tc", [], specialization="Free spirit")                        # teaches nothing: the most free teacher
    for sid, tid in ((S["maths"], S["ta"]), (S["phys"], S["tb"])):
        assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": sid, "teacher_id": tid}).status_code == 200
    draft = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(), "name": "Plan R"}).json()["id"]
    for day, start, sub in ((MON, 1, "maths"), (WED, 1, "maths"), (FRI, 1, "maths"), (MON, 2, "phys")):
        r = p1.api(p1.hod, "POST", f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, "subject_id": S[sub]})
        assert r.status_code == 201, r.text
    assert p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls()}).status_code == 200


# ====================================================================================================
# 1) who may see what
# ====================================================================================================
def test_a_teacher_gets_nothing_from_any_report(p1):
    for name in REPORTS:
        assert rep(p1, p1.teacher, name).status_code == 403, name
        assert p1.api(p1.teacher, "GET", f"/api/reports/export/{name}").status_code == 403, name
        assert p1.api(p1.teacher_b, "GET", f"/api/reports/{name}").status_code == 403, name
    assert p1.api(p1.teacher, "GET", "/api/reports/norms").status_code == 403
    assert p1.api(p1.teacher, "PUT", "/api/reports/norms", json={"norm_hours": 10, "min_hours": 5}).status_code == 403
    assert p1.api(p1.teacher, "DELETE", "/api/reports/norms").status_code == 403


def test_an_hod_is_locked_to_his_own_school_in_every_report(p1):
    for name in REPORTS:
        mine = rep(p1, p1.hod, name)
        assert mine.status_code == 200, (name, mine.text)
        assert [s["id"] for s in mine.json()["scope"]["schools"]] == [p1.school], name
        assert rep(p1, p1.hod, name, school_id=p1.school_b).status_code == 403, name        # another school: refused
        assert rep(p1, p1.hod, name, school_id="all").json()["scope"]["schools"][0]["id"] == p1.school     # 'all' is still only his school
        assert p1.api(p1.hod, "GET", f"/api/reports/export/{name}", params={"school_id": p1.school_b}).status_code == 403, name
    assert p1.api(p1.hod, "GET", "/api/reports/norms", params={"school_id": p1.school_b}).status_code == 403
    assert p1.api(p1.hod, "PUT", "/api/reports/norms", json={"school_id": p1.school_b, "norm_hours": 10, "min_hours": 5}).status_code == 403
    assert p1.api(p1.hod, "DELETE", "/api/reports/norms", params={"school_id": p1.school_b}).status_code == 403
    rows = rep(p1, p1.hod, "workload").json()["rows"]
    assert rows and all(r["school_id"] == p1.school for r in rows)                          # not one person of any other school


def test_the_admin_sees_every_school_or_one_through_the_filter(p1):
    everything = rep(p1, p1.admin, "workload").json()
    schools = {s["id"] for s in everything["scope"]["schools"]}
    assert p1.school in schools and len(schools) >= 2 and everything["scope"]["all"] is True
    assert {r["school_id"] for r in everything["rows"]} >= {p1.school}
    one = rep(p1, p1.admin, "workload", school_id=p1.school).json()
    assert [s["id"] for s in one["scope"]["schools"]] == [p1.school] and one["scope"]["all"] is False
    assert all(r["school_id"] == p1.school for r in one["rows"])
    other = rep(p1, p1.admin, "workload", school_id=p1.school_b).json()
    assert other["rows"] and not {r["teacher"] for r in other["rows"]} & {"P6R ta", "P6R tb", "P6R tc"}
    assert rep(p1, p1.admin, "workload", school_id=str(uuid.UUID(int=1))).status_code == 404
    assert rep(p1, p1.admin, "workload", school_id="nope").status_code == 422


# ====================================================================================================
# 2) workload against the norm
# ====================================================================================================
def test_workload_numbers_match_the_published_timetable(p1):
    w = rep(p1, p1.admin, "workload", school_id=p1.school).json()
    ta, tb, tc = (row_of(w["rows"], "teacher", f"P6R {k}") for k in ("ta", "tb", "tc"))
    assert (ta["hours"], ta["lectures"], ta["teaching_days"], ta["max_day"], ta["periods"]) == (3.0, 3, 3, 1, 3)
    assert ta["capacity"] == 30 and ta["free_periods"] == 27 and ta["load_pct"] == 19 and ta["norm_hours"] == 16 and ta["status"] == "under"
    assert (tb["hours"], tb["lectures"], tb["teaching_days"]) == (1.0, 1, 1) and tb["free_periods"] == 29
    assert (tc["hours"], tc["lectures"], tc["free_periods"]) == (0.0, 0, 30) and tc["specialization"] == "Free spirit"
    assert w["rows"][0]["teacher"] == "P6R ta"                                              # sorted: the busiest first
    s = w["summary"]
    regular = [r for r in w["rows"] if not r["is_visiting"]]
    visiting = [r for r in w["rows"] if r["is_visiting"]]      # (other test files may have left visiting accounts in this shared school)
    assert s["teachers"] == len(w["rows"]) and s["regular"] == len(regular) and s["visiting"] == len(visiting) and s["norm_hours"] == 16 and s["min_hours"] == 12 and s["norm_source"] == "default"
    assert s["avg_hours"] == round(sum(r["hours"] for r in regular) / len(regular), 1)
    assert s["under"] == len(regular) and s["within"] == 0 and s["over"] == 0 and s["total_hours"] == 4.0 and s["published"] is True
    assert w["norm"] == {"norm_hours": 16, "min_hours": 12, "label": "UGC / AICTE norm", "source": "default"}
    assert [c["key"] for c in w["columns"]][:3] == ["teacher", "role", "hours"]
    assert w["per_school"][0]["school_id"] == p1.school and w["per_school"][0]["teachers"] == s["teachers"]


def test_the_norm_is_configurable_and_changes_every_status(p1):
    r = p1.api(p1.hod, "PUT", "/api/reports/norms", json={"norm_hours": 3, "min_hours": 2, "label": "Our norm"})
    assert r.status_code == 200 and r.json()["effective"] == {"norm_hours": 3, "min_hours": 2, "label": "Our norm", "source": "school"}
    w = rep(p1, p1.hod, "workload").json()
    ta, tb, tc = (row_of(w["rows"], "teacher", f"P6R {k}") for k in ("ta", "tb", "tc"))
    assert ta["status"] == "within" and ta["load_pct"] == 100 and tb["status"] == "under" and tc["status"] == "under"
    assert w["summary"]["within"] == 1 and w["summary"]["norm_source"] == "school" and w["summary"]["norm_label"] == "Our norm"
    p1.api(p1.hod, "PUT", "/api/reports/norms", json={"norm_hours": 2, "min_hours": 1})
    ta2 = row_of(rep(p1, p1.hod, "workload").json()["rows"], "teacher", "P6R ta")
    assert ta2["status"] == "over" and ta2["load_pct"] == 150
    # nonsense is refused
    assert p1.api(p1.hod, "PUT", "/api/reports/norms", json={"norm_hours": 5, "min_hours": 9}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/reports/norms", json={"norm_hours": 0, "min_hours": 0}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/reports/norms", json={"norm_hours": 61, "min_hours": 1}).status_code == 422


def test_school_norm_beats_the_college_default_and_only_the_admin_sets_the_default(p1):
    assert p1.api(p1.hod, "PUT", "/api/reports/norms", json={"scope": "college", "norm_hours": 14, "min_hours": 10}).status_code == 403
    r = p1.api(p1.admin, "PUT", "/api/reports/norms", json={"scope": "college", "norm_hours": 14, "min_hours": 10, "label": "College default"})
    assert r.status_code == 200 and r.json()["college"]["norm_hours"] == 14
    got = p1.api(p1.hod, "GET", "/api/reports/norms").json()
    assert got["school"]["norm_hours"] == 2 and got["college"]["norm_hours"] == 14 and got["effective"]["source"] == "school"   # the school's own wins
    gone = p1.api(p1.hod, "DELETE", "/api/reports/norms")
    assert gone.status_code == 200 and gone.json()["school"] is None
    assert gone.json()["effective"] == {"norm_hours": 14, "min_hours": 10, "label": "College default", "source": "college"}
    assert rep(p1, p1.hod, "workload").json()["summary"]["norm_source"] == "college"
    admin_view = p1.api(p1.admin, "GET", "/api/reports/norms").json()
    assert admin_view["college"]["norm_hours"] == 14 and admin_view["built_in"] == {"norm_hours": 16, "min_hours": 12}
    db_exec("delete from public.workload_norms where school_id is null")
    assert p1.api(p1.hod, "GET", "/api/reports/norms").json()["effective"]["source"] == "default"
    assert p1.api(p1.admin, "PUT", "/api/reports/norms", json={"scope": "school", "norm_hours": 10, "min_hours": 5}).status_code == 422     # admin must name a school
    assert db_val("select count(*) from public.audit_log where entity = 'workload_norm' and actor_email = $1", p1.hod.account.email) >= 3


# ====================================================================================================
# 3) a substitution to count, then rankings
# ====================================================================================================
def test_a_substitution_happens_this_month(p1):
    d = first_weekday_of_month(MON)
    S["sub_date"] = d
    ab = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["ta"], "date": d.isoformat(), "reason": "conference"})
    assert ab.status_code == 201, ab.text
    sub = next(a for a in ab.json()["affected"] if a["status"] == "open" and a["start_period"] == 1)["id"]
    assert p1.api(p1.hod, "POST", f"/api/subs/{sub}/assign", json={"teacher_id": S["tb"]}).status_code == 200


def test_rankings_name_the_busiest_the_most_free_and_the_substitutes(p1):
    r = rep(p1, p1.admin, "rankings", school_id=p1.school, limit=3).json()
    busiest, free, subs = r["lists"]["busiest"], r["lists"]["most_free"], r["lists"]["substitutes"]
    assert [b["teacher"] for b in busiest][:2] == ["P6R ta", "P6R tb"] and busiest[0]["rank"] == 1 and busiest[0]["hours"] == 3.0
    assert len(busiest) == len(free) == 3 and free[0]["free_periods"] == 30 and free[0]["free_periods"] >= free[1]["free_periods"] >= free[2]["free_periods"]
    assert free[-1]["free_periods"] <= 30 and "P6R ta" not in [f["teacher"] for f in free[:1]]
    assert [s["teacher"] for s in subs] == ["P6R tb"] and subs[0]["subs_covered"] == 1
    assert r["rows"] == busiest and len(r["sections"]) == 2                                  # the table and the two extra sheets
    assert len(rep(p1, p1.admin, "rankings", school_id=p1.school, limit=1).json()["lists"]["busiest"]) == 1
    assert len(rep(p1, p1.admin, "rankings", school_id=p1.school, limit=500).json()["lists"]["busiest"]) <= 50   # the limit is capped


def test_substitutions_covered_this_month_show_in_the_workload(p1):
    tb = row_of(rep(p1, p1.hod, "workload").json()["rows"], "teacher", "P6R tb")
    assert tb["subs_covered"] == 1


# ====================================================================================================
# 4) the free-teacher finder
# ====================================================================================================
def test_the_finder_lists_who_is_free_at_a_chosen_period(p1):
    d = fday()
    p1_ = rep(p1, p1.hod, "free-teachers", date=d.isoformat(), period=1).json()
    names = {t["name"]: t for t in p1_["rows"]}
    assert "P6R ta" not in names                                                             # teaching Maths at Mon P1
    assert "P6R tb" in names and "P6R tc" in names
    tb = names["P6R tb"]
    assert tb["load_today"] == 1 and tb["free_count"] == 5 and [x["period"] for x in tb["free_periods"]] == [1, 3, 4, 5, 6]
    assert tb["free_periods"][0]["time"] == "10:00-11:00" and tb["free_text"].startswith("P1 10:00-11:00")
    assert tb["free_mask"] == [True, False, True, True, True, True] and tb["specialization"] is None
    assert names["P6R tc"]["specialization"] == "Free spirit" and names["P6R tc"]["load_today"] == 0
    assert p1_["schools"][0]["period"] == 1 and p1_["schools"][0]["time"] == "10:00-11:00" and len(p1_["schools"][0]["periods"]) == 6
    assert p1_["date"] == d.isoformat() and p1_["summary"]["free"] == len(p1_["rows"])
    at2 = {t["name"] for t in rep(p1, p1.hod, "free-teachers", date=d.isoformat(), period=2).json()["rows"]}
    assert "P6R ta" in at2 and "P6R tb" not in at2                                           # tb teaches Physics at Mon P2


def test_the_whole_day_view_has_a_free_busy_mask_per_teacher(p1):
    d = fday()
    day = rep(p1, p1.hod, "free-teachers", date=d.isoformat()).json()
    ta = row_of(day["rows"], "name", "P6R ta")
    assert ta["free_mask"] == [False, True, True, True, True, True] and ta["free_count"] == 5
    assert [p["period"] for p in day["schools"][0]["periods"]] == [1, 2, 3, 4, 5, 6]
    assert all(t["free_count"] >= 1 for t in day["rows"])                                # someone with no free period is not listed


def test_an_absent_teacher_is_not_free_and_a_not_available_mark_is_flagged_and_last(p1):
    d = fday() + timedelta(days=7)
    assert p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tc"], "date": d.isoformat()}).status_code == 201
    absent = rep(p1, p1.hod, "free-teachers", date=d.isoformat(), period=2).json()
    assert "P6R tc" not in {t["name"] for t in absent["rows"]}
    p1.api(p1.hod, "DELETE", f"/api/absence/{db_val('select id from public.teacher_absences where teacher_id = $1', uuid.UUID(S['tc']))}")
    set_prefs(S["tc"], no={(MON, 2)})
    flagged = rep(p1, p1.hod, "free-teachers", date=d.isoformat(), period=2).json()["rows"]
    assert flagged[-1]["name"] == "P6R tc" and flagged[-1]["marked_unavailable"] is True
    assert all(not t["marked_unavailable"] for t in flagged[:-1])                            # listed, but last and labelled
    set_prefs(S["tc"])


def test_the_finder_copes_with_now_a_sunday_and_bad_input(p1):
    now = rep(p1, p1.hod, "free-teachers", now="true")
    assert now.status_code == 200
    n = now.json()
    assert n["now"] is True and n["date"] == vw.today_ist().isoformat() and isinstance(n["rows"], list) and isinstance(n["notes"], list)
    sunday = vw.today_ist() + timedelta(days=(7 - vw.today_ist().isoweekday()) % 7 or 7)
    s = rep(p1, p1.hod, "free-teachers", date=sunday.isoformat(), period=1).json()
    assert s["rows"] == [] and any("not a working day" in x for x in s["notes"])
    assert rep(p1, p1.hod, "free-teachers", date=fday().isoformat(), period=99).json()["rows"] == []
    assert rep(p1, p1.hod, "free-teachers", date="soon").status_code == 422
    assert rep(p1, p1.hod, "free-teachers", period="x").status_code == 422
    two = rep(p1, p1.hod, "free-teachers", date=fday().isoformat(), period=1, length=2).json()
    assert "P6R tc" in {t["name"] for t in two["rows"]} and "P6R tb" not in {t["name"] for t in two["rows"]}   # tb is busy at P2


# ====================================================================================================
# 5) subject hours progress
# ====================================================================================================
def test_hours_progress_is_per_subject_and_per_class_and_flags_the_short_ones(p1):
    h = rep(p1, p1.admin, "hours", school_id=p1.school).json()
    maths = row_of(h["rows"], "subject", "P6R Maths")
    assert maths["required"] == 200 and maths["shortfall"] == 200 - maths["deliverable"] > 0 and maths["state"] == "short" and maths["teachers"] == "P6R ta"
    assert maths["percent_planned"] == round(100 * maths["deliverable"] / 200) and maths["delivered"] <= maths["deliverable"]
    phys = row_of(h["rows"], "subject", "P6R Phys")
    assert phys["required"] == 10 and phys["state"] in ("on track", "short")
    mine = row_of(h["classes"], "class", "ZZ P6R Course - Sem 1")
    assert mine["required"] == 210 and mine["shortfall"] == maths["shortfall"] + phys["shortfall"] and mine["short_subjects"] >= 1
    assert mine["planned"] == mine["required"] - mine["shortfall"] and mine["percent_planned"] == round(100 * mine["planned"] / 210) < 100   # a surplus never hides a shortfall
    assert h["summary"]["planned"] == sum(c["planned"] for c in h["classes"]) and h["summary"]["percent_planned"] < 100
    assert h["summary"]["short_subjects"] >= 1 and h["summary"]["short_hours"] >= maths["shortfall"] and h["rows"][0]["shortfall"] == max(r["shortfall"] for r in h["rows"])
    only = rep(p1, p1.admin, "hours", school_id=p1.school, only_short="true").json()
    assert only["rows"] and all(r["shortfall"] > 0 for r in only["rows"]) and "P6R Maths" in {r["subject"] for r in only["rows"]}
    one = rep(p1, p1.admin, "hours", school_id=p1.school, class_id=S["sem"]).json()
    assert {r["class"] for r in one["rows"]} == {"ZZ P6R Course - Sem 1"} and len(one["classes"]) == 1
    assert rep(p1, p1.admin, "hours", school_id=p1.school, class_id="junk").json()["rows"] == []


# ====================================================================================================
# 6) cancelled sessions
# ====================================================================================================
def test_a_one_day_cancel_shows_in_the_cancelled_report_and_counts_as_lost(p1):
    before = row_of(rep(p1, p1.hod, "hours", class_id=S["sem"]).json()["rows"], "subject", "P6R Maths")
    d = first_weekday_of_month(WED)
    S["cancel_date"] = d
    r = p1.api(p1.hod, "POST", "/api/adjust/cancel", json={"semester_id": S["sem"], "date": d.isoformat(), "start_period": 1, "reason": "Unit test"})
    assert r.status_code == 201, r.text
    c = rep(p1, p1.admin, "cancelled", school_id=p1.school, month=d.strftime("%Y-%m")).json()
    assert c["summary"]["total"] == 1 and c["summary"]["month"] == d.strftime("%Y-%m")
    row = c["rows"][0]
    assert (row["date"], row["class"], row["subject"], row["period"], row["time"], row["teachers"], row["reason"]) == \
        (d.isoformat(), "ZZ P6R Course - Sem 1", "P6R Maths", 1, "10:00-11:00", "P6R ta", "Unit test")
    assert row["weekday"] == "Wednesday" and row["by"]                                      # who cancelled it is recorded
    assert c["summary"]["top_teachers"] == [{"name": "P6R ta", "count": 1}] and c["summary"]["top_classes"][0]["count"] == 1
    assert [x for x in c["per_day"] if x["count"]] == [{"day": d.day, "label": str(d.day), "count": 1}] and len(c["per_day"]) >= 28
    mine = row_of(c["lost"], "class", "ZZ P6R Course - Sem 1")
    assert mine["lost_hours"] >= 1 and mine["cancelled_by_hod"] == 1
    after = row_of(rep(p1, p1.hod, "hours", class_id=S["sem"]).json()["rows"], "subject", "P6R Maths")
    assert after["lost"] == before["lost"] + 1 and after["deliverable"] == before["deliverable"] - 1      # the cancelled lecture is a lost hour
    other_month = (d.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert rep(p1, p1.admin, "cancelled", school_id=p1.school, month=other_month).json()["rows"] == []
    assert rep(p1, p1.admin, "cancelled", school_id=p1.school, month="2026-13").status_code == 422
    assert rep(p1, p1.admin, "cancelled", school_id=p1.school, month="october").status_code == 422


# ====================================================================================================
# 7) monthly substitutions
# ====================================================================================================
def test_the_monthly_substitution_report_counts_absences_and_the_fill_rate(p1):
    m = S["sub_date"].strftime("%Y-%m")
    s = rep(p1, p1.admin, "substitutions", school_id=p1.school, month=m).json()
    sm = s["summary"]
    assert sm["month"] == m and sm["absences"] == 1 and sm["absent_teachers"] == 1 and sm["needs"] == 1 and sm["filled"] == 1
    assert sm["unfilled"] == 0 and sm["fill_rate"] == 100 and sm["filled_manual"] == 1 and sm["filled_auto"] == 0 and sm["avg_minutes_to_fill"] is not None
    ta, tb = row_of(s["rows"], "teacher", "P6R ta"), row_of(s["rows"], "teacher", "P6R tb")
    assert (ta["absent_days"], ta["lectures_needing_cover"], ta["subs_covered"]) == (1, 1, 0)
    assert (tb["subs_covered"], tb["periods_covered"], tb["absent_days"]) == (1, 1, 0)
    assert s["rows"][0]["teacher"] == "P6R tb"                                               # who covered the most comes first
    assert len(s["trend"]) == 6 and s["trend"][-1]["month"] == m and s["trend"][-1]["needs"] == 1 and s["trend"][-1]["filled"] == 1
    assert [t["month"] for t in s["trend"]] == sorted(t["month"] for t in s["trend"])
    empty = rep(p1, p1.admin, "substitutions", school_id=p1.school, month="2020-02").json()
    assert empty["summary"]["needs"] == 0 and empty["summary"]["fill_rate"] == 0 and empty["rows"] == [] and empty["summary"]["avg_minutes_to_fill"] is None
    assert rep(p1, p1.hod, "substitutions", month="2020-02").status_code == 200
    # an unfilled need lowers the rate
    d2 = S["sub_date"] + timedelta(days=7)
    if d2.strftime("%Y-%m") == m:
        p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["ta"], "date": d2.isoformat()})
        half = rep(p1, p1.admin, "substitutions", school_id=p1.school, month=m).json()["summary"]
        assert half["needs"] == 2 and half["filled"] == 1 and half["unfilled"] == 1 and half["fill_rate"] == 50


# ====================================================================================================
# 8) school vs school
# ====================================================================================================
def test_the_comparison_has_one_row_per_school_in_scope(p1):
    m = S["sub_date"].strftime("%Y-%m")
    c = rep(p1, p1.admin, "compare", school_id=p1.school, month=m).json()
    assert len(c["rows"]) == 1
    r = c["rows"][0]
    hours = rep(p1, p1.admin, "hours", school_id=p1.school).json()["summary"]
    wl = rep(p1, p1.admin, "workload", school_id=p1.school).json()["summary"]
    subs = rep(p1, p1.admin, "substitutions", school_id=p1.school, month=m).json()["summary"]
    assert r["school_id"] == p1.school and r["teachers"] == wl["teachers"] and r["avg_hours"] == wl["avg_hours"] and r["avg_pct"] == wl["avg_pct"]
    assert (r["under"], r["within"], r["over"]) == (wl["under"], wl["within"], wl["over"])
    assert r["short_subjects"] == hours["short_subjects"] and r["short_hours"] == hours["short_hours"]
    assert (r["sub_needs"], r["sub_filled"], r["fill_rate"]) == (subs["needs"], subs["filled"], subs["fill_rate"]) and r["absences"] == subs["absences"]
    assert r["classes"] >= 1 and r["published_classes"] >= 1 and r["pref_filled"] is None and r["pref_pct"] is None      # no window was ever opened here
    allc = rep(p1, p1.admin, "compare", month=m).json()
    assert len(allc["rows"]) >= 2 and p1.school in {x["school_id"] for x in allc["rows"]}
    assert [x["school"] for x in allc["rows"]] == sorted(x["school"] for x in allc["rows"])
    mine = rep(p1, p1.hod, "compare", month=m).json()
    assert [x["school_id"] for x in mine["rows"]] == [p1.school]                              # an HOD "compares" only his own school


def test_preference_progress_appears_in_the_comparison_once_a_window_exists(p1):
    assert p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"deadline": (datetime.now(vw.IST) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")}).status_code == 201
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": []}).status_code == 200
    r = rep(p1, p1.admin, "compare", school_id=p1.school).json()["rows"][0]
    total = db_val("select count(*) from public.profiles where school_id = $1 and is_active and role in ('teacher','hod')", uuid.UUID(p1.school))
    assert r["pref_filled"] == 1 and r["pref_pct"] == round(100 / total)
    p1.api(p1.hod, "POST", "/api/prefs/window/close", json={})


# ====================================================================================================
# 9) Excel
# ====================================================================================================
def xlsx(p1, who, name: str, **params):
    r = p1.api(who, "GET", f"/api/reports/export/{name}", params=params)
    assert r.status_code == 200, (name, r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert ".xlsx" in r.headers["content-disposition"] and "Report" in r.headers["content-disposition"]
    return load_workbook(io.BytesIO(r.content))


def table_of(ws, first_label: str) -> tuple[int, list[str], list[list]]:
    """(header row, header labels, data rows) of the first table on a sheet."""
    for r in range(1, 40):
        if ws.cell(r, 1).value == first_label:
            labels = [ws.cell(r, c).value for c in range(1, ws.max_column + 1) if ws.cell(r, c).value is not None]
            rows = []
            for rr in range(r + 1, ws.max_row + 1):
                if ws.cell(rr, 1).value in (None, ""):
                    break
                rows.append([ws.cell(rr, c).value for c in range(1, len(labels) + 1)])
            return r, labels, rows
    raise AssertionError(f"no table starting with {first_label!r}")


def test_every_report_downloads_as_a_real_excel_file_with_the_same_numbers(p1):
    m = S["sub_date"].strftime("%Y-%m")
    for name, params in (("workload", {}), ("rankings", {"limit": 5}), ("free-teachers", {"date": fday().isoformat(), "period": 1}), ("hours", {}),
                         ("cancelled", {"month": S["cancel_date"].strftime("%Y-%m")}), ("substitutions", {"month": m}), ("compare", {"month": m})):
        json_ = rep(p1, p1.admin, name, school_id=p1.school, **params).json()
        wb = xlsx(p1, p1.admin, name, school_id=p1.school, **params)
        ws = wb.worksheets[0]
        first = json_["columns"][0]["label"]
        assert ws.cell(3, 1).value == json_["title"], name                                    # the title band
        _row, labels, data = table_of(ws, first)
        assert labels == [c["label"] for c in json_["columns"]], name
        assert len(data) == len(json_["rows"]), (name, len(data), len(json_["rows"]))
        assert len(wb.worksheets) == 1 + len(json_.get("sections", [])), name                 # one extra sheet per extra table
        assert ws.cell(1, 1).value                                                            # the college name


def test_the_workload_excel_has_the_right_values_formats_and_print_setup(p1):
    ws = xlsx(p1, p1.admin, "workload", school_id=p1.school).worksheets[0]
    header, labels, data = table_of(ws, "Teacher")
    ta = next(r for r in data if r[0] == "P6R ta")
    assert ta[labels.index("Hours / week")] == 3.0 and ta[labels.index("Lectures")] == 3 and ta[labels.index("Status")] == "under"
    assert ta[labels.index("% of norm")] == 19
    assert ws.auto_filter.ref and ws.freeze_panes and ws.page_setup.orientation == "landscape" and ws.sheet_properties.pageSetUpPr.fitToPage
    summary = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(5, header)}
    assert summary.get("Teachers") and summary.get("Norm") and "16 h/week" in str(summary["Norm"])
    # a status cell is coloured
    status_col = labels.index("Status") + 1
    ta_row = header + 1 + [r[0] for r in data].index("P6R ta")
    assert ws.cell(ta_row, status_col).fill.start_color.rgb.endswith("FEF3C7")                # 'under' = amber
    wb = xlsx(p1, p1.admin, "rankings", school_id=p1.school)
    assert [ws.title for ws in wb.worksheets] == ["Busiest", "Most free", "Substitutes this month"]
    assert xlsx(p1, p1.admin, "workload").worksheets[0].cell(3, 1).value == "Faculty workload vs norm"     # all schools
    assert p1.api(p1.admin, "GET", "/api/reports/export/nonsense").status_code == 404


def test_audit_rows_exist_for_the_norm_changes(p1):
    rows = db_fetch("select action, summary from public.audit_log where entity = 'workload_norm' order by id")
    assert {r["action"] for r in rows} >= {"update", "delete"} and any("College" in r["summary"] or "college" in r["summary"] for r in rows)
