"""Phase 4 API tests: weekly-lectures override, publish (+ versions, audit, diff), the views, exports, notifications (one per person
per event, only the people whose own lectures changed), the live stream, Web Push registration, semester hours and holiday
compensation - in a throw-away school against the REAL API + Supabase (the seeded School of IT is never touched).  Tests build on each other.
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
from datetime import timedelta

import pytest

from app.services import views as vw

from .conftest import bearer, db_exec, db_fetch, db_val, mail_for, new_client

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5


def clean_slate(p1) -> None:
    """The test school and the test people are shared by every test file of one run: start from a school with NO drafts, NO published
    timetable and NO holidays, so the counts below only ever talk about this file's own classes (the real holiday list is put back at the end)."""
    school = uuid.UUID(p1.school)
    for sql in (
        "delete from public.extra_lectures where school_id = $1", "delete from public.publish_events where school_id = $1",
        "delete from public.publications where school_id = $1", "delete from public.timetable_drafts where school_id = $1",
        "delete from public.generation_runs where school_id = $1", "delete from public.calendar_entries where school_id = $1 or is_preloaded",
    ):
        db_exec(sql, school)
    people = [uuid.UUID(x.account.user_id) for x in (p1.admin, p1.hod, p1.teacher, p1.teacher_b)]
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", people)
    db_exec("delete from public.push_subscriptions where user_id = any($1::uuid[])", people)


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    clean_slate(p1)
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ P4 Course', 'ZZP4', 5) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 5) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in sems}
    S["term"] = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    today = vw.today_ist()
    S["monday"] = vw.monday_of(today)
    S["today"] = today
    for n in (1, 3):  # semester dates: 2 weeks ago .. 10 weeks ahead
        db_exec(
            "insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date) values ($1,$2,$3,$4,$5)",
            uuid.UUID(S["term"]), uuid.UUID(S["sem"][n]), uuid.UUID(p1.school), S["monday"] - timedelta(days=14), S["monday"] + timedelta(days=69),
        )
    yield


def cls(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str) -> str:
    r = p1.api(p1.hod, "POST", "/api/users", json={"full_name": f"P4 {key}", "email": mail_for(f"p4-{key}"), "subject_ids": []})
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def teach(p1, subject_id: str, teacher_id: str) -> None:
    db_exec("insert into public.teacher_subjects (teacher_id, subject_id, school_id) values ($1,$2,$3) on conflict do nothing",
            uuid.UUID(teacher_id), uuid.UUID(subject_id), uuid.UUID(p1.school))
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id})
    assert r.status_code == 200, r.text


def mk_draft(p1, n: int, name: str) -> str:
    r = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(n), "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def place(p1, draft: str, day: int, start: int, **body):
    return p1.api(p1.hod, "POST", f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, **body})


def notes_of(user_id: str, kind: str | None = None) -> list[dict]:
    sql = "select kind, title, body, link, is_read, dedupe_key from public.notifications where user_id = $1"
    args: list = [uuid.UUID(user_id)]
    if kind:
        sql += " and kind = $2"
        args.append(kind)
    return db_fetch(sql + " order by created_at", *args)


def week(p1, who, monday=None) -> dict:
    r = p1.api(who, "GET", f"/api/views/my/week?week_of={monday or S['monday']}")
    assert r.status_code == 200, r.text
    return r.json()


def titles(view: dict) -> list[str]:
    return sorted(i["title"] for c in view["cells"] for i in c["items"])


# ====================================================================================================
# 0) the weekly-lectures override (API side)
# ====================================================================================================
def test_weekly_lectures_default_to_credits_and_can_be_overridden_and_cleared(p1):
    sid = mk_subject(p1, 1, "P4-W1", "Weekly One", credits=4)
    row = next(s for s in p1.api(p1.hod, "GET", f"/api/subjects?semester_id={cls(1)}").json()["subjects"] if s["id"] == sid)
    assert row["credits"] == 4 and row["weekly_lectures"] is None and row["weekly_effective"] == 4
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{sid}", json={"weekly_lectures": 3}).status_code == 200
    row = next(s for s in p1.api(p1.hod, "GET", f"/api/subjects?semester_id={cls(1)}").json()["subjects"] if s["id"] == sid)
    assert row["credits"] == 4 and row["weekly_lectures"] == 3 and row["weekly_effective"] == 3        # credits stay as they were
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{sid}", json={"name": "Weekly Renamed"}).status_code == 200
    row = next(s for s in p1.api(p1.hod, "GET", f"/api/subjects?semester_id={cls(1)}").json()["subjects"] if s["id"] == sid)
    assert row["weekly_lectures"] == 3                                                                   # an edit of something else keeps it
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{sid}", json={"weekly_lectures": None}).status_code == 200
    row = next(s for s in p1.api(p1.hod, "GET", f"/api/subjects?semester_id={cls(1)}").json()["subjects"] if s["id"] == sid)
    assert row["weekly_lectures"] is None and row["weekly_effective"] == 4
    for bad in (0, 31, -2):
        assert p1.api(p1.hod, "PATCH", f"/api/subjects/{sid}", json={"weekly_lectures": bad}).status_code == 422
    assert p1.api(p1.hod, "POST", "/api/subjects", json={"semester_id": cls(1), "name": "X", "code": "P4-X", "credits": 2, "semester_hours": 30, "weekly_lectures": 0}).status_code == 422
    S["w1"] = sid


def test_the_draft_editor_counts_against_the_override_not_the_credits(p1):
    d = mk_draft(p1, 1, "weekly check")
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['w1']}", json={"weekly_lectures": 2}).status_code == 200
    det = p1.api(p1.hod, "GET", f"/api/drafts/{d}").json()
    sub = next(s for s in det["subjects"] if s["id"] == S["w1"])
    assert sub["credits"] == 4 and sub["weekly_lectures"] == 2 and sub["required"] == 2
    assert place(p1, d, MON, 1, subject_id=S["w1"]).status_code == 201
    assert place(p1, d, TUE, 1, subject_id=S["w1"]).status_code == 201
    det = p1.api(p1.hod, "GET", f"/api/drafts/{d}").json()
    assert next(s for s in det["subjects"] if s["id"] == S["w1"])["status"] == "complete"                # 2 of 2, though credits = 4
    over = place(p1, d, WED, 1, subject_id=S["w1"])
    assert over.status_code == 201 and any("already has all its 2" in w for w in over.json().get("warnings", []) + sum([p["warnings"] for p in over.json()["detail"]["placements"]], []))
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{d}").status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{S['w1']}").status_code == 200


def test_the_teacher_load_counts_weekly_lectures(p1):
    sid = mk_subject(p1, 1, "P4-L1", "Load One", credits=5, weekly_lectures=2)
    t = mk_teacher(p1, "load")
    teach(p1, sid, t)
    mine = next(x for x in p1.api(p1.hod, "GET", "/api/assignments").json()["teachers"] if x["id"] == t)
    assert mine["weekly_load"] == 2                                                              # the override (2), not the credits (5)
    db_exec("delete from public.assignments where subject_id = $1", uuid.UUID(sid))
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{sid}").status_code == 200


# ====================================================================================================
# 1) publish
# ====================================================================================================
def test_set_up_two_classes_with_drafts(p1):
    t1 = p1.teacher.account.user_id
    S["t1"], S["t2"], S["t3"] = t1, mk_teacher(p1, "t2"), mk_teacher(p1, "t3")
    S["a_math"] = mk_subject(p1, 1, "P4-AM", "A Maths", credits=2)
    S["a_phys"] = mk_subject(p1, 1, "P4-AP", "A Physics", credits=1)
    S["b_chem"] = mk_subject(p1, 3, "P4-BC", "B Chem", credits=1)
    S["b_bio"] = mk_subject(p1, 3, "P4-BB", "B Bio", credits=1)
    teach(p1, S["a_math"], t1)
    teach(p1, S["a_phys"], S["t2"])
    teach(p1, S["b_chem"], t1)
    teach(p1, S["b_bio"], S["t3"])
    S["da"], S["db"] = mk_draft(p1, 1, "Plan A"), mk_draft(p1, 3, "Plan B")
    for day, start in ((MON, 1), (WED, 1)):
        assert place(p1, S["da"], day, start, subject_id=S["a_math"]).status_code == 201
    assert place(p1, S["da"], TUE, 2, subject_id=S["a_phys"]).status_code == 201
    assert place(p1, S["db"], THU, 1, subject_id=S["b_chem"]).status_code == 201
    assert place(p1, S["db"], FRI, 3, subject_id=S["b_bio"]).status_code == 201


def test_nothing_is_visible_to_teachers_before_publishing(p1):
    v = week(p1, p1.teacher)
    assert v["published"] is False and v["cells"] == [] and not v.get("publications")
    today = p1.api(p1.teacher, "GET", "/api/views/my/today").json()
    assert today["published"] is False and today["cells"] == []
    assert p1.api(p1.teacher, "GET", f"/api/drafts/{S['da']}").status_code == 403
    assert p1.api(p1.teacher, "GET", "/api/drafts").status_code == 403
    assert p1.api(p1.teacher, "GET", "/api/publish/status").status_code == 403
    assert p1.api(p1.teacher, "POST", "/api/publish", json={"scope": "all"}).status_code == 403
    assert p1.api(p1.teacher, "GET", f"/api/exports/class/{cls(1)}").status_code == 403
    assert p1.api(p1.teacher, "GET", "/api/exports/my").status_code == 409                   # nothing published to export yet
    assert notes_of(S["t1"]) == []


def test_status_shows_every_class_with_its_primary_draft_and_no_blockers(p1):
    st = p1.api(p1.hod, "GET", "/api/publish/status").json()
    by = {c["semester_id"]: c for c in st["classes"]}
    for n in (1, 3):
        c = by[cls(n)]
        assert c["state"] == "not_published" and c["primary"]["name"] == ("Plan A" if n == 1 else "Plan B") and c["can_publish"] and c["blockers"] == []
        assert c["changes"]["added_count"] > 0
    empty = by[cls(5)]
    assert empty["state"] == "no_draft" and empty["can_publish"] is False and empty["primary"] is None
    assert st["summary"]["unpublished"] == 2 and st["summary"]["can_publish"] == 2


def test_publish_all_makes_the_primary_drafts_live_and_notifies_each_teacher_once(p1):
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "all"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert {p["class_label"]: p["version"] for p in res["published"]} == {"ZZ P4 Course - Sem 1": 1, "ZZ P4 Course - Sem 3": 1}
    assert res["notified"] == 3 and res["teachers_changed"] == 3                       # t1 teaches in BOTH classes but is told once
    assert any(s["class_label"].endswith("Sem 5") for s in res["skipped"])              # the class without a draft is reported, not an error
    for t, n in ((S["t1"], 1), (S["t2"], 1), (S["t3"], 1)):
        got = notes_of(t, "timetable_published")
        assert len(got) == 1 and got[0]["link"] == "/my/timetable" and got[0]["is_read"] is False, (t, got)
    t1_note = notes_of(S["t1"])[0]
    assert "3 lectures a week" in t1_note["body"] or "3 lectures" in t1_note["body"]
    assert db_val("select count(*) from public.publish_events where school_id = $1", uuid.UUID(p1.school)) == 1
    assert db_val("select count(*) from public.audit_log where entity = 'publication' and school_id = $1", uuid.UUID(p1.school)) == 2
    S["event"] = res["event"]["id"]


def test_a_teacher_now_sees_exactly_their_published_lectures(p1):
    v = week(p1, p1.teacher)
    assert v["published"] is True and titles(v) == ["A Maths", "A Maths", "B Chem"]
    assert v["stats"]["lectures"] == 3 and len(v["publications"]) == 2
    cell = next(c for c in v["cells"] if c["items"][0]["title"] == "B Chem")
    assert cell["day"] == THU and cell["start_period"] == 1 and cell["time"] == "10:00-11:00" and cell["classes"][0]["label"] == "ZZ P4 Course - Sem 3"
    assert cell["date"] == (S["monday"] + timedelta(days=3)).isoformat() and cell["status"] is None
    # another teacher sees only their own lecture, and a teacher of another school sees nothing
    assert titles(week(p1, p1.hod)) == []                                              # the HOD teaches nothing in this test
    other = week(p1, p1.teacher_b)
    assert other["published"] is False and other["cells"] == []


def test_today_shows_only_todays_lectures_and_handles_non_working_days(p1):
    monday = S["monday"]
    mon = p1.api(p1.teacher, "GET", f"/api/views/my/today?on={monday}").json()
    assert mon["weekday_name"] == "Monday" and mon["is_working_day"] and [c["items"][0]["title"] for c in mon["cells"]] == ["A Maths"]
    thu = p1.api(p1.teacher, "GET", f"/api/views/my/today?on={monday + timedelta(days=3)}").json()
    assert [c["items"][0]["title"] for c in thu["cells"]] == ["B Chem"] and thu["stats"] == {"lectures": 1, "hours": 1, "cancelled": 0, "extra": 0}
    sat = p1.api(p1.teacher, "GET", f"/api/views/my/today?on={monday + timedelta(days=5)}").json()
    assert sat["is_working_day"] is False and sat["cells"] == []                       # a 5-day college: Saturday is a day off
    sun = p1.api(p1.teacher, "GET", f"/api/views/my/today?on={monday + timedelta(days=6)}").json()
    assert sun["weekday_name"] == "Sunday" and sun["is_working_day"] is False and sun["cells"] == []
    assert p1.api(p1.teacher, "GET", "/api/views/my/today?on=not-a-date").status_code == 422
    assert p1.api(p1.teacher, "GET", "/api/views/my/today?on=9999-12-31").status_code == 422        # absurd dates are refused, not crashed on
    assert p1.api(p1.teacher, "GET", "/api/views/my/week?week_of=1900-01-01").status_code == 422


def test_a_holiday_is_shown_on_the_day_and_cancels_that_lecture(p1):
    d = S["monday"] + timedelta(days=7)                                                # next Monday
    r = p1.api(p1.hod, "POST", "/api/calendar", json={"scope": "school", "kind": "holiday", "title": "ZZ P4 Holiday", "date_from": d.isoformat()})
    assert r.status_code == 201, r.text
    S["hol"] = r.json()["id"]
    today = p1.api(p1.teacher, "GET", f"/api/views/my/today?on={d}").json()
    assert today["banners"][0]["title"] == "ZZ P4 Holiday" and today["cells"][0]["status"] == "holiday" and today["stats"]["cancelled"] == 1
    wk = week(p1, p1.teacher, d)
    assert wk["stats"]["cancelled"] == 1 and wk["days"][0]["banners"][0]["title"] == "ZZ P4 Holiday"
    assert p1.api(p1.hod, "DELETE", f"/api/calendar/{S['hol']}").status_code == 200


def test_edits_to_a_draft_change_nothing_for_teachers_until_it_is_published(p1):
    det = p1.api(p1.hod, "GET", f"/api/drafts/{S['da']}").json()
    phys = next(p for p in det["placements"] if p["entries"][0]["subject_name"] == "A Physics")
    r = p1.api(p1.hod, "PATCH", f"/api/drafts/{S['da']}/placements/{phys['key']}", json={"day": TUE, "start_period": 3})
    assert r.status_code == 200, r.text
    assert titles(week(p1, p1.teacher)) == ["A Maths", "A Maths", "B Chem"]                        # teacher: unchanged
    st = {c["semester_id"]: c for c in p1.api(p1.hod, "GET", "/api/publish/status").json()["classes"]}
    assert st[cls(1)]["state"] == "changed" and st[cls(3)]["state"] == "in_sync"
    ch = st[cls(1)]["changes"]
    assert ch["added_count"] == 1 and ch["removed_count"] == 1 and "A Physics" in ch["added"][0] and "A Physics" in ch["removed"][0]
    ov = p1.api(p1.hod, "GET", "/api/hod/overview").json()
    assert ov["publish"]["published"] == 2 and ov["publish"]["with_primary"] == 2 and ov["publish"]["classes"] >= 3


def test_republishing_raises_the_version_and_notifies_only_the_teacher_whose_lecture_moved(p1):
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "all"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert [(p["class_label"], p["version"]) for p in res["published"]] == [("ZZ P4 Course - Sem 1", 2)]   # class 3 was up to date: no new version
    assert res["notified"] == 1 and res["teachers_changed"] == 1
    t2 = notes_of(S["t2"])
    assert [n["kind"] for n in t2] == ["timetable_published", "timetable_changed"]
    assert "1 moved" in t2[1]["body"] and "A Physics" in t2[1]["body"]
    assert len(notes_of(S["t1"])) == 1 and len(notes_of(S["t3"])) == 1                 # not told: nothing of theirs changed
    # one notification per person per publish EVENT
    events = db_fetch("select dedupe_key, user_id from public.notifications where kind like 'timetable_%'")
    keys = [(e["dedupe_key"], str(e["user_id"])) for e in events]
    assert len(keys) == len(set(keys))
    assert db_val("select version from public.publications where semester_id = $1", uuid.UUID(cls(1))) == 2
    assert db_val("select count(*) from public.audit_log where entity = 'publication' and school_id = $1", uuid.UUID(p1.school)) == 3
    hist = p1.api(p1.hod, "GET", "/api/publish/events").json()["events"]
    assert [e["number"] for e in hist] == [2, 1] and hist[0]["notified"] == 1
    st = {c["semester_id"]: c for c in p1.api(p1.hod, "GET", "/api/publish/status").json()["classes"]}
    assert st[cls(1)]["state"] == "in_sync" and st[cls(1)]["published"]["version"] == 2
    again = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "all"})
    assert again.status_code == 409 and again.json()["code"] == "NOTHING_TO_PUBLISH"


def test_a_clash_in_the_primary_draft_blocks_publishing_and_changes_nothing(p1):
    # t1 is placed in class 3 at Thursday P1 (published). Put t1's class-1 lecture at the same time in the draft of class 1 -> refused by the draft engine,
    # so build the clash through an alternative route: swap the teacher of A Physics to t1 and place it at Thu P1.
    before = db_val("select version from public.publications where semester_id = $1", uuid.UUID(cls(1)))
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["a_phys"], "teacher_id": S["t1"]}).status_code in (200, 422)
    db_exec("insert into public.teacher_subjects (teacher_id, subject_id, school_id) values ($1,$2,$3) on conflict do nothing", uuid.UUID(S["t1"]), uuid.UUID(S["a_phys"]), uuid.UUID(p1.school))
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["a_phys"], "teacher_id": S["t1"]}).status_code == 200
    det = p1.api(p1.hod, "GET", f"/api/drafts/{S['da']}").json()
    phys = next(p for p in det["placements"] if p["entries"][0]["subject_name"] == "A Physics")
    # t1 teaches B Chem on Thursday P1 (primary draft of class 3): the engine refuses a direct move there ...
    refused = p1.api(p1.hod, "PATCH", f"/api/drafts/{S['da']}/placements/{phys['key']}", json={"day": THU, "start_period": 1})
    assert refused.status_code == 422
    # ... but the assignment change alone makes the PUBLISHED state differ: publishing class 1 gives t1 Tue P3 (free) - fine, then
    # a direct DB edit simulates a clash that slipped in (e.g. data changed behind the screens)
    db_exec("update public.draft_entries set day = $2, start_period = 1 where id = $1", uuid.UUID(phys["entries"][0]["id"]), THU)
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(1)})
    assert r.status_code == 409 and r.json()["code"] == "PUBLISH_BLOCKED"
    assert "Thursday" in r.json()["message"] and "already teaches" in r.json()["message"]
    assert db_val("select version from public.publications where semester_id = $1", uuid.UUID(cls(1))) == before          # nothing was published
    assert titles(week(p1, p1.teacher)).count("A Physics") == 0
    # repair for the next tests
    db_exec("update public.draft_entries set day = $2, start_period = 3 where id = $1", uuid.UUID(phys["entries"][0]["id"]), TUE)
    p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["a_phys"], "teacher_id": S["t2"]})


def test_the_hod_is_locked_to_the_own_school_when_publishing(p1):
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "all", "school_id": p1.school_b})
    assert r.status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/publish/status?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/views/master?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": p1.b_sems[1]}).status_code == 404       # a class of the other school
    ok = p1.api(p1.admin, "GET", f"/api/publish/status?school_id={p1.school}")
    assert ok.status_code == 200 and len(ok.json()["classes"]) >= 3


# ====================================================================================================
# 2) the views for HOD / admin
# ====================================================================================================
def test_class_teacher_and_master_views_with_filters(p1):
    cv = p1.api(p1.hod, "GET", f"/api/views/class/{cls(1)}?week_of={S['monday']}").json()
    assert titles(cv) == ["A Maths", "A Maths", "A Physics"] and cv["owner"]["name"] == "ZZ P4 Course - Sem 1" and cv["off_days"][cls(1)]["1"]["is_off"] is False
    tv = p1.api(p1.hod, "GET", f"/api/views/teacher/{S['t1']}?week_of={S['monday']}").json()
    assert titles(tv) == ["A Maths", "A Maths", "B Chem"] and tv["owner"]["id"] == S["t1"]
    m = p1.api(p1.hod, "GET", f"/api/views/master?scope=week&week_of={S['monday']}").json()
    assert m["stats"]["lectures"] == 5 and {c["label"] for c in m["classes"]} >= {"ZZ P4 Course - Sem 1", "ZZ P4 Course - Sem 3"}
    f = p1.api(p1.hod, "GET", f"/api/views/master?scope=week&week_of={S['monday']}&semester_id={cls(3)}").json()
    assert titles(f) == ["B Chem", "B Bio"] or sorted(titles(f)) == ["B Bio", "B Chem"]
    f = p1.api(p1.hod, "GET", f"/api/views/master?scope=week&week_of={S['monday']}&teacher_id={S['t2']}").json()
    assert titles(f) == ["A Physics"]
    f = p1.api(p1.hod, "GET", f"/api/views/master?scope=week&week_of={S['monday']}&day={MON}").json()
    assert titles(f) == ["A Maths"] and f["day"] == MON
    t = p1.api(p1.hod, "GET", f"/api/views/master?scope=today&on={S['monday']}").json()
    assert t["scope"] == "today" and t["date"] == S["monday"].isoformat() and titles(t) == ["A Maths"]
    assert p1.api(p1.hod, "GET", "/api/views/master?day=7").status_code == 422
    opts = p1.api(p1.hod, "GET", "/api/views/options").json()
    assert {c["label"]: c["published"] for c in opts["classes"]}["ZZ P4 Course - Sem 1"] is True
    assert next(x for x in opts["teachers"] if x["id"] == S["t1"])["lectures"] == 3


def test_views_are_locked_to_the_school_and_to_the_role(p1):
    assert p1.api(p1.hod, "GET", f"/api/views/class/{p1.b_sems[1]}").status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/views/teacher/{p1.teacher_b.account.user_id}").status_code == 404
    assert p1.api(p1.admin, "GET", f"/api/views/class/{cls(1)}").status_code == 200
    assert p1.api(p1.admin, "GET", f"/api/views/master?school_id={p1.school}").status_code == 200
    assert p1.api(p1.admin, "GET", "/api/views/master").status_code == 422                          # the admin must name a school
    for path in (f"/api/views/class/{cls(1)}", f"/api/views/teacher/{S['t1']}", "/api/views/master", "/api/views/options"):
        assert p1.api(p1.teacher, "GET", path).status_code == 403
    assert p1.api(p1.admin, "GET", "/api/views/my/week").status_code == 403                         # the admin is not a teacher


# ====================================================================================================
# 3) PDF / Excel exports
# ====================================================================================================
def test_exports_are_real_pdf_and_excel_files_for_the_right_people(p1):
    from openpyxl import load_workbook
    from pypdf import PdfReader

    for kind, path in (("class", f"/api/exports/class/{cls(1)}"), ("teacher", f"/api/exports/teacher/{S['t1']}"), ("master", "/api/exports/master")):
        r = p1.api(p1.hod, "GET", f"{path}?format=pdf")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-", (kind, r.status_code)
        assert f'filename="Timetable_' in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.pdf"')
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        assert "School of IT" in text or "ZZ Test School" in text
        x = p1.api(p1.hod, "GET", f"{path}?format=xlsx")
        assert x.status_code == 200 and "spreadsheetml" in x.headers["content-type"]
        wb = load_workbook(io.BytesIO(x.content))
        assert wb.sheetnames
    cls_text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(p1.api(p1.hod, "GET", f"/api/exports/class/{cls(1)}?format=pdf").content)).pages)
    assert "ZZ P4 Course - Sem 1" in cls_text and "A Maths" in cls_text and "LUNCH" in cls_text and "break" in cls_text
    assert "B Chem" not in cls_text                                                                  # only that class
    mine = p1.api(p1.teacher, "GET", "/api/exports/my?format=pdf")
    assert mine.status_code == 200 and "A Maths" in "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(mine.content)).pages)
    assert p1.api(p1.teacher, "GET", "/api/exports/my?format=xlsx").status_code == 200
    assert p1.api(p1.hod, "GET", f"/api/exports/class/{cls(1)}?format=doc").status_code == 422
    assert p1.api(p1.hod, "GET", f"/api/exports/class/{p1.b_sems[1]}").status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/exports/class/{cls(5)}").status_code == 409                  # not published
    f = p1.api(p1.hod, "GET", f"/api/exports/master?format=pdf&semester_id={cls(3)}&day={THU}")
    assert f.status_code == 200 and "B Chem" in "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(f.content)).pages)


# ====================================================================================================
# 4) notifications: list, unread, read, live stream, push registration
# ====================================================================================================
def test_the_bell_lists_only_my_notifications_and_marks_them_read(p1):
    r = p1.api(p1.teacher, "GET", "/api/notifications").json()
    assert r["unread"] == 1 and [n["kind"] for n in r["notifications"]] == ["timetable_published"]
    assert p1.api(p1.teacher, "GET", "/api/notifications/unread-count").json() == {"unread": 1}
    t2_ids = [str(x) for x in db_fetch("select id from public.notifications where user_id = $1", uuid.UUID(S["t2"]))]
    # marking somebody else's notification does nothing
    other = db_val("select id from public.notifications where user_id = $1 limit 1", uuid.UUID(S["t2"]))
    assert p1.api(p1.teacher, "POST", "/api/notifications/read", json={"ids": [str(other)]}).json()["unread"] == 1
    assert db_val("select is_read from public.notifications where id = $1", other) is False
    nid = r["notifications"][0]["id"]
    assert p1.api(p1.teacher, "POST", "/api/notifications/read", json={"ids": [nid]}).json() == {"unread": 0}
    assert p1.api(p1.teacher, "GET", "/api/notifications?unread_only=true").json()["notifications"] == []
    assert p1.api(p1.teacher, "POST", "/api/notifications/read", json={"all": True}).json() == {"unread": 0}
    assert p1.api(p1.teacher, "POST", "/api/notifications/read", json={"ids": ["not-an-id"]}).status_code == 422
    assert t2_ids                                                                                    # (they still exist)
    page = p1.api(p1.teacher, "GET", "/api/notifications?limit=1").json()
    assert len(page["notifications"]) == 1 and page["has_more"] is False


def test_the_live_stream_delivers_a_new_notification_at_once(p1):
    got: list[str] = []
    started = threading.Event()

    def listen():
        with new_client() as c:
            with c.stream("GET", "/api/notifications/stream", headers=bearer(p1.teacher.token)) as r:
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
                started.set()
                for line in r.iter_lines():
                    if line.startswith("data:") and "title" in line:
                        got.append(line)
                        return

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    assert started.wait(15)
    time.sleep(0.5)
    t0 = time.time()
    assert p1.api(p1.teacher, "POST", "/api/push/test").json()["sent"] == 1
    th.join(10)
    assert got and "Test notification" in got[0] and time.time() - t0 < 5
    assert json.loads(got[0][5:])["kind"] == "test"
    # nobody else hears it
    assert db_val("select count(*) from public.notifications where kind = 'test' and user_id <> $1", uuid.UUID(S["t1"])) == 0
    with new_client() as c:
        assert c.get("/api/notifications/stream").status_code == 401


def test_web_push_registration_is_checked_and_per_person(p1):
    cfg = p1.api(p1.teacher, "GET", "/api/push/config").json()
    assert cfg["enabled"] is True and len(cfg["public_key"]) == 87 and cfg["devices"] == 0
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/zz-p4-test-endpoint", "keys": {"p256dh": "B" * 87, "auth": "A" * 22}}
    assert p1.api(p1.teacher, "POST", "/api/push/subscribe", json=good).json() == {"subscribed": True, "devices": 1}
    assert p1.api(p1.teacher, "POST", "/api/push/subscribe", json=good).json()["devices"] == 1       # the same browser twice = still one
    for bad in ("http://fcm.googleapis.com/x-12345", "https://evil.example/push-endpoint", "http://127.0.0.1:8000/api/health", "https://169.254.169.254/latest"):
        r = p1.api(p1.teacher, "POST", "/api/push/subscribe", json={**good, "endpoint": bad})
        assert r.status_code == 422 and r.json()["code"] == "BAD_ENDPOINT", bad
    assert p1.api(p1.teacher, "POST", "/api/push/subscribe", json={"endpoint": good["endpoint"], "keys": {"p256dh": "x", "auth": "y"}}).status_code == 422
    assert p1.api(p1.hod, "GET", "/api/push/config").json()["devices"] == 0                          # my browsers are not the HOD's
    # the keys are secrets: no browser can read the table
    assert db_val("select count(*) from public.push_subscriptions where user_id = $1", uuid.UUID(S["t1"])) == 1
    assert p1.api(p1.teacher, "POST", "/api/push/unsubscribe", json={"endpoint": good["endpoint"]}).json() == {"subscribed": False, "devices": 0}
    assert p1.api(p1.teacher, "GET", "/api/push/config").json()["devices"] == 0


# ====================================================================================================
# 5) semester hours + holiday compensation
# ====================================================================================================
def hours_row(p1, subject_id: str, who=None, n: int = 1) -> dict:
    d = p1.api(who or p1.hod, "GET", f"/api/hours/subjects?semester_id={cls(n)}").json()
    return next(s for c in d["classes"] for s in c["subjects"] if s["subject_id"] == subject_id)


def test_hours_are_the_published_weekly_timetable_times_the_weeks(p1):
    h = hours_row(p1, S["a_math"])
    assert h["weekly"] == 2 and h["required"] == 30 and h["status"] in ("ok", "short")
    expected_weeks = 12                                                                                # 2 weeks ago .. 10 weeks ahead = 12 full weeks
    assert h["scheduled"] == 2 * expected_weeks and h["lost"] == 0 and h["shortfall"] == 30 - 24
    assert h["delivered"] == 2 * 2 + sum(1 for k in (0, 2) if S["monday"] + timedelta(days=k) <= S["today"])      # two past weeks + this week so far
    # an unpublished class is said so, not miscounted
    d = p1.api(p1.hod, "GET", f"/api/hours/subjects?semester_id={cls(3)}").json()
    assert all(s["status"] in ("ok", "short") for s in d["classes"][0]["subjects"])
    me = p1.api(p1.teacher, "GET", "/api/hours/my").json()
    assert {s["name"] for s in me["subjects"]} == {"A Maths", "B Chem"} and me["summary"]["short_subjects"] >= 1
    assert p1.api(p1.hod, "GET", "/api/hours/summary").json()["schools"][0]["summary"]["subjects"] >= 4
    assert p1.api(p1.teacher, "GET", "/api/hours/subjects").status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/hours/subjects?school_id={p1.school_b}").status_code == 403


def test_a_new_holiday_shows_up_as_a_bigger_shortfall_at_once(p1):
    before = hours_row(p1, S["a_math"])
    start = S["monday"] + timedelta(days=21)
    r = p1.api(p1.hod, "POST", "/api/calendar", json={
        "scope": "school", "kind": "holiday", "title": "ZZ P4 Break", "date_from": start.isoformat(), "date_to": (start + timedelta(days=6)).isoformat(),
        "semester_ids": [cls(1)],
    })
    assert r.status_code == 201, r.text
    S["break"] = r.json()["id"]
    after = hours_row(p1, S["a_math"])
    assert after["lost"] == before["lost"] + 2 and after["shortfall"] == before["shortfall"] + 2
    assert hours_row(p1, S["b_chem"], p1.hod, 3)["lost"] == 0                                             # the other class is not affected
    assert any(w["subject_id"] == S["a_math"] for w in p1.api(p1.hod, "GET", "/api/hours/summary").json()["schools"][0]["summary"]["worst"])


def test_suggested_extra_slots_are_clash_free(p1):
    sug = p1.api(p1.hod, "POST", "/api/hours/suggest", json={"subject_id": S["a_math"]}).json()
    assert sug["needed"] == hours_row(p1, S["a_math"])["extra_lectures_needed"] and sug["suggestions"], sug["message"]
    t1_week_by_day = {}
    monday = S["monday"]
    brk = (monday + timedelta(days=21), monday + timedelta(days=27))
    seen = set()
    for s in sug["suggestions"]:
        d = S["today"].fromisoformat(s["date"])
        assert d > S["today"] and d.isoweekday() <= 5 and (monday - timedelta(days=14)) <= d <= monday + timedelta(days=69)
        assert not (brk[0] <= d <= brk[1])                                                             # never on the holiday
        wd = d.isoweekday()
        assert (wd, s["start_period"]) not in {(MON, 1), (WED, 1), (TUE, 3)}                           # class 1 is free there
        assert (wd, s["start_period"]) != (THU, 1)                                                     # t1 teaches class 3 then
        assert (s["date"], s["start_period"]) not in seen
        seen.add((s["date"], s["start_period"]))
    assert p1.api(p1.hod, "POST", "/api/hours/suggest", json={"subject_id": p1.b_subject}).status_code == 404       # another school's subject
    assert p1.api(p1.teacher, "POST", "/api/hours/suggest", json={"subject_id": S["a_math"]}).status_code == 403
    S["sug"] = sug["suggestions"]


def test_approving_extra_slots_adds_the_lectures_and_tells_the_teacher(p1):
    before = hours_row(p1, S["a_math"])
    pick = S["sug"][:2]
    slots = [{"date": s["date"], "start_period": s["start_period"]} for s in pick]
    r = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": slots, "reason": "ZZ compensation"})
    assert r.status_code == 201 and r.json()["lectures"] == 2 and r.json()["notified"] == 1, r.text
    after = hours_row(p1, S["a_math"])
    assert after["extra"] == 2 and after["shortfall"] == before["shortfall"] - 2
    note = notes_of(S["t1"], "extra_lecture")
    assert len(note) == 1 and "2 extra lectures" in note[0]["title"] and "ZZ compensation" in note[0]["body"] and note[0]["link"] == "/my/timetable"
    # it is on the teacher's own week and on the class view of that date, and nowhere else
    d0 = S["today"].fromisoformat(pick[0]["date"])
    mine = week(p1, p1.teacher, vw.monday_of(d0))
    extra = [c for c in mine["cells"] if c["kind"] == "extra"]
    assert extra and extra[0]["date"] == pick[0]["date"] and extra[0]["extra"]["reason"] == "ZZ compensation"
    assert mine["stats"]["extra"] >= 1
    cv = p1.api(p1.hod, "GET", f"/api/views/class/{cls(1)}?week_of={vw.monday_of(d0)}").json()
    assert any(c["kind"] == "extra" for c in cv["cells"])
    tv2 = p1.api(p1.hod, "GET", f"/api/views/teacher/{S['t2']}?week_of={vw.monday_of(d0)}").json()
    assert not any(c["kind"] == "extra" for c in tv2["cells"])
    # the very same slot cannot be approved twice, and a clashing one is refused
    dup = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": slots[:1]})
    assert dup.status_code == 422 and dup.json()["code"] == "EXTRA_BLOCKED"
    thu = (S["monday"] + timedelta(days=17))                                                           # a Thursday: t1 teaches class 3 at P1
    clash = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": [{"date": thu.isoformat(), "start_period": 1}]})
    assert clash.status_code == 422 and "teaching another class" in clash.json()["message"]
    on_holiday = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": [{"date": (S["monday"] + timedelta(days=22)).isoformat(), "start_period": 5}]})
    assert on_holiday.status_code == 422 and "ZZ P4 Break" in on_holiday.json()["message"]
    sunday = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": [{"date": (S["monday"] + timedelta(days=13)).isoformat(), "start_period": 5}]})
    assert sunday.status_code == 422 and "not a working day" in sunday.json()["message"]
    assert p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": []}).status_code == 422
    assert p1.api(p1.teacher, "POST", "/api/hours/extra", json={"subject_id": S["a_math"], "slots": slots}).status_code == 403
    S["extra_slots"] = slots


def test_extra_lectures_can_be_listed_and_cancelled_and_the_teacher_is_told(p1):
    lst = p1.api(p1.hod, "GET", "/api/hours/extra").json()["extra"]
    assert len(lst) == 2 and all(x["can_cancel"] and x["subjects"] == ["A Maths"] and x["teacher_name"] for x in lst)
    assert p1.api(p1.hod, "DELETE", f"/api/hours/extra/{lst[0]['id']}").json()["cancelled"] is True
    assert len(p1.api(p1.hod, "GET", "/api/hours/extra").json()["extra"]) == 1
    assert [n["title"] for n in notes_of(S["t1"], "extra_lecture_cancelled")] == ["Extra lecture cancelled"]
    assert hours_row(p1, S["a_math"])["extra"] == 1
    assert p1.api(p1.hod, "DELETE", f"/api/hours/extra/{uuid.uuid4()}").status_code == 404
    assert p1.api(p1.teacher, "DELETE", f"/api/hours/extra/{lst[1]['id']}").status_code == 403
    # an extra lecture that already happened cannot be cancelled
    old = db_val(
        "insert into public.extra_lectures (school_id, term_id, semester_id, subject_id, lecture_date, start_period, length) values ($1,$2,$3,$4,$5,6,1) returning id",
        uuid.UUID(p1.school), uuid.UUID(S["term"]), uuid.UUID(cls(1)), uuid.UUID(S["a_math"]), S["monday"] - timedelta(days=7),
    )
    r = p1.api(p1.hod, "DELETE", f"/api/hours/extra/{old}")
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_HAPPENED"
    db_exec("delete from public.extra_lectures where id = $1", old)


def test_an_elective_group_gets_its_extra_lecture_together(p1):
    g = p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": cls(3), "name": "ZZ P4 Group"}).json()
    gid = g["id"]
    e1 = mk_subject(p1, 3, "P4-E1", "Elective One", is_elective=True, elective_group_id=gid, semester_hours=15)
    e2 = mk_subject(p1, 3, "P4-E2", "Elective Two", is_elective=True, elective_group_id=gid, semester_hours=15)
    teach(p1, e1, S["t2"])
    teach(p1, e2, S["t3"])
    det = p1.api(p1.hod, "GET", f"/api/drafts/{S['db']}").json()
    grp = next(x for x in det["groups"] if x["name"] == "ZZ P4 Group")
    r = p1.api(p1.hod, "POST", f"/api/drafts/{S['db']}/placements", json={"day": TUE, "start_period": 4, "group_id": grp["id"]})
    assert r.status_code == 201, r.text
    assert p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(3)}).status_code == 200
    sug = p1.api(p1.hod, "POST", "/api/hours/suggest", json={"subject_id": e1, "count": 1}).json()
    assert sug["group"] and {x["name"] for x in sug["group"]} == {"Elective One", "Elective Two"} and len(sug["suggestions"]) == 1
    s = sug["suggestions"][0]
    r = p1.api(p1.hod, "POST", "/api/hours/extra", json={"subject_id": e1, "slots": [{"date": s["date"], "start_period": s["start_period"]}]})
    assert r.status_code == 201 and r.json()["lectures"] == 1 and r.json()["notified"] == 2            # both option teachers are told
    rows = db_fetch("select subject_id, parallel_key from public.extra_lectures where semester_id = $1", uuid.UUID(cls(3)))
    assert len(rows) == 2 and rows[0]["parallel_key"] == rows[1]["parallel_key"] and rows[0]["parallel_key"] is not None
    lst = p1.api(p1.hod, "GET", f"/api/hours/extra?semester_id={cls(3)}").json()["extra"]
    assert len(lst) == 1 and sorted(lst[0]["subjects"]) == ["Elective One", "Elective Two"]
    assert p1.api(p1.hod, "DELETE", f"/api/hours/extra/{lst[0]['id']}").status_code == 200
    assert db_val("select count(*) from public.extra_lectures where semester_id = $1", uuid.UUID(cls(3))) == 0


def test_deleting_a_subject_that_is_published_is_refused(p1):
    r = p1.api(p1.hod, "DELETE", f"/api/subjects/{S['a_phys']}")
    assert r.status_code == 409 and "published" in r.json()["message"]
    p1.api(p1.hod, "DELETE", f"/api/calendar/{S['break']}")


def test_college_name_is_printed_on_exports_and_editable_by_the_admin_only(p1):
    cur = p1.api(p1.admin, "GET", "/api/timing").json()["college"]
    assert cur["college_name"]
    body = {**REAL}
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json={**body, "college_name": "ZZ P4 University"}).json()["college"]["college_name"] == "ZZ P4 University"
    from pypdf import PdfReader

    pdf = p1.api(p1.hod, "GET", f"/api/exports/class/{cls(1)}?format=pdf").content
    assert "ZZ P4 University" in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert p1.api(p1.hod, "PUT", "/api/timing/college", json=body).status_code == 403
    p1.api(p1.admin, "PUT", "/api/timing/college", json={**body, "college_name": cur["college_name"]})


def test_the_signed_in_screens_of_the_bell_exist_for_every_role(p1):
    for who in (p1.admin, p1.hod, p1.teacher):
        assert p1.api(who, "GET", "/api/notifications").status_code == 200
        assert p1.api(who, "GET", "/api/push/config").status_code == 200
    with new_client() as c:
        assert c.get("/api/notifications").status_code == 401
        assert c.post("/api/push/subscribe", json={}).status_code == 401


def test_a_class_cannot_go_live_alone_when_it_would_double_book_a_teacher_against_the_published_timetable(p1):
    det_a = p1.api(p1.hod, "GET", f"/api/drafts/{S['da']}").json()
    det_b = p1.api(p1.hod, "GET", f"/api/drafts/{S['db']}").json()
    maths_mon = next(p for p in det_a["placements"] if p["entries"][0]["subject_name"] == "A Maths" and p["day"] == MON)
    chem = next(p for p in det_b["placements"] if p["entries"][0]["subject_name"] == "B Chem")
    # draft A: t1's Monday-P1 lecture moves to Friday P1; only then may draft B put t1's other lecture on Monday P1
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['da']}/placements/{maths_mon['key']}", json={"day": FRI, "start_period": 1}).status_code == 200
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['db']}/placements/{chem['key']}", json={"day": MON, "start_period": 1}).status_code == 200
    alone = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(3)})
    assert alone.status_code == 409 and alone.json()["code"] == "PUBLISH_BLOCKED"
    assert "ZZ P4 Course - Sem 1" in alone.json()["message"] and "together" in alone.json()["message"]       # it tells the HOD to publish both
    assert titles(week(p1, p1.teacher)).count("B Chem") == 1 and next(c for c in week(p1, p1.teacher)["cells"] if c["items"][0]["title"] == "B Chem")["day"] == THU
    both = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "all"})
    assert both.status_code == 200 and {p["class_label"] for p in both.json()["published"]} == {"ZZ P4 Course - Sem 1", "ZZ P4 Course - Sem 3"}
    mine = week(p1, p1.teacher)
    days = sorted((c["items"][0]["title"], c["day"], c["start_period"]) for c in mine["cells"])
    assert days == [("A Maths", WED, 1), ("A Maths", FRI, 1), ("B Chem", MON, 1)]
    # t1 had lectures in both classes and both moved: exactly ONE new notification for this publish click
    changed = notes_of(S["t1"], "timetable_changed")
    assert len(changed) == 1 and "2 moved" in changed[0]["body"]
