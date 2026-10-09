"""Phase 6 Row Level Security, through Supabase's PUBLIC data API: a teacher reads only their own preferences and filled-status,
the HOD reads the whole school, another school reads nothing, the window and the school's limit are visible to the school's
own people only, the workload norm is for managers only, the audit log is for the admin only - and NO browser can write any of it.
"""
from __future__ import annotations

import uuid

import pytest

from .conftest import db_exec, db_val
from .test_rls import DENIED, rest

S: dict = {}
TABLES = ["teacher_preferences", "teacher_pref_slots", "preference_settings", "preference_windows", "preference_responses", "workload_norms"]


def keys(resp, field: str) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {str(row[field]) for row in resp.json()}


@pytest.fixture(scope="module", autouse=True)
def rows(p1):
    school, school_b = uuid.UUID(p1.school), uuid.UUID(p1.school_b)
    me, boss, other = (uuid.UUID(x.account.user_id) for x in (p1.teacher, p1.hod, p1.teacher_b))
    for sql in (
        "delete from public.preference_windows where school_id = any($1::uuid[])", "delete from public.teacher_pref_slots where school_id = any($1::uuid[])",
        "delete from public.teacher_preferences where school_id = any($1::uuid[])", "delete from public.preference_settings where school_id = any($1::uuid[])",
        "delete from public.workload_norms where school_id = any($1::uuid[])",
    ):
        db_exec(sql, [school, school_b])
    db_exec("delete from public.workload_norms where school_id is null")
    for t, sch in ((me, school), (boss, school), (other, school_b)):
        db_exec("insert into public.teacher_preferences (teacher_id, school_id, prefer_first, max_per_day) values ($1,$2,true,4)", t, sch)
        db_exec("insert into public.teacher_pref_slots (teacher_id, school_id, day, period, kind) values ($1,$2,1,1,'unavailable')", t, sch)
    for sch in (school, school_b):
        db_exec("insert into public.preference_settings (school_id, max_unavailable) values ($1, 5)", sch)
    S["win_p"] = str(db_val("insert into public.preference_windows (school_id, deadline) values ($1, now() + interval '1 day') returning id", school))
    S["win_b"] = str(db_val("insert into public.preference_windows (school_id, deadline) values ($1, now() + interval '1 day') returning id", school_b))
    db_exec("insert into public.preference_responses (window_id, teacher_id, school_id) values ($1,$2,$3)", uuid.UUID(S["win_p"]), me, school)
    db_exec("insert into public.preference_responses (window_id, teacher_id, school_id) values ($1,$2,$3)", uuid.UUID(S["win_p"]), boss, school)
    db_exec("insert into public.preference_responses (window_id, teacher_id, school_id) values ($1,$2,$3)", uuid.UUID(S["win_b"]), other, school_b)
    S["norm_p"] = str(db_val("insert into public.workload_norms (school_id, norm_hours, min_hours) values ($1, 15, 10) returning id", school))
    S["norm_b"] = str(db_val("insert into public.workload_norms (school_id, norm_hours, min_hours) values ($1, 14, 9) returning id", school_b))
    S["norm_c"] = str(db_val("insert into public.workload_norms (norm_hours, min_hours) values (16, 12) returning id"))
    yield
    db_exec("delete from public.workload_norms where id = any($1::uuid[])", [uuid.UUID(S[k]) for k in ("norm_p", "norm_b", "norm_c")])
    db_exec("delete from public.preference_windows where school_id = any($1::uuid[])", [school, school_b])
    db_exec("delete from public.teacher_pref_slots where school_id = any($1::uuid[])", [school, school_b])
    db_exec("delete from public.teacher_preferences where school_id = any($1::uuid[])", [school, school_b])
    db_exec("delete from public.preference_settings where school_id = any($1::uuid[])", [school, school_b])


@pytest.mark.parametrize("table", TABLES)
def test_anonymous_reads_and_writes_nothing(p1, table):
    assert rest("GET", f"/{table}?select=*").status_code in DENIED
    assert rest("POST", f"/{table}", json={}).status_code in DENIED


def test_a_teacher_reads_only_their_own_preferences_and_slots(p1):
    me = p1.teacher.account.user_id
    assert keys(rest("GET", "/teacher_preferences?select=teacher_id", p1.teacher.token), "teacher_id") == {me}
    assert keys(rest("GET", "/teacher_pref_slots?select=teacher_id", p1.teacher.token), "teacher_id") == {me}
    # even when they ask for somebody else's row by name
    other = rest("GET", f"/teacher_preferences?select=teacher_id&teacher_id=eq.{p1.hod.account.user_id}", p1.teacher.token)
    assert other.status_code == 200 and other.json() == []


def test_a_teacher_reads_only_their_own_filled_status(p1):
    got = rest("GET", "/preference_responses?select=teacher_id,window_id", p1.teacher.token)
    assert keys(got, "teacher_id") == {p1.teacher.account.user_id}


def test_the_whole_school_can_read_its_window_and_limit_but_not_another_schools(p1):
    for who in (p1.teacher, p1.hod):
        assert keys(rest("GET", "/preference_windows?select=id", who.token), "id") == {S["win_p"]}, who.account.role
        assert keys(rest("GET", "/preference_settings?select=school_id", who.token), "school_id") == {p1.school}
    assert keys(rest("GET", "/preference_windows?select=id", p1.teacher_b.token), "id") == {S["win_b"]}


def test_the_hod_reads_every_teacher_of_the_school_and_nobody_else(p1):
    school = {p1.teacher.account.user_id, p1.hod.account.user_id}
    assert keys(rest("GET", "/teacher_preferences?select=teacher_id", p1.hod.token), "teacher_id") == school
    assert keys(rest("GET", "/teacher_pref_slots?select=teacher_id", p1.hod.token), "teacher_id") == school
    assert keys(rest("GET", "/preference_responses?select=teacher_id", p1.hod.token), "teacher_id") == school
    assert keys(rest("GET", "/teacher_preferences?select=teacher_id", p1.teacher_b.token), "teacher_id") == {p1.teacher_b.account.user_id}


def test_the_admin_reads_everything(p1):
    both = {p1.teacher.account.user_id, p1.hod.account.user_id, p1.teacher_b.account.user_id}
    assert both <= keys(rest("GET", "/teacher_preferences?select=teacher_id", p1.admin.token), "teacher_id")
    assert {S["win_p"], S["win_b"]} <= keys(rest("GET", "/preference_windows?select=id", p1.admin.token), "id")


def test_the_workload_norm_is_for_managers_only(p1):
    assert rest("GET", "/workload_norms?select=id", p1.teacher.token).json() == []                     # a teacher sees none
    assert rest("GET", "/workload_norms?select=id", p1.teacher_b.token).json() == []
    assert keys(rest("GET", "/workload_norms?select=id", p1.hod.token), "id") == {S["norm_p"], S["norm_c"]}   # own school + the college default
    assert {S["norm_p"], S["norm_b"], S["norm_c"]} <= keys(rest("GET", "/workload_norms?select=id", p1.admin.token), "id")


def test_the_audit_log_is_admin_only(p1):
    assert keys(rest("GET", "/audit_log?select=id&limit=1", p1.admin.token), "id")
    for who in (p1.hod, p1.teacher, p1.teacher_b):
        r = rest("GET", "/audit_log?select=id&limit=5", who.token)
        assert r.status_code in DENIED or r.json() == [], who.account.role
    assert rest("GET", "/audit_log?select=id&limit=1").status_code in DENIED


@pytest.mark.parametrize("table", TABLES)
def test_nothing_can_be_written_from_a_browser(p1, table):
    for who in (p1.teacher, p1.hod, p1.admin):
        assert rest("POST", f"/{table}", who.token, json={}).status_code in DENIED, (who.account.role, table)
        assert rest("PATCH", f"/{table}?school_id=not.is.null", who.token, json={"school_id": str(uuid.uuid4())}).status_code in DENIED
        assert rest("DELETE", f"/{table}?school_id=not.is.null", who.token).status_code in DENIED
    assert db_val("select count(*) from public.preference_windows where id = $1", uuid.UUID(S["win_p"])) == 1                  # all still there
    assert db_val("select max_per_day from public.teacher_preferences where teacher_id = $1", uuid.UUID(p1.teacher.account.user_id)) == 4
