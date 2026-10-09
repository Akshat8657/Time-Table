"""Phase 1 API tests, part B: subjects + electives, assignment (+ co-teacher), timing (day-wise schedule),
fixed slots, holidays & events, semester dates.

Runs in a throw-away school against the REAL API + Supabase. The tests build on each other (run them in file order).
NOTE: every request makes several round trips to the remote database (~0.5 s each), so this file takes a few minutes.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from .conftest import RUN_ID, bearer, connect_one, db_exec, db_fetch, db_val, mail_for, run

S: dict = {}
ODD = "Odd 2026"
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}


def fields(resp) -> dict:
    return resp.json().get("fields", {})


@pytest.fixture(scope="module", autouse=True)
def odd_term(p1):
    r = p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    assert r.status_code == 200
    # the timing tests expect the real college timing (10:00-16:50, 1 hour lectures, 5 min breaks, Mon-Fri)
    r = p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL)
    assert r.status_code == 200, r.text


def sem(p1, n):
    return p1.sems[n]


def subj(p1, who, n, _code, **over):
    body = {"semester_id": sem(p1, n), "name": f"Subject {_code}", "code": _code, "credits": 4, "semester_hours": 60}
    body.update(over)
    return p1.api(who, "POST", "/api/subjects", json=body)


def new_teacher(p1, key, **over):
    body = {"full_name": f"Tea {key}", "email": mail_for(f"b-{key}")}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]


# ====================================================================================================
# 1) SUBJECTS & CREDITS
# ====================================================================================================
def test_hod_creates_subjects_for_running_classes(p1):
    r = subj(p1, p1.hod, 5, "DBMS5", name="Database Management", credits=4, semester_hours=60)
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []
    S["dbms"] = r.json()["id"]
    assert subj(p1, p1.hod, 1, "MATH1", name="Mathematics").status_code == 201
    assert subj(p1, p1.hod, 3, "OS3", name="Operating Systems", credits=3, semester_hours=45).status_code == 201

    listing = p1.api(p1.hod, "GET", "/api/subjects").json()
    assert listing["active_term"]["name"] == ODD
    row = next(s for s in listing["subjects"] if s["code"] == "DBMS5")
    assert (row["class_label"], row["credits"], row["semester_hours"], row["is_lab"], row["is_elective"]) == ("ZZ Course - Sem 5", 4, 60, False, False)
    assert row["teacher_id"] is None and row["co_teacher_id"] is None


def test_labs_are_typically_one_credit_and_never_get_a_credits_warning(p1):
    # plan V8: a lab is usually 1 credit yet always ONE 2-period block per week - no credits-vs-hours warning, ever
    for code, credits in (("LAB1", 1), ("LAB2", 2), ("LAB3", 3), ("LAB4", 4)):
        r = subj(p1, p1.hod, 1, code, name=f"Lab {code}", credits=credits, semester_hours=30, is_lab=True)
        assert r.status_code == 201 and r.json()["warnings"] == [], (code, r.json())
    row = next(s for s in p1.api(p1.hod, "GET", "/api/subjects?q=LAB1").json()["subjects"])
    assert row["is_lab"] is True and row["credits"] == 1
    r = p1.api(p1.hod, "PATCH", f"/api/subjects/{row['id']}", json={"credits": 3})
    assert r.status_code == 200 and r.json()["warnings"] == []


def test_pe_and_sol_hints(p1):
    r = subj(p1, p1.hod, 1, "PE1", name="Physical Education", credits=1, semester_hours=30, special_kind="PE")
    assert r.status_code == 201 and any("PE" in w and "credits" in w for w in r.json()["warnings"])
    S["pe1"] = r.json()["id"]
    assert subj(p1, p1.hod, 1, "SOL1", name="Science of Living", credits=1, semester_hours=30, special_kind="SOL").status_code == 201
    assert subj(p1, p1.hod, 1, "BADPE", special_kind="PE", is_lab=True).status_code == 422          # PE cannot also be a lab
    assert subj(p1, p1.hod, 1, "BADSOL", special_kind="SOL", is_elective=True).status_code == 422
    assert subj(p1, p1.hod, 1, "BADKIND", special_kind="GYM").status_code == 422


def test_subjects_of_classes_that_do_not_run_are_refused(p1):
    r = subj(p1, p1.hod, 2, "EVEN2")
    assert r.status_code == 409 and r.json()["code"] == "SEMESTER_NOT_ACTIVE" and ODD in r.json()["message"]
    assert db_val("select count(*) from public.subjects where code = 'EVEN2'") == 0


def test_subjects_need_an_active_term(p1):
    db_exec("update public.academic_terms set is_active = false")
    try:
        r = subj(p1, p1.hod, 1, "NOTERM")
        assert r.status_code == 409 and r.json()["code"] == "NO_ACTIVE_TERM"
        assert p1.api(p1.hod, "GET", "/api/subjects").json() == {"subjects": [], "active_term": None}
    finally:
        assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200


def test_listing_shows_only_the_running_semesters(p1):
    db_exec("insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours) values ($1,$2,'Even Subject','EVENHIDDEN',3,45)",
            uuid.UUID(p1.school), uuid.UUID(sem(p1, 2)))
    subjects = p1.api(p1.hod, "GET", "/api/subjects").json()["subjects"]
    assert {s["semester_number"] for s in subjects} <= {1, 3, 5}
    assert "EVENHIDDEN" not in {s["code"] for s in subjects}
    hidden = db_val("select id from public.subjects where code = 'EVENHIDDEN'")
    r = p1.api(p1.hod, "PATCH", f"/api/subjects/{hidden}", json={"credits": 5})
    assert r.status_code == 409 and r.json()["code"] == "SEMESTER_NOT_ACTIVE"
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{hidden}").status_code == 409


def test_subject_search_and_filters(p1):
    by = lambda qs: {s["code"] for s in p1.api(p1.hod, "GET", "/api/subjects" + qs).json()["subjects"]}  # noqa: E731
    assert by("?q=database") == {"DBMS5"}
    assert by("?q=dbms5") == {"DBMS5"}
    assert by(f"?semester_id={sem(p1, 3)}") == {"OS3"}
    assert "DBMS5" in by(f"?course_id={p1.course}") and "ZZB-1" not in by(f"?course_id={p1.course}")
    assert by("?q=zzzznothing") == set()
    assert p1.api(p1.hod, "GET", "/api/subjects?q=%25").status_code == 200      # '%' is just text, not a wildcard
    assert p1.api(p1.hod, "GET", "/api/subjects?semester_id=bad").status_code == 422


@pytest.mark.parametrize("over", [
    {"credits": 0}, {"credits": 31}, {"semester_hours": 0}, {"semester_hours": 401}, {"name": ""}, {"code": ""},
    {"name": "N" * 151}, {"code": "C" * 31}, {"credits": "four"}, {"unknown": 1}, {"elective_group_id": str(uuid.uuid4())},
])
def test_subject_input_is_validated(p1, over):
    r = subj(p1, p1.hod, 1, "VAL1", **over)
    assert r.status_code == 422, (over, r.text)
    assert db_val("select count(*) from public.subjects where code in ('VAL1')") == 0


def test_duplicate_subject_code_is_refused(p1):
    r = subj(p1, p1.hod, 3, "dbms5")          # same code, other case, other class - codes are unique per school
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_EXISTS"


def test_edit_and_delete_a_subject(p1):
    r = p1.api(p1.hod, "PATCH", f"/api/subjects/{S['dbms']}", json={"credits": 5, "semester_hours": 75, "name": "DBMS (Updated)"})
    assert r.status_code == 200
    row = next(s for s in p1.api(p1.hod, "GET", "/api/subjects?q=dbms5").json()["subjects"])
    assert (row["credits"], row["semester_hours"], row["name"]) == (5, 75, "DBMS (Updated)")
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['dbms']}", json={"code": "MATH1"}).status_code == 409
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['dbms']}", json={"credits": 99}).status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['dbms']}", json={"credits": 4, "semester_hours": 60, "name": "Database Management"}).status_code == 200
    tmp = subj(p1, p1.hod, 1, "TMP1").json()["id"]
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{tmp}").status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{tmp}").status_code == 404


def test_hod_is_locked_to_his_own_school_for_subjects(p1):
    assert p1.api(p1.hod, "POST", "/api/subjects", json={"semester_id": p1.b_sems[1], "name": "X", "code": "XB1", "credits": 3, "semester_hours": 40}).status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/subjects?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/subjects/groups?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{p1.b_subject}", json={"credits": 9}).status_code == 404
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{p1.b_subject}").status_code == 404
    assert db_val("select credits from public.subjects where id = $1", uuid.UUID(p1.b_subject)) == 3
    assert p1.api(p1.teacher, "GET", "/api/subjects").status_code == 403
    assert subj(p1, p1.teacher, 1, "TEACHERS").status_code == 403
    assert p1.api(p1.admin, "GET", "/api/subjects").status_code == 422        # admin must say which school
    assert p1.api(p1.admin, "GET", f"/api/subjects?school_id={p1.school}").status_code == 200
    assert subj(p1, p1.admin, 1, "ADMINMADE").status_code == 201               # the admin can do everything an HOD can


# ---------------------------------------------------------------------- electives (plan Section 9)
def test_elective_groups_and_the_same_credits_warning(p1):
    g = p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": sem(p1, 5), "name": "Group A"})
    assert g.status_code == 201
    gid = g.json()["id"]
    S["group"] = gid
    assert p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": sem(p1, 5), "name": "group a"}).status_code == 409   # same name
    assert p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": sem(p1, 2), "name": "Even group"}).status_code == 409  # class not running
    assert p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": p1.b_sems[1], "name": "Foreign"}).status_code == 404

    ai = subj(p1, p1.hod, 5, "AI5E", name="Artificial Intelligence", credits=4, is_elective=True, elective_group_id=gid)
    assert ai.status_code == 201 and ai.json()["warnings"] == []                      # alone in the group: nothing to compare
    cy = subj(p1, p1.hod, 5, "CY5E", name="Cyber Security", credits=3, semester_hours=45, is_elective=True, elective_group_id=gid)
    assert cy.status_code == 201
    assert any("different credits" in w for w in cy.json()["warnings"])               # 4 vs 3  -> warning
    ds = subj(p1, p1.hod, 5, "DS5E", name="Data Science", credits=4, is_elective=True, elective_group_id=gid)
    assert any("different credits" in w for w in ds.json()["warnings"])
    S["ai"], S["cy"], S["ds"] = ai.json()["id"], cy.json()["id"], ds.json()["id"]

    groups = p1.api(p1.hod, "GET", "/api/subjects/groups").json()["groups"]
    a = next(x for x in groups if x["id"] == gid)
    assert a["class_label"] == "ZZ Course - Sem 5" and {m["code"] for m in a["members"]} == {"AI5E", "CY5E", "DS5E"}
    assert any("Credits differ" in w for w in a["warnings"])

    r = p1.api(p1.hod, "PATCH", f"/api/subjects/{S['cy']}", json={"credits": 4, "semester_hours": 60})   # fix the credits -> warning gone
    assert r.status_code == 200 and r.json()["warnings"] == []
    a = next(x for x in p1.api(p1.hod, "GET", "/api/subjects/groups").json()["groups"] if x["id"] == gid)
    assert a["warnings"] == []


def test_elective_rules(p1):
    assert subj(p1, p1.hod, 5, "E1", elective_group_id=S["group"]).status_code == 422          # a non-elective cannot join a group
    other = p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": sem(p1, 3), "name": "Group S3"}).json()["id"]
    r = subj(p1, p1.hod, 5, "E2", is_elective=True, elective_group_id=other)                  # a group of another class cannot be used
    assert r.status_code == 422 and "elective_group_id" in fields(r)
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['ai']}", json={"elective_group_id": other}).status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/{S['ds']}", json={"is_elective": False}).status_code == 200   # normal again -> leaves its group
    assert db_val("select elective_group_id from public.subjects where id = $1", uuid.UUID(S["ds"])) is None
    only = next(x for x in p1.api(p1.hod, "GET", "/api/subjects/groups").json()["groups"] if x["id"] == other)
    assert only["members"] == [] and p1.api(p1.hod, "PATCH", f"/api/subjects/groups/{other}", json={"name": "Renamed S3"}).status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/groups/{other}").status_code == 200


def test_deleting_a_group_only_ungroups_its_subjects(p1):
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/groups/{S['group']}").status_code == 200
    row = db_fetch("select is_elective, elective_group_id from public.subjects where id = $1", uuid.UUID(S["ai"]))[0]
    assert row == {"is_elective": True, "elective_group_id": None}
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/groups/{S['group']}").status_code == 404
    foreign = db_val("insert into public.elective_groups (school_id, semester_id, name) values ($1,$2,'Foreign group') returning id",
                     uuid.UUID(p1.school_b), uuid.UUID(p1.b_sems[1]))
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/groups/{foreign}").status_code == 404
    assert p1.api(p1.hod, "PATCH", f"/api/subjects/groups/{foreign}", json={"name": "Hacked"}).status_code == 404


# ====================================================================================================
# 2) ASSIGNMENT (teacher <-> subject <-> class)  + optional co-teacher
# ====================================================================================================
def test_a_subject_cannot_go_to_a_teacher_who_cannot_teach_it(p1):
    t = new_teacher(p1, "plain")
    S["plain"] = t
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t["id"]})
    assert r.status_code == 422 and r.json()["code"] == "TEACHER_CANNOT_TEACH"
    assert "teacher_id" in fields(r)
    assert db_val("select count(*) from public.assignments where subject_id = $1", uuid.UUID(S["dbms"])) == 0

    async def sneaky():   # the DATABASE refuses it too (foreign key), even if somebody bypasses the API
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            await conn.execute("insert into public.assignments (school_id, term_id, subject_id, teacher_id) values ($1,$2,$3,$4)",
                               uuid.UUID(p1.school), term, uuid.UUID(S["dbms"]), uuid.UUID(t["id"]))
        finally:
            await conn.close()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        run(sneaky())


def test_assign_after_adding_the_subject_to_the_teachers_list(p1):
    t = S["plain"]
    assert p1.api(p1.hod, "PATCH", f"/api/users/{t['id']}", json={"subject_ids": [S["dbms"], S["ai"], S["pe1"]]}).status_code == 200
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t["id"]})
    assert r.status_code == 200 and r.json()["warnings"] == []
    S["assignment"] = r.json()["id"]
    rows = p1.api(p1.hod, "GET", "/api/assignments").json()
    row = next(s for s in rows["subjects"] if s["id"] == S["dbms"])
    assert row["teacher_id"] == t["id"] and row["assignment_id"] == S["assignment"] and row["co_teacher_id"] is None
    assert t["id"] in {e["id"] for e in row["eligible_teachers"]}
    assert next(x for x in rows["teachers"] if x["id"] == t["id"])["weekly_load"] == 4            # DBMS = 4 credits
    assert next(s for s in rows["subjects"] if s["code"] == "PE1")["eligible_teachers"] != []    # plain can teach PE
    # PE / SOL credits are not counted as weekly lectures (nobody is assigned PE here, so still 4)


def test_changing_the_teacher_replaces_the_assignment(p1):
    t2 = new_teacher(p1, "second", subject_ids=[S["dbms"]])
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"]})
    assert r.status_code == 200 and r.json()["id"] == S["assignment"]                 # same assignment, new teacher
    assert db_val("select count(*) from public.assignments where subject_id = $1", uuid.UUID(S["dbms"])) == 1
    row = next(s for s in p1.api(p1.hod, "GET", "/api/assignments").json()["subjects"] if s["id"] == S["dbms"])
    assert row["teacher_id"] == t2["id"]
    S["t2"] = t2


def test_a_shared_subject_has_a_primary_teacher_and_a_co_teacher(p1):
    plain, t2 = S["plain"], S["t2"]
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": plain["id"]})
    assert r.status_code == 200, r.text
    row = next(s for s in p1.api(p1.hod, "GET", "/api/assignments").json()["subjects"] if s["id"] == S["dbms"])
    assert (row["teacher_id"], row["co_teacher_id"], row["co_teacher_name"]) == (t2["id"], plain["id"], "Tea plain")
    listing = p1.api(p1.hod, "GET", "/api/subjects?q=dbms5").json()["subjects"][0]
    assert listing["co_teacher_name"] == "Tea plain"
    teachers = {x["id"]: x for x in p1.api(p1.hod, "GET", "/api/assignments").json()["teachers"]}
    assert teachers[t2["id"]]["weekly_load"] == 4 and teachers[plain["id"]]["weekly_load"] == 4   # BOTH are present for all 4 lectures
    assert [s["code"] for s in p1.api(p1.hod, "GET", "/api/assignments?q=tea plain").json()["subjects"]] == ["DBMS5"]   # searchable by co-teacher

    # the rules apply to the co-teacher as well
    nodb = new_teacher(p1, "nodb")
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": nodb["id"]})
    assert r.status_code == 422 and r.json()["code"] == "TEACHER_CANNOT_TEACH" and "co_teacher_id" in fields(r)
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": t2["id"]})
    assert r.status_code == 422 and "co_teacher_id" in fields(r)
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": p1.teacher_b.account.user_id})
    assert r.status_code == 404
    ghost = new_teacher(p1, "ghost2", subject_ids=[S["dbms"]])
    assert p1.api(p1.hod, "POST", f"/api/users/{ghost['id']}/deactivate").status_code == 200
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": ghost["id"]})
    assert r.status_code == 409 and r.json()["code"] == "TEACHER_INACTIVE"

    async def sneaky():   # database level: the co-teacher must also be allowed to teach the subject
        conn = await connect_one()
        try:
            await conn.execute("update public.assignments set co_teacher_id = $1 where id = $2", uuid.UUID(nodb["id"]), uuid.UUID(S["assignment"]))
        finally:
            await conn.close()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        run(sneaky())
    with pytest.raises(asyncpg.CheckViolationError):                                 # and cannot be the same person
        db_exec("update public.assignments set co_teacher_id = teacher_id where id = $1", uuid.UUID(S["assignment"]))

    # co-teacher is protected like a teacher: cannot be deactivated / lose the subject while assigned
    assert p1.api(p1.hod, "POST", f"/api/users/{plain['id']}/deactivate").status_code == 409
    r = p1.api(p1.hod, "PATCH", f"/api/users/{plain['id']}", json={"subject_ids": [S["ai"], S["pe1"]]})
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"

    # removing the co-teacher: send the assignment again without one
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"]})
    assert r.status_code == 200
    assert next(s for s in p1.api(p1.hod, "GET", "/api/assignments").json()["subjects"] if s["id"] == S["dbms"])["co_teacher_id"] is None
    assert p1.api(p1.hod, "POST", f"/api/users/{plain['id']}/deactivate").status_code == 200   # no longer protected ...
    assert p1.api(p1.hod, "POST", f"/api/users/{plain['id']}/reactivate").status_code == 200   # ... (put back for the later tests)
    # final state used by the later tests: Tea second + co-teacher Tea plain
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["dbms"], "teacher_id": t2["id"], "co_teacher_id": plain["id"]}).status_code == 200


def test_assignment_protects_the_teachers_subject_list_and_account(p1):
    t2 = S["t2"]
    r = p1.api(p1.hod, "PATCH", f"/api/users/{t2['id']}", json={"subject_ids": []})
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"
    r = p1.api(p1.hod, "POST", f"/api/users/{t2['id']}/deactivate")
    assert r.status_code == 409 and "reassign" in r.json()["message"].lower()
    assert db_val("select is_active from public.profiles where id = $1", uuid.UUID(t2["id"])) is True
    assert db_val("select count(*) from public.teacher_subjects where teacher_id = $1", uuid.UUID(t2["id"])) == 1


def test_assignment_rules_and_school_locking(p1):
    plain = S["plain"]
    ghost = new_teacher(p1, "ghost", subject_ids=[S["ai"]])
    assert p1.api(p1.hod, "POST", f"/api/users/{ghost['id']}/deactivate").status_code == 200
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": ghost["id"]})
    assert r.status_code == 409 and r.json()["code"] == "TEACHER_INACTIVE"
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": p1.teacher_b.account.user_id})
    assert r.status_code == 404
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": p1.b_subject, "teacher_id": plain["id"]})
    assert r.status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/assignments?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": "x", "teacher_id": plain["id"]}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"]}).status_code == 422

    async def foreign():
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            await conn.execute("insert into public.teacher_subjects (teacher_id, subject_id, school_id) values ($1,$2,$3)",
                               uuid.UUID(p1.teacher_b.account.user_id), uuid.UUID(p1.b_subject), uuid.UUID(p1.school_b))
            return await conn.fetchval("insert into public.assignments (school_id, term_id, subject_id, teacher_id) values ($1,$2,$3,$4) returning id",
                                       uuid.UUID(p1.school_b), term, uuid.UUID(p1.b_subject), uuid.UUID(p1.teacher_b.account.user_id))
        finally:
            await conn.close()

    foreign_id = run(foreign())
    assert p1.api(p1.hod, "DELETE", f"/api/assignments/{foreign_id}").status_code == 404     # another school's assignment
    assert db_val("select count(*) from public.assignments where id = $1", foreign_id) == 1
    assert p1.api(p1.teacher, "GET", "/api/assignments").status_code == 403
    assert p1.api(p1.teacher, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": plain["id"]}).status_code == 403
    assert p1.api(p1.teacher, "DELETE", f"/api/assignments/{S['assignment']}").status_code == 403


def test_visiting_teacher_hour_limit_warns(p1):
    vis = new_teacher(p1, "visiting", is_visiting=True, visiting_days=[2, 4], max_weekly_hours=2, subject_ids=[S["ai"]])
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": vis["id"]})
    assert r.status_code == 200 and any("limit of 2 hours" in w for w in r.json()["warnings"])
    S["vis"], S["ai_assignment"] = vis, r.json()["id"]
    # as a CO-teacher the same limit applies
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": S["plain"]["id"], "co_teacher_id": vis["id"]})
    assert r.status_code == 200 and any("limit of 2 hours" in w and "Tea visiting" in w for w in r.json()["warnings"])
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": S["ai"], "teacher_id": vis["id"]}).status_code == 200   # back: visiting primary


def test_search_and_remove_assignments(p1):
    names = lambda qs: {s["code"] for s in p1.api(p1.hod, "GET", "/api/assignments" + qs).json()["subjects"]}  # noqa: E731
    assert "AI5E" in names("?q=visiting")                            # searching by the teacher's name
    assert names(f"?semester_id={sem(p1, 3)}") == {"OS3"}
    r = p1.api(p1.hod, "DELETE", f"/api/assignments/{S['ai_assignment']}")
    assert r.status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/assignments/{S['ai_assignment']}").status_code == 404
    row = next(s for s in p1.api(p1.hod, "GET", "/api/assignments").json()["subjects"] if s["id"] == S["ai"])
    assert row["teacher_id"] is None
    r = p1.api(p1.hod, "DELETE", f"/api/subjects/{S['dbms']}")                       # an assigned subject cannot be deleted
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"


# ====================================================================================================
# 3) TIMING SETUP (college hours, lunch, DAY-WISE schedule of every semester)
# ====================================================================================================
def week(p1, label="ZZ Course - Sem 1"):
    return next(c for c in p1.api(p1.hod, "GET", "/api/timing").json()["classes"] if c["label"] == label)["days"]


def test_the_real_college_timing_is_the_default_and_the_grid_is_auto_built(p1):
    r = p1.api(p1.hod, "GET", "/api/timing").json()
    assert {k: r["college"][k] for k in REAL} == REAL
    assert r["college"]["college_name"]                                   # the name printed on the PDF / Excel exports
    assert r["school"]["id"] == p1.school and r["school"]["lunch_start"] is None          # the test school has no lunch yet
    periods = r["grid"]["periods"]
    assert len(periods) == 6 and (periods[0]["start"], periods[0]["end"]) == ("10:00", "11:00") and periods[1]["start"] == "11:05"
    assert {c["label"] for c in r["classes"]} == {"ZZ Course - Sem 1", "ZZ Course - Sem 3", "ZZ Course - Sem 5"}   # only the running classes
    d = week(p1)
    assert d[0] == {"day": 1, "name": "Monday", "short": "Mon", "working": True, "is_off": False, "end_time": None, "periods_available": 6}
    assert [x["working"] for x in d] == [True] * 5 + [False]
    assert p1.api(p1.admin, "GET", "/api/timing").json()["school"] is None                           # admin: college view
    assert p1.api(p1.admin, "GET", f"/api/timing?school_id={p1.school}").json()["school"]["id"] == p1.school
    assert p1.api(p1.hod, "GET", f"/api/timing?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.teacher, "GET", "/api/timing").status_code == 403


def test_a_new_school_starts_with_the_real_lunch_time(p1):
    r = p1.api(p1.admin, "POST", "/api/structure/schools", json={"name": f"ZZ Test School Lunch{RUN_ID}"})
    assert r.status_code == 201
    row = db_fetch("select lunch_start::text as s, lunch_end::text as e from public.school_timing where school_id = $1", uuid.UUID(r.json()["id"]))[0]
    assert (row["s"], row["e"]) == ("13:10:00", "13:40:00")
    assert p1.api(p1.admin, "DELETE", f"/api/structure/schools/{r.json()['id']}").status_code == 200


def test_only_the_admin_sets_college_timing(p1):
    body = {"college_start": "09:00", "college_end": "16:00", "lecture_minutes": 50, "break_minutes": 10, "days_per_week": 6}
    assert p1.api(p1.hod, "PUT", "/api/timing/college", json=body).status_code == 403
    assert p1.api(p1.teacher, "PUT", "/api/timing/college", json=body).status_code == 403
    r = p1.api(p1.admin, "PUT", "/api/timing/college", json=body)
    assert r.status_code == 200 and r.json()["college"]["days_per_week"] == 6
    grid = p1.api(p1.hod, "GET", "/api/timing").json()["grid"]["periods"]
    assert grid[0] == {"number": 1, "start": "09:00", "end": "09:50"} and grid[1]["start"] == "10:00"      # 10 min break
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200                # back to the real timing


@pytest.mark.parametrize("over", [
    {"college_end": "09:00"}, {"college_end": "10:00"}, {"lecture_minutes": 10}, {"lecture_minutes": 200}, {"break_minutes": 31},
    {"break_minutes": -1}, {"days_per_week": 7}, {"days_per_week": 4}, {"college_start": "25:00"}, {"college_start": "ten"},
    {"college_end": "10:30"},   # not even one 60 minute lecture fits
])
def test_college_timing_input_is_validated(p1, over):
    body = {**REAL, **over}
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=body).status_code == 422, over
    assert db_val("select college_end::text from public.college_settings") == "16:50:00"


def test_preview_builds_a_grid_without_saving(p1):
    body = {"college_start": "08:30", "college_end": "14:30", "lecture_minutes": 45, "break_minutes": 5, "lunch_start": "11:00", "lunch_end": "11:30"}
    r = p1.api(p1.hod, "POST", "/api/timing/preview", json=body)
    assert r.status_code == 200
    periods = r.json()["grid"]["periods"]
    assert periods[0] == {"number": 1, "start": "08:30", "end": "09:15"} and r.json()["grid"]["lunch"] == {"start": "11:00", "end": "11:30"}
    assert all(not ("11:00" < p["start"] < "11:30") for p in periods)
    assert db_val("select college_start::text from public.college_settings") == "10:00:00"      # nothing was saved
    assert p1.api(p1.hod, "POST", "/api/timing/preview", json={**body, "lunch_end": None}).status_code == 422
    assert p1.api(p1.hod, "POST", "/api/timing/preview", json={**body, "college_end": "08:00"}).status_code == 422


def test_hod_sets_lunch_for_his_school_and_the_grid_skips_it(p1):
    r = p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"})
    assert r.status_code == 200, r.text
    periods = r.json()["grid"]["periods"]
    assert [(p["start"], p["end"]) for p in periods] == [        # exactly the real School of IT day
        ("10:00", "11:00"), ("11:05", "12:05"), ("12:10", "13:10"), ("13:40", "14:40"), ("14:45", "15:45"), ("15:50", "16:50")]
    assert r.json()["warnings"] == []
    assert p1.api(p1.hod, "GET", "/api/timing").json()["school"]["lunch_start"] == "13:10"
    assert db_val("select count(*) from public.school_timing where school_id = $1", uuid.UUID(p1.school_b)) == 0   # other schools untouched


@pytest.mark.parametrize("body", [
    {"lunch_start": "13:40", "lunch_end": "13:10"}, {"lunch_start": "13:00", "lunch_end": "13:05"}, {"lunch_start": "12:00", "lunch_end": "15:00"},
    {"lunch_start": "08:00", "lunch_end": "08:45"}, {"lunch_start": "16:30", "lunch_end": "17:30"}, {"lunch_start": "13:00"}, {"lunch_end": "13:45"},
    {"lunch_start": "xx", "lunch_end": "13:45"},
])
def test_lunch_input_is_validated(p1, body):
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json=body).status_code == 422, body
    assert p1.api(p1.hod, "GET", "/api/timing").json()["school"]["lunch_start"] == "13:10"      # still the old value


def test_lunch_school_locking_and_admin_override(p1):
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"school_id": p1.school_b, "lunch_start": "13:00", "lunch_end": "13:30"}).status_code == 403
    assert p1.api(p1.teacher, "PUT", "/api/timing/school", json={"lunch_start": "13:00", "lunch_end": "13:30"}).status_code == 403
    assert p1.api(p1.admin, "PUT", "/api/timing/school", json={"lunch_start": "13:00", "lunch_end": "13:30"}).status_code == 422   # admin must name the school
    r = p1.api(p1.admin, "PUT", "/api/timing/school", json={"school_id": p1.school, "lunch_start": "13:00", "lunch_end": "13:30"})
    assert r.status_code == 200 and p1.api(p1.hod, "GET", "/api/timing").json()["school"]["lunch_end"] == "13:30"
    r = p1.api(p1.admin, "PUT", "/api/timing/school", json={"school_id": p1.school, "lunch_start": "13:10", "lunch_end": "13:40"})
    assert r.status_code == 200
    assert db_val("select count(*) from public.school_timing where school_id = $1", uuid.UUID(p1.school_b)) == 0
    assert p1.api(p1.admin, "PUT", "/api/timing/school", json={"school_id": str(uuid.uuid4()), "lunch_start": "13:00", "lunch_end": "13:30"}).status_code == 404


def test_day_wise_schedule_one_end_time_for_the_week_then_single_days(p1):
    s1 = sem(p1, 1)
    # "set one end time for the whole week", then change single days
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{s1}/days", json={"days": [{"day": d, "end_time": "14:40"} for d in range(1, 6)]})
    assert r.status_code == 200, r.text
    assert [d["periods_available"] for d in r.json()["days"]] == [4, 4, 4, 4, 4, 0]       # P1-P4 end at 14:40; Saturday is not a working day
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{s1}/days", json={"days": [{"day": 4, "end_time": "15:45"}, {"day": 5, "is_off": True}]})
    d = r.json()["days"]
    assert [x["periods_available"] for x in d] == [4, 4, 4, 5, 0, 0]
    assert d[3]["end_time"] == "15:45" and d[4]["is_off"] is True and d[4]["end_time"] is None and d[0]["end_time"] == "14:40"
    assert week(p1) == d                                                                      # the screen sees the same week
    assert [x["periods_available"] for x in week(p1, "ZZ Course - Sem 3")] == [6, 6, 6, 6, 6, 0]    # other classes: untouched
    # back to a normal full day: no end time and not OFF
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{s1}/days", json={"days": [{"day": x} for x in range(1, 6)]})
    assert [x["periods_available"] for x in r.json()["days"]] == [6, 6, 6, 6, 6, 0]
    assert db_val("select count(*) from public.semester_days where semester_id = $1", uuid.UUID(s1)) == 0


@pytest.mark.parametrize("days", [
    [{"day": 6, "end_time": "14:40"}],                          # Saturday is not a working day (5-day week)
    [{"day": 1, "end_time": "16:50"}], [{"day": 1, "end_time": "17:30"}],   # not before the closing time
    [{"day": 1, "end_time": "10:30"}],                          # not even one lecture fits
    [{"day": 1, "end_time": "14:40"}, {"day": 1, "is_off": True}],   # listed twice
    [{"day": 1, "is_off": True, "end_time": "14:40"}],          # OFF has no end time
    [{"day": 0}], [{"day": 7}], [], [{"day": 1, "end_time": "bad"}], [{"day": 1, "unknown": 1}],
])
def test_day_schedule_input_is_validated(p1, days):
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{sem(p1, 1)}/days", json={"days": days})
    assert r.status_code == 422, (days, r.text)
    assert db_val("select count(*) from public.semester_days where semester_id = $1", uuid.UUID(sem(p1, 1))) == 0
    if days and days[0].get("day") == 6:
        assert "day-6" in fields(r)


def test_day_schedule_roles_term_and_school_locking(p1, live):
    ok = {"days": [{"day": 1, "end_time": "14:40"}]}
    assert p1.api(p1.teacher, "PUT", f"/api/timing/semesters/{sem(p1, 1)}/days", json=ok).status_code == 403
    assert p1.api(p1.hod, "PUT", f"/api/timing/semesters/{sem(p1, 2)}/days", json=ok).status_code == 409            # does not run this term
    assert p1.api(p1.hod, "PUT", f"/api/timing/semesters/{p1.b_sems[1]}/days", json=ok).status_code == 404          # another school
    r = live["hod"].client.put(f"/api/timing/semesters/{sem(p1, 1)}/days", json=ok, headers=bearer(live["hod"].token))
    assert r.status_code == 404                                                                                     # HOD of the real School of IT
    assert db_val("select count(*) from public.semester_days where school_id in ($1, $2)", uuid.UUID(p1.school), uuid.UUID(p1.school_b)) == 0
    assert p1.api(p1.admin, "PUT", f"/api/timing/semesters/{sem(p1, 1)}/days", json={"days": [{"day": 1}]}).status_code == 200   # admin may do it


# ====================================================================================================
# 4) FIXED SLOTS (PE / SOL double blocks)   - real timing: P1 10:00 ... P3 12:10-13:10 | lunch | P4 13:40 ... P6 15:50-16:50
# ====================================================================================================
def slot(p1, who, n, day, start, kind="PE", **over):
    body = {"semester_id": sem(p1, n), "day": day, "start_period": start, "kind": kind}
    body.update(over)
    return p1.api(who, "POST", "/api/fixed-slots", json=body)


def set_days(p1, n, days):
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{sem(p1, n)}/days", json={"days": days})
    assert r.status_code == 200, r.text
    return r.json()


def get_slots(p1):
    return {s["id"]: s for s in p1.api(p1.hod, "GET", "/api/fixed-slots").json()["slots"]}


def test_pe_and_sol_are_always_double_blocks(p1):
    r = slot(p1, p1.hod, 1, 1, 1, "PE", length=1)                         # the client even asks for 1 - it becomes 2
    assert r.status_code == 201 and r.json()["length"] == 2 and r.json()["notes"] == []
    S["pe"] = r.json()["id"]
    r = slot(p1, p1.hod, 1, 2, 3, "SOL", length=5)                        # Period 3 + Period 4 = AROUND LUNCH, like the real SOL III
    assert r.status_code == 201 and r.json()["length"] == 2
    assert any("lunch" in n.lower() for n in r.json()["notes"])           # a friendly note, not a refusal
    S["sol"] = r.json()["id"]
    data = p1.api(p1.hod, "GET", "/api/fixed-slots").json()
    pe = next(s for s in data["slots"] if s["id"] == S["pe"])
    assert (pe["length"], pe["time"], pe["day_name"], pe["problems"], pe["notes"]) == (2, "10:00-12:05", "Monday", [], [])
    assert pe["subject_id"] == S["pe1"]                                    # linked to the class's own PE subject automatically
    sol = next(s for s in data["slots"] if s["id"] == S["sol"])
    assert sol["time"] == "12:10-14:40" and sol["label"] == "Science of Living (SOL)" and sol["problems"] == []
    assert any("lunch" in n.lower() for n in sol["notes"])
    cls = {c["label"]: c for c in data["classes"]}
    assert cls["ZZ Course - Sem 1"]["has_pe"] and cls["ZZ Course - Sem 1"]["has_sol"]
    assert not cls["ZZ Course - Sem 3"]["has_pe"] and not cls["ZZ Course - Sem 3"]["has_sol"]
    assert len(cls["ZZ Course - Sem 1"]["days"]) == 6
    assert db_val("select length from public.fixed_slots where id = $1", uuid.UUID(S["pe"])) == 2


def test_double_blocks_around_lunch_pass_the_check(p1):
    for start in (3, 2):                                                  # P3+P4 (lunch between) and P2+P3 (plain break between)
        c = p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": sem(p1, 3), "day": 3, "start_period": start, "kind": "PE"})
        assert c.status_code == 200 and c.json()["ok"] is True and c.json()["problems"] == [], c.text
    assert any("lunch" in n.lower() for n in p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": sem(p1, 3), "day": 3, "start_period": 3, "kind": "PE"}).json()["notes"])
    assert db_val("select count(*) from public.fixed_slots where semester_id = $1", uuid.UUID(sem(p1, 3))) == 0    # check never saves


def test_slot_after_the_days_end_time_or_on_an_off_day_is_refused(p1):
    set_days(p1, 3, [{"day": 4, "end_time": "14:40"}, {"day": 5, "is_off": True}])                  # Thu ends 14:40, Fri is OFF (e.g. OJT)
    r = slot(p1, p1.hod, 3, 4, 4, "PE")                                    # P4+P5: P5 ends 15:45 > 14:40
    assert r.status_code == 422 and r.json()["code"] == "SLOT_NOT_POSSIBLE" and "end time on Thursday (14:40)" in r.json()["message"]
    ok = slot(p1, p1.hod, 3, 4, 3, "PE")                                   # P3+P4 end exactly at 14:40 - fine, AND around lunch
    assert ok.status_code == 201
    assert slot(p1, p1.hod, 3, 4, 5, "OTHER", length=1, label="Seminar").status_code == 422          # P5 alone is also too late
    r = slot(p1, p1.hod, 3, 5, 1, "OTHER", length=1, label="Seminar")
    assert r.status_code == 422 and "no lectures on Friday" in r.json()["message"] and r.json()["problems"]
    r = slot(p1, p1.hod, 3, 5, 1, "PE")
    assert r.status_code == 422 and "OFF" in r.json()["message"]
    assert slot(p1, p1.hod, 3, 4, 3, "OTHER", length=1, label="Seminar").status_code == 422          # overlaps the PE just created
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{ok.json()['id']}").status_code == 200
    set_days(p1, 3, [{"day": 4}, {"day": 5}])                              # back to full days
    assert slot(p1, p1.hod, 3, 5, 1, "OTHER", length=1, label="Seminar").status_code == 201          # Friday works again
    S["seminar3"] = next(s["id"] for s in get_slots(p1).values() if s["label"] == "Seminar")
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['seminar3']}").status_code == 200


def test_slots_off_the_grid_or_the_calendar_are_refused(p1):
    r = slot(p1, p1.hod, 1, 4, 6, "PE")                                    # period 6 + 7: the day has only 6 periods
    assert r.status_code == 422 and "do not exist" in r.json()["message"]
    r = slot(p1, p1.hod, 1, 6, 1, "PE")                                    # Saturday, 5-day week
    assert r.status_code == 422 and "not a working day" in r.json()["message"]
    assert slot(p1, p1.hod, 1, 7, 1, "PE").status_code == 422
    assert slot(p1, p1.hod, 1, 1, 0, "PE").status_code == 422
    assert slot(p1, p1.hod, 1, 1, 1, "GYM").status_code == 422
    assert slot(p1, p1.hod, 1, 5, 1, "OTHER").status_code == 422             # an 'other' slot needs a name
    assert slot(p1, p1.hod, 1, 5, 1, "OTHER", label="x" * 81).status_code == 422
    assert slot(p1, p1.hod, 1, 5, 1, "PE", unknown=1).status_code == 422


def test_overlaps_are_refused_for_the_class_and_for_the_teachers(p1):
    r = slot(p1, p1.hod, 1, 1, 2, "OTHER", length=1, label="Quiz")        # Monday P2 is inside the PE block P1-P2
    assert r.status_code == 422 and "already has" in r.json()["message"]
    plain, t2 = S["plain"]["id"], S["t2"]["id"]
    ok = slot(p1, p1.hod, 1, 5, 1, "OTHER", length=1, label="Seminar", teacher_id=plain)
    assert ok.status_code == 201
    S["seminar"] = ok.json()["id"]
    r = slot(p1, p1.hod, 5, 5, 1, "OTHER", length=1, label="Guest lecture", teacher_id=plain)       # the same teacher, another class, same time
    assert r.status_code == 422 and "already in a locked slot" in r.json()["message"]
    r = slot(p1, p1.hod, 5, 5, 1, "OTHER", length=1, label="Guest lecture", teacher_id=t2, co_teacher_id=plain)   # ... or as the CO-teacher
    assert r.status_code == 422 and "Tea plain is already in a locked slot" in r.json()["message"]

    async def sneaky():
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            await conn.execute("insert into public.fixed_slots (school_id, term_id, semester_id, day, start_period, length, kind, label) values ($1,$2,$3,1,2,2,'PE','x')",
                               uuid.UUID(p1.school), term, uuid.UUID(sem(p1, 1)))
        finally:
            await conn.close()

    with pytest.raises(asyncpg.ExclusionViolationError):                     # the DATABASE refuses overlaps as well
        run(sneaky())


def test_teacher_co_teacher_and_subject_rules_for_locked_slots(p1):
    vis, plain, t2 = S["vis"]["id"], S["plain"]["id"], S["t2"]["id"]                # vis: Tue + Thu only
    r = slot(p1, p1.hod, 5, 1, 1, "OTHER", length=1, label="Guest", teacher_id=vis)             # Monday
    assert r.status_code == 422 and "visiting faculty" in r.json()["message"]
    r = slot(p1, p1.hod, 5, 1, 1, "OTHER", length=1, label="Guest", teacher_id=t2, co_teacher_id=vis)   # the co-teacher is visiting too
    assert r.status_code == 422 and "Tea visiting is visiting faculty" in r.json()["message"]
    ok = slot(p1, p1.hod, 5, 2, 1, "OTHER", length=1, label="Guest", teacher_id=vis)            # Tuesday is fine
    assert ok.status_code == 201
    S["guest"] = ok.json()["id"]
    r = slot(p1, p1.hod, 5, 3, 1, "OTHER", length=1, label="Talk", teacher_id=t2, subject_id=S["ai"])
    assert r.status_code == 422 and "cannot teach" in r.json()["message"]
    r = slot(p1, p1.hod, 5, 3, 1, "OTHER", length=1, label="Talk", teacher_id=plain, co_teacher_id=t2, subject_id=S["ai"])   # the CO-teacher cannot teach it
    assert r.status_code == 422 and "Tea second cannot teach" in r.json()["message"]
    r = slot(p1, p1.hod, 5, 3, 1, "OTHER", length=1, label="Talk", teacher_id=t2, co_teacher_id=t2)
    assert r.status_code == 422 and "two different people" in r.json()["message"]
    r = slot(p1, p1.hod, 5, 3, 1, "OTHER", length=1, label="Talk", co_teacher_id=plain)
    assert r.status_code == 422 and "main teacher" in r.json()["message"]
    r = slot(p1, p1.hod, 3, 3, 1, "OTHER", length=1, label="Talk", subject_id=S["dbms"])        # DBMS belongs to Sem 5, not Sem 3
    assert r.status_code == 422 and "does not belong" in r.json()["message"]
    assert slot(p1, p1.hod, 3, 3, 1, "OTHER", length=1, label="Talk", teacher_id=p1.teacher_b.account.user_id).status_code == 422   # other school

    # teacher AND co-teacher are taken from the assignment (DBMS: Tea second + co-teacher Tea plain)
    r = slot(p1, p1.hod, 5, 3, 2, "OTHER", length=1, label="DBMS tutorial", subject_id=S["dbms"])
    assert r.status_code == 201, r.text
    got = get_slots(p1)[r.json()["id"]]
    assert (got["teacher_id"], got["co_teacher_id"], got["co_teacher_name"], got["subject_name"]) == (t2, plain, "Tea plain", "Database Management")
    assert p1.api(p1.hod, "POST", f"/api/users/{vis}/deactivate").status_code == 409            # a person in a locked slot cannot be deactivated
    assert p1.api(p1.hod, "POST", f"/api/users/{plain}/deactivate").status_code == 409


def test_a_slot_is_flagged_when_the_class_schedule_changes_later(p1):
    r = p1.api(p1.hod, "PUT", f"/api/timing/semesters/{sem(p1, 1)}/days", json={"days": [{"day": 1, "end_time": "11:00"}]})   # Monday now ends at 11:00
    assert r.status_code == 200 and any("locked slot" in w for w in r.json()["warnings"])
    pe = get_slots(p1)[S["pe"]]                                            # Monday P1+P2: P2 ends 12:05
    assert pe["problems"] and "end time on Monday (11:00)" in " ".join(pe["problems"])
    assert get_slots(p1)[S["sol"]]["problems"] == []                       # other days are fine
    # the same with the lunch: a different lunch time never breaks a double block any more
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "11:30", "lunch_end": "12:15"}).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    set_days(p1, 1, [{"day": 1}])
    assert get_slots(p1)[S["pe"]]["problems"] == []


def test_move_and_delete_a_slot(p1):
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"day": 3, "start_period": 3})   # P3+P4: around lunch is fine
    assert r.status_code == 200 and any("lunch" in n.lower() for n in r.json()["notes"])
    moved = get_slots(p1)[S["pe"]]
    assert (moved["day"], moved["start_period"], moved["length"], moved["time"]) == (3, 3, 2, "12:10-14:40")
    r = p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"day": 2, "start_period": 3})       # clashes with SOL (Tue P3-P4)
    assert r.status_code == 422 and "already has" in r.json()["message"]
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"start_period": 6}).status_code == 422   # off the end of the day
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"length": 9}).status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"day": 1, "start_period": 1}).status_code == 200   # back to Monday P1
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"day": 1, "start_period": 1}).status_code == 200   # a slot may be moved onto its own place
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{S['pe']}", json={"note": "Ground floor"}).status_code == 200
    assert get_slots(p1)[S["pe"]]["note"] == "Ground floor"
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['sol']}").status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{S['sol']}").status_code == 404
    cls = {c["label"]: c for c in p1.api(p1.hod, "GET", "/api/fixed-slots").json()["classes"]}
    assert cls["ZZ Course - Sem 1"]["has_pe"] and not cls["ZZ Course - Sem 1"]["has_sol"]


def test_fixed_slots_are_locked_to_the_hods_school(p1):
    async def foreign():
        conn = await connect_one()
        try:
            term = await conn.fetchval("select id from public.academic_terms where is_active")
            return await conn.fetchval(
                "insert into public.fixed_slots (school_id, term_id, semester_id, day, start_period, length, kind, label) values ($1,$2,$3,1,1,2,'PE','Foreign PE') returning id",
                uuid.UUID(p1.school_b), term, uuid.UUID(p1.b_sems[1]))
        finally:
            await conn.close()

    fid = run(foreign())
    assert p1.api(p1.hod, "GET", f"/api/fixed-slots?school_id={p1.school_b}").status_code == 403
    assert fid not in {s["id"] for s in p1.api(p1.hod, "GET", "/api/fixed-slots").json()["slots"]}
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{fid}").status_code == 404
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{fid}", json={"day": 2}).status_code == 404
    assert slot(p1, p1.hod, 1, 3, 3, "PE", semester_id=p1.b_sems[1]).status_code == 404
    assert p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": p1.b_sems[1], "day": 1, "start_period": 1, "kind": "PE"}).status_code == 404
    assert db_val("select count(*) from public.fixed_slots where id = $1", fid) == 1
    assert p1.api(p1.teacher, "GET", "/api/fixed-slots").status_code == 403
    assert slot(p1, p1.teacher, 1, 4, 1, "PE").status_code == 403
    assert p1.api(p1.admin, "GET", "/api/fixed-slots").status_code == 422
    assert p1.api(p1.admin, "GET", f"/api/fixed-slots?school_id={p1.school}").status_code == 200


# ====================================================================================================
# 5) HOLIDAYS & EVENTS
# ====================================================================================================
def cal(p1, who, **body):
    base = {"kind": "holiday", "title": "ZZ Holiday", "date_from": "2026-12-10"}
    base.update(body)
    return p1.api(who, "POST", "/api/calendar", json=base)


def test_standard_india_holidays_are_preloaded_and_everybody_sees_them(p1, live):
    for who in (p1.hod, p1.teacher, p1.admin, p1.teacher_b):
        entries = p1.api(who, "GET", "/api/calendar?year=2026").json()["entries"]
        pre = [e for e in entries if e["is_preloaded"]]
        assert len(pre) == 17 and {"Republic Day", "Independence Day", "Christmas Day", "Diwali (Deepavali)"} <= {e["title"] for e in pre}
    pre27 = [e for e in p1.api(p1.hod, "GET", "/api/calendar?year=2027").json()["entries"] if e["is_preloaded"]]
    assert len(pre27) == 17
    rd = next(e for e in pre27 if e["title"] == "Republic Day")
    assert (rd["date_from"], rd["scope"], rd["full_day"], rd["school_id"]) == ("2027-01-26", "college", True, None)
    assert rd["can_edit"] is False and next(e for e in p1.api(p1.admin, "GET", "/api/calendar?year=2027").json()["entries"] if e["title"] == "Republic Day")["can_edit"] is True
    assert {e["title"] for e in p1.api(p1.hod, "GET", "/api/calendar?q=gandhi").json()["entries"]} == {"Mahatma Gandhi Jayanti"}
    assert all(e["kind"] == "holiday" for e in p1.api(p1.hod, "GET", "/api/calendar?kind=holiday").json()["entries"])
    assert p1.api(p1.hod, "GET", f"/api/calendar?school_id={p1.school_b}").status_code == 403
    assert p1.api(live["teacher"], "GET", "/api/calendar").status_code == 200


def test_admin_manages_college_wide_holidays_and_the_hod_cannot(p1):
    r = cal(p1, p1.admin, scope="college", title="ZZ Founders Day")
    assert r.status_code == 201
    hid = r.json()["id"]
    S["college_holiday"] = hid
    for who in (p1.hod, p1.teacher_b):
        assert any(e["id"] == hid for e in p1.api(who, "GET", "/api/calendar?year=2026").json()["entries"])
    assert cal(p1, p1.hod, scope="college", title="ZZ Sneaky").status_code == 403
    assert p1.api(p1.hod, "PATCH", f"/api/calendar/{hid}", json={"title": "ZZ Hacked"}).status_code == 403
    assert p1.api(p1.hod, "DELETE", f"/api/calendar/{hid}").status_code == 403
    assert p1.api(p1.teacher, "PATCH", f"/api/calendar/{hid}", json={"title": "ZZ Hacked"}).status_code == 403
    assert cal(p1, p1.admin, scope="college", title="ZZ Partial", full_day=False, start_time="10:00", end_time="11:00").status_code == 422
    assert cal(p1, p1.admin, scope="college", title="ZZ Event", kind="event").status_code == 422
    assert db_val("select title from public.calendar_entries where id = $1", uuid.UUID(hid)) == "ZZ Founders Day"
    assert p1.api(p1.admin, "PATCH", f"/api/calendar/{hid}", json={"title": "ZZ Founders Day (moved)", "date_from": "2026-12-11"}).status_code == 200
    got = next(e for e in p1.api(p1.admin, "GET", "/api/calendar?year=2026&q=zz founders").json()["entries"])
    assert (got["title"], got["date_from"], got["date_to"]) == ("ZZ Founders Day (moved)", "2026-12-11", "2026-12-11")


def test_hod_adds_school_holidays_and_events_for_selected_classes(p1):
    r = cal(p1, p1.hod, title="ZZ School Day", date_from="2026-11-02", date_to="2026-11-03")
    assert r.status_code == 201
    S["school_holiday"] = r.json()["id"]

    r = cal(p1, p1.hod, kind="event", title="ZZ Workshop", date_from="2026-11-10", full_day=False, start_time="11:00", end_time="13:00",
            semester_ids=[sem(p1, 1), sem(p1, 3)], note="Guest speaker")
    assert r.status_code == 201
    entry = next(e for e in p1.api(p1.hod, "GET", "/api/calendar?q=zz workshop").json()["entries"])
    assert (entry["scope"], entry["school_id"], entry["full_day"], entry["start_time"], entry["end_time"]) == ("school", p1.school, False, "11:00", "13:00")
    assert {c["label"] for c in entry["classes"]} == {"ZZ Course - Sem 1", "ZZ Course - Sem 3"} and entry["can_edit"] is True
    S["event"] = entry["id"]

    assert p1.api(p1.hod, "PATCH", f"/api/calendar/{S['event']}", json={"semester_ids": [sem(p1, 5)], "end_time": "14:00"}).status_code == 200
    entry = next(e for e in p1.api(p1.hod, "GET", "/api/calendar?q=zz workshop").json()["entries"])
    assert [c["label"] for c in entry["classes"]] == ["ZZ Course - Sem 5"] and entry["end_time"] == "14:00"
    assert p1.api(p1.hod, "PATCH", f"/api/calendar/{S['event']}", json={"full_day": True}).status_code == 200
    assert next(e for e in p1.api(p1.hod, "GET", "/api/calendar?q=zz workshop").json()["entries"])["start_time"] is None


@pytest.mark.parametrize("over", [
    {"date_to": "2026-12-01"}, {"date_to": "2027-03-01"}, {"date_from": "2026-13-45"}, {"date_from": "10-12-2026"}, {"title": ""},
    {"full_day": False}, {"full_day": False, "start_time": "13:00", "end_time": "12:00"},
    {"full_day": False, "start_time": "08:00", "end_time": "09:00"}, {"full_day": False, "start_time": "16:00", "end_time": "18:00"},
    {"semester_ids": [str(uuid.uuid4())]}, {"kind": "party"}, {"unknown": 1},
])
def test_calendar_input_is_validated(p1, over):
    r = cal(p1, p1.hod, **over)
    assert r.status_code == 422, (over, r.text)


def test_other_schools_calendar_entries_are_invisible_and_untouchable(p1, live):
    r = cal(p1, p1.hod, title="ZZ P Only", date_from="2026-12-20")
    assert r.status_code == 201
    mine = r.json()["id"]
    b_entry = db_val("insert into public.calendar_entries (school_id, kind, title, date_from, date_to) values ($1,'event','ZZ B Only','2026-12-21','2026-12-21') returning id",
                     uuid.UUID(p1.school_b))
    titles = lambda who, qs="": {e["title"] for e in p1.api(who, "GET", "/api/calendar?q=zz" + qs).json()["entries"]}  # noqa: E731
    assert "ZZ P Only" in titles(p1.hod) and "ZZ P Only" in titles(p1.teacher) and "ZZ P Only" in titles(p1.admin, f"&school_id={p1.school}")
    assert "ZZ B Only" not in titles(p1.hod) and "ZZ B Only" not in titles(p1.teacher)
    assert "ZZ P Only" not in titles(p1.teacher_b) and "ZZ B Only" in titles(p1.teacher_b)
    assert "ZZ P Only" not in titles(live["hod"])                                                # HOD of the real School of IT
    assert p1.api(p1.hod, "PATCH", f"/api/calendar/{b_entry}", json={"title": "ZZ Hacked"}).status_code == 404
    assert p1.api(p1.hod, "DELETE", f"/api/calendar/{b_entry}").status_code == 404
    assert live["hod"].client.delete(f"/api/calendar/{mine}", headers=bearer(live["hod"].token)).status_code == 404
    assert cal(p1, p1.hod, title="ZZ X", school_id=p1.school_b).status_code == 403
    assert cal(p1, p1.hod, title="ZZ X", semester_ids=[p1.b_sems[1]]).status_code == 422
    assert db_val("select title from public.calendar_entries where id = $1", b_entry) == "ZZ B Only"
    assert cal(p1, p1.teacher, title="ZZ T").status_code == 403
    assert p1.api(p1.teacher, "DELETE", f"/api/calendar/{mine}").status_code == 403
    assert cal(p1, p1.admin, title="ZZ Admin made").status_code == 422                          # admin: school entry needs a school (or scope=college)
    assert cal(p1, p1.admin, title="ZZ Admin made", school_id=p1.school).status_code == 201


def test_delete_entries_and_restore_the_standard_list(p1):
    assert p1.api(p1.hod, "DELETE", f"/api/calendar/{S['school_holiday']}").status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/calendar/{S['school_holiday']}").status_code == 404
    assert p1.api(p1.admin, "DELETE", f"/api/calendar/{S['college_holiday']}").status_code == 200

    pre = p1.api(p1.admin, "GET", "/api/calendar?q=christmas").json()["entries"]
    assert len(pre) == 2
    assert p1.api(p1.admin, "DELETE", f"/api/calendar/{pre[0]['id']}").status_code == 200           # even a standard holiday can be removed
    assert len(p1.api(p1.admin, "GET", "/api/calendar?q=christmas").json()["entries"]) == 1
    assert p1.api(p1.hod, "POST", "/api/calendar/restore-india-list").status_code == 403
    r = p1.api(p1.admin, "POST", "/api/calendar/restore-india-list")
    assert r.status_code == 200 and r.json()["added"] == 1
    assert len(p1.api(p1.admin, "GET", "/api/calendar?q=christmas").json()["entries"]) == 2
    assert p1.api(p1.admin, "POST", "/api/calendar/restore-india-list").json()["added"] == 0         # nothing doubled


# ====================================================================================================
# 6) SEMESTER START / END DATES
# ====================================================================================================
def test_semester_dates(p1):
    term = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    got = p1.api(p1.hod, "GET", f"/api/terms/{term}/dates").json()
    assert [c["number"] for c in got["classes"]] == [1, 3, 5]                      # only the odd semesters of THIS school
    assert all(c["start_date"] is None for c in got["classes"])
    items = [{"semester_id": sem(p1, n), "start_date": "2026-07-01", "end_date": "2026-11-30"} for n in (1, 3, 5)]
    r = p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": items})
    assert r.status_code == 200 and r.json()["saved"] == 3
    got = p1.api(p1.hod, "GET", f"/api/terms/{term}/dates").json()["classes"]
    assert {(c["start_date"], c["end_date"]) for c in got} == {("2026-07-01", "2026-11-30")}
    items[0]["start_date"] = "2026-07-15"
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": items[:1]}).status_code == 200   # update one class only
    got = {c["number"]: c["start_date"] for c in p1.api(p1.hod, "GET", f"/api/terms/{term}/dates").json()["classes"]}
    assert got == {1: "2026-07-15", 3: "2026-07-01", 5: "2026-07-01"}

    bad = p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": sem(p1, 1), "start_date": "2026-11-30", "end_date": "2026-07-01"}]})
    assert bad.status_code == 422 and sem(p1, 1) in fields(bad)
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": sem(p1, 1), "start_date": "2020-01-01", "end_date": "2020-06-01"}]}).status_code == 422
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": sem(p1, 1), "start_date": "2026-07-01", "end_date": "2027-08-01"}]}).status_code == 422
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": sem(p1, 2), "start_date": "2026-07-01", "end_date": "2026-11-30"}]}).status_code == 409   # even class
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": p1.b_sems[1], "start_date": "2026-07-01", "end_date": "2026-11-30"}]}).status_code == 404
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"school_id": p1.school_b, "items": items}).status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/terms/{term}/dates?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "PUT", f"/api/terms/{term}/dates", json={"items": []}).status_code == 422
    assert p1.api(p1.teacher, "GET", f"/api/terms/{term}/dates").status_code == 403
    assert p1.api(p1.teacher, "PUT", f"/api/terms/{term}/dates", json={"items": items}).status_code == 403
    assert p1.api(p1.admin, "GET", f"/api/terms/{term}/dates?school_id={p1.school}").status_code == 200
    old = db_val("insert into public.academic_terms (kind, year) values ('even', 2031) returning id")   # (removed again by the test clean-up)
    assert p1.api(p1.hod, "PUT", f"/api/terms/{old}/dates", json={"items": items}).status_code == 409
    assert p1.api(p1.hod, "PUT", f"/api/terms/{uuid.uuid4()}/dates", json={"items": items}).status_code == 404
