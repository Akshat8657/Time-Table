"""Phase 5 Row Level Security, through Supabase's PUBLIC data API: a teacher reads only their own absence / leave / offers,
and only the substitutions where they are the absent teacher or the substitute, and only the day-adjustments of their own
lectures; HOD/admin read the whole school; another school reads nothing; no browser can write any of it."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from .conftest import db_exec, db_val
from .test_rls import DENIED, ids, rest

S: dict = {}


@pytest.fixture(scope="module", autouse=True)
def rows(p1):
    school = uuid.UUID(p1.school)
    # start from a school with no Phase-5 rows (other test files of this run leave some behind)
    for sql in (
        "delete from public.substitution_offers where school_id = $1", "delete from public.substitutions where school_id = $1",
        "delete from public.day_adjustments where school_id = $1", "delete from public.teacher_absences where school_id = $1",
        "delete from public.leave_requests where school_id = $1",
    ):
        db_exec(sql, school)
    me = uuid.UUID(p1.teacher.account.user_id)
    other = uuid.UUID(p1.hod.account.user_id)
    db_exec("delete from public.academic_terms where kind = 'even' and year = 2098")  # a crashed earlier run may have left this sentinel term behind
    term = db_val("insert into public.academic_terms (kind, year, is_active) values ('even', 2098, false) returning id")
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1,'ZZ RLS5','ZZR5',2) returning id", school)
    sem = db_val("insert into public.semesters (school_id, course_id, number) values ($1,$2,1) returning id", school, cid)
    S["term"] = term
    leave_me = db_val("insert into public.leave_requests (school_id, term_id, teacher_id, date_from, date_to) values ($1,$2,$3,$4,$4) returning id", school, term, me, date(2098, 1, 5))
    leave_other = db_val("insert into public.leave_requests (school_id, term_id, teacher_id, date_from, date_to) values ($1,$2,$3,$4,$4) returning id", school, term, other, date(2098, 1, 6))
    ab_me = db_val("insert into public.teacher_absences (school_id, term_id, teacher_id, absence_date) values ($1,$2,$3,$4) returning id", school, term, me, date(2098, 1, 5))
    ab_other = db_val("insert into public.teacher_absences (school_id, term_id, teacher_id, absence_date) values ($1,$2,$3,$4) returning id", school, term, other, date(2098, 1, 6))

    def mk_sub(absent, substitute, start):
        return db_val(
            "insert into public.substitutions (school_id, term_id, lecture_date, weekday, start_period, length, semester_ids, label, absent_teacher_id, substitute_id, status) "
            "values ($1,$2,$3,1,$4,1,$5::uuid[],'X',$6,$7,$8) returning id",
            school, term, date(2098, 1, 5), start, [sem], absent, substitute, "filled" if substitute else "open",
        )

    S.update(
        leave_me=str(leave_me), leave_other=str(leave_other), ab_me=str(ab_me), ab_other=str(ab_other),
        sub_absent=str(mk_sub(me, None, 1)), sub_cover=str(mk_sub(other, me, 2)), sub_other=str(mk_sub(other, None, 3)),
    )
    S["offer_me"] = str(db_val("insert into public.substitution_offers (substitution_id, school_id, teacher_id, expires_at) values ($1,$2,$3, now() + interval '1 hour') returning id", uuid.UUID(S["sub_other"]), school, me))
    S["offer_other"] = str(db_val("insert into public.substitution_offers (substitution_id, school_id, teacher_id, expires_at) values ($1,$2,$3, now() + interval '1 hour') returning id", uuid.UUID(S["sub_other"]), school, other))
    S["adj_me"] = str(db_val("insert into public.day_adjustments (school_id, term_id, lecture_date, kind, semester_ids, orig_start, length, new_start, label, teacher_id) values ($1,$2,$3,'move',$4::uuid[],1,1,4,'X',$5) returning id", school, term, date(2098, 1, 5), [sem], me))
    S["adj_other"] = str(db_val("insert into public.day_adjustments (school_id, term_id, lecture_date, kind, semester_ids, orig_start, length, new_start, label, teacher_id) values ($1,$2,$3,'move',$4::uuid[],2,1,5,'X',$5) returning id", school, term, date(2098, 1, 5), [sem], other))
    yield
    db_exec("delete from public.academic_terms where id = $1", term)   # cascades everything above


TABLES = ["leave_requests", "teacher_absences", "substitutions", "substitution_offers", "day_adjustments"]


@pytest.mark.parametrize("table", TABLES)
def test_anonymous_reads_and_writes_nothing(p1, table):
    assert rest("GET", f"/{table}?select=*").status_code in DENIED
    assert rest("POST", f"/{table}", json={}).status_code in DENIED


def test_teacher_reads_only_their_own_absence_and_leave(p1):
    assert ids(rest("GET", "/teacher_absences?select=id", p1.teacher.token)) == {S["ab_me"]}
    assert ids(rest("GET", "/leave_requests?select=id", p1.teacher.token)) == {S["leave_me"]}


def test_teacher_reads_substitutions_only_where_absent_or_substitute(p1):
    seen = ids(rest("GET", "/substitutions?select=id", p1.teacher.token))
    assert seen == {S["sub_absent"], S["sub_cover"]} and S["sub_other"] not in seen


def test_teacher_reads_only_their_own_offers_and_adjustments(p1):
    assert ids(rest("GET", "/substitution_offers?select=id", p1.teacher.token)) == {S["offer_me"]}
    assert ids(rest("GET", "/day_adjustments?select=id", p1.teacher.token)) == {S["adj_me"]}


def test_hod_reads_the_whole_school_and_another_school_reads_nothing(p1):
    for table, mine in (("teacher_absences", S["ab_other"]), ("leave_requests", S["leave_other"]), ("substitutions", S["sub_other"]),
                        ("substitution_offers", S["offer_other"]), ("day_adjustments", S["adj_other"])):
        assert mine in ids(rest("GET", f"/{table}?select=id", p1.hod.token)), table
        assert ids(rest("GET", f"/{table}?select=id", p1.teacher_b.token)) == set(), table


@pytest.mark.parametrize("table", TABLES)
def test_nothing_can_be_written_from_a_browser(p1, table):
    for who in (p1.teacher, p1.hod, p1.admin):
        assert rest("POST", f"/{table}", who.token, json={}).status_code in DENIED, (who.account.role, table)
        assert rest("PATCH", f"/{table}?id=not.is.null", who.token, json={"id": str(uuid.uuid4())}).status_code in DENIED
        assert rest("DELETE", f"/{table}?id=not.is.null", who.token).status_code in DENIED
    assert db_val("select status from public.substitutions where id = $1", uuid.UUID(S["sub_absent"])) == "open"
