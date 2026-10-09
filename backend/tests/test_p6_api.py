"""Phase 6 API tests: teacher preferences (grid, limits, visiting days), the preference window (open / extend / close / reopen,
who is notified, reminder + auto-close sweep), the hard rules in the manual grid and at publish, a real generation run that
respects them (and shows the star scoring), the substitution last-resort rule, the Audit Log screen.

Runs in a throw-away school against the REAL API + Supabase (the seeded School of IT is never touched). Tests build on each other.
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import views as vw

from .conftest import connect_one, db_exec, db_fetch, db_val, mail_for, run

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5
IST = vw.IST


def in_days(n: int) -> str:
    """A deadline typed like the browser's datetime-local box: plain local (IST) time."""
    return (datetime.now(IST) + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M")


def clean_slate(p1) -> None:
    school = uuid.UUID(p1.school)
    for sql in (
        "delete from public.preference_windows where school_id = $1", "delete from public.teacher_pref_slots where school_id = $1",
        "delete from public.teacher_preferences where school_id = $1", "delete from public.preference_settings where school_id = $1",
        "delete from public.workload_norms where school_id = $1",
        "delete from public.day_adjustments where school_id = $1", "delete from public.substitutions where school_id = $1",
        "delete from public.teacher_absences where school_id = $1", "delete from public.leave_requests where school_id = $1",
        "delete from public.extra_lectures where school_id = $1", "delete from public.publish_events where school_id = $1",
        "delete from public.publications where school_id = $1", "delete from public.timetable_drafts where school_id = $1",
        "delete from public.generation_runs where school_id = $1", "delete from public.calendar_entries where school_id = $1 or is_preloaded",
    ):
        db_exec(sql, school)
    people = [uuid.UUID(x.account.user_id) for x in (p1.admin, p1.hod, p1.teacher, p1.teacher_b)]
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", people)


def set_prefs(teacher_id: str, *, no=(), star=(), max_day=None, first=False, last=False) -> None:
    """Writes a teacher's preferences straight into the tables (the API only allows that inside an open window)."""
    tid = uuid.UUID(teacher_id)
    school = db_val("select school_id from public.profiles where id = $1", tid)

    async def go():
        conn = await connect_one()
        try:
            await conn.execute("delete from public.teacher_pref_slots where teacher_id = $1", tid)
            await conn.execute("delete from public.teacher_preferences where teacher_id = $1", tid)
            if no or star or max_day or first or last:
                await conn.execute(
                    "insert into public.teacher_preferences (teacher_id, school_id, prefer_first, avoid_last, max_per_day) values ($1,$2,$3,$4,$5)",
                    tid, school, first, last, max_day,
                )
            rows = [(tid, school, d, p, "unavailable") for d, p in no] + [(tid, school, d, p, "preferred") for d, p in star]
            if rows:
                await conn.executemany("insert into public.teacher_pref_slots (teacher_id, school_id, day, period, kind) values ($1,$2,$3,$4,$5)", rows)
        finally:
            await conn.close()

    run(go())


def notes_of(user_id: str, kind: str | None = None) -> list[dict]:
    sql = "select kind, title, body, link from public.notifications where user_id = $1"
    args: list = [uuid.UUID(user_id)]
    if kind:
        sql += " and kind = $2"
        args.append(kind)
    return db_fetch(sql + " order by created_at", *args)


def cls(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, subject_ids: list[str], **over) -> str:
    body = {"full_name": f"P6 {key}", "email": mail_for(f"p6-{key}"), "subject_ids": subject_ids}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def assign(p1, subject_id: str, teacher_id: str) -> None:
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id}).status_code == 200


def place(p1, draft: str, day: int, start: int, **body):
    return p1.api(p1.hod, "POST", f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, **body})


def nxt(wd: int) -> date:
    base = S["today"]
    days = (wd - base.isoweekday()) % 7
    return base + timedelta(days=days or 7)


def total_teachers(p1) -> int:
    return db_val("select count(*) from public.profiles where school_id = $1 and is_active and role in ('teacher','hod')", uuid.UUID(p1.school))


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    clean_slate(p1)
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ P6 Course', 'ZZP6', 5) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 5) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in sems}
    S["term"] = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    S["today"] = vw.today_ist()
    yield
    clean_slate(p1)


# ====================================================================================================
# 0) a published little class (to test the hard rules, publishing and substitution against)
# ====================================================================================================
def test_00_setup_teachers_a_class_and_a_published_timetable(p1):
    S["maths"] = mk_subject(p1, 1, "P6-M", "P6 Maths")
    S["chem"] = mk_subject(p1, 1, "P6-C", "P6 Chem")
    S["phys"] = mk_subject(p1, 1, "P6-P", "P6 Phys")
    S["m3"] = mk_subject(p1, 3, "P6-M3", "P6 Maths III")
    S["tm"] = mk_teacher(p1, "math", [S["maths"], S["m3"]], specialization="Mathematics")
    S["tc"] = mk_teacher(p1, "chem", [S["chem"]])
    S["tp"] = mk_teacher(p1, "phys", [S["phys"]])
    for sid, tid in ((S["maths"], S["tm"]), (S["m3"], S["tm"]), (S["chem"], S["tc"]), (S["phys"], S["tp"])):
        assign(p1, sid, tid)
    S["draft"] = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(1), "name": "Plan P6"}).json()["id"]
    for day, start, sub in ((MON, 3, "maths"), (WED, 3, "maths"), (TUE, 2, "chem"), (THU, 2, "chem"), (FRI, 1, "phys")):
        assert place(p1, S["draft"], day, start, subject_id=S[sub]).status_code == 201
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(1)})
    assert r.status_code == 200, r.text
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", [uuid.UUID(x) for x in (S["tm"], S["tc"], S["tp"], p1.teacher.account.user_id)])


# ====================================================================================================
# 1) nothing can be edited before the HOD opens a window
# ====================================================================================================
def test_a_teacher_can_view_but_not_edit_before_any_window_exists(p1):
    r = p1.api(p1.teacher, "GET", "/api/prefs/me")
    assert r.status_code == 200
    me = r.json()
    assert me["window"] is None and me["can_edit"] is False and "No preference window" in me["reason"]
    assert me["limit"] == 8 and me["days"] == 5 and me["periods"] == 6 and me["slots"] == {} and me["locked_days"] == []
    assert me["teacher"]["id"] == p1.teacher.account.user_id and me["submitted"] is False
    put = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 1, "period": 1, "kind": "unavailable"}]})
    assert put.status_code == 409 and put.json()["code"] == "WINDOW_CLOSED"
    assert db_val("select count(*) from public.teacher_pref_slots where teacher_id = $1", uuid.UUID(p1.teacher.account.user_id)) == 0


# ====================================================================================================
# 2) the HOD opens the window: everybody in the school is told, nobody else
# ====================================================================================================
def test_the_hod_opens_a_window_and_every_teacher_of_the_school_is_told(p1):
    past = p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"deadline": (datetime.now(IST) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")})
    assert past.status_code == 422 and past.json()["code"] == "DEADLINE_PAST"
    far = p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"deadline": in_days(400)})
    assert far.status_code == 422 and far.json()["code"] == "DEADLINE_FAR"
    r = p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"deadline": in_days(3), "note": "Please be sensible"})
    assert r.status_code == 201, r.text
    w = r.json()["window"]
    S["window"] = w["id"]
    assert w["status"] == "open" and w["is_open"] and w["seconds_left"] > 2.5 * 86400 and w["note"] == "Please be sensible"
    assert r.json()["notified"] >= 4
    for who in (S["tm"], S["tc"], S["tp"], p1.teacher.account.user_id):
        got = notes_of(who, "pref_window_open")
        assert len(got) == 1 and got[0]["link"] == "/my/preferences" and "Please be sensible" in got[0]["body"], who
    # not the HOD who opened it, not the admin, not another school
    for who in (p1.hod.account.user_id, p1.admin.account.user_id, p1.teacher_b.account.user_id):
        assert not notes_of(who, "pref_window_open"), who
    assert db_val("select count(*) from public.audit_log where entity = 'preference_window' and action = 'open' and actor_email = $1", p1.hod.account.email) == 1


def test_only_one_window_can_be_open_at_a_time(p1):
    r = p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"deadline": in_days(5)})
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_OPEN"
    assert db_val("select count(*) from public.preference_windows where school_id = $1", uuid.UUID(p1.school)) == 1


# ====================================================================================================
# 3) a teacher fills the weekly grid
# ====================================================================================================
SAVE = {
    "slots": [
        {"day": 1, "period": 1, "kind": "unavailable"}, {"day": 1, "period": 2, "kind": "unavailable"},
        {"day": 2, "period": 3, "kind": "preferred"}, {"day": 3, "period": 4, "kind": "preferred"},
    ],
    "prefer_first": True, "avoid_last": True, "max_per_day": 3,
}


def test_a_teacher_saves_the_grid_and_reads_it_back(p1):
    r = p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["can_edit"] is True and me["submitted"] is True
    assert me["slots"] == {"1:1": "unavailable", "1:2": "unavailable", "2:3": "preferred", "3:4": "preferred"}
    assert (me["prefer_first"], me["avoid_last"], me["max_per_day"]) == (True, True, 3)
    assert me["unavailable_count"] == 2 and me["preferred_count"] == 2
    assert me["summary"] == "2 not available · 2 preferred · max 3 a day · likes the first lecture · avoids the last lecture"
    assert p1.api(p1.teacher, "GET", "/api/prefs/me").json()["slots"] == me["slots"]
    # saving again replaces (does not add to) the old choices
    again = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={**SAVE, "slots": SAVE["slots"][:1]})
    assert again.status_code == 200 and again.json()["slots"] == {"1:1": "unavailable"}
    assert db_val("select count(*) from public.teacher_pref_slots where teacher_id = $1", uuid.UUID(p1.teacher.account.user_id)) == 1
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE).status_code == 200
    assert db_val("select count(*) from public.preference_responses where window_id = $1 and teacher_id = $2",
                  uuid.UUID(S["window"]), uuid.UUID(p1.teacher.account.user_id)) == 1                    # still ONE response
    audit = db_fetch("select summary from public.audit_log where entity = 'preference' and actor_email = $1 order by id desc limit 1", p1.teacher.account.email)
    assert audit and "saved their preferences" in audit[0]["summary"] and "2 not available" in audit[0]["summary"]


def test_more_not_available_marks_than_the_limit_is_refused_and_nothing_is_saved(p1):
    lim = p1.api(p1.hod, "PUT", "/api/prefs/settings", json={"max_unavailable": 2})
    assert lim.status_code == 200 and lim.json()["settings"]["max_unavailable"] == 2 and lim.json()["teachers_over_limit"] == 0
    three = [{"day": d, "period": 1, "kind": "unavailable"} for d in (1, 2, 3)]
    r = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": three})
    assert r.status_code == 422 and r.json()["code"] == "TOO_MANY_UNAVAILABLE"
    assert "at most 2" in r.json()["message"] and "you marked 3" in r.json()["message"] and r.json()["limit"] == 2 and r.json()["marked"] == 3
    assert p1.api(p1.teacher, "GET", "/api/prefs/me").json()["slots"] == SAVE_SLOTS()                  # the old choices survived
    assert p1.api(p1.hod, "PUT", "/api/prefs/settings", json={"max_unavailable": 3}).status_code == 200
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": three}).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/prefs/settings", json={"max_unavailable": 8}).status_code == 200
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE).status_code == 200                    # back to the normal choices
    assert p1.api(p1.hod, "PUT", "/api/prefs/settings", json={"max_unavailable": 999}).status_code == 422   # a silly limit


def SAVE_SLOTS() -> dict:
    return {"1:1": "unavailable", "1:2": "unavailable", "2:3": "preferred", "3:4": "preferred"}


def test_nonsense_is_rejected_with_clear_messages(p1):
    bad_period = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 1, "period": 9, "kind": "preferred"}]})
    assert bad_period.status_code == 422 and "only has 6 periods" in bad_period.json()["message"]
    sat = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 6, "period": 1, "kind": "preferred"}]})
    assert sat.status_code == 422 and "not a working day" in sat.json()["message"]                     # a 5-day college
    twice = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 1, "period": 1, "kind": "preferred"}, {"day": 1, "period": 1, "kind": "unavailable"}]})
    assert twice.status_code == 422 and "twice" in twice.json()["message"]
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [], "max_per_day": 7}).json()["code"] == "BAD_MAX"
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 1, "period": 1, "kind": "maybe"}]}).status_code == 422
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [], "surprise": True}).status_code == 422   # unknown fields refused
    assert p1.api(p1.teacher, "GET", "/api/prefs/me").json()["slots"] == SAVE_SLOTS()


def test_visiting_faculty_days_stay_a_separate_hard_rule(p1):
    me = uuid.UUID(p1.teacher.account.user_id)
    db_exec("update public.profiles set is_visiting = true, visiting_days = '{1,2,3}', max_weekly_hours = 12 where id = $1", me)
    try:
        view = p1.api(p1.teacher, "GET", "/api/prefs/me").json()
        assert view["teacher"]["is_visiting"] and view["teacher"]["visiting_days"] == [1, 2, 3] and view["locked_days"] == [4, 5]
        r = p1.api(p1.teacher, "PUT", "/api/prefs/me", json={"slots": [{"day": 4, "period": 1, "kind": "unavailable"}]})
        assert r.status_code == 422 and r.json()["code"] == "VISITING_DAY" and "Monday, Tuesday, Wednesday" in r.json()["message"]
        assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE).status_code == 200                 # on visiting days: fine
    finally:
        db_exec("update public.profiles set is_visiting = false, visiting_days = '{}', max_weekly_hours = null where id = $1", me)


def test_the_hod_can_fill_their_own_preferences_as_a_teacher(p1):
    r = p1.api(p1.hod, "PUT", "/api/prefs/me", json={"slots": [{"day": 5, "period": 6, "kind": "unavailable"}], "max_per_day": 4})
    assert r.status_code == 200 and r.json()["submitted"] is True
    p1.api(p1.hod, "PUT", "/api/prefs/me", json={"slots": []})


# ====================================================================================================
# 4) the HOD's live list, read-only views, and who may do what
# ====================================================================================================
def test_the_hod_sees_who_has_filled_and_who_has_not(p1):
    r = p1.api(p1.hod, "GET", "/api/prefs/overview")
    assert r.status_code == 200
    o = r.json()
    rows = {t["id"]: t for t in o["teachers"]}
    mine = rows[p1.teacher.account.user_id]
    assert mine["filled"] is True and mine["unavailable"] == 2 and mine["preferred"] == 2 and mine["max_per_day"] == 3 and mine["summary"].startswith("2 not available")
    for pending in (S["tm"], S["tc"], S["tp"]):
        assert rows[pending]["filled"] is False and rows[pending]["has_prefs"] is False and rows[pending]["summary"] == "No preferences set"
    assert o["total"] == total_teachers(p1) and o["filled"] == sum(1 for t in o["teachers"] if t["filled"]) and o["filled"] >= 2
    assert o["window"]["id"] == S["window"] and o["window"]["is_open"] and o["settings"] == {"max_unavailable": 8, "reminder_hours": 24}
    assert o["school"]["id"] == p1.school and o["history"][0]["id"] == S["window"] and o["history"][0]["filled"] == o["filled"]


def test_the_hod_and_admin_can_view_any_teachers_preferences_read_only(p1):
    for who in (p1.hod, p1.admin):
        r = p1.api(who, "GET", f"/api/prefs/teacher/{p1.teacher.account.user_id}")
        assert r.status_code == 200, who.account.role
        view = r.json()
        assert view["slots"] == SAVE_SLOTS() and view["can_edit"] is False and "View only" in view["reason"] and view["max_per_day"] == 3
    # an HOD cannot look at another school's teacher (it simply does not exist for him)
    assert p1.api(p1.hod, "GET", f"/api/prefs/teacher/{p1.teacher_b.account.user_id}").status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/prefs/teacher/{p1.admin.account.user_id}").status_code == 404     # an admin is not a teacher
    assert p1.api(p1.hod, "GET", f"/api/prefs/teacher/{uuid.UUID(int=0)}").status_code == 404
    assert p1.api(p1.hod, "GET", "/api/prefs/teacher/not-an-id").status_code == 422


def test_roles_and_school_locks_on_every_preference_route(p1):
    # a teacher is not a manager
    for method, path, body in (("GET", "/api/prefs/overview", None), ("PUT", "/api/prefs/settings", {"max_unavailable": 3}),
                               ("POST", "/api/prefs/window/open", {"deadline": in_days(2)}), ("POST", "/api/prefs/window/close", {}),
                               ("POST", "/api/prefs/window/reopen", {"deadline": in_days(2)}), ("POST", "/api/prefs/window/extend", {"deadline": in_days(2)}),
                               ("GET", f"/api/prefs/teacher/{p1.teacher.account.user_id}", None)):
        assert p1.api(p1.teacher, method, path, **({"json": body} if body is not None else {})).status_code == 403, path
    # an admin has no preferences of their own
    assert p1.api(p1.admin, "GET", "/api/prefs/me").status_code == 403
    assert p1.api(p1.admin, "PUT", "/api/prefs/me", json={"slots": []}).status_code == 403
    # an HOD is locked to his own school
    assert p1.api(p1.hod, "GET", "/api/prefs/overview", params={"school_id": p1.school_b}).status_code == 403
    assert p1.api(p1.hod, "PUT", "/api/prefs/settings", json={"school_id": p1.school_b, "max_unavailable": 1}).status_code == 403
    assert p1.api(p1.hod, "POST", "/api/prefs/window/open", json={"school_id": p1.school_b, "deadline": in_days(2)}).status_code == 403
    assert p1.api(p1.hod, "POST", "/api/prefs/window/close", json={"school_id": p1.school_b}).status_code == 403
    # the admin must name the school, then may work on any
    assert p1.api(p1.admin, "GET", "/api/prefs/overview").status_code == 422
    adm = p1.api(p1.admin, "GET", "/api/prefs/overview", params={"school_id": p1.school})
    assert adm.status_code == 200 and adm.json()["window"]["id"] == S["window"]
    other = p1.api(p1.admin, "GET", "/api/prefs/overview", params={"school_id": p1.school_b}).json()
    assert other["window"] is None and all(t["id"] != p1.teacher.account.user_id for t in other["teachers"])
    # a teacher of ANOTHER school only ever sees their own grid and is not in this school's window
    theirs = p1.api(p1.teacher_b, "GET", "/api/prefs/me")
    assert theirs.status_code == 200 and theirs.json()["teacher"]["id"] == p1.teacher_b.account.user_id and theirs.json()["window"] is None
    assert p1.api(p1.teacher_b, "PUT", "/api/prefs/me", json={"slots": []}).status_code == 409           # no window in their school
    assert p1.api(p1.teacher, "GET", "/api/prefs/me").json()["slots"] == SAVE_SLOTS()                  # untouched by any of this


# ====================================================================================================
# 5) the HARD rules in the manual grid and at publish
# ====================================================================================================
def test_the_manual_grid_blocks_a_not_available_slot_with_the_reason(p1):
    set_prefs(S["tm"], no={(MON, 1), (TUE, 5)}, max_day=None)
    r = place(p1, S["draft"], MON, 1, subject_id=S["maths"])
    assert r.status_code == 422 and r.json()["code"] == "PLACEMENT_BLOCKED"
    assert "P6 math marked Monday period 1 as not available" in r.json()["message"]
    opts = p1.api(p1.hod, "POST", f"/api/drafts/{S['draft']}/options", json={"subject_id": S["maths"]}).json()["cells"]
    assert opts["1:1"]["state"] == "blocked" and "not available" in opts["1:1"]["hard"][0]
    assert opts["2:5"]["state"] == "blocked" and opts["1:2"]["state"] in ("ok", "warn")
    assert place(p1, S["draft"], MON, 2, subject_id=S["maths"]).status_code == 201                       # the next period is fine
    # moving an existing placement onto the marked cell is refused as well
    key = next(e["key"] for e in p1.api(p1.hod, "GET", f"/api/drafts/{S['draft']}").json()["placements"] if e["day"] == WED and e["start_period"] == 3)
    mv = p1.api(p1.hod, "PATCH", f"/api/drafts/{S['draft']}/placements/{key}", json={"day": MON, "start_period": 1})
    assert mv.status_code == 422 and "not available" in mv.json()["message"]


def test_the_daily_maximum_blocks_a_second_lecture_the_same_day(p1):
    set_prefs(S["tc"], max_day=1)
    # Chem already sits on Tue P2 and Thu P2 in the draft; a third on Tuesday would be the 2nd that day
    r = place(p1, S["draft"], TUE, 4, subject_id=S["chem"])
    assert r.status_code == 422 and "asked for at most 1 lecture(s) a day" in r.json()["message"] and "make 2 on Tuesday" in r.json()["message"]
    assert place(p1, S["draft"], WED, 5, subject_id=S["chem"]).status_code == 201                         # Wednesday is empty
    set_prefs(S["tc"])


def test_a_lecture_on_a_newly_marked_slot_shows_in_the_conflict_report_and_blocks_publishing(p1):
    set_prefs(S["tm"], no={(MON, 2)})                      # the HOD placed Maths on Mon P2 earlier; now the teacher marks it
    rep = p1.api(p1.hod, "GET", "/api/conflicts", params={"semester_id": cls(1)}).json()
    hits = [i for i in rep["items"] if i["code"] == "TEACHER_UNAVAILABLE"]
    assert hits and hits[0]["kind"] == "clash" and hits[0]["day"] == MON and hits[0]["start_period"] == 2 and "not available" in hits[0]["message"]
    assert rep["counts"]["clashes"] >= 1
    pub = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(1)})
    assert pub.status_code == 409 and pub.json()["code"] == "PUBLISH_BLOCKED"
    status = p1.api(p1.hod, "GET", "/api/publish/status").json()
    row = next(c for c in status["classes"] if c["semester_id"] == cls(1))
    assert not row["can_publish"] and any("not available" in b for b in row["blockers"])
    set_prefs(S["tm"])                                                                                    # the teacher relents
    assert not [i for i in p1.api(p1.hod, "GET", "/api/conflicts", params={"semester_id": cls(1)}).json()["items"] if i["code"] == "TEACHER_UNAVAILABLE"]
    assert p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(1)}).status_code == 200


# ====================================================================================================
# 6) generation respects the hard rules and scores the stars
# ====================================================================================================
def wait_run(p1, run_id: str, timeout: float = 150.0) -> dict:
    t0 = time.monotonic()
    while True:
        got = p1.api(p1.hod, "GET", f"/api/generation/runs/{run_id}")
        assert got.status_code == 200, got.text
        data = got.json()
        if data["status"] != "running":
            return data
        assert time.monotonic() - t0 < timeout, f"run stuck: {data['progress']}% {data['message']}"
        time.sleep(0.5)


def test_generation_never_uses_a_not_available_slot_and_shows_the_star_scoring(p1):
    no = {(d, pn) for d in (MON, TUE) for pn in (1, 2, 3)}
    stars = {(WED, 2), (WED, 3), (THU, 2), (THU, 3)}
    set_prefs(S["tm"], no=no, star=stars, max_day=2)
    weights = {k: 0 for k in ("twice_a_day", "class_gaps", "teacher_gaps", "balance_load", "spread", "lab_lunch", "visiting_packed")}
    weights["preferred_slots"] = 100
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json={"name": "P6 prefs", "time_limit_s": 60, "weights": weights})
    assert r.status_code == 201, r.text
    data = wait_run(p1, r.json()["id"])
    assert data["status"] == "done", data
    best = data["solutions"][0]
    rule = next(x for x in best["breakdown"]["rules"] if x["rule"] == "preferred_slots")
    assert rule["weight"] == 100 and "Starred periods honoured" in rule["detail"]
    mine = next(t for t in rule["teachers"] if t["name"] == "P6 math")
    got, possible = mine["stars"]
    assert possible >= 1 and got >= 1, rule["detail"]
    assert got >= 2                                                       # at least half of the four starred periods were used
    rows = db_fetch(
        "select e.day, e.start_period, e.subject_id from public.draft_entries e join public.timetable_drafts d on d.id = e.draft_id where d.solution_id = $1",
        uuid.UUID(best["id"]),
    )
    maths = [(x["day"], x["start_period"]) for x in rows if str(x["subject_id"]) in (S["maths"], S["m3"])]
    assert len(maths) == 4                                                # every Maths lecture of both classes was placed
    assert not (set(maths) & no)                                          # NEVER on a slot the teacher marked
    per_day: dict = {}
    for d, _p in maths:
        per_day[d] = per_day.get(d, 0) + 1
    assert max(per_day.values()) <= 2                                     # NEVER over the teacher's daily maximum
    assert len(set(maths)) == 4                                           # and not in two places at once
    # the engine's own re-check found nothing
    assert all(not c["clashes"] if "clashes" in c else True for c in best["breakdown"]["classes"].values())
    assert p1.api(p1.hod, "DELETE", f"/api/generation/runs/{r.json()['id']}").status_code == 200
    set_prefs(S["tm"])


def test_a_preference_setup_that_cannot_work_is_explained_before_the_run(p1):
    set_prefs(S["tm"], no={(d, pn) for d in range(1, 6) for pn in range(1, 7)} - {(MON, 1)})   # P6 math teaches 4 periods a week but has ONE free cell
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json={"name": "P6 impossible", "time_limit_s": 30})
    assert r.status_code == 201
    findings = r.json()["precheck"]
    f = [x for x in findings if x["about"] == "P6 math"]
    assert f and "P6 math" in f[0]["message"], findings
    wait_run(p1, r.json()["id"], 100)
    p1.api(p1.hod, "DELETE", f"/api/generation/runs/{r.json()['id']}")
    set_prefs(S["tm"])


# ====================================================================================================
# 7) substitution: the last-resort rule
# ====================================================================================================
def test_a_teacher_who_marked_the_period_not_available_is_the_last_resort(p1):
    d = nxt(MON)
    ab = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat()})
    assert ab.status_code == 201, ab.text
    sub = next(a for a in ab.json()["affected"] if a["status"] == "open" and a["start_period"] == 3)["id"]   # (Maths also sits on Mon P2 by now)
    S["sub"] = sub
    set_prefs(p1.teacher.account.user_id, no={(MON, 3)})
    cands = p1.api(p1.hod, "GET", f"/api/subs/{sub}/candidates").json()["candidates"]
    ids = [c["teacher_id"] for c in cands]
    assert p1.teacher.account.user_id in ids                                       # still LISTED (the HOD may still ask)
    last = cands[-1]
    assert last["teacher_id"] == p1.teacher.account.user_id and last["marked_unavailable"] is True
    assert "Last resort: marked this period as not available" in last["reasons"]
    assert all(not c["marked_unavailable"] for c in cands[:-1])
    # auto offer: the others are asked, NOT the one who marked it
    offer = p1.api(p1.hod, "POST", f"/api/subs/{sub}/offer", json={"count": 8})
    assert offer.status_code == 200
    offered = [c["teacher_id"] for c in offer.json()["offered"]]
    assert p1.teacher.account.user_id not in offered and len(offered) >= 2 and offer.json()["last_resort"] is False
    assert not notes_of(p1.teacher.account.user_id, "sub_offer")


def test_when_nobody_else_is_free_the_offer_goes_to_them_and_says_so(p1):
    d = nxt(MON)
    others = [c["teacher_id"] for c in p1.api(p1.hod, "GET", f"/api/subs/{S['sub']}/candidates").json()["candidates"] if c["teacher_id"] != p1.teacher.account.user_id]
    for t in others:                                                              # everybody else is absent that day
        db_exec("insert into public.teacher_absences (school_id, term_id, teacher_id, absence_date) values ($1,$2,$3,$4) on conflict do nothing",
                uuid.UUID(p1.school), uuid.UUID(S["term"]), uuid.UUID(t), d)
    offer = p1.api(p1.hod, "POST", f"/api/subs/{S['sub']}/offer", json={"count": 5})
    assert offer.status_code == 200, offer.text
    assert [c["teacher_id"] for c in offer.json()["offered"]] == [p1.teacher.account.user_id] and offer.json()["last_resort"] is True
    got = notes_of(p1.teacher.account.user_id, "sub_offer")
    assert len(got) == 1
    reasons = db_val("select reasons from public.substitution_offers where substitution_id = $1 and teacher_id = $2", uuid.UUID(S["sub"]), uuid.UUID(p1.teacher.account.user_id))
    assert "Last resort" in str(reasons)                                          # the teacher sees WHY it is last-resort in the popup
    audit = db_fetch("select summary from public.audit_log where entity = 'substitution' and action = 'offer' order by id desc limit 1")
    assert "last resort" in audit[0]["summary"]
    p1.api(p1.hod, "POST", f"/api/subs/{S['sub']}/cancel")
    db_exec("delete from public.teacher_absences where school_id = $1", uuid.UUID(p1.school))
    set_prefs(p1.teacher.account.user_id, no={(1, 1), (1, 2)}, star={(2, 3), (3, 4)}, max_day=3, first=True, last=True)   # back to what they saved


# ====================================================================================================
# 8) the window: close, reopen, extend, reminder, auto-close
# ====================================================================================================
def test_closing_the_window_blocks_edits_but_not_viewing(p1):
    r = p1.api(p1.hod, "POST", "/api/prefs/window/close", json={})
    assert r.status_code == 200 and r.json()["window"]["status"] == "closed" and r.json()["window"]["closed_reason"] == "manual"
    me = p1.api(p1.teacher, "GET", "/api/prefs/me").json()
    assert me["can_edit"] is False and "closed" in me["reason"] and me["slots"] == SAVE_SLOTS() and me["window"]["is_open"] is False
    put = p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE)
    assert put.status_code == 409 and put.json()["code"] == "WINDOW_CLOSED"
    assert p1.api(p1.hod, "POST", "/api/prefs/window/close", json={}).status_code == 409                # nothing open to close
    assert p1.api(p1.hod, "POST", "/api/prefs/window/extend", json={"deadline": in_days(9)}).status_code == 409


def test_reopening_tells_only_the_teachers_who_have_not_filled(p1):
    before = {w: len(notes_of(w, "pref_window_open")) for w in (S["tm"], S["tc"], S["tp"], p1.teacher.account.user_id)}
    r = p1.api(p1.hod, "POST", "/api/prefs/window/reopen", json={"deadline": in_days(4)})
    assert r.status_code == 200, r.text
    assert r.json()["window"]["status"] == "open" and r.json()["window"]["reopened_count"] == 1 and r.json()["window"]["id"] == S["window"]
    for t in (S["tm"], S["tc"], S["tp"]):
        assert len(notes_of(t, "pref_window_open")) == before[t] + 1, t
    assert len(notes_of(p1.teacher.account.user_id, "pref_window_open")) == before[p1.teacher.account.user_id]    # they already filled
    assert any(n["title"] == "Preference window reopened" for n in notes_of(S["tm"], "pref_window_open"))
    # the people who had filled stay "filled" - the same window came back
    o = p1.api(p1.hod, "GET", "/api/prefs/overview").json()
    assert next(t for t in o["teachers"] if t["id"] == p1.teacher.account.user_id)["filled"] is True
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE).status_code == 200                       # editing works again
    assert p1.api(p1.hod, "POST", "/api/prefs/window/reopen", json={"deadline": in_days(4)}).status_code == 409   # already open


def test_extending_the_deadline_must_go_forward_and_tells_the_unfilled(p1):
    before = len(notes_of(S["tm"], "pref_window_open"))
    assert p1.api(p1.hod, "POST", "/api/prefs/window/extend", json={"deadline": in_days(1)}).status_code == 422          # earlier than now+4 days
    r = p1.api(p1.hod, "POST", "/api/prefs/window/extend", json={"deadline": in_days(8)})
    assert r.status_code == 200 and r.json()["window"]["seconds_left"] > 7 * 86400 and r.json()["notified"] >= 3
    assert len(notes_of(S["tm"], "pref_window_open")) == before + 1
    assert any("extended" in n["title"] for n in notes_of(S["tm"], "pref_window_open"))
    assert not [n for n in notes_of(p1.teacher.account.user_id, "pref_window_open") if "extended" in n["title"]]
    # extending twice to the same moment would not notify twice (dedupe)
    p1.api(p1.hod, "POST", "/api/prefs/window/extend", json={"deadline": in_days(9)})


def test_the_reminder_goes_to_unfilled_teachers_once_and_the_window_closes_itself_at_the_deadline(p1):
    from app import db
    from app.services import notify, prefflow

    wid = uuid.UUID(S["window"])
    # make the window look 2 hours old with 10 minutes left -> the reminder is due (half-way point of a short window)
    db_exec("update public.preference_windows set opened_at = now() - interval '2 hours', deadline = now() + interval '10 minutes', reminder_sent_at = null where id = $1", wid)
    reminders_before = {t: len(notes_of(t, "pref_reminder")) for t in (S["tm"], S["tc"], S["tp"], p1.teacher.account.user_id)}

    async def go():
        await db.init_pool()
        try:
            first = await prefflow.sweep(datetime.now(timezone.utc))
            second = await prefflow.sweep(datetime.now(timezone.utc))
            await notify.drain()
            return first, second
        finally:
            await db.close_pool()

    first, second = run(go())
    assert first["reminded"] >= 3 and first["closed"] == 0
    assert second == {"reminded": 0, "closed": 0}                                           # once per window, never twice
    for t in (S["tm"], S["tc"], S["tp"]):
        assert len(notes_of(t, "pref_reminder")) == reminders_before[t] + 1, t
    assert len(notes_of(p1.teacher.account.user_id, "pref_reminder")) == reminders_before[p1.teacher.account.user_id]    # already filled -> no nagging
    assert db_val("select reminder_sent_at is not null from public.preference_windows where id = $1", wid)
    # now the deadline passes: the next sweep closes it by itself and tells the HOD (and admin) how many filled
    db_exec("update public.preference_windows set deadline = now() - interval '1 minute' where id = $1", wid)

    async def go2():
        await db.init_pool()
        try:
            n = await prefflow.sweep(datetime.now(timezone.utc))
            await notify.drain()
            return n
        finally:
            await db.close_pool()

    assert run(go2())["closed"] == 1
    row = db_fetch("select status, closed_reason, closed_at from public.preference_windows where id = $1", wid)[0]
    assert row["status"] == "closed" and row["closed_reason"] == "deadline" and row["closed_at"] is not None
    done = db_val("select count(*) from public.preference_responses where window_id = $1", wid)
    note = notes_of(p1.hod.account.user_id, "pref_window_closed")[-1]
    assert f"{done} of {total_teachers(p1)} teachers filled" in note["body"] and note["link"] == "/preference-window"
    assert notes_of(p1.admin.account.user_id, "pref_window_closed")
    me = p1.api(p1.teacher, "GET", "/api/prefs/me").json()
    assert me["can_edit"] is False and "closed" in me["reason"]
    assert p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE).status_code == 409


def test_a_deadline_that_has_passed_blocks_a_save_even_before_the_sweep_runs(p1):
    p1.api(p1.hod, "POST", "/api/prefs/window/reopen", json={"deadline": in_days(2)})
    db_exec("update public.preference_windows set deadline = now() - interval '30 seconds' where id = $1", uuid.UUID(S["window"]))
    put = p1.api(p1.teacher, "PUT", "/api/prefs/me", json=SAVE)
    assert put.status_code == 409 and put.json()["code"] == "WINDOW_CLOSED"
    me = p1.api(p1.teacher, "GET", "/api/prefs/me").json()
    assert me["can_edit"] is False and me["window"]["is_open"] is False
    p1.api(p1.hod, "POST", "/api/prefs/window/close", json={})


# ====================================================================================================
# 9) the Audit Log screen (admin only)
# ====================================================================================================
def test_only_the_admin_may_read_the_audit_log(p1):
    for who in (p1.hod, p1.teacher, p1.teacher_b):
        assert p1.api(who, "GET", "/api/audit").status_code == 403, who.account.role
        assert p1.api(who, "GET", "/api/audit/facets").status_code == 403
    assert p1.api(p1.admin, "GET", "/api/audit").status_code == 200


def test_the_audit_log_shows_real_history_newest_first_with_who_what_when(p1):
    r = p1.api(p1.admin, "GET", "/api/audit", params={"page_size": 20})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 20 and len(d["entries"]) == 20 and d["page"] == 1 and d["pages"] >= 1
    ids = [e["id"] for e in d["entries"]]
    assert ids == sorted(ids, reverse=True)                                    # newest first
    e = d["entries"][0]
    assert set(e) >= {"id", "at", "action", "entity", "summary", "via", "actor", "school", "details", "entity_id"}
    assert e["actor"]["email"] and e["via"] in ("ui", "ai", "system") and e["at"].endswith("+05:30")


def test_the_audit_filters_person_action_entity_date_text_and_school(p1):
    hod = p1.hod.account.user_id
    by_hod = p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": hod, "entity": "preference_window", "page_size": 100}).json()
    assert by_hod["total"] >= 3 and {e["action"] for e in by_hod["entries"]} >= {"open", "close", "reopen"}
    assert all(e["actor"]["id"] == hod and e["entity"] == "preference_window" for e in by_hod["entries"])
    assert all(e["school"]["id"] == p1.school for e in by_hod["entries"])
    only_open = p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": hod, "action": "open", "entity": "preference_window"}).json()
    assert only_open["total"] == 1 and "Opened the preference window" in only_open["entries"][0]["summary"]
    teacher = p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": p1.teacher.account.user_id, "entity": "preference"}).json()
    assert teacher["total"] >= 2 and all("saved their preferences" in e["summary"] for e in teacher["entries"])
    assert teacher["entries"][0]["details"]["unavailable"] == 2 and teacher["entries"][0]["details"]["max_per_day"] == 3
    today = datetime.now(IST).date().isoformat()
    in_range = p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": hod, "date_from": today, "date_to": today, "entity": "preference_window"}).json()
    assert in_range["total"] == by_hod["total"]                                  # the range is the whole IST day, inclusive
    future = (datetime.now(IST).date() + timedelta(days=2)).isoformat()
    assert p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": hod, "date_from": future}).json()["total"] == 0
    assert p1.api(p1.admin, "GET", "/api/audit", params={"actor_id": hod, "date_to": "2020-01-01"}).json()["total"] == 0
    text = p1.api(p1.admin, "GET", "/api/audit", params={"q": "preference window", "actor_id": hod}).json()
    assert text["total"] >= 3 and all("preference window" in e["summary"].lower() for e in text["entries"])
    assert p1.api(p1.admin, "GET", "/api/audit", params={"q": "50% off_"}).json()["total"] == 0                 # % and _ are literal, not wildcards
    assert p1.api(p1.admin, "GET", "/api/audit", params={"via": "ai", "actor_id": hod}).json()["total"] == 0
    assert p1.api(p1.admin, "GET", "/api/audit", params={"school_id": p1.school, "entity": "preference_window"}).json()["total"] >= 3
    assert p1.api(p1.admin, "GET", "/api/audit", params={"school_id": p1.school_b, "entity": "preference_window"}).json()["total"] == 0


def test_the_audit_log_is_paginated_and_rejects_bad_input(p1):
    base = {"actor_id": p1.hod.account.user_id, "entity": "preference_window", "page_size": 2}
    p1_ = p1.api(p1.admin, "GET", "/api/audit", params=base).json()
    p2_ = p1.api(p1.admin, "GET", "/api/audit", params={**base, "page": 2}).json()
    assert len(p1_["entries"]) == 2 and p1_["pages"] >= 2 and p2_["page"] == 2
    assert not ({e["id"] for e in p1_["entries"]} & {e["id"] for e in p2_["entries"]})
    assert min(e["id"] for e in p1_["entries"]) > max(e["id"] for e in p2_["entries"])               # page 2 is older
    beyond = p1.api(p1.admin, "GET", "/api/audit", params={**base, "page": 500}).json()
    assert beyond["entries"] == [] and beyond["total"] == p1_["total"]
    assert p1.api(p1.admin, "GET", "/api/audit", params={"page_size": 5000}).json()["page_size"] == 100  # capped
    assert p1.api(p1.admin, "GET", "/api/audit", params={"page": 99999}).status_code == 422            # absurdly deep
    for bad in ({"date_from": "yesterday"}, {"date_to": "31/12/2026"}, {"actor_id": "nobody"}, {"school_id": "x"}, {"via": "robot"},
                {"date_from": "2026-12-01", "date_to": "2026-11-01"}):
        assert p1.api(p1.admin, "GET", "/api/audit", params=bad).status_code == 422, bad


def test_the_audit_facets_offer_what_the_filters_need(p1):
    f = p1.api(p1.admin, "GET", "/api/audit/facets").json()
    assert {"open", "close", "reopen", "update"} <= {a["value"] for a in f["actions"]}
    assert "preference_window" in {e["value"] for e in f["entities"]}
    hod = next(x for x in f["people"] if x["id"] == p1.hod.account.user_id)
    assert hod["count"] >= 3 and hod["email"] == p1.hod.account.email
    assert p1.school in {s["id"] for s in f["schools"]} and f["vias"] == ["ui", "ai", "system"]
    assert f["total"] >= 20 and f["first"] and f["last"] and f["first"] <= f["last"]
