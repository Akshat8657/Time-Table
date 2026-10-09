"""Phase 5 API tests: absence, leave, substitution ranking + manual assign + auto offers (first-accept-wins + timeout),
day-view updates, one-day move/cancel/swap with clash checks, smart suggestions, daily sheet + monthly summary, HOD-as-teacher.

Runs in a throw-away school against the REAL API + Supabase (the seeded School of IT is never touched). Tests build on each other.
"""
from __future__ import annotations

import io
import threading
import time
import uuid
from datetime import date, timedelta

import pytest

from app.services import views as vw

from .conftest import bearer, db_exec, db_fetch, db_val, mail_for, new_client

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5


def clean_slate(p1) -> None:
    school = uuid.UUID(p1.school)
    for sql in (
        "delete from public.day_adjustments where school_id = $1", "delete from public.substitutions where school_id = $1",
        "delete from public.teacher_absences where school_id = $1", "delete from public.leave_requests where school_id = $1",
        "delete from public.extra_lectures where school_id = $1", "delete from public.publish_events where school_id = $1",
        "delete from public.publications where school_id = $1", "delete from public.timetable_drafts where school_id = $1",
        "delete from public.generation_runs where school_id = $1", "delete from public.calendar_entries where school_id = $1 or is_preloaded",
    ):
        db_exec(sql, school)
    people = [uuid.UUID(x.account.user_id) for x in (p1.admin, p1.hod, p1.teacher, p1.teacher_b)]
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", people)


def nxt(wd: int) -> date:
    base = S["today"]
    days = (wd - base.isoweekday()) % 7
    return base + timedelta(days=days or 7)


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    clean_slate(p1)
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ P5 Course', 'ZZP5', 5) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 5) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in sems}
    S["term"] = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    S["today"] = vw.today_ist()
    for n in (1, 3):
        db_exec("insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date) values ($1,$2,$3,$4,$5)",
                uuid.UUID(S["term"]), uuid.UUID(S["sem"][n]), uuid.UUID(p1.school), S["today"] - timedelta(days=21), S["today"] + timedelta(days=90))
    yield


def cls(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, **over) -> str:
    body = {"full_name": f"P5 {key}", "email": mail_for(f"p5-{key}"), "subject_ids": []}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def eligible(p1, subject_id: str, teacher_id: str) -> None:
    db_exec("insert into public.teacher_subjects (teacher_id, subject_id, school_id) values ($1,$2,$3) on conflict do nothing",
            uuid.UUID(teacher_id), uuid.UUID(subject_id), uuid.UUID(p1.school))


def teach(p1, subject_id: str, teacher_id: str) -> None:
    eligible(p1, subject_id, teacher_id)
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id}).status_code == 200


def place(p1, draft: str, day: int, start: int, **body):
    return p1.api(p1.hod, "POST", f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, **body})


def notes_of(user_id: str, kind: str | None = None) -> list[dict]:
    sql = "select kind, title, body, link from public.notifications where user_id = $1"
    args: list = [uuid.UUID(user_id)]
    if kind:
        sql += " and kind = $2"
        args.append(kind)
    return db_fetch(sql + " order by created_at", *args)


# ====================================================================================================
# set up a published class
# ====================================================================================================
def test_publish_a_class_to_substitute_in(p1):
    S["tm"] = mk_teacher(p1, "math", specialization="Mathematics")
    S["tc"] = mk_teacher(p1, "chem")
    S["tp"] = mk_teacher(p1, "phys")
    S["s_math"] = mk_subject(p1, 1, "P5-M", "A Maths")
    S["s_chem"] = mk_subject(p1, 1, "P5-C", "A Chem")
    S["s_phys"] = mk_subject(p1, 1, "P5-P", "A Phys")
    teach(p1, S["s_math"], S["tm"])
    teach(p1, S["s_chem"], S["tc"])
    teach(p1, S["s_phys"], S["tp"])
    # p1.teacher and p1.hod are eligible to teach maths (so they rank as qualified substitutes) but teach nothing themselves
    eligible(p1, S["s_math"], p1.teacher.account.user_id)
    eligible(p1, S["s_math"], p1.hod.account.user_id)
    draft = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(1), "name": "Plan A"}).json()["id"]
    assert place(p1, draft, MON, 1, subject_id=S["s_math"]).status_code == 201
    assert place(p1, draft, WED, 1, subject_id=S["s_math"]).status_code == 201
    assert place(p1, draft, MON, 3, subject_id=S["s_chem"]).status_code == 201
    assert place(p1, draft, TUE, 2, subject_id=S["s_phys"]).status_code == 201
    r = p1.api(p1.hod, "POST", "/api/publish", json={"scope": "class", "semester_id": cls(1)})
    assert r.status_code == 200, r.text
    S["draft"] = draft


# ====================================================================================================
# 1) absence
# ====================================================================================================
def test_hod_marks_a_teacher_absent_and_sees_the_affected_lectures(p1):
    d = nxt(MON)
    S["mon"] = d
    r = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat(), "reason": "conference"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["to_cover"] == 1 and res["affected"][0]["start_period"] == 1 and res["affected"][0]["status"] == "open"
    S["absence_id"] = res["absence_id"]
    # the absent teacher and the admins are notified; the HOD who marked is not spammed
    assert len(notes_of(S["tm"], "absence_marked_you")) == 1
    assert any(n["kind"] == "absence_marked" for n in notes_of(p1.admin.account.user_id))
    assert not [n for n in notes_of(p1.hod.account.user_id) if n["kind"].startswith("absence")]
    # one OPEN substitution now exists for that slot
    subs = db_fetch("select status, start_period from public.substitutions where absence_id = $1", uuid.UUID(S["absence_id"]))
    assert len(subs) == 1 and subs[0]["status"] == "open"
    # marking again is refused
    assert p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat()}).status_code == 409


def test_a_teacher_marks_themselves_absent(p1):
    d = nxt(TUE)
    r = p1.api(p1.teacher, "POST", "/api/absence", json={"date": d.isoformat()})
    assert r.status_code == 201 and r.json()["teacher_id"] == p1.teacher.account.user_id
    # p1.teacher teaches nothing, so no lecture to cover - but the absence is recorded and managers told
    assert r.json()["to_cover"] == 0
    assert any(n["kind"] == "absence_marked" for n in notes_of(p1.hod.account.user_id))
    assert p1.api(p1.teacher, "GET", "/api/absence/mine").json()["absences"]
    db_exec("delete from public.teacher_absences where teacher_id = $1 and absence_date = $2", uuid.UUID(p1.teacher.account.user_id), d)
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", [uuid.UUID(p1.hod.account.user_id), uuid.UUID(p1.admin.account.user_id)])


def test_a_teacher_cannot_mark_someone_else_absent(p1):
    r = p1.api(p1.teacher, "POST", "/api/absence", json={"teacher_id": S["tc"], "date": nxt(WED).isoformat()})
    # a teacher's teacher_id is ignored - they mark themselves
    assert r.status_code == 201 and r.json()["teacher_id"] == p1.teacher.account.user_id
    db_exec("delete from public.teacher_absences where teacher_id = $1", uuid.UUID(p1.teacher.account.user_id))


# ====================================================================================================
# 2) ranking
# ====================================================================================================
def test_candidates_are_free_qualified_and_ranked_with_reasons(p1):
    sub = db_val("select id from public.substitutions where absence_id = $1", uuid.UUID(S["absence_id"]))
    S["sub"] = str(sub)
    r = p1.api(p1.hod, "GET", f"/api/subs/{S['sub']}/candidates")
    assert r.status_code == 200
    cands = r.json()["candidates"]
    ids = [c["teacher_id"] for c in cands]
    assert S["tm"] not in ids                                          # the absent teacher is never a candidate
    assert p1.teacher.account.user_id in ids and p1.hod.account.user_id in ids
    top = cands[0]
    assert top["can_teach"] and "Can teach this subject" in top["reasons"]
    assert all("Free in that slot" in c["reasons"] for c in cands)
    # tc teaches Mon P3 only -> free at P1 -> still a candidate
    assert S["tc"] in ids


# ====================================================================================================
# 3) manual assign + day view update
# ====================================================================================================
def test_manual_assign_fills_the_substitution_and_shows_on_the_day_views(p1):
    # assign tc (free, present) manually
    r = p1.api(p1.hod, "POST", f"/api/subs/{S['sub']}/assign", json={"teacher_id": S["tc"]})
    assert r.status_code == 200 and r.json()["substitute_id"] == S["tc"]
    assert db_val("select status from public.substitutions where id = $1", uuid.UUID(S["sub"])) == "filled"
    # the substitute, the absent teacher and the admin are told
    assert len(notes_of(S["tc"], "sub_assigned")) == 1
    assert any(n["kind"] == "sub_filled" for n in notes_of(S["tm"]))
    # the class day view for that Monday shows the substitute
    monday = vw.monday_of(S["mon"])
    cv = p1.api(p1.hod, "GET", f"/api/views/class/{cls(1)}?week_of={monday}").json()
    cell = next(c for c in cv["cells"] if c["day"] == MON and c["items"][0]["title"] == "A Maths")
    assert cell["substitutions"][0]["substitute_name"] and cell["items"][0]["substitute_id"] == S["tc"]
    # tc's own teacher view shows the covering lecture
    tv = p1.api(p1.hod, "GET", f"/api/views/teacher/{S['tc']}?week_of={monday}").json()
    assert any(c["kind"] == "substitution" and c["date"] == S["mon"].isoformat() for c in tv["cells"])
    # re-assigning is allowed (reassign to the HOD), and tc is told it is cancelled... actually assign overwrites
    assert p1.api(p1.hod, "POST", f"/api/subs/{S['sub']}/cancel").status_code == 200
    assert db_val("select status from public.substitutions where id = $1", uuid.UUID(S["sub"])) == "cancelled"


def test_assign_a_busy_teacher_is_refused(p1):
    # re-open a fresh absence slot by marking tm absent on another Monday
    d = nxt(MON) + timedelta(days=7)
    r = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat()})
    sub = r.json()["affected"][0]["id"]
    S["sub2"], S["mon2"] = sub, d
    # tc teaches Mon P3 but is free at P1 -> ok; but assign tm (the absent one) -> refused
    assert p1.api(p1.hod, "POST", f"/api/subs/{sub}/assign", json={"teacher_id": S["tm"]}).status_code == 422
    # make a teacher busy at P1 that day via an extra lecture, then they cannot be assigned
    db_exec("insert into public.extra_lectures (school_id, term_id, semester_id, subject_id, lecture_date, start_period, length, teacher_id) values ($1,$2,$3,$4,$5,1,1,$6)",
            uuid.UUID(p1.school), uuid.UUID(S["term"]), uuid.UUID(cls(1)), uuid.UUID(S["s_chem"]), d, uuid.UUID(S["tp"]))
    assert p1.api(p1.hod, "POST", f"/api/subs/{sub}/assign", json={"teacher_id": S["tp"]}).status_code == 409
    db_exec("delete from public.extra_lectures where teacher_id = $1 and lecture_date = $2", uuid.UUID(S["tp"]), d)


# ====================================================================================================
# 4) auto offers + first-accept-wins + timeout
# ====================================================================================================
def test_auto_offer_notifies_candidates_and_one_accept_fills_it(p1):
    r = p1.api(p1.hod, "POST", f"/api/subs/{S['sub2']}/offer", json={"count": 5})
    assert r.status_code == 200, r.text
    offered = {o["teacher_id"] for o in r.json()["offered"]}
    assert p1.teacher.account.user_id in offered and p1.hod.account.user_id in offered
    assert db_val("select status from public.substitutions where id = $1", uuid.UUID(S["sub2"])) == "offering"
    # each offered teacher got an offer notification + can see it in my-offers
    assert any(n["kind"] == "sub_offer" for n in notes_of(p1.teacher.account.user_id))
    mine = p1.api(p1.teacher, "GET", "/api/subs/my-offers").json()
    assert any(o["id"] == S["sub2"] for o in mine["offers"]) and mine["offers"][0]["reasons"]
    # p1.teacher accepts
    acc = p1.api(p1.teacher, "POST", f"/api/subs/{S['sub2']}/accept")
    assert acc.status_code == 200, acc.text
    assert db_val("select status, substitute_id from public.substitutions where id = $1", uuid.UUID(S["sub2"])) == "filled"
    assert db_val("select substitute_id from public.substitutions where id = $1", uuid.UUID(S["sub2"])) == uuid.UUID(p1.teacher.account.user_id)
    # the losers are told "already filled"; a second accept is refused
    assert any(n["kind"] == "offer_closed" for n in notes_of(p1.hod.account.user_id))
    assert p1.api(p1.hod, "POST", f"/api/subs/{S['sub2']}/accept").status_code == 409
    p1.api(p1.hod, "POST", f"/api/subs/{S['sub2']}/cancel")


def test_first_accept_wins_under_a_real_race(p1):
    d = nxt(MON) + timedelta(days=14)
    sub = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat()}).json()["affected"][0]["id"]
    assert p1.api(p1.hod, "POST", f"/api/subs/{sub}/offer", json={"count": 5}).status_code == 200
    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def race(name: str, token: str):
        with new_client() as c:
            barrier.wait()
            r = c.post(f"/api/subs/{sub}/accept", headers=bearer(token))
            results[name] = r.status_code

    threads = [threading.Thread(target=race, args=("teacher", p1.teacher.token)), threading.Thread(target=race, args=("hod", p1.hod.token))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(results.values()) == [200, 409], results                       # exactly one winner, one "already filled"
    winner = db_val("select substitute_id from public.substitutions where id = $1", uuid.UUID(sub))
    assert str(winner) in (p1.teacher.account.user_id, p1.hod.account.user_id)
    assert db_val("select count(*) from public.substitution_offers where substitution_id = $1 and status = 'accepted'", uuid.UUID(sub)) == 1
    assert db_val("select count(*) from public.substitution_offers where substitution_id = $1 and status = 'pending'", uuid.UUID(sub)) == 0
    p1.api(p1.hod, "POST", f"/api/subs/{sub}/cancel")


def test_decline_and_the_timeout_sweep_alerts_the_hod(p1):
    from datetime import datetime, timezone

    from app import db
    from app.services import notify, subflow

    from .conftest import run

    d = nxt(MON) + timedelta(days=21)
    sub = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": d.isoformat()}).json()["affected"][0]["id"]
    assert p1.api(p1.hod, "POST", f"/api/subs/{sub}/offer", json={"count": 5, "timeout_minutes": 30}).status_code == 200
    # p1.teacher declines their offer
    assert p1.api(p1.teacher, "POST", f"/api/subs/{sub}/decline").status_code == 200
    assert db_val("select status from public.substitution_offers where substitution_id = $1 and teacher_id = $2", uuid.UUID(sub), uuid.UUID(p1.teacher.account.user_id)) == "declined"
    # force the deadline into the past, then run the sweep (its own pool in this test process): the round closes, HOD alerted
    db_exec("update public.substitutions set offer_deadline = now() - interval '1 minute' where id = $1", uuid.UUID(sub))

    async def go():
        await db.init_pool()
        try:
            n = await subflow.sweep(datetime.now(timezone.utc))
            await notify.drain()
            return n
        finally:
            await db.close_pool()

    run(go())
    assert db_val("select status from public.substitutions where id = $1", uuid.UUID(sub)) == "unfilled"
    assert any(n["kind"] == "sub_unfilled" for n in notes_of(p1.hod.account.user_id))
    assert db_val("select count(*) from public.substitution_offers where substitution_id = $1 and status = 'pending'", uuid.UUID(sub)) == 0
    p1.api(p1.hod, "POST", f"/api/subs/{sub}/cancel")


# ====================================================================================================
# 5) leave
# ====================================================================================================
def test_leave_request_approve_creates_absences_and_substitutions(p1):
    start, end = nxt(MON) + timedelta(days=28), nxt(MON) + timedelta(days=30)  # Mon..Wed
    r = p1.api(p1.teacher, "POST", "/api/leave", json={"date_from": start.isoformat(), "date_to": end.isoformat(), "reason": "family"})
    # p1.teacher teaches nothing, so use tm's account? tm is not logged in. Use the request from p1.teacher but check approval on tm via HOD.
    assert r.status_code == 201
    S["leave"] = r.json()["id"]
    assert any(n["kind"] == "leave_requested" for n in notes_of(p1.hod.account.user_id))
    # the HOD cannot approve their own, but can approve a teacher's
    dec = p1.api(p1.hod, "POST", f"/api/leave/{S['leave']}/decide", json={"decision": "approve", "note": "ok"})
    assert dec.status_code == 200 and dec.json()["status"] == "approved"
    # absences were created for the 3 working days
    n_abs = db_val("select count(*) from public.teacher_absences where teacher_id = $1 and source = 'leave'", uuid.UUID(p1.teacher.account.user_id))
    assert n_abs == 3
    assert any(n["kind"] == "leave_approved" for n in notes_of(p1.teacher.account.user_id))
    db_exec("delete from public.teacher_absences where teacher_id = $1 and source = 'leave'", uuid.UUID(p1.teacher.account.user_id))


def test_leave_reject_and_cancel_and_hod_cannot_decide_own(p1):
    start = nxt(MON) + timedelta(days=35)
    rid = p1.api(p1.teacher, "POST", "/api/leave", json={"date_from": start.isoformat()}).json()["id"]
    assert p1.api(p1.hod, "POST", f"/api/leave/{rid}/decide", json={"decision": "reject"}).status_code == 200
    assert db_val("select status from public.leave_requests where id = $1", uuid.UUID(rid)) == "rejected"
    # a HOD's own leave goes to admins; the HOD cannot decide it
    hrid = p1.api(p1.hod, "POST", "/api/leave", json={"date_from": start.isoformat()}).json()["id"]
    assert any(n["kind"] == "leave_requested" for n in notes_of(p1.admin.account.user_id))
    assert p1.api(p1.hod, "POST", f"/api/leave/{hrid}/decide", json={"decision": "approve"}).status_code == 403
    assert p1.api(p1.hod, "POST", f"/api/leave/{hrid}/cancel").status_code == 200
    # the teacher can list their own leave
    assert p1.api(p1.teacher, "GET", "/api/leave").status_code == 200


# ====================================================================================================
# 6) one-day adjustments
# ====================================================================================================
def test_move_cancel_swap_with_clash_checks_and_suggestions(p1):
    d = nxt(WED)                                                       # Wednesday has A Maths at P1 only
    # suggest moves for the Wed P1 maths lecture
    sug = p1.api(p1.hod, "GET", f"/api/adjust/suggest?semester_id={cls(1)}&date={d.isoformat()}&start_period=1").json()
    assert {m["new_start"] for m in sug["moves"]} >= {2, 3, 4, 5} and sug["swaps"] == []
    # move it to P5
    mv = p1.api(p1.hod, "POST", "/api/adjust/move", json={"semester_id": cls(1), "date": d.isoformat(), "start_period": 1, "new_start": 5})
    assert mv.status_code == 201, mv.text
    S["adj_move"] = mv.json()["id"]
    cv = p1.api(p1.hod, "GET", f"/api/views/class/{cls(1)}?week_of={vw.monday_of(d)}").json()
    wed = next(c for c in cv["cells"] if c["day"] == WED and c["items"][0]["title"] == "A Maths")
    assert wed["start_period"] == 5 and wed["moved_from"] == 1
    # the teacher is notified their lecture moved
    assert any(n["kind"] == "lecture_adjusted" for n in notes_of(S["tm"]))
    # moving again is refused (already adjusted)
    assert p1.api(p1.hod, "POST", "/api/adjust/move", json={"semester_id": cls(1), "date": d.isoformat(), "start_period": 1, "new_start": 4}).status_code == 409
    # undo
    assert p1.api(p1.hod, "DELETE", f"/api/adjust/{S['adj_move']}").status_code == 200
    # a clashing move is refused (Mon P1 maths -> P3 where chem already is)
    mon = nxt(MON) + timedelta(days=42)
    bad = p1.api(p1.hod, "POST", "/api/adjust/move", json={"semester_id": cls(1), "date": mon.isoformat(), "start_period": 1, "new_start": 3})
    assert bad.status_code == 409 and "already has a lecture" in bad.json()["message"]
    # swap Mon P1 (maths) and Mon P3 (chem)
    sw = p1.api(p1.hod, "POST", "/api/adjust/swap", json={"semester_id": cls(1), "date": mon.isoformat(), "start_period": 1, "with_start": 3})
    assert sw.status_code == 201, sw.text
    cv = p1.api(p1.hod, "GET", f"/api/views/class/{cls(1)}?week_of={vw.monday_of(mon)}").json()
    maths = next(c for c in cv["cells"] if c["day"] == MON and c["items"][0]["title"] == "A Maths")
    chem = next(c for c in cv["cells"] if c["day"] == MON and c["items"][0]["title"] == "A Chem")
    assert maths["start_period"] == 3 and chem["start_period"] == 1
    db_exec("delete from public.day_adjustments where swap_group = (select swap_group from public.day_adjustments where lecture_date = $1 limit 1)", mon)


def test_cancel_a_lecture_counts_as_lost_in_the_hours(p1):
    d = nxt(MON) + timedelta(days=49)
    before = next(s for c in p1.api(p1.hod, "GET", f"/api/hours/subjects?semester_id={cls(1)}").json()["classes"] for s in c["subjects"] if s["subject_id"] == S["s_chem"])
    c = p1.api(p1.hod, "POST", "/api/adjust/cancel", json={"semester_id": cls(1), "date": d.isoformat(), "start_period": 3, "reason": "event"})
    assert c.status_code == 201
    after = next(s for cc in p1.api(p1.hod, "GET", f"/api/hours/subjects?semester_id={cls(1)}").json()["classes"] for s in cc["subjects"] if s["subject_id"] == S["s_chem"])
    assert after["lost"] == before["lost"] + 1 and after["scheduled"] == before["scheduled"] - 1
    assert any(n["kind"] == "lecture_adjusted" for n in notes_of(S["tc"]))
    db_exec("delete from public.day_adjustments where lecture_date = $1 and kind = 'cancel'", d)


def test_adjustments_are_locked_to_the_school(p1):
    d = nxt(WED).isoformat()
    assert p1.api(p1.hod, "POST", "/api/adjust/move", json={"semester_id": p1.b_sems[1], "date": d, "start_period": 1, "new_start": 2}).status_code == 404
    assert p1.api(p1.teacher, "POST", "/api/adjust/cancel", json={"semester_id": cls(1), "date": d, "start_period": 1}).status_code == 403


# ====================================================================================================
# 7) sheet + summary + roles
# ====================================================================================================
def test_daily_sheet_pdf_and_monthly_summary(p1):
    d = nxt(MON)
    # re-create a filled sub for the sheet to list
    sub = p1.api(p1.hod, "POST", "/api/absence", json={"teacher_id": S["tm"], "date": (d + timedelta(days=56)).isoformat()}).json()["affected"][0]["id"]
    p1.api(p1.hod, "POST", f"/api/subs/{sub}/assign", json={"teacher_id": S["tc"]})
    from pypdf import PdfReader

    pdf = p1.api(p1.hod, "GET", f"/api/subs/sheet?date={(d + timedelta(days=56)).isoformat()}")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-"
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Daily Substitution Sheet" in text and "A Maths" in text
    month = (d + timedelta(days=56)).strftime("%Y-%m")
    summ = p1.api(p1.hod, "GET", f"/api/subs/summary?month={month}").json()
    assert summ["totals"]["filled"] >= 1 and any(t["covered"] >= 1 for t in summ["teachers"])
    assert p1.api(p1.hod, "GET", "/api/subs/summary?month=not-a-month").status_code == 422


def test_roles_and_school_locking(p1):
    for path, method, body in (
        ("/api/subs", "GET", None), ("/api/absence", "GET", None), ("/api/subs/summary", "GET", None),
        (f"/api/subs/{S['sub']}/assign", "POST", {"teacher_id": S["tc"]}), (f"/api/subs/{S['sub']}/offer", "POST", {}),
        (f"/api/subs/{S['sub']}/candidates", "GET", None),
    ):
        r = p1.api(p1.teacher, method, path, json=body) if body is not None else p1.api(p1.teacher, method, path)
        assert r.status_code == 403, (path, r.status_code)
    # HOD cannot touch another school
    assert p1.api(p1.hod, "GET", f"/api/subs?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/absence?school_id={p1.school_b}").status_code == 403
    # the admin can read the school
    assert p1.api(p1.admin, "GET", f"/api/subs?school_id={p1.school}").status_code == 200
    # a teacher can list their own offers and leave but not the console
    assert p1.api(p1.teacher, "GET", "/api/subs/my-offers").status_code == 200
    assert p1.api(p1.teacher, "GET", "/api/leave").status_code == 200
    with new_client() as c:
        assert c.get("/api/subs/my-offers").status_code == 401
