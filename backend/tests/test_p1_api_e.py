"""Phase 1 API tests, part E: COMBINED sessions (master plan V9, Section 8).

One locked slot may cover SEVERAL classes together (e.g. SOL for two classes with one teacher). It is ONE session:
 - the teacher is NOT double-booked (no false clash),
 - EVERY covered class is occupied in that slot,
 - a real clash (another subject / another teacher's slot for a covered class, the teacher elsewhere) is still caught.
Runs in the throw-away school against the REAL API + Supabase. The tests build on each other (run them in file order).
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from .conftest import connect_one, db_fetch, db_val, mail_for, run

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    # an own little course, so these tests do not depend on what the other test files left in "ZZ Course"
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ Combo', 'ZZK', 5) returning id", uuid.UUID(p1.school))
    rows = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 5) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in rows}
    S["label"] = {n: f"ZZ Combo - Sem {n}" for n in S["sem"]}
    yield


def class_id(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over):
    body = {"semester_id": class_id(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, subject_ids: list[str] | None = None, **over):
    body = {"full_name": f"Combo {key}", "email": mail_for(f"e-{key}"), "subject_ids": subject_ids or []}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def assign(p1, subject_id: str, teacher_id: str):
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id})
    assert r.status_code == 200, r.text


def slot(p1, who, classes: list[int], day: int, start: int, kind: str = "SOL", **over):
    body = {"semester_ids": [class_id(n) for n in classes], "day": day, "start_period": start, "kind": kind}
    body.update(over)
    return p1.api(who, "POST", "/api/fixed-slots", json=body)


def slots(p1) -> dict[str, dict]:
    return {s["id"]: s for s in p1.api(p1.hod, "GET", "/api/fixed-slots").json()["slots"]}


def rows_of(key: str):
    return db_fetch("select id, combined_group_id, semester_id, teacher_id, subject_id, day, start_period, length from public.fixed_slots "
                    "where coalesce(combined_group_id, id) = $1 order by semester_id", uuid.UUID(key))


# ====================================================================================================
# 1) ONE session, one teacher, no false clash
# ====================================================================================================
def test_one_session_covers_several_classes_with_one_teacher(p1):
    sol1 = mk_subject(p1, 1, "K1-SOL", "Science of Living I", special_kind="SOL")
    sol3 = mk_subject(p1, 3, "K3-SOL", "Science of Living III", special_kind="SOL")
    teacher = mk_teacher(p1, "sol", [sol1, sol3])
    assign(p1, sol1, teacher)
    assign(p1, sol3, teacher)
    S.update(sol1=sol1, sol3=sol3, teacher=teacher)

    r = slot(p1, p1.hod, [1, 3], 3, 5)                                   # Wednesday P5+P6, both classes, teacher from the assignments
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["combined"] is True and body["classes"] == 2 and body["length"] == 2
    S["combo"] = body["id"]

    rows = rows_of(S["combo"])
    assert len(rows) == 2                                                # one row per covered class ...
    assert {r["combined_group_id"] for r in rows} == {uuid.UUID(S["combo"])}   # ... all in ONE session
    assert {str(r["teacher_id"]) for r in rows} == {teacher}             # one teacher
    assert {str(r["semester_id"]) for r in rows} == {class_id(1), class_id(3)}
    assert {str(r["subject_id"]) for r in rows} == {sol1, sol3}          # each class got its OWN SOL subject
    assert {(r["day"], r["start_period"], r["length"]) for r in rows} == {(3, 5, 2)}

    # to the outside it is ONE slot
    data = p1.api(p1.hod, "GET", "/api/fixed-slots").json()
    mine = [s for s in data["slots"] if s["id"] == S["combo"]]
    assert len(mine) == 1
    s = mine[0]
    assert s["combined"] is True and set(s["semester_ids"]) == {class_id(1), class_id(3)} and len(s["classes"]) == 2
    assert s["class_label"] == f"{S['label'][1]} + {S['label'][3]}"
    assert s["teacher_id"] == teacher and s["teacher_name"] == "Combo sol" and s["kind"] == "SOL" and s["length"] == 2
    assert s["problems"] == []                                           # NO false teacher clash
    assert {c["subject_id"] for c in s["classes"]} == {sol1, sol3}
    assert sum(1 for x in data["slots"] if x["kind"] == "SOL" and x["label"] == s["label"] and x["day"] == 3) >= 1
    cls = {c["semester_id"]: c for c in data["classes"]}
    assert cls[class_id(1)]["has_sol"] and cls[class_id(3)]["has_sol"]   # both classes count as having their SOL
    assert not cls[class_id(5)]["has_sol"]


def test_the_check_endpoint_knows_combined_slots_too(p1):
    c = p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_ids": [class_id(1), class_id(5)], "day": 1, "start_period": 1, "kind": "PE"})
    assert c.status_code == 200 and c.json()["ok"] is True and c.json()["combined"] is True and c.json()["length"] == 2
    assert db_val("select count(*) from public.fixed_slots where semester_id = $1 and day = 1", uuid.UUID(class_id(5))) == 0   # check never saves


# ====================================================================================================
# 2) EVERY covered class is occupied
# ====================================================================================================
@pytest.mark.parametrize("n", [1, 3])
@pytest.mark.parametrize("start", [5, 6])
def test_every_covered_class_is_blocked_in_the_combined_slot(p1, n, start):
    r = slot(p1, p1.hod, [n], 3, start, "OTHER", length=1, label="Seminar")
    assert r.status_code == 422 and r.json()["code"] == "SLOT_NOT_POSSIBLE" and "already has" in r.json()["message"], r.text
    c = p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": class_id(n), "day": 3, "start_period": start, "kind": "OTHER", "length": 1, "label": "Seminar"})
    assert c.status_code == 200 and c.json()["ok"] is False


def test_the_periods_around_the_combined_slot_stay_free_for_each_class(p1):
    for n, start in ((1, 4), (3, 4)):                                    # P4 ends where P5 starts - not an overlap
        r = slot(p1, p1.hod, [n], 3, start, "OTHER", length=1, label="Before SOL")
        assert r.status_code == 201, r.text
        assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{r.json()['id']}").status_code == 200


def test_a_combined_slot_cannot_cover_a_class_that_is_already_busy(p1):
    r = slot(p1, p1.hod, [3, 5], 3, 4, "OTHER", length=2, label="Guest lecture")      # periods 4-5: class 3 is busy at P5
    assert r.status_code == 422 and S["label"][3] in r.json()["message"] and "already has" in r.json()["message"], r.text
    assert S["label"][5] not in r.json()["message"]                                    # class 5 is free - only the busy class is named
    assert db_val("select count(*) from public.fixed_slots where semester_id = $1", uuid.UUID(class_id(5))) == 0


# ====================================================================================================
# 3) a REAL clash is still caught
# ====================================================================================================
def test_another_subject_for_a_covered_class_at_the_same_time_is_refused(p1):
    other = mk_subject(p1, 3, "K3-ALG", "Algorithms", credits=3, semester_hours=45)
    teacher2 = mk_teacher(p1, "alg", [other])
    assign(p1, other, teacher2)
    r = slot(p1, p1.hod, [3], 3, 5, "OTHER", length=1, label="Algorithms tutorial", subject_id=other)
    assert r.status_code == 422 and "already has" in r.json()["message"], r.text
    r = slot(p1, p1.hod, [3], 3, 6, "OTHER", length=1, label="Algorithms tutorial", subject_id=other)   # overlapping the 2nd period
    assert r.status_code == 422
    assert db_val("select count(*) from public.fixed_slots where subject_id = $1", uuid.UUID(other)) == 0
    S["other"], S["teacher2"] = other, teacher2


def test_the_teacher_of_a_combined_session_is_busy_for_any_other_class(p1):
    r = slot(p1, p1.hod, [5], 3, 5, "OTHER", length=2, label="Extra", teacher_id=S["teacher"])
    assert r.status_code == 422 and "already in a locked slot" in r.json()["message"], r.text
    assert "Combo sol" in r.json()["message"]
    assert slot(p1, p1.hod, [5], 3, 5, "OTHER", length=2, label="Extra").status_code == 201      # without that teacher the class is free
    extra = next(s["id"] for s in slots(p1).values() if s["label"] == "Extra")
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{extra}").status_code == 200


def test_the_database_itself_knows_one_session_from_two(p1):
    async def attempt(group: str | None) -> str:
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            try:
                async with conn.transaction():
                    await conn.execute(
                        "insert into public.fixed_slots (school_id, term_id, semester_id, day, start_period, length, kind, label, teacher_id, combined_group_id) "
                        "values ($1,$2,$3,3,5,2,'OTHER','Raw',$4,$5)",
                        uuid.UUID(p1.school), term, uuid.UUID(class_id(5)), uuid.UUID(S["teacher"]), uuid.UUID(group) if group else None)
                    raise _Rollback()                                      # never keep the row
            except _Rollback:
                return "accepted"
            except asyncpg.ExclusionViolationError:
                return "refused"
        finally:
            await conn.close()

    assert run(attempt(None)) == "refused"              # same teacher, same time, NOT the same session -> the database says no
    assert run(attempt(S["combo"])) == "accepted"       # the same session may cover one more class with that teacher
    assert db_val("select count(*) from public.fixed_slots where label = 'Raw'") == 0


class _Rollback(Exception):
    pass


def test_different_teachers_in_the_covered_classes_need_a_decision(p1):
    pe1 = mk_subject(p1, 1, "K1-PE", "PE & Yoga I", credits=1, special_kind="PE")
    pe5 = mk_subject(p1, 5, "K5-PE", "PE & Yoga V", credits=1, special_kind="PE")
    ta = mk_teacher(p1, "pe-a", [pe1])
    tb = mk_teacher(p1, "pe-b", [pe5])
    assign(p1, pe1, ta)
    assign(p1, pe5, tb)
    S.update(pe1=pe1, pe5=pe5, ta=ta, tb=tb)
    r = slot(p1, p1.hod, [1, 5], 1, 5, "PE")
    assert r.status_code == 422 and "different teachers" in r.json()["message"] and "Combo pe-a" in r.json()["message"], r.text
    r = slot(p1, p1.hod, [1, 5], 1, 5, "PE", teacher_id=ta)                          # explicit teacher: must be able to teach EACH class's subject
    assert r.status_code == 422 and "cannot teach" in r.json()["message"] and S["label"][5] in r.json()["message"], r.text
    assert p1.api(p1.hod, "PATCH", f"/api/users/{ta}", json={"subject_ids": [pe1, pe5]}).status_code == 200
    r = slot(p1, p1.hod, [1, 5], 1, 5, "PE", teacher_id=ta)
    assert r.status_code == 201, r.text
    S["pe"] = r.json()["id"]
    assert {str(x["subject_id"]) for x in rows_of(S["pe"])} == {pe1, pe5}


def test_a_combined_slot_takes_each_class_subject_so_a_subject_cannot_be_chosen(p1):
    r = slot(p1, p1.hod, [1, 3], 4, 1, "OTHER", length=1, label="Talk", subject_id=S["other"])
    assert r.status_code == 422 and "leave the subject empty" in r.json()["message"], r.text


def test_visiting_days_are_enforced_for_a_combined_session_too(p1):
    vf = mk_teacher(p1, "visit", [], is_visiting=True, visiting_days=[1, 2], max_weekly_hours=12)
    r = slot(p1, p1.hod, [3, 5], 4, 1, "OTHER", length=1, label="Guest", teacher_id=vf)           # Thursday
    assert r.status_code == 422 and "visiting faculty" in r.json()["message"], r.text
    assert slot(p1, p1.hod, [3, 5], 2, 1, "OTHER", length=1, label="Guest", teacher_id=vf).status_code == 201   # Tuesday is fine
    guest = next(s["id"] for s in slots(p1).values() if s["label"] == "Guest")
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{guest}").status_code == 200


# ====================================================================================================
# 4) edit / shrink / delete a combined session as ONE slot
# ====================================================================================================
def test_move_a_combined_session_moves_every_class(p1):
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"day": 4, "start_period": 3})   # Thursday P3+P4 = around lunch
    assert r.status_code == 200 and r.json()["combined"] is True and any("lunch" in n.lower() for n in r.json()["notes"]), r.text
    assert {(x["day"], x["start_period"]) for x in rows_of(S["combo"])} == {(4, 3)}
    assert len(rows_of(S["combo"])) == 2
    s = slots(p1)[S["combo"]]
    assert (s["day"], s["start_period"], s["time"]) == (4, 3, "12:10-14:40")
    # the old place is free again for both classes
    for n in (1, 3):
        r = slot(p1, p1.hod, [n], 3, 5, "OTHER", length=1, label="Free again")
        assert r.status_code == 201, r.text
        assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{r.json()['id']}").status_code == 200
    # ... and the NEW place is blocked for both
    assert slot(p1, p1.hod, [1], 4, 3, "OTHER", length=1, label="Clash").status_code == 422
    assert slot(p1, p1.hod, [3], 4, 4, "OTHER", length=1, label="Clash").status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"day": 3, "start_period": 5}).status_code == 200   # back to Wed P5


def test_add_and_remove_classes_of_a_session(p1):
    # add class 5 (it has no SOL subject: the slot just has no subject for it)
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": [class_id(1), class_id(3), class_id(5)]})
    assert r.status_code == 200 and r.json()["classes"] == 3, r.text
    assert len(rows_of(S["combo"])) == 3
    s = slots(p1)[S["combo"]]
    assert s["combined"] and len(s["semester_ids"]) == 3 and s["teacher_id"] == S["teacher"]
    assert slot(p1, p1.hod, [5], 3, 5, "OTHER", length=1, label="Blocked now").status_code == 422        # the new class is occupied too
    # remove class 3 again
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": [class_id(1), class_id(5)]})
    assert r.status_code == 200 and r.json()["classes"] == 2, r.text
    assert {str(x["semester_id"]) for x in rows_of(S["combo"])} == {class_id(1), class_id(5)}
    r = slot(p1, p1.hod, [3], 3, 5, "OTHER", length=1, label="Free")
    assert r.status_code == 201                                                                                  # class 3 is free again
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{r.json()['id']}").status_code == 200
    # shrink to ONE class: it is a normal slot again
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": [class_id(1)]})
    assert r.status_code == 200 and r.json()["combined"] is False, r.text
    left = db_fetch("select id, combined_group_id, semester_id from public.fixed_slots where label like 'Science of Living%' and day = 3 and start_period = 5 and semester_id = any($1::uuid[])",
                    [uuid.UUID(class_id(n)) for n in (1, 3, 5)])
    assert len(left) == 1 and left[0]["combined_group_id"] is None and str(left[0]["semester_id"]) == class_id(1)
    s = slots(p1)[str(left[0]["id"])]
    assert s["combined"] is False and s["semester_ids"] == [class_id(1)] and s["subject_id"] == S["sol1"]
    # and grow it again: one class -> combined
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{left[0]['id']}", json={"semester_ids": [class_id(1), class_id(3)]})
    assert r.status_code == 200 and r.json()["combined"] is True
    S["combo"] = r.json()["id"]
    assert {str(x["semester_id"]) for x in rows_of(S["combo"])} == {class_id(1), class_id(3)}
    assert {str(x["subject_id"]) for x in rows_of(S["combo"])} == {S["sol1"], S["sol3"]}


def test_a_class_without_pe_sol_cannot_join_a_combined_pe(p1):
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['pe']}").status_code == 200                   # remove the combined PE (whole session)
    assert db_val("select count(*) from public.fixed_slots where kind = 'PE' and semester_id = any($1::uuid[])", [uuid.UUID(class_id(1)), uuid.UUID(class_id(5))]) == 0
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{class_id(5)}/no-pe-sol", json={"no_pe_sol": True}).status_code == 200
    r = slot(p1, p1.hod, [3, 5], 2, 5, "PE")
    assert r.status_code == 422 and "no PE / SOL" in r.json()["message"] and S["label"][5] in r.json()["message"], r.text
    assert S["label"][3] + ": " not in r.json()["message"]                                              # class 3 itself is fine
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{class_id(5)}/no-pe-sol", json={"no_pe_sol": False}).status_code == 200


def test_delete_removes_the_whole_session_and_is_audited_once(p1):
    before = db_val("select coalesce(max(id), 0) from public.audit_log")
    r = p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['combo']}")
    assert r.status_code == 200
    assert db_val("select count(*) from public.fixed_slots where coalesce(combined_group_id, id) = $1", uuid.UUID(S["combo"])) == 0
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['combo']}").status_code == 404
    rows = db_fetch("select action, entity, summary from public.audit_log where id > $1 and entity = 'fixed_slot' order by id", before)
    assert len(rows) == 1 and rows[0]["action"] == "delete" and S["label"][1] in rows[0]["summary"] and S["label"][3] in rows[0]["summary"]


def test_creating_a_combined_session_is_audited_once_with_all_classes(p1):
    before = db_val("select coalesce(max(id), 0) from public.audit_log")
    r = slot(p1, p1.hod, [1, 3], 3, 5)
    assert r.status_code == 201
    S["combo"] = r.json()["id"]
    rows = db_fetch("select action, entity, summary, details from public.audit_log where id > $1 and entity = 'fixed_slot' order by id", before)
    assert len(rows) == 1 and rows[0]["action"] == "create"
    assert "combined session" in rows[0]["summary"] and S["label"][1] in rows[0]["summary"] and S["label"][3] in rows[0]["summary"]


# ====================================================================================================
# 5) workload, checks of the input, roles and schools
# ====================================================================================================
def test_the_teacher_workload_counts_a_combined_session_once(p1):
    # SOL in 2 classes at the same time = ONE double block = 2 lectures for the teacher (not 4)
    teachers = p1.api(p1.hod, "GET", "/api/assignments").json()["teachers"]
    me = next(t for t in teachers if t["id"] == S["teacher"])
    assert me["weekly_load"] == 2, me


def test_input_of_a_combined_slot_is_checked(p1):
    base = {"day": 2, "start_period": 1, "kind": "PE"}
    post = lambda body: p1.api(p1.hod, "POST", "/api/fixed-slots", json=body)  # noqa: E731
    assert post({**base}).status_code == 422                                                        # no class at all
    assert post({**base, "semester_ids": []}).status_code == 422
    assert post({**base, "semester_id": class_id(1), "semester_ids": [class_id(3)]}).status_code == 422   # both forms together
    assert post({**base, "semester_ids": [class_id(1), class_id(1)]}).status_code == 422                  # the same class twice
    assert post({**base, "semester_ids": [str(uuid.uuid4()) for _ in range(13)]}).status_code == 422      # more than 12
    assert post({**base, "semester_ids": ["not-an-id"]}).status_code in (404, 422)
    assert post({**base, "semester_ids": [str(uuid.uuid4())]}).status_code == 404
    assert post({**base, "semester_ids": [class_id(1), class_id(2)]}).status_code == 409                    # Sem 2 does not run in an odd term
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": []}).status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": [class_id(1), class_id(1)]}).status_code == 422
    assert db_val("select count(*) from public.fixed_slots where day = 2 and start_period = 1 and kind = 'PE' and school_id = $1", uuid.UUID(p1.school)) == 0


def test_combined_slots_are_locked_to_roles_and_schools(p1):
    foreign = p1.b_sems[1]
    # an HOD can never reach another school's class - not even inside a combined slot
    r = p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_ids": [class_id(1), foreign], "day": 2, "start_period": 1, "kind": "PE"})
    assert r.status_code == 404
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"semester_ids": [class_id(1), foreign]})
    assert r.status_code == 404
    assert db_val("select count(*) from public.fixed_slots where semester_id = $1", uuid.UUID(foreign)) in (0, 1)   # (part B may have put one raw 'Foreign PE' row there)
    # the admin may use any school, but one combined session never mixes schools
    r = p1.api(p1.admin, "POST", "/api/fixed-slots", json={"semester_ids": [class_id(1), foreign], "day": 2, "start_period": 1, "kind": "PE"})
    assert r.status_code == 422 and r.json()["code"] == "MIXED_SCHOOLS", r.text
    # teachers cannot lock anything
    assert p1.api(p1.teacher, "POST", "/api/fixed-slots", json={"semester_ids": [class_id(1), class_id(3)], "day": 2, "start_period": 1, "kind": "PE"}).status_code == 403
    assert p1.api(p1.teacher, "PATCH", f"/api/fixed-slots/{S['combo']}", json={"day": 2}).status_code == 403
    assert p1.api(p1.teacher, "DELETE", f"/api/fixed-slots/{S['combo']}").status_code == 403


def test_a_combined_slot_of_another_school_does_not_exist_for_an_hod(p1):
    async def foreign_group():
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            gid = uuid.uuid4()
            for n in (2, 3):
                await conn.execute(
                    "insert into public.fixed_slots (id, combined_group_id, school_id, term_id, semester_id, day, start_period, length, kind, label) "
                    "values (coalesce($1::uuid, gen_random_uuid()), $2, $3, $4, $5, 1, 1, 2, 'PE', 'Foreign combined')",
                    gid if n == 2 else None, gid, uuid.UUID(p1.school_b), term, uuid.UUID(p1.b_sems[n]))
            return str(gid)
        finally:
            await conn.close()

    gid = run(foreign_group())
    assert gid not in slots(p1)
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{gid}", json={"day": 2}).status_code == 404
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{gid}").status_code == 404
    assert db_val("select count(*) from public.fixed_slots where combined_group_id = $1", uuid.UUID(gid)) == 2
    assert p1.api(p1.admin, "DELETE", f"/api/fixed-slots/{gid}").status_code == 200        # the admin may remove it - the whole session
    assert db_val("select count(*) from public.fixed_slots where combined_group_id = $1", uuid.UUID(gid)) == 0


def test_a_teacher_in_a_combined_session_cannot_be_deactivated_and_is_counted_once(p1):
    r = p1.api(p1.hod, "POST", f"/api/users/{S['teacher']}/deactivate")
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"
    assert "still has 3 assignment(s)" in r.json()["message"], r.json()["message"]            # 2 assignments + ONE session (not one per class)
