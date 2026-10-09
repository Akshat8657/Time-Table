"""Phase 4 Row Level Security, through Supabase's PUBLIC data API: teachers read only the PUBLISHED rows where they teach and only
their own notifications; the Web Push keys are invisible to everybody; nothing can be written from a browser."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from .conftest import db_exec, db_fetch, db_val
from .test_rls import DENIED, ids, rest

S: dict = {}


@pytest.fixture(scope="module", autouse=True)
def rows(p1):
    school = uuid.UUID(p1.school)
    # the test school and people are shared by the whole run: forget what other test files published / sent
    for sql in ("delete from public.extra_lectures where school_id = $1", "delete from public.publish_events where school_id = $1",
                "delete from public.publications where school_id = $1"):
        db_exec(sql, school)
    people = [uuid.UUID(x.account.user_id) for x in (p1.admin, p1.hod, p1.teacher, p1.teacher_b)]
    db_exec("delete from public.notifications where user_id = any($1::uuid[])", people)
    db_exec("delete from public.push_subscriptions where user_id = any($1::uuid[])", people)
    term = db_val("insert into public.academic_terms (kind, year, is_active) values ('even', 2099, false) returning id")
    S["term"] = term
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ RLS Course', 'ZZR', 2) returning id", school)
    sem = db_val("insert into public.semesters (school_id, course_id, number) values ($1, $2, 1) returning id", school, cid)
    subj = db_val("insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours) values ($1,$2,'ZZ RLS Subject','ZZ-RLS-1',2,30) returning id", school, sem)
    pub = db_val("insert into public.publications (school_id, term_id, semester_id) values ($1,$2,$3) returning id", school, term, sem)
    t_me = uuid.UUID(p1.teacher.account.user_id)
    t_other = uuid.UUID(p1.hod.account.user_id)         # (the HOD plays "another person" here)
    mine = db_val(
        "insert into public.published_entries (publication_id, school_id, term_id, semester_id, kind, subject_id, label, day, start_period, length, teacher_id) "
        "values ($1,$2,$3,$4,'lecture',$5,'Mine',1,1,1,$6) returning id", pub, school, term, sem, subj, t_me,
    )
    theirs = db_val(
        "insert into public.published_entries (publication_id, school_id, term_id, semester_id, kind, subject_id, label, day, start_period, length, teacher_id) "
        "values ($1,$2,$3,$4,'lecture',$5,'Theirs',2,1,1,$6) returning id", pub, school, term, sem, subj, t_other,
    )
    co = db_val(
        "insert into public.published_entries (publication_id, school_id, term_id, semester_id, kind, subject_id, label, day, start_period, length, teacher_id, co_teacher_id) "
        "values ($1,$2,$3,$4,'lecture',$5,'Co-taught',3,1,1,$6,$7) returning id", pub, school, term, sem, subj, t_other, t_me,
    )
    ev = db_val("insert into public.publish_events (school_id, term_id, number, scope) values ($1,$2,1,'all') returning id", school, term)
    ex_mine = db_val(
        "insert into public.extra_lectures (school_id, term_id, semester_id, subject_id, lecture_date, start_period, length, teacher_id) values ($1,$2,$3,$4,$5,5,1,$6) returning id",
        school, term, sem, subj, date(2099, 1, 5), t_me,
    )
    ex_theirs = db_val(
        "insert into public.extra_lectures (school_id, term_id, semester_id, subject_id, lecture_date, start_period, length, teacher_id) values ($1,$2,$3,$4,$5,6,1,$6) returning id",
        school, term, sem, subj, date(2099, 1, 6), t_other,
    )
    n_me = db_val("insert into public.notifications (user_id, school_id, kind, title) values ($1,$2,'test','mine') returning id", t_me, school)
    n_other = db_val("insert into public.notifications (user_id, school_id, kind, title) values ($1,$2,'test','theirs') returning id", t_other, school)
    sub = db_val(
        "insert into public.push_subscriptions (user_id, endpoint, p256dh, auth) values ($1, 'https://fcm.googleapis.com/fcm/send/zz-rls', $2, $3) returning id",
        t_me, "B" * 87, "A" * 22,
    )
    S.update(pub=pub, mine=str(mine), theirs=str(theirs), co=str(co), ev=str(ev), ex_mine=str(ex_mine), ex_theirs=str(ex_theirs),
             n_me=str(n_me), n_other=str(n_other), sub=str(sub))
    yield
    db_exec("delete from public.publications where id = $1", pub)
    db_exec("delete from public.extra_lectures where term_id = $1", term)
    db_exec("delete from public.publish_events where term_id = $1", term)
    db_exec("delete from public.academic_terms where id = $1", term)


TABLES = ["publications", "published_entries", "publish_events", "extra_lectures", "notifications", "push_subscriptions"]


@pytest.mark.parametrize("table", TABLES)
def test_the_anonymous_visitor_reads_and_writes_nothing(p1, table):
    assert rest("GET", f"/{table}?select=*").status_code in DENIED
    assert rest("POST", f"/{table}", json={}).status_code in DENIED
    assert rest("DELETE", f"/{table}?id=not.is.null").status_code in DENIED


def test_a_teacher_reads_only_the_published_rows_where_they_teach(p1):
    seen = ids(rest("GET", "/published_entries?select=id", p1.teacher.token))
    assert seen == {S["mine"], S["co"]}                                   # their own + the one where they are co-teacher; never "Theirs"
    assert S["theirs"] not in seen


def test_the_hod_and_admin_read_the_whole_school_but_another_school_reads_nothing(p1):
    assert {S["mine"], S["theirs"], S["co"]} <= ids(rest("GET", "/published_entries?select=id", p1.hod.token))
    assert {S["mine"], S["theirs"], S["co"]} <= ids(rest("GET", "/published_entries?select=id", p1.admin.token))
    assert ids(rest("GET", "/published_entries?select=id", p1.teacher_b.token)) == set()
    assert str(S["pub"]) not in ids(rest("GET", "/publications?select=id", p1.teacher_b.token))
    assert str(S["pub"]) in ids(rest("GET", "/publications?select=id", p1.teacher.token))          # the header (version / date) of the own school


def test_extra_lectures_follow_the_same_rule(p1):
    assert ids(rest("GET", "/extra_lectures?select=id", p1.teacher.token)) == {S["ex_mine"]}
    assert {S["ex_mine"], S["ex_theirs"]} <= ids(rest("GET", "/extra_lectures?select=id", p1.hod.token))
    assert ids(rest("GET", "/extra_lectures?select=id", p1.teacher_b.token)) == set()


def test_the_publish_log_is_for_the_hod_and_admin_only(p1):
    assert ids(rest("GET", "/publish_events?select=id", p1.teacher.token)) == set()
    assert S["ev"] in ids(rest("GET", "/publish_events?select=id", p1.hod.token))
    assert ids(rest("GET", "/publish_events?select=id", p1.teacher_b.token)) == set()


def test_everybody_reads_only_their_own_notifications(p1):
    assert ids(rest("GET", "/notifications?select=id", p1.teacher.token)) == {S["n_me"]}
    assert S["n_other"] in ids(rest("GET", "/notifications?select=id", p1.hod.token))
    assert S["n_me"] not in ids(rest("GET", "/notifications?select=id", p1.hod.token))
    assert S["n_me"] not in ids(rest("GET", "/notifications?select=id", p1.admin.token))              # not even the admin reads other people's bell


def test_the_push_keys_are_invisible_to_every_browser(p1):
    for who in (p1.teacher, p1.hod, p1.admin, p1.teacher_b):
        r = rest("GET", "/push_subscriptions?select=*", who.token)
        assert r.status_code in DENIED or r.json() == [], (who.account.role, r.status_code, r.text[:120])


@pytest.mark.parametrize("table", TABLES)
def test_nothing_can_be_written_from_a_browser(p1, table):
    for who in (p1.teacher, p1.hod, p1.admin):
        assert rest("POST", f"/{table}", who.token, json={}).status_code in DENIED, (who.account.role, table)
        assert rest("PATCH", f"/{table}?id=not.is.null", who.token, json={"id": str(uuid.uuid4())}).status_code in DENIED
        assert rest("DELETE", f"/{table}?id=not.is.null", who.token).status_code in DENIED
    assert db_val("select count(*) from public.published_entries where school_id = $1", uuid.UUID(p1.school)) == 3
    assert db_fetch("select is_read from public.notifications where id = $1", uuid.UUID(S["n_me"]))[0]["is_read"] is False
